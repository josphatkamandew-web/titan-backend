"""
Twelve Data adapter — PRIMARY live data source.

Cloud-friendly REST API, works from any hosted backend (no local
terminal required, unlike MT5).

Key limitation (see data/base.py NO_TRUE_VOLUME_INSTRUMENTS):
Twelve Data's time_series endpoint includes real trading volume
mainly for exchange-listed instruments (stocks, crypto, indices).
Spot FX pairs (EURUSD, GBPUSD) are OTC — there is no centralized
tape, so a "volume" figure there is not true traded size. XAUUSD's
volume support depends on plan/classification — check has_volume
in a live response rather than assuming either way; if it's still
UNAVAILABLE after upgrading your plan, that plan tier doesn't
include it for this instrument and VSA will stay non-functional
for gold until it does.

PAGINATION: a single time_series call is capped at 5000 bars by the
provider. Requesting more than that here walks backwards in 5000-bar
chunks using the `end_date` param and stitches them together — this
is what makes "1-2 years of H1 history" possible instead of being
stuck at ~7 months. Each chunk is a separate request, so asking for
a lot of history costs more API credits — be mindful of your plan's
rate limits if bumping this up a lot.
"""

from __future__ import annotations

import os
import re
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

INTERVAL_TIMEDELTA = {
    "H4": pd.Timedelta(hours=4),
    "H1": pd.Timedelta(hours=1),
    "M15": pd.Timedelta(minutes=15),
    "M5": pd.Timedelta(minutes=5),
}

SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
}

MAX_CHUNK_SIZE = 5000
MAX_CHUNKS = 6  # hard cap so a large `bars` request can't runaway into excessive API credit use


class TwelveDataAdapter(DataAdapter):
    name = "TWELVE_DATA"

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None,
                 timeout: float = 15.0):
        self.api_key = api_key or os.environ.get("TWELVE_DATA_API_KEY")
        if not self.api_key:
            raise DataUnavailableError(
                "TWELVE_DATA_API_KEY is not set. Refusing to run without a real key "
                "rather than silently falling back."
            )
        self.session = session or requests.Session()
        self.timeout = timeout

    def _fetch_chunk(self, td_symbol: str, interval: str, outputsize: int,
                      end_date: Optional[str] = None) -> tuple[pd.DataFrame, bool]:
        params = {
            "symbol": td_symbol,
            "interval": interval,
            "outputsize": min(outputsize, MAX_CHUNK_SIZE),
            "apikey": self.api_key,
            "order": "ASC",
            "timezone": "UTC",
        }
        if end_date:
            params["end_date"] = end_date

        try:
            resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise DataUnavailableError(f"Twelve Data request failed: {_redact(str(exc))}") from exc

        if payload.get("status") == "error" or "values" not in payload:
            raise DataUnavailableError(f"Twelve Data error response: {_redact(str(payload))}")

        values = payload["values"]
        if not values:
            return pd.DataFrame(), False

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

        return df.sort_values("time").reset_index(drop=True), has_volume

    def fetch_ohlcv(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        if timeframe not in INTERVAL_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        td_symbol = SYMBOL_MAP.get(symbol, symbol)
        interval = INTERVAL_MAP[timeframe]

        frames = []
        remaining = bars
        end_date: Optional[str] = None
        has_volume_any = False
        chunks = 0

        while remaining > 0 and chunks < MAX_CHUNKS:
            requested = min(remaining, MAX_CHUNK_SIZE)
            chunk_df, has_volume = self._fetch_chunk(td_symbol, interval, requested, end_date)
            if chunk_df.empty:
                break
            frames.append(chunk_df)
            has_volume_any = has_volume_any or has_volume
            remaining -= len(chunk_df)
            chunks += 1

            if len(chunk_df) < requested:
                # Provider returned fewer bars than asked for — it's run out of history.
                break

            earliest = chunk_df["time"].min()
            end_date = (earliest - INTERVAL_TIMEDELTA[timeframe]).strftime("%Y-%m-%d %H:%M:%S")

        if not frames:
            raise DataUnavailableError(f"Twelve Data returned no bars for {symbol} {timeframe}")

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)

        if symbol in NO_TRUE_VOLUME_INSTRUMENTS:
            volume_type = "TICK_ESTIMATE" if has_volume_any else "UNAVAILABLE"
        else:
            volume_type = "TRUE" if has_volume_any else "UNAVAILABLE"

        data_quality = "HIGH" if volume_type != "UNAVAILABLE" else "MEDIUM"

        return FetchResult(df=df, source=self.name, volume_type=volume_type, data_quality=data_quality)


def _redact(text: str) -> str:
    """Strip the API key out of any error text before it can reach a log,
    an API response, or a screenshot."""
    return re.sub(r"(apikey=)[^&\s]+", r"\1[REDACTED]", text)
