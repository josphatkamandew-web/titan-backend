"""
Fusion Engine.

THE critical fix from the audit: previously, fuse_engines() applied
full weight to every MVP engine regardless of whether it had ever
been backtested. That directly violated the spec's central rule —
"no engine's evidence enters the live Fusion Engine's weighting
until it has been tested against real history." Now every engine's
weight is looked up from the ValidationStore and multiplied by a
status factor: RETAIN=full weight, DE_EMPHASIZE=reduced, INVESTIGATE
and REJECT=zero. Freshly deployed, with nothing backtested yet, this
means Titan will correctly output NEUTRAL / low-confidence for
everything until the backtest runner (validation/backtest_runner.py)
actually promotes engines — which is the intended, conservative
behavior, not a bug.
"""

from __future__ import annotations

from typing import Any, Dict, List

from db.store import ValidationStore

BASE_WEIGHTS = {
    "STRUCTURE": 0.30,
    "LIQUIDITY_SWEEP": 0.22,
    "VSA": 0.22,
    "REGIME": 0.13,
    "SESSION": 0.13,
}

STATUS_MULTIPLIER = {
    "RETAIN": 1.0,
    "DE_EMPHASIZE": 0.4,
    "INVESTIGATE": 0.0,
    "REJECT": 0.0,
}

QUALITY_MULTIPLIER = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.40}


def fuse_engines(results: List[Dict[str, Any]], instrument: str, store: ValidationStore) -> Dict[str, Any]:
    weighted_score = 0.0
    weight_used = 0.0
    engine_weight_report = []

    for result in results:
        engine = result["engine"]
        base_weight = BASE_WEIGHTS.get(engine)
        if base_weight is None:
            continue

        status_row = store.get_status(instrument, engine, "DEFAULT")
        status = status_row["status"]
        status_mult = STATUS_MULTIPLIER.get(status, 0.0)

        quality = result.get("data_quality", "MEDIUM")
        quality_mult = QUALITY_MULTIPLIER.get(quality, 0.5)

        effective_weight = base_weight * status_mult * quality_mult
        contribution = result.get("directional_contribution", 0)

        weighted_score += contribution * effective_weight
        weight_used += effective_weight

        engine_weight_report.append({
            "engine": engine,
            "base_weight": base_weight,
            "validation_status": status,
            "sample_size": status_row.get("sample_size", 0),
            "data_quality": quality,
            "effective_weight": round(effective_weight, 4),
            "contribution": contribution,
        })

    final_score = (weighted_score / weight_used) if weight_used > 0 else 0.0
    final_score = max(-100, min(100, final_score))

    if weight_used == 0:
        direction = "NEUTRAL"
    elif final_score >= 25:
        direction = "BULLISH"
    elif final_score <= -25:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    directional = [r.get("direction") for r in results
                   if r.get("engine") != "RISK" and r.get("direction") in ("BULLISH", "BEARISH")]

    if directional and direction in ("BULLISH", "BEARISH"):
        agreement = directional.count(direction) / len(directional)
    elif direction == "NEUTRAL":
        agreement = 0.5
    else:
        agreement = 0.0

    # No engine actually validated yet -> nothing to be confident about.
    # Confidence is capped hard by how much real weight is behind the
    # call, not just by directional agreement of unvalidated engines.
    validated_fraction = weight_used / sum(BASE_WEIGHTS.values()) if BASE_WEIGHTS else 0.0

    confidence = round(
        min(95.0, (40 + abs(final_score) * 0.35 + agreement * 20) * validated_fraction),
        1,
    )

    return {
        "direction": direction,
        "directional_strength": round(final_score, 1),
        "confidence_percent": confidence,
        "agreement_percent": round(agreement * 100, 1),
        "validated_weight_fraction": round(validated_fraction, 3),
        "engine_weight_report": engine_weight_report,
    }
