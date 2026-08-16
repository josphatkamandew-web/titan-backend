"""
Backtest runner v2 — two principled improvements over the first pass,
both aimed at testing what these engines are actually for, not just
generating a number:

1. STRUCTURAL STOPS/TARGETS instead of blind ATR multiples. A stop now
   sits just beyond the real recent swing low/high; a target aims at
   the opposing swing extreme (the next real liquidity), falling back
   to a guaranteed-2R synthetic target only when the structural target
   is too close to be worth the trade. This is what the original spec
   asked for (Section 15.1) — the first version simplified it away for
   MVP speed, at the cost of ignoring the very structure the engines
   just detected.

2. CONFLUENCE FILTERING. Previously every engine fired in total
   isolation — a Liquidity Sweep counted as a signal even if H4/H1
   structure was screaming the opposite direction, a VSA read counted
   even in a dead-flat range. Now each engine's trigger is checked
   against the other context engines before a hypothetical trade opens
   at all. Fewer trades, but each one reflects agreement rather than
   one engine's opinion in a vacuum — which is the whole premise behind
   the Fusion Engine treating agreement as a confidence input.

Neither change was chosen to make the win rate look better — both are
grounded in "how would you actually read this signal," not tuned
after seeing a result. That distinction matters: tuning parameters
until backtest numbers improve is curve-fitting, and it would quietly
recreate the exact "sounds sophisticated, no real edge" problem this
whole system was built to avoid.

Walk-forward split (70% in-sample / 30% out-of-sample) is unchanged —
promotion is still decided on the out-of-sample slice only.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from engines.metrics import add_metrics
from engines.liquidity import liquidity_sweep_engine
from engines.regime import regime_engine
from engines.session_risk import get_session
from engines.structure import structure_engine
from engines.vsa import vsa_engine
from validation.stats import TradeResult, validate_setup

MIN_WINDOW = 60
MAX_HOLD_BARS = 60          # ~2.5 days on H1 — matches an hours-to-days hold, not a scalp
STOP_ATR_MULT = 1.5         # fallback only, when structure doesn't offer a valid stop
DIRECTIONAL_TRIGGER_THRESHOLD = 40
STRUCTURE_LOOKBACK = 20     # bars used to find the swing extremes for stop/target
MIN_STRUCTURAL_RR = 1.2     # below this, the structural target isn't worth it — use fallback
FALLBACK_RR = 2.0           # guaranteed reward multiple when falling back


EngineFn = Callable[[pd.DataFrame], Dict[str, Any]]

ENGINE_REGISTRY: Dict[str, EngineFn] = {
    "STRUCTURE": lambda w: structure_engine(w, "BACKTEST"),
    "LIQUIDITY_SWEEP": liquidity_sweep_engine,
    "REGIME": regime_engine,
}


def _vsa_wrapper(window: pd.DataFrame, volume_type: str) -> Dict[str, Any]:
    return vsa_engine(window, volume_type)


# ---------------------------------------------------------------- #
# 1. Structural stop / target
# ---------------------------------------------------------------- #

def _swing_extremes(df: pd.DataFrame, i: int, lookback: int = STRUCTURE_LOOKBACK) -> Tuple[float, float]:
    window = df.iloc[max(0, i - lookback): i]
    return float(window["high"].max()), float(window["low"].min())


def compute_stop_target(df: pd.DataFrame, i: int, direction: str, entry: float, atr: float) -> Tuple[float, float]:
    swing_high, swing_low = _swing_extremes(df, i)
    buffer = 0.1 * atr

    if direction == "BULLISH":
        stop = swing_low - buffer
        risk = entry - stop
        if risk <= 0:
            stop = entry - atr * STOP_ATR_MULT
            risk = entry - stop
        target = swing_high
        reward = target - entry
        if reward <= 0 or reward / risk < MIN_STRUCTURAL_RR:
            target = entry + risk * FALLBACK_RR
    else:
        stop = swing_high + buffer
        risk = stop - entry
        if risk <= 0:
            stop = entry + atr * STOP_ATR_MULT
            risk = stop - entry
        target = swing_low
        reward = entry - target
        if reward <= 0 or reward / risk < MIN_STRUCTURAL_RR:
            target = entry - risk * FALLBACK_RR

    return stop, target


# ---------------------------------------------------------------- #
# 2. Confluence filtering
# ---------------------------------------------------------------- #

def _confluence_ok(engine_name: str, direction: str, context: Dict[str, Dict[str, Any]]) -> bool:
    structure = context["structure"]
    regime = context["regime"]

    if engine_name == "LIQUIDITY_SWEEP":
        # A sweep against the higher-timeframe read is exactly the kind
        # of "stop run continuing the real trend" case where the sweep
        # signal is least trustworthy — require structure to at least
        # not be actively opposing it.
        s_dir = structure.get("direction", "NEUTRAL")
        if s_dir != "NEUTRAL" and s_dir != direction:
            return False

    if engine_name == "VSA":
        # No Demand / No Supply reads are far weaker evidence inside a
        # dead-flat range than inside a real trend.
        if regime.get("regime") == "RANGING" and regime.get("regime_strength", 0) > 60:
            return False

    if engine_name == "STRUCTURE":
        # A fresh HH/HL read inside a strongly contracting/balancing
        # market is more likely noise than a real continuation.
        if regime.get("regime") == "CONTRACTION_BALANCE" and regime.get("regime_strength", 0) > 70:
            return False

    return True


def _resolve_trade(df: pd.DataFrame, start_idx: int, direction: str, entry: float,
                    stop: float, target: float) -> Optional[TradeResult]:
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
            return TradeResult("BACKTEST", direction, entry, stop, target, "LOSS", -1.0)
        if hit_stop:
            return TradeResult("BACKTEST", direction, entry, stop, target, "LOSS", -1.0)
        if hit_target:
            reward_distance = abs(target - entry)
            r = reward_distance / risk_distance
            return TradeResult("BACKTEST", direction, entry, stop, target, "WIN", r)

    last_close = df.iloc[end_idx]["close"]
    r = ((last_close - entry) if direction == "BULLISH" else (entry - last_close)) / risk_distance
    outcome = "WIN" if r > 0 else "LOSS" if r < 0 else "SCRATCH"
    return TradeResult("BACKTEST", direction, entry, stop, target, outcome, round(r, 3))


def backtest_engine(df: pd.DataFrame, engine_name: str, volume_type: str = "UNAVAILABLE") -> List[TradeResult]:
    df = add_metrics(df)
    trades: List[TradeResult] = []

    i = MIN_WINDOW
    while i < len(df) - 1:
        window = df.iloc[max(0, i - MIN_WINDOW): i + 1]

        # Context engines computed every step, regardless of which
        # engine is under test — confluence needs to know what the
        # others say too.
        context = {
            "structure": structure_engine(window, "BACKTEST"),
            "regime": regime_engine(window),
        }

        if engine_name == "VSA":
            result = _vsa_wrapper(window, volume_type)
        elif engine_name in ("STRUCTURE", "REGIME"):
            result = context[engine_name.lower()] if engine_name == "STRUCTURE" else context["regime"]
        else:
            fn = ENGINE_REGISTRY.get(engine_name)
            if fn is None:
                raise ValueError(f"No backtest wiring for engine {engine_name}")
            result = fn(window)

        contribution = result.get("directional_contribution", 0)
        if abs(contribution) >= DIRECTIONAL_TRIGGER_THRESHOLD:
            direction = "BULLISH" if contribution > 0 else "BEARISH"

            if _confluence_ok(engine_name, direction, context):
                entry_bar = df.iloc[i]
                entry = float(entry_bar["close"])
                atr = float(entry_bar["atr"]) if pd.notna(entry_bar.get("atr")) else None

                if atr and atr > 0:
                    stop, target = compute_stop_target(df, i, direction, entry, atr)
                    trade = _resolve_trade(df, i, direction, entry, stop, target)
                    if trade is not None:
                        trade.regime = context["regime"].get("regime", "")
                        trade.session = get_session(entry_bar["time"]) if "time" in entry_bar else ""
                        trades.append(trade)
                        i += MAX_HOLD_BARS
                        continue
        i += 1

    return trades


def run_validation(df: pd.DataFrame, engine_name: str, instrument: str, volume_type: str,
                    store, minimum_sample: int = 100) -> Dict[str, Any]:
    all_trades = backtest_engine(df, engine_name, volume_type)

    split_idx = int(len(all_trades) * 0.7)
    in_sample, out_of_sample = all_trades[:split_idx], all_trades[split_idx:]

    in_sample_result = validate_setup(in_sample, minimum_sample)
    out_of_sample_result = validate_setup(out_of_sample, minimum_sample)

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
