import os
os.environ["BRIEFING_SECRET"] = "test-secret-123"

import api.server as server_module
from data.manager import DataManager
from data.synthetic_adapter import SyntheticAdapter
from db.store import ValidationStore
from fastapi.testclient import TestClient

server_module._data_manager = DataManager([SyntheticAdapter()])
server_module.store = ValidationStore(db_path="/tmp/titan_briefing_api_test.db")
server_module.BRIEFING_SECRET = "test-secret-123"

client = TestClient(server_module.app)

# Wrong secret should be rejected
r = client.post("/briefing/generate", headers={"X-Briefing-Secret": "wrong"})
assert r.status_code == 403, r.text
print("Wrong secret correctly rejected:", r.status_code)

# No secret should be rejected
r = client.post("/briefing/generate")
assert r.status_code == 403, r.text
print("Missing secret correctly rejected:", r.status_code)

# Correct secret should work
r = client.post("/briefing/generate", headers={"X-Briefing-Secret": "test-secret-123"})
assert r.status_code == 200, r.text
body = r.json()
print("Briefing generated for:", list(body["results"].keys()))
assert set(body["results"].keys()) == {"XAUUSD", "EURUSD", "GBPUSD"}

# Fetch one back
r = client.get("/briefing/XAUUSD")
assert r.status_code == 200, r.text
b = r.json()
print("Fetched briefing narrative (first 100 chars):", b["narrative"][:100])
assert "analysis" in b and "final" in b["analysis"]

# History
r = client.get("/briefing-history/XAUUSD")
assert r.status_code == 200, r.text
print("History entries:", len(r.json()["briefings"]))

print("\nALL BRIEFING API TESTS PASSED.")
