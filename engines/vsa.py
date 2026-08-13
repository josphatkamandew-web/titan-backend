from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def vsa_engine(df: pd.DataFrame, volume_type: str) -> Dict[str, Any]:
    last = df.iloc[-1]
    spread_median = df["spread"].rolling(20).median().iloc[-1]
    spread = last["spread"]

    rvol = last["rvol"] if pd.notna(last.get("rvol")) else np.nan
    close_position = (last["close"] - last["low"]) / spread if spread > 0 else 0.5

    direction, contribution, pattern = "NEUTRAL", 0, "NO CLEAR VSA SIGNAL"

    if not np.isnan(rvol):
        if rvol >= 1.5 and last["direction"] == -1 and close_position > 0.60:
            pattern, direction, contribution = "POTENTIAL STOPPING VOLUME", "BULLISH", 55
        elif rvol >= 1.5 and last["direction"] == 1 and close_position < 0.40:
            pattern, direction, contribution = "POTENTIAL UPTHRUST", "BEARISH", -55
        elif rvol < 0.75 and last["direction"] == -1 and spread <= spread_median:
            pattern, direction, contribution = "POTENTIAL NO SUPPLY", "BULLISH", 35
        elif rvol < 0.75 and last["direction"] == 1 and spread <= spread_median:
            pattern, direction, contribution = "POTENTIAL NO DEMAND", "BEARISH", -35

    # If volume is UNAVAILABLE, we can still read the price/spread-only
    # subset (Test, Narrow/Wide Spread) — but never the volume-weighted
    # patterns above. Downstream data_quality reflects this.
    if volume_type == "UNAVAILABLE":
        direction, contribution, pattern = "NEUTRAL", 0, "VOLUME UNAVAILABLE — PRICE-ONLY VSA NOT YET IMPLEMENTED IN MVP"

    data_quality = {"TRUE": "HIGH", "TICK_ESTIMATE": "MEDIUM", "UNAVAILABLE": "LOW"}.get(volume_type, "MEDIUM")

    return {
        "engine": "VSA",
        "direction": direction,
        "directional_contribution": contribution,
        "pattern": pattern,
        "rvol": float(rvol) if not np.isnan(rvol) else None,
        "effort": "HIGH" if (not np.isnan(rvol) and rvol >= 1.5) else "LOW" if (not np.isnan(rvol) and rvol < 0.75) else "NORMAL",
        "result": {"spread": float(spread), "close_position": float(close_position)},
        "volume_type": volume_type,
        "volume_disclaimer": "Tick/quote-derived volume, not centralized true traded volume." if volume_type == "TICK_ESTIMATE" else None,
        "data_quality": data_quality,
    }
