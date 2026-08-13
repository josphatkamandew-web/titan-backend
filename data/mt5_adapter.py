"""
MetaTrader 5 adapter — LOCAL / BACKUP source only.

Important operational constraint: the `MetaTrader5` Python package
only works against a running MT5 terminal, logged into a broker,
on the same (Windows) machine. It cannot be called from a typical
hosted Linux backend. This adapter is intended to run:
  - on your own machine when you're trading locally, or
  - as a secondary source behind Twelve Data, only reachable when
    that specific worker process has MT5 available.

Never assume this adapter is reachable from the main web backend.
"""

from __future__ import annotations

import pandas as pd

from .base import DataAdapter, DataUnavailableError, FetchResult, NO_TRUE_VOLUME_INSTRUMENTS

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

TIMEFRAME_MAP = {
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
}


class MT5Adapter(DataAdapter):
    name = "MT5"

    def __init__(self):
        if mt5 is None:
            raise DataUnavailableError("MetaTrader5 package not installed on this host.")
        if not mt5.initialize():
            raise DataUnavailableError(f"MT5 initialization failed: {mt5.last_error()}")

    def fetch_ohlcv(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        tf_name = TIMEFRAME_MAP.get(timeframe)
        if not tf_name:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        tf = getattr(mt5, tf_name)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            raise DataUnavailableError(f"No MT5 data returned for {symbol} {timeframe}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df[["time", "open", "high", "low", "close", "volume"]]

        # MT5 FX volume is always broker tick volume — never true exchange volume,
        # regardless of instrument.
        volume_type = "TICK_ESTIMATE"
        data_quality = "MEDIUM" if symbol in NO_TRUE_VOLUME_INSTRUMENTS else "MEDIUM"

        return FetchResult(df=df, source=self.name, volume_type=volume_type, data_quality=data_quality)
