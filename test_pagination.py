"""Test the pagination logic against a mocked HTTP layer -- api.twelvedata.com
isn't reachable from this sandbox's network allowlist, so this verifies the
chunk-stitching logic is correct without needing a real key or real network."""
import sys
sys.path.insert(0, ".")

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from data.twelvedata_adapter import TwelveDataAdapter


def make_mock_response(start_dt, n_bars, interval_hours=1):
    """Build n_bars of fake hourly candles ending at start_dt, going backwards."""
    times = [start_dt - timedelta(hours=interval_hours * i) for i in range(n_bars)]
    times.reverse()
    values = [
        {"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": "2000.0", "high": "2005.0",
         "low": "1995.0", "close": "2001.0", "volume": "1000"}
        for t in times
    ]
    return {"status": "ok", "values": values}


call_log = []

def fake_get(url, params=None, timeout=None):
    call_log.append(dict(params))
    end_date = params.get("end_date")
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    else:
        end_dt = datetime(2026, 8, 15, tzinfo=timezone.utc)

    requested = int(params["outputsize"])
    # Simulate the provider having exactly 12000 bars of history total, no more.
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    # crude: just always return the full requested amount (we're testing stitching logic,
    # not provider history limits, in this first test)
    resp.json = lambda: make_mock_response(end_dt, requested)
    return resp


print("=== Test 1: bars <= 5000 -> exactly one request ===")
call_log.clear()
adapter = TwelveDataAdapter(api_key="fake-key-for-test")
with patch.object(adapter.session, "get", side_effect=fake_get):
    result = adapter.fetch_ohlcv("XAUUSD", "H1", bars=3000)
assert len(call_log) == 1, f"expected 1 call, got {len(call_log)}"
assert len(result.df) == 3000, f"expected 3000 bars, got {len(result.df)}"
print(f"PASS: {len(call_log)} call(s), {len(result.df)} bars")

print("\n=== Test 2: bars > 5000 -> paginates across multiple requests ===")
call_log.clear()
with patch.object(adapter.session, "get", side_effect=fake_get):
    result = adapter.fetch_ohlcv("XAUUSD", "H1", bars=12000)
print(f"calls made: {len(call_log)}")
for i, c in enumerate(call_log):
    print(f"  call {i+1}: outputsize={c['outputsize']}, end_date={c.get('end_date')}")
assert len(call_log) >= 3, f"expected at least 3 chunked calls for 12000 bars, got {len(call_log)}"
assert len(result.df) == 12000, f"expected 12000 bars total, got {len(result.df)}"
assert result.df["time"].is_monotonic_increasing, "stitched bars must be chronological"
assert result.df["time"].duplicated().sum() == 0, "no duplicate timestamps after stitching"
print(f"PASS: {len(call_log)} calls, {len(result.df)} bars stitched, chronological, no duplicates")

print("\n=== Test 3: MAX_CHUNKS caps runaway requests ===")
call_log.clear()
with patch.object(adapter.session, "get", side_effect=fake_get):
    result = adapter.fetch_ohlcv("XAUUSD", "H1", bars=100000)
print(f"calls made: {len(call_log)} (should be capped at MAX_CHUNKS=6)")
assert len(call_log) <= 6, f"expected at most 6 calls, got {len(call_log)}"
print(f"PASS: capped at {len(call_log)} calls")

print("\nALL PAGINATION TESTS PASSED.")
