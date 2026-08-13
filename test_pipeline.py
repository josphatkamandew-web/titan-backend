"""Smoke test: run the full pipeline on synthetic data and print results."""
import json
import sys

from data.manager import DataManager
from data.synthetic_adapter import SyntheticAdapter
from db.store import ValidationStore
from orchestrator import analyze
from validation.backtest_runner import run_validation

dm = DataManager([SyntheticAdapter()])
store = ValidationStore(db_path="/tmp/titan_test.db")

print("=" * 70)
print("STEP 1: Analysis BEFORE any backtest (everything should default")
print("to INVESTIGATE -> zero fusion weight -> NEUTRAL / low confidence)")
print("=" * 70)
result = analyze("XAUUSD", "M15", dm, store)
print(json.dumps({
    "direction": result["final"]["direction"],
    "confidence": result["final"]["confidence_percent"],
    "status": result["final"]["status"],
    "engine_weight_report": result["fusion"]["engine_weight_report"],
}, indent=2))

assert result["final"]["status"] != "VALIDATED", "Should never validate with nothing backtested yet!"
assert all(e["validation_status"] == "INVESTIGATE" for e in result["fusion"]["engine_weight_report"]), \
    "All engines should default to INVESTIGATE with zero weight"
print("\nPASS: unvalidated engines correctly contribute zero weight.\n")

print("=" * 70)
print("STEP 2: Backtest each MVP engine on synthetic history")
print("=" * 70)
h4_data = dm.fetch("XAUUSD", "H4", bars=3000)
for engine_name in ["STRUCTURE", "LIQUIDITY_SWEEP", "REGIME", "VSA"]:
    bt = run_validation(h4_data.df, engine_name, "XAUUSD", h4_data.volume_type, store, minimum_sample=20)
    print(f"{engine_name:20s} total_trades={bt['total_trades_all_history']:4d}  "
          f"promoted_status={bt['promoted_status']:12s}  "
          f"oos_sample={bt['out_of_sample']['statistics'].get('sample_size', 0)}")

print("\n" + "=" * 70)
print("STEP 3: Analysis AFTER backtest (weights should now reflect status)")
print("=" * 70)
result2 = analyze("XAUUSD", "M15", dm, store)
print(json.dumps({
    "direction": result2["final"]["direction"],
    "confidence": result2["final"]["confidence_percent"],
    "status": result2["final"]["status"],
    "engine_weight_report": result2["fusion"]["engine_weight_report"],
}, indent=2))

print("\n" + "=" * 70)
print("STEP 4: Validation store contents")
print("=" * 70)
print(json.dumps(store.all_statuses("XAUUSD"), indent=2))

print("\nALL SMOKE TESTS COMPLETED.")
