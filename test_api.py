"""Smoke test for the FastAPI layer, using the synthetic adapter so it
doesn't require a live Twelve Data key or MT5 terminal."""
import os

os.environ.setdefault("TITAN_TEST_MODE", "1")

import api.server as server_module
from data.manager import DataManager
from data.synthetic_adapter import SyntheticAdapter
from db.store import ValidationStore
from fastapi.testclient import TestClient

# Inject synthetic data manager + isolated test DB before any request is made.
server_module._data_manager = DataManager([SyntheticAdapter()])
server_module.store = ValidationStore(db_path="/tmp/titan_api_test.db")

client = TestClient(server_module.app)

r = client.get("/health")
assert r.status_code == 200, r.text
print("GET /health ->", r.json())

r = client.get("/analysis/XAUUSD?timeframe=M15")
assert r.status_code == 200, r.text
body = r.json()
print("GET /analysis/XAUUSD ->", {"direction": body["final"]["direction"],
                                    "status": body["final"]["status"],
                                    "confidence": body["final"]["confidence_percent"]})

r = client.get("/validation/XAUUSD")
assert r.status_code == 200, r.text
print("GET /validation/XAUUSD ->", r.json())

r = client.post("/backtest/XAUUSD", json={"engine": "LIQUIDITY_SWEEP", "timeframe": "H4",
                                            "bars": 3000, "minimum_sample": 15})
assert r.status_code == 200, r.text
print("POST /backtest/XAUUSD ->", {"promoted_status": r.json()["promoted_status"],
                                     "total_trades": r.json()["total_trades_all_history"]})

r = client.get("/data-health/XAUUSD")
assert r.status_code == 200, r.text
print("GET /data-health/XAUUSD ->", r.json())

r = client.post("/journal", json={"instrument": "XAUUSD", "timeframe": "M15", "direction": "BULLISH",
                                    "entry": 2000.0, "stop": 1990.0, "target": 2020.0})
assert r.status_code == 200, r.text
print("POST /journal ->", r.json())

r = client.get("/calibration/XAUUSD")
assert r.status_code == 200, r.text
print("GET /calibration/XAUUSD ->", r.json())

r = client.get("/journal?instrument=XAUUSD")
assert r.status_code == 200, r.text
print("GET /journal ->", r.json())

print("\nALL API SMOKE TESTS PASSED.")
