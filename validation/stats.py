from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np

MIN_SAMPLE_FOR_PROMOTION = 100
INVESTIGATE_SAMPLE_FLOOR = 30  # below this, don't even bother reporting reliable stats


@dataclass
class TradeResult:
    setup: str
    direction: str
    entry: float
    stop: float
    target: float
    outcome: str          # "WIN" | "LOSS" | "SCRATCH"
    r_multiple: float
    regime: str = ""
    session: str = ""


def calculate_statistics(trades: List[TradeResult]) -> Dict[str, Any]:
    if not trades:
        return {"sample_size": 0, "status": "INSUFFICIENT_DATA"}

    r = np.array([t.r_multiple for t in trades])
    wins, losses = r[r > 0], r[r < 0]

    win_rate = len(wins) / len(r)
    expectancy = float(r.mean())

    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0

    # Fix from the original: float('inf') is not valid JSON and breaks
    # strict frontend JSON.parse(). Cap instead of using inf, and flag it.
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
        uncapped = False
    else:
        profit_factor = 999.0
        uncapped = True

    equity = np.cumsum(r)
    peaks = np.maximum.accumulate(equity)
    drawdown = equity - peaks
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    return {
        "sample_size": int(len(r)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_percent": round(win_rate * 100, 2),
        "average_r": round(float(r.mean()), 4),
        "expectancy_r": round(expectancy, 4),
        "profit_factor": round(profit_factor, 3),
        "profit_factor_uncapped_no_losses": uncapped,
        "average_winner_r": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "average_loser_r": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "max_drawdown_r": round(max_drawdown, 4),
        "status": "READY_FOR_REVIEW",
    }


def promotion_gate(stats: Dict[str, Any], minimum_sample: int = MIN_SAMPLE_FOR_PROMOTION) -> Dict[str, Any]:
    if stats["sample_size"] < INVESTIGATE_SAMPLE_FLOOR:
        return {"decision": "INVESTIGATE", "reason": "Below minimum sample floor for any statistical claim."}

    if stats["sample_size"] < minimum_sample:
        return {"decision": "INVESTIGATE", "reason": "Promising sample but below promotion threshold."}

    if stats["expectancy_r"] <= 0:
        return {"decision": "REJECT", "reason": "Non-positive expectancy at adequate sample size."}

    if stats["profit_factor"] < 1.20:
        return {"decision": "DE_EMPHASIZE", "reason": "Positive but marginal profit factor."}

    return {"decision": "RETAIN", "reason": "Passed minimum statistical gate."}


def validate_setup(trades: List[TradeResult], minimum_sample: int = MIN_SAMPLE_FOR_PROMOTION) -> Dict[str, Any]:
    stats = calculate_statistics(trades)
    if stats["sample_size"] == 0:
        return {"statistics": stats, "promotion": {"decision": "INVESTIGATE", "reason": "No trades logged."}}
    promotion = promotion_gate(stats, minimum_sample)
    return {"statistics": stats, "promotion": promotion}
