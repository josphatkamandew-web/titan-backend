from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def get_session(timestamp) -> str:
    hour = timestamp.hour
    if 0 <= hour < 8:
        return "ASIAN"
    if 8 <= hour < 13:
        return "LONDON"
    if 13 <= hour < 21:
        return "NEW_YORK"
    return "OFF_SESSION"


def session_engine(df: pd.DataFrame) -> Dict[str, Any]:
    last_time = df["time"].iloc[-1]
    session = get_session(last_time)
    current_day = last_time.date()
    day = df[df["time"].dt.date == current_day]

    if day.empty:
        return {"engine": "SESSION", "session": session, "directional_contribution": 0,
                "direction": "NEUTRAL", "data_quality": "LOW"}

    session_high, session_low = float(day["high"].max()), float(day["low"].min())
    price = float(df["close"].iloc[-1])
    midpoint = (session_high + session_low) / 2

    if price > midpoint:
        contribution, context = 10, "UPPER_HALF"
    elif price < midpoint:
        contribution, context = -10, "LOWER_HALF"
    else:
        contribution, context = 0, "MID_RANGE"

    return {
        "engine": "SESSION",
        "session": session,
        "session_high": session_high,
        "session_low": session_low,
        "directional_context": context,
        "directional_contribution": contribution,
        "direction": "BULLISH" if contribution > 0 else "BEARISH" if contribution < 0 else "NEUTRAL",
        "data_quality": "HIGH",
    }


def calculate_trade_risk(direction: str, entry: float, stop: float, target: float,
                          account_equity: float, risk_per_trade: float, min_rr: float) -> Dict[str, Any]:
    if direction == "BULLISH":
        risk_distance, reward_distance = entry - stop, target - entry
    elif direction == "BEARISH":
        risk_distance, reward_distance = stop - entry, entry - target
    else:
        return {"valid": False, "reason": "No directional setup."}

    if risk_distance <= 0:
        return {"valid": False, "reason": "Invalid stop placement."}

    rr = reward_distance / risk_distance
    risk_money = account_equity * risk_per_trade

    return {
        "valid": rr >= min_rr,
        "risk_percent": risk_per_trade * 100,
        "risk_money": risk_money,
        "risk_distance": risk_distance,
        "reward_distance": reward_distance,
        "rr": rr,
        "minimum_rr": min_rr,
    }


def risk_engine(direction: str, entry: Optional[float], stop: Optional[float], target: Optional[float],
                 account_equity: float, risk_per_trade: float, min_rr: float,
                 trades_taken_today: int, max_daily_trades: int) -> Dict[str, Any]:
    if trades_taken_today >= max_daily_trades:
        return {
            "engine": "RISK",
            "valid": False,
            "reason": f"Daily trade cap reached ({trades_taken_today}/{max_daily_trades}).",
            "max_daily_trades": max_daily_trades,
            "trades_taken_today": trades_taken_today,
            "data_quality": "HIGH",
        }

    if entry is None or stop is None or target is None:
        return {"engine": "RISK", "valid": False, "reason": "No trade plan to size.",
                "max_daily_trades": max_daily_trades, "trades_taken_today": trades_taken_today,
                "data_quality": "HIGH"}

    result = calculate_trade_risk(direction, entry, stop, target, account_equity, risk_per_trade, min_rr)
    return {
        "engine": "RISK",
        **result,
        "max_daily_trades": max_daily_trades,
        "trades_taken_today": trades_taken_today,
        "data_quality": "HIGH",
    }
