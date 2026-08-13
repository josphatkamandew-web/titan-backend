"""
TEST-ONLY adapter that generates a random-walk OHLCV series so the
whole pipeline (data -> engines -> fusion -> backtest -> API) can be
exercised without a live Twelve Data key or an MT5 terminal. Not
part of the production adapter chain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import DataAdapter, FetchResult


class SyntheticAdapter(DataAdapter):
    name = "SYNTHETIC"

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)

    def fetch_ohlcv(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        n = bars
        freq = {"H4": "4h", "H1": "1h", "M15": "15min", "M5": "5min"}[timeframe]
        times = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")

        drift = self.rng.normal(0, 1, n).cumsum() * 0.5
        base = 2000 + drift
        noise = self.rng.normal(0, 2.5, n)
        close = base + noise

        open_ = np.roll(close, 1)
        open_[0] = close[0]
        high = np.maximum(open_, close) + np.abs(self.rng.normal(1, 1, n))
        low = np.minimum(open_, close) - np.abs(self.rng.normal(1, 1, n))
        volume = np.abs(self.rng.normal(1000, 300, n))

        df = pd.DataFrame({
            "time": times, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        })

        return FetchResult(df=df, source=self.name, volume_type="TICK_ESTIMATE", data_quality="MEDIUM")
