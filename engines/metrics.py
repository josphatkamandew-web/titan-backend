from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Idempotent: safe to call once per fetch and pass the same
    enriched frame into every engine instead of each engine
    recomputing it independently."""
    df = df.copy()
    df["spread"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["direction"] = np.where(
        df["close"] > df["open"], 1, np.where(df["close"] < df["open"], -1, 0)
    )
    df["atr"] = calculate_atr(df, 14)

    if "volume" in df and df["volume"].notna().any():
        df["avg_volume"] = df["volume"].rolling(20).mean()
        df["rvol"] = df["volume"] / df["avg_volume"].replace(0, np.nan)
    else:
        df["avg_volume"] = np.nan
        df["rvol"] = np.nan

    return df
