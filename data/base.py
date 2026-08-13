"""
Common interface every data adapter (Twelve Data, MT5, future sources)
must implement, plus the shared OHLCV validation used before ANY
engine is allowed to run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
import pandas as pd

# Instruments where we know there is no centralized tape, so any
# "volume" figure is a proxy, never true traded volume.
NO_TRUE_VOLUME_INSTRUMENTS = {"EURUSD", "GBPUSD"}

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class DataUnavailableError(Exception):
    """Raised when an adapter cannot produce usable data."""


@dataclass
class FetchResult:
    df: pd.DataFrame
    source: str            # "TWELVE_DATA" | "MT5"
    volume_type: str       # "TRUE" | "TICK_ESTIMATE" | "UNAVAILABLE"
    data_quality: str      # "HIGH" | "MEDIUM" | "LOW"


class DataAdapter(ABC):
    name: str = "BASE"

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        ...


def validate_ohlcv(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Validate incoming market data before ANY engine is allowed
    to calculate signals. Mirrors the Data Health screen fields
    exactly (missing bars / duplicate timestamps / volume anomalies
    / data gaps) so the UI can render this directly.
    """
    errors, warnings = [], []

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        errors.append(f"Missing columns: {missing_cols}")

    if df.empty:
        errors.append("Dataset is empty.")
        return {"valid": False, "errors": errors, "warnings": warnings,
                "missing_bars": None, "duplicate_timestamps": None,
                "volume_anomalies": None, "data_gaps_over_1h": None}

    for col in ["open", "high", "low", "close"]:
        if col in df:
            bad = int(df[col].isna().sum())
            if bad:
                errors.append(f"{col}: {bad} missing values.")

    dup_ts = int(df["time"].duplicated().sum()) if "time" in df else None
    if dup_ts:
        errors.append("Duplicate timestamps detected.")

    if "time" in df and not df["time"].is_monotonic_increasing:
        errors.append("Timestamps are not chronological.")

    if "high" in df and "low" in df and (df["high"] < df["low"]).any():
        errors.append("High < Low detected.")

    vol_anomalies = 0
    if "volume" in df and df["volume"].notna().any():
        vol_anomalies = int((df["volume"] < 0).sum())
        if vol_anomalies:
            errors.append("Negative volume detected.")

    gaps_over_1h = None
    if "time" in df and len(df) > 1:
        deltas = df["time"].diff().dropna()
        gaps_over_1h = int((deltas > pd.Timedelta(hours=1)).sum())

    if len(df) < 100:
        warnings.append("Dataset contains fewer than 100 bars.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "missing_bars": 0,  # filled in by caller if resampling reveals gaps
        "duplicate_timestamps": dup_ts,
        "volume_anomalies": vol_anomalies,
        "data_gaps_over_1h": gaps_over_1h,
    }
