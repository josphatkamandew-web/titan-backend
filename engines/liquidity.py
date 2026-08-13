from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def liquidity_sweep_engine(df: pd.DataFrame, sweep_min_atr: float = 0.05) -> Dict[str, Any]:
    last = df.iloc[-1]
    lookback = df.iloc[-21:-1]

    prior_high = lookback["high"].max()
    prior_low = lookback["low"].min()
    atr = float(last["atr"]) if pd.notna(last.get("atr")) else 0.0
    min_penetration = sweep_min_atr * atr if atr > 0 else 0.0

    bull_penetration = prior_low - last["low"]
    bear_penetration = last["high"] - prior_high

    bullish_sweep = last["low"] < prior_low and last["close"] > prior_low and bull_penetration >= min_penetration
    bearish_sweep = last["high"] > prior_high and last["close"] < prior_high and bear_penetration >= min_penetration

    if bullish_sweep:
        direction, contribution, event = "BULLISH", 65, "SELL-SIDE LIQUIDITY SWEEP"
    elif bearish_sweep:
        direction, contribution, event = "BEARISH", -65, "BUY-SIDE LIQUIDITY SWEEP"
    else:
        direction, contribution, event = "NEUTRAL", 0, "NO CONFIRMED SWEEP"

    return {
        "engine": "LIQUIDITY_SWEEP",
        "direction": direction,
        "directional_contribution": contribution,
        "event": event,
        "prior_high": float(prior_high),
        "prior_low": float(prior_low),
        "penetration_atr_multiple": round(max(bull_penetration, bear_penetration, 0) / atr, 3) if atr > 0 else None,
        "data_quality": "HIGH",
    }
