"""
Engine 1 — Structure.

Fix from the previous version: structure_engine() used to be run on
whatever single timeframe analyze_market() happened to be called
with, so "Structure" silently meant M15 structure when called on
M15 data — contradicting the spec's H4 (macro) / H1 (direction)
hierarchy. Now it's timeframe-aware, and multi_timeframe_structure()
combines H4 + H1 with H4 given priority, so M15/M5 noise can never
silently override higher-timeframe structure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd


def detect_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> Tuple[List[int], List[int]]:
    highs, lows = [], []
    for i in range(left, len(df) - right):
        window_high = df["high"].iloc[i - left : i + right + 1]
        if df["high"].iloc[i] == window_high.max():
            highs.append(i)
        window_low = df["low"].iloc[i - left : i + right + 1]
        if df["low"].iloc[i] == window_low.min():
            lows.append(i)
    return highs, lows


def structure_engine(df: pd.DataFrame, timeframe: str) -> Dict[str, Any]:
    highs, lows = detect_swings(df, 2, 2)
    bias = 0
    events: List[str] = []

    if len(highs) >= 2:
        h1, h2 = df["high"].iloc[highs[-2]], df["high"].iloc[highs[-1]]
        if h2 > h1:
            events.append("Higher High")
            bias += 20
        else:
            events.append("Lower High")
            bias -= 20

    if len(lows) >= 2:
        l1, l2 = df["low"].iloc[lows[-2]], df["low"].iloc[lows[-1]]
        if l2 > l1:
            events.append("Higher Low")
            bias += 25
        else:
            events.append("Lower Low")
            bias -= 25

    bias = max(-100, min(100, bias))
    direction = "BULLISH" if bias > 15 else "BEARISH" if bias < -15 else "NEUTRAL"

    return {
        "timeframe": timeframe,
        "direction": direction,
        "directional_contribution": bias,
        "evidence_strength": "STRONG" if abs(bias) >= 40 else "MODERATE" if abs(bias) >= 20 else "WEAK",
        "events": events,
        "last_price": float(df["close"].iloc[-1]),
    }


def multi_timeframe_structure(h4_df: pd.DataFrame, h1_df: pd.DataFrame) -> Dict[str, Any]:
    """H4 sets the dominant bias; H1 can confirm or flag a conflict but
    cannot flip the H4-derived direction on its own — that requires an
    explicit CHoCH read, which is a Phase 2/3 addition, not silent override."""
    h4 = structure_engine(h4_df, "H4")
    h1 = structure_engine(h1_df, "H1")

    if h4["direction"] == "NEUTRAL":
        combined_direction = h1["direction"]
        combined_contribution = h1["directional_contribution"] * 0.5
    else:
        combined_direction = h4["direction"]
        agree = h1["direction"] == h4["direction"]
        combined_contribution = h4["directional_contribution"] * (1.0 if agree else 0.6)

    conflict = h4["direction"] != "NEUTRAL" and h1["direction"] != "NEUTRAL" and h4["direction"] != h1["direction"]

    return {
        "engine": "STRUCTURE",
        "direction": combined_direction,
        "directional_contribution": round(combined_contribution),
        "evidence_strength": "STRONG" if abs(combined_contribution) >= 30 else "MODERATE" if abs(combined_contribution) >= 15 else "WEAK",
        "h4": h4,
        "h1": h1,
        "h4_h1_conflict": conflict,
        "data_quality": "HIGH",
    }
