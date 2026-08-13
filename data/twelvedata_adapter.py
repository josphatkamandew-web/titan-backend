"""
Twelve Data adapter — PRIMARY live data source.

Cloud-friendly REST API, works from any hosted backend (no local
terminal required, unlike MT5). This is why it's primary: the
website needs to run without a Windows machine sitting behind it.

Key limitation (see data/base.py NO_TRUE_VOLUME_INSTRUMENTS):
Twelve Data's time_series endpoint includes real trading volume
mainly for exchange-listed instruments (stocks, crypto, indices).
Spot FX pairs (EURUSD, GBPUSD) are OTC — there is no centralized
tape, so a "volume" figure there is not true traded size. XAUUSD's
volume support depends on plan/classification and must be checked
against a live response before being trusted as TRUE.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import requests

from .base import DataAdapter, DataUnavailableError, FetchResult, NO_TRUE_VOLUME_INSTRUMENTS

BASE_URL = "https://api.twelvedata.com/time_series"

INTERVAL_MAP = {
    "H4": "4h",
    "H1": "1h",
    "M15": "15min",
    "M5": "5min",
}

SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
}


class TwelveDataAdapter(DataAdapter):
    name = "TWELVE_DATA"

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None,
                 timeout: float = 10.0):
        self.api_key = api_key or os.environ.get("TWELVE_DATA_API_KEY")
        if not self.api_key:
            raise DataUnavailableError(
                "TWELVE_DATA_API_KEY is not set. Refusing to run without a real key "
                "rather than silently falling back."
            )
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_ohlcv(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        if timeframe not in INTERVAL_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        td_symbol = SYMBOL_MAP.get(symbol, symbol)
        params = {
            "symbol": td_symbol,
            "interval": INTERVAL_MAP[timeframe],
            "outputsize": min(bars, 5000),
            "apikey": self.api_key,
            "order": "ASC",
            "timezone": "UTC",
        }

        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise DataUnavailableError(f"Twelve Data request failed: {exc}") from exc

        if payload.get("status") == "error" or "values" not in payload:
            raise DataUnavailableError(f"Twelve Data error response: {payload}")

        values = payload["values"]
        if not values:
            raise DataUnavailableError(f"Twelve Data returned no bars for {symbol} {timeframe}")

        df = pd.DataFrame(values)
        df = df.rename(columns={"datetime": "time"})
        df["time"] = pd.to_datetime(df["time"], utc=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        has_volume = "volume" in df.columns and df["volume"].notna().any()
        if has_volume:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        else:
            df["volume"] = pd.NA

        df = df.sort_values("time").reset_index(drop=True)

        if symbol in NO_TRUE_VOLUME_INSTRUMENTS:
            # Never let an FX pair claim TRUE volume even if the field is populated —
            # it is tick/quote-derived, not centralized traded size.
            volume_type = "TICK_ESTIMATE" if has_volume else "UNAVAILABLE"
        else:
            # e.g. XAUUSD — confirm against a live response; do not assume.
            volume_type = "TRUE" if has_volume else "UNAVAILABLE"

        data_quality = "HIGH" if volume_type != "UNAVAILABLE" else "MEDIUM"

        return FetchResult(df=df, source=self.name, volume_type=volume_type, data_quality=data_quality)
