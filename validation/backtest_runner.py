"""
Backtest runner — was completely missing before. validation/stats.py
had solid statistics and a promotion gate but nothing that actually
generated a List[TradeResult] from history. This module is that
missing bridge: walk historical bars, run each MVP engine on a
rolling window, open a hypothetical trade when an engine fires past
a threshold, walk forward to resolve it against stop/target, and
hand the results to validate_setup().

Walk-forward: the run is split into an IN_SAMPLE period (first 70%)
and an OUT_OF_SAMPLE period (last 30%). Only OUT_OF_SAMPLE trades are
used for the promotion decision — this stops a rule (even a fixed,
untuned one) from being promoted purely on the same stretch of data
used to eyeball it. IN_SAMPLE stats are reported alongside for
reference but never gate promotion on their own.

This is intentionally still simple: fixed thresholds, one open trade
per engine at a time (no pyramiding), ATR-based stop/target. Good
enough to get real numbers behind the MVP engines; refine later.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from engines.metrics import add_metrics
from engines.liquidity import liquidity_sweep_engine
from engines.regime import regime_engine
from engines.session_risk import get_session
from engines.structure import structure_engine
from engines.vsa import vsa_engine
from validation.stats import TradeResult, validate_setup

MIN_WINDOW = 60
MAX_HOLD_BARS = 40
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 3.0
DIRECTIONAL_TRIGGER_THRESHOLD = 40  # only backtest reasonably confident triggers


EngineFn = Callable[[pd.DataFrame], Dict[str, Any]]

ENGINE_REGISTRY: Dict[str, EngineFn] = {
    "STRUCTURE": lambda w: structure_engine(w, "BACKTEST"),
    "LIQUIDITY_SWEEP": liquidity_sweep_engine,
    "REGIME": regime_engine,
}


def _vsa_wrapper(window: pd.DataFrame, volume_type: str) -> Dict[str, Any]:
    return vsa_engine(window, volume_type)


def _resolve_trade(df: pd.DataFrame, start_idx: int, direction: str, entry: float,
                    stop: float, target: float) -> TradeResult | None:
    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        return None

    end_idx = min(start_idx + MAX_HOLD_BARS, len(df) - 1)
    for i in range(start_idx + 1, end_idx + 1):
        bar = df.iloc[i]
        if direction == "BULLISH":
            hit_stop = bar["low"] <= stop
            hit_target = bar["high"] >= target
        else:
            hit_stop = bar["high"] >= stop
            hit_target = bar["low"] <= target

        if hit_stop and hit_target:
            # Ambiguous same-bar resolution — conservative assumption: stop hit first.
            r = -1.0
            return TradeResult("BACKTEST", direction, entry, stop, target, "LOSS", r)
        if hit_stop:
            return TradeResult("BACKTEST", direction, entry, stop, target, "LOSS", -1.0)
        if hit_target:
            reward_distance = abs(target - entry)
            r = reward_distance / risk_distance
            return TradeResult("BACKTEST", direction, entry, stop, target, "WIN", r)

    # Timed out without hitting either — scratch at the last held price.
    last_close = df.iloc[end_idx]["close"]
    r = ((last_close - entry) if direction == "BULLISH" else (entry - last_close)) / risk_distance
    outcome = "WIN" if r > 0 else "LOSS" if r < 0 else "SCRATCH"
    return TradeResult("BACKTEST", direction, entry, stop, target, outcome, round(r, 3))


def backtest_engine(df: pd.DataFrame, engine_name: str, volume_type: str = "UNAVAILABLE") -> List[TradeResult]:
    """Run one engine across the full history and return every
    hypothetical trade it would have triggered."""
    df = add_metrics(df)
    trades: List[TradeResult] = []

    i = MIN_WINDOW
    while i < len(df) - 1:
        window = df.iloc[max(0, i - MIN_WINDOW): i + 1]

        if engine_name == "VSA":
            result = _vsa_wrapper(window, volume_type)
        else:
            fn = ENGINE_REGISTRY.get(engine_name)
            if fn is None:
                raise ValueError(f"No backtest wiring for engine {engine_name}")
            result = fn(window)

        contribution = result.get("directional_contribution", 0)
        if abs(contribution) >= DIRECTIONAL_TRIGGER_THRESHOLD:
            direction = "BULLISH" if contribution > 0 else "BEARISH"
            entry_bar = df.iloc[i]
            entry = float(entry_bar["close"])
            atr = float(entry_bar["atr"]) if pd.notna(entry_bar.get("atr")) else None
            if atr and atr > 0:
                if direction == "BULLISH":
                    stop, target = entry - atr * STOP_ATR_MULT, entry + atr * TARGET_ATR_MULT
                else:
                    stop, target = entry + atr * STOP_ATR_MULT, entry - atr * TARGET_ATR_MULT

                trade = _resolve_trade(df, i, direction, entry, stop, target)
                if trade is not None:
                    trade.regime = ""  # left for a future pass that tags regime per trade
                    trade.session = get_session(entry_bar["time"]) if "time" in entry_bar else ""
                    trades.append(trade)
                    i += MAX_HOLD_BARS  # don't open overlapping trades on the same engine
                    continue
        i += 1

    return trades


def run_validation(df: pd.DataFrame, engine_name: str, instrument: str, volume_type: str,
                    store, minimum_sample: int = 100) -> Dict[str, Any]:
    """Full pipeline: backtest -> walk-forward split -> statistics ->
    promotion decision -> persist to ValidationStore."""
    all_trades = backtest_engine(df, engine_name, volume_type)

    split_idx = int(len(all_trades) * 0.7)
    in_sample, out_of_sample = all_trades[:split_idx], all_trades[split_idx:]

    in_sample_result = validate_setup(in_sample, minimum_sample)
    out_of_sample_result = validate_setup(out_of_sample, minimum_sample)

    # Promotion is decided on OUT_OF_SAMPLE only.
    decision = out_of_sample_result["promotion"]["decision"]
    stats = out_of_sample_result["statistics"]

    store.upsert_status(
        instrument=instrument,
        engine=engine_name,
        setup="DEFAULT",
        status=decision,
        sample_size=stats.get("sample_size", 0),
        win_rate_percent=stats.get("win_rate_percent"),
        expectancy_r=stats.get("expectancy_r"),
        profit_factor=stats.get("profit_factor"),
        reason=out_of_sample_result["promotion"]["reason"],
    )

    return {
        "instrument": instrument,
        "engine": engine_name,
        "total_trades_all_history": len(all_trades),
        "in_sample": in_sample_result,
        "out_of_sample": out_of_sample_result,
        "promoted_status": decision,
    }
