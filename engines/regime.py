"""
Engine 0 — Market Regime.

Was listed in the spec's MVP but missing from the original core.py.
Every other engine's read depends on knowing trend vs. range first —
a "No Demand" bar means something different in a strong uptrend than
it does in a balanced range. This engine is price/volatility-derived
only (no volume dependency), so it works identically on FX and metals.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

LOOKBACK = 40


def regime_engine(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < LOOKBACK + 5:
        return {
            "engine": "REGIME",
            "regime": "UNKNOWN",
            "regime_strength": 0,
            "directional_contribution": 0,
            "data_quality": "LOW",
            "note": "Insufficient bars for regime classification.",
        }

    window = df.iloc[-LOOKBACK:]

    # Directional persistence: net displacement vs. total path traveled.
    net_move = window["close"].iloc[-1] - window["close"].iloc[0]
    path_length = window["close"].diff().abs().sum()
    persistence = abs(net_move) / path_length if path_length > 0 else 0.0

    # Volatility expansion/contraction: recent ATR vs. its own longer average.
    atr = window["atr"] if "atr" in window else None
    vol_ratio = 1.0
    if atr is not None and atr.notna().sum() >= 10:
        recent_atr = atr.iloc[-10:].mean()
        base_atr = atr.mean()
        vol_ratio = recent_atr / base_atr if base_atr > 0 else 1.0

    direction_sign = 1 if net_move > 0 else (-1 if net_move < 0 else 0)

    if persistence >= 0.35 and vol_ratio >= 0.9:
        regime = "TRENDING_BULLISH" if direction_sign > 0 else "TRENDING_BEARISH"
        strength = min(100, round(persistence * 150))
        contribution = direction_sign * min(20, round(strength * 0.2))
    elif vol_ratio < 0.6:
        regime = "CONTRACTION_BALANCE"
        strength = round((1 - vol_ratio) * 100)
        contribution = 0
    else:
        regime = "RANGING"
        strength = round((1 - persistence) * 100)
        contribution = 0

    return {
        "engine": "REGIME",
        "regime": regime,
        "regime_strength": int(max(0, min(100, strength))),
        "persistence_score": round(float(persistence), 3),
        "volatility_ratio": round(float(vol_ratio), 3),
        "directional_contribution": int(contribution),
        "direction": "BULLISH" if contribution > 0 else "BEARISH" if contribution < 0 else "NEUTRAL",
        "data_quality": "HIGH",
    }
