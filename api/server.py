"""
FastAPI service — the piece that was entirely missing before. Exposes
the JSON contract the Stitch "Command Center" / "Validation Lab" /
"Data Health" / "Trade Journal" screens are designed against.

Run locally:
    uvicorn api.server:app --reload --port 8000

Requires TWELVE_DATA_API_KEY to be set for live data; without it,
DataManager.default() will raise on first request unless an MT5
terminal happens to be reachable (see data/mt5_adapter.py).
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import CONFIG
from data.base import DataUnavailableError
from data.manager import DataManager
from db.store import ValidationStore
from orchestrator import analyze
from validation.backtest_runner import run_validation

app = FastAPI(title="Titan VSA X API", version="2.0")

# ALLOWED_ORIGINS: comma-separated list, e.g. "https://your-site.netlify.app".
# Defaults to "*" for local development only — set this explicitly in
# production once the Netlify URL is known, rather than leaving it wide open.
_origins_env = os.environ.get("ALLOWED_ORIGINS", "*")
_allow_origins = ["*"] if _origins_env.strip() == "*" else [o.strip() for o in _origins_env.split(",")]
app.add_middleware(CORSMiddleware, allow_origins=_allow_origins, allow_methods=["*"], allow_headers=["*"])

store = ValidationStore()
_data_manager: Optional[DataManager] = None


def get_data_manager() -> DataManager:
    global _data_manager
    if _data_manager is None:
        _data_manager = DataManager.default()
    return _data_manager


@app.get("/analysis/{instrument}")
def get_analysis(instrument: str, timeframe: str = Query("M15"), account_equity: float = Query(10000.0)):
    if instrument not in CONFIG["instruments"]:
        raise HTTPException(400, f"Unsupported instrument. Use one of {CONFIG['instruments']}")
    try:
        dm = get_data_manager()
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return analyze(instrument, timeframe, dm, store, account_equity)


@app.get("/validation/{instrument}")
def get_validation(instrument: str):
    return {"instrument": instrument, "engines": store.all_statuses(instrument)}


@app.get("/validation")
def get_all_validation():
    return {"engines": store.all_statuses()}


class BacktestRequest(BaseModel):
    engine: str
    timeframe: str = "M15"
    bars: int = 5000
    minimum_sample: int = 100


@app.post("/backtest/{instrument}")
def run_backtest(instrument: str, req: BacktestRequest):
    if instrument not in CONFIG["instruments"]:
        raise HTTPException(400, f"Unsupported instrument. Use one of {CONFIG['instruments']}")
    try:
        dm = get_data_manager()
        fetched = dm.fetch(instrument, req.timeframe, bars=req.bars)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc

    return run_validation(fetched.df, req.engine, instrument, fetched.volume_type, store, req.minimum_sample)


@app.get("/data-health/{instrument}")
def data_health(instrument: str, timeframe: str = Query("M15")):
    try:
        dm = get_data_manager()
        fetched = dm.fetch(instrument, timeframe, bars=500)
    except DataUnavailableError as exc:
        return {"status": "DATA_UNAVAILABLE", "error": str(exc)}
    return {
        "source": fetched.source,
        "volume_type": fetched.volume_type,
        "data_quality": fetched.data_quality,
        "validation": getattr(fetched, "validation", None),
    }


class JournalEntry(BaseModel):
    instrument: str
    timeframe: str
    setup: Optional[str] = None
    direction: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    result: Optional[str] = None
    r_multiple: Optional[float] = None
    confidence_percent: Optional[float] = None
    directional_strength: Optional[float] = None
    tradeability_percent: Optional[float] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None


@app.post("/journal")
def log_journal_entry(entry: JournalEntry):
    row_id = store.log_trade(**entry.model_dump(), raw_analysis_json=None)
    return {"id": row_id}


@app.get("/journal")
def get_journal(instrument: Optional[str] = Query(None), limit: int = Query(200, le=1000)):
    return {"entries": store.list_journal(instrument, limit)}


@app.get("/calibration/{instrument}")
def calibration(instrument: str):
    return store.calibration_summary(instrument)


@app.get("/health")
def health():
    return {"status": "OK"}
