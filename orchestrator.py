"""
The ONE function the API layer calls. Ties together:
  data (DataManager: Twelve Data primary / MT5 fallback)
  -> multi-timeframe structure (H4 + H1)
  -> regime, liquidity sweep, VSA, session (on the requested timeframe)
  -> fusion (gated by ValidationStore)
  -> risk (gated by daily trade cap, pulled from the journal)
  -> the exact JSON contract the Stitch "Command Center" screen renders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import CONFIG
from data.base import DataUnavailableError
from data.manager import DataManager
from db.store import ValidationStore
from engines.fusion import fuse_engines
from engines.liquidity import liquidity_sweep_engine
from engines.metrics import add_metrics
from engines.regime import regime_engine
from engines.session_risk import risk_engine, session_engine
from engines.structure import multi_timeframe_structure
from engines.vsa import vsa_engine


def analyze(instrument: str, timeframe: str, data_manager: DataManager, store: ValidationStore,
            account_equity: Optional[float] = None) -> Dict[str, Any]:
    account_equity = account_equity or CONFIG["account_equity_default"]

    try:
        h4 = data_manager.fetch(instrument, "H4", bars=500)
        h1 = data_manager.fetch(instrument, "H1", bars=500)
        primary = data_manager.fetch(instrument, timeframe, bars=500)
    except DataUnavailableError as exc:
        return {
            "system": "TITAN VSA X",
            "status": "DATA_UNAVAILABLE",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    h4_df, h1_df, df = add_metrics(h4.df), add_metrics(h1.df), add_metrics(primary.df)

    structure = multi_timeframe_structure(h4_df, h1_df)
    regime = regime_engine(df)
    sweep = liquidity_sweep_engine(df)
    vsa = vsa_engine(df, primary.volume_type)
    session = session_engine(df)

    results = [structure, regime, sweep, vsa, session]
    fusion = fuse_engines(results, instrument, store)

    price = float(df["close"].iloc[-1])
    atr = float(df["atr"].iloc[-1]) if df["atr"].notna().iloc[-1] else None

    stop = target = entry = None
    if fusion["direction"] in ("BULLISH", "BEARISH") and atr:
        if fusion["direction"] == "BULLISH":
            stop = price - atr * CONFIG["stop_atr_mult"]
            target = price + atr * CONFIG["target_atr_mult"]
        else:
            stop = price + atr * CONFIG["stop_atr_mult"]
            target = price - atr * CONFIG["target_atr_mult"]
        entry = price

    trades_today = store.open_trades_today(instrument)
    risk = risk_engine(
        fusion["direction"], entry, stop, target, account_equity,
        CONFIG["risk_per_trade"], CONFIG["min_rr"], trades_today, CONFIG["max_daily_trades"],
    )

    tradeability = 0
    if fusion["direction"] != "NEUTRAL":
        tradeability += 30
    if abs(fusion["directional_strength"]) >= 50:
        tradeability += 20
    if abs(sweep["directional_contribution"]) >= 50:
        tradeability += 20
    if abs(vsa["directional_contribution"]) >= 35:
        tradeability += 15
    if risk.get("valid"):
        tradeability += 15
    tradeability = min(100, tradeability)

    # Confidence is already discounted by validated_weight_fraction in fuse_engines(),
    # so a low-tradeability / low-validation read correctly falls to WAIT, not a
    # confident-looking NO_TRADE.
    if fusion["confidence_percent"] < 30:
        status = "WAIT"
    elif tradeability >= 75:
        status = "VALIDATED"
    elif tradeability >= 35:
        status = "WAIT"
    else:
        status = "NO_TRADE"

    if status == "VALIDATED":
        prediction_id = store.log_prediction(instrument, timeframe, fusion["confidence_percent"], fusion["direction"])
    else:
        prediction_id = None

    return {
        "system": "TITAN VSA X",
        "version": "MVP-2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market": {"symbol": instrument, "timeframe": timeframe, "price": price},
        "data": {
            "primary_source": primary.source,
            "h4_source": h4.source,
            "h1_source": h1.source,
            "volume_type": primary.volume_type,
            "data_quality": primary.data_quality,
            "validation": getattr(primary, "validation", None),
        },
        "engines": results,
        "fusion": fusion,
        "risk": risk,
        "tradeability_percent": tradeability,
        "prediction_id": prediction_id,
        "final": {
            "direction": fusion["direction"],
            "directional_strength": fusion["directional_strength"],
            "confidence_percent": fusion["confidence_percent"],
            "tradeability_percent": tradeability,
            "status": status,
            "entry": entry if status == "VALIDATED" else None,
            "stop": stop if status == "VALIDATED" else None,
            "target": target if status == "VALIDATED" else None,
        },
    }
