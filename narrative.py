"""
Turns a real /analysis result into readable prose for the morning briefing.

This is the piece the Kimi mockup got right conceptually (a narrative is
more useful at 6am than raw JSON) — but every sentence here is built from
fields that actually came out of the engines. If an engine's evidence is
weak, unavailable, or unvalidated, the narrative says so explicitly rather
than writing around it. No number in this text is invented; if we don't
have it, we say "unavailable" instead of guessing.
"""

from __future__ import annotations

from typing import Any, Dict


def _structure_sentence(structure: Dict[str, Any]) -> str:
    h4, h1 = structure.get("h4", {}), structure.get("h1", {})
    parts = []
    if h4.get("events"):
        parts.append(f"H4 shows {' and '.join(h4['events']).lower()}, reading {h4.get('direction', 'NEUTRAL').lower()}")
    if h1.get("events"):
        parts.append(f"H1 shows {' and '.join(h1['events']).lower()}, reading {h1.get('direction', 'NEUTRAL').lower()}")
    if structure.get("h4_h1_conflict"):
        parts.append("H4 and H1 currently disagree, so treat the higher-timeframe read as tentative")
    if not parts:
        return "No clear higher-timeframe structure signal this morning."
    return "; ".join(parts) + "."


def _regime_sentence(regime: Dict[str, Any]) -> str:
    r = regime.get("regime", "UNKNOWN")
    strength = regime.get("regime_strength")
    if r == "UNKNOWN":
        return "Not enough recent bars to classify the market regime."
    label = r.replace("_", " ").lower()
    strength_txt = f" (strength {strength}/100)" if strength is not None else ""
    return f"Market regime: {label}{strength_txt}."


def _liquidity_sentence(sweep: Dict[str, Any]) -> str:
    event = sweep.get("event", "NO CONFIRMED SWEEP")
    if event == "NO CONFIRMED SWEEP":
        return "No confirmed liquidity sweep against the recent range."
    return f"{event.replace('_', ' ').title()} detected against prior high {sweep.get('prior_high')} / low {sweep.get('prior_low')}."


def _vsa_sentence(vsa: Dict[str, Any]) -> str:
    pattern = vsa.get("pattern", "NO CLEAR VSA SIGNAL")
    if pattern in ("NO CLEAR VSA SIGNAL",):
        return "No distinct VSA pattern on the current bar."
    if "UNAVAILABLE" in pattern:
        return "Volume data unavailable for this instrument, so VSA reads are not usable this morning."
    quality = vsa.get("data_quality", "MEDIUM")
    caveat = " (tick/estimated volume, not centralized true volume)" if vsa.get("volume_type") == "TICK_ESTIMATE" else ""
    return f"VSA: {pattern.replace('_', ' ').title()}{caveat}. Data quality: {quality}."


def _session_sentence(session: Dict[str, Any]) -> str:
    sess = session.get("session", "UNKNOWN")
    high, low = session.get("session_high"), session.get("session_low")
    if high is None or low is None:
        return f"Session: {sess}."
    return f"Session: {sess}. Today's range so far: {low} - {high}, price in the {session.get('directional_context', 'mid-range').replace('_', ' ').lower()}."


def _validation_sentence(fusion: Dict[str, Any]) -> str:
    report = fusion.get("engine_weight_report", [])
    validated = [e for e in report if e.get("validation_status") == "RETAIN"]
    if not validated:
        return ("None of today's engines have a validated (backtested) track record yet, so this bias is "
                "directional context only, not a confidence-weighted signal. Treat it as one input among "
                "several for your own analysis.")
    names = ", ".join(e["engine"] for e in validated)
    return f"Contributing validated engines today: {names}."


def generate_narrative(analysis: Dict[str, Any]) -> str:
    if analysis.get("status") == "DATA_UNAVAILABLE":
        return f"Briefing could not be generated: {analysis.get('error', 'data unavailable')}."

    engines_by_name = {e["engine"]: e for e in analysis.get("engines", [])}
    final = analysis.get("final", {})
    fusion = analysis.get("fusion", {})
    market = analysis.get("market", {})

    lines = [
        f"{market.get('symbol', '?')} at {market.get('price', '?')} ({analysis.get('timestamp', '')[:16].replace('T', ' ')} UTC).",
        f"Directional bias: {final.get('direction', 'NEUTRAL')} "
        f"(strength {final.get('directional_strength', 0)}/100, confidence {final.get('confidence_percent', 0)}%, "
        f"status {final.get('status', 'WAIT')}).",
        "",
        _structure_sentence(engines_by_name.get("STRUCTURE", {})),
        _regime_sentence(engines_by_name.get("REGIME", {})),
        _liquidity_sentence(engines_by_name.get("LIQUIDITY_SWEEP", {})),
        _vsa_sentence(engines_by_name.get("VSA", {})),
        _session_sentence(engines_by_name.get("SESSION", {})),
        "",
        _validation_sentence(fusion),
    ]

    return "\n".join(l for l in lines if l is not None)
