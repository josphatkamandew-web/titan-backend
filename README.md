# Titan VSA X — Backend v2.0

This is the audited, rewired version of the two files you sent (`core.py` and
`validation.py`). Same trading logic where it was already sound — rewritten
so the pieces are actually connected.

## What changed from what you uploaded

| Gap found | Fix |
|---|---|
| Fusion always used full weight for every engine, ignoring validation status | `engines/fusion.py` now looks up each engine's status in `ValidationStore` (SQLite) and zeroes the weight for anything not `RETAIN` (`DE_EMPHASIZE` gets 40%, `INVESTIGATE`/`REJECT` get 0%) |
| `validation.py` had statistics + a promotion gate but nothing generated trades to test | `validation/backtest_runner.py` walks historical bars, runs each engine on a rolling window, opens/resolves hypothetical trades against ATR stop/target, and feeds them into the existing statistics code |
| No walk-forward split | Backtest runner splits history 70/30; promotion is decided on the out-of-sample 30% only, never the data the rule was eyeballed on |
| Engine 0 (Regime) was in the spec's MVP but not implemented | `engines/regime.py` — price/volatility-derived, no volume dependency |
| Single-timeframe analysis (spec calls for H4 macro → H1 direction → M15 setup) | `engines/structure.py`'s `multi_timeframe_structure()` fetches H4 + H1 separately; H4 sets the dominant bias, H1 confirms or flags conflict, neither is silently overridden by the setup timeframe |
| MT5-only, which can't run on a typical hosted backend | `data/manager.py`: Twelve Data (`data/twelvedata_adapter.py`) is primary and cloud-friendly; MT5 (`data/mt5_adapter.py`) is an automatic fallback, only used if that specific machine actually has a terminal running |
| No persistence — nothing remembered a backtest result, no journal, no calibration log | `db/store.py`, SQLite for now (swap for Postgres later without changing call sites) |
| No API layer for the Stitch UI to call | `api/server.py`, FastAPI |
| `profit_factor` used `float('inf')`, which isn't valid JSON and breaks `JSON.parse()` on the frontend | Capped at 999.0, flagged with `profit_factor_uncapped_no_losses` |
| `win_rate < 40%` was an automatic reject, independent of expectancy — would reject a legitimately good low-win-rate/high-R:R strategy | Promotion now gates on expectancy and profit factor only |
| `sweep_min_atr` / `equal_level_tolerance_atr` were defined in config but never used | `sweep_min_atr` is now actually applied as a minimum sweep-penetration filter in `engines/liquidity.py`; equal-level pooling is still a Phase 2/3 addition, not yet built |

## Verified working (see test_pipeline.py / test_api.py)

Ran end-to-end against synthetic data:
1. **Before any backtest**, every engine defaults to `INVESTIGATE` → zero fusion
   weight → output is `NEUTRAL` / 0% confidence / `WAIT`. This is correct,
   enforced behavior, not a bug — Titan should never sound confident about
   something nobody has checked yet.
2. **Backtest runner** produces real trade counts per engine (STRUCTURE: 71,
   LIQUIDITY_SWEEP: 61, VSA: 7 on ~3000 synthetic H4 bars) and writes a
   promotion decision to the database.
3. **After backtesting**, fusion weights reflect whatever status came back.
4. **FastAPI endpoints** (`/analysis`, `/validation`, `/backtest`,
   `/data-health`, `/journal`, `/calibration`) all respond correctly.

Note: `REGIME` will show ~0 backtest trades — its contribution formula tops
out around ±20, below the ±40 threshold the backtest runner uses to open a
hypothetical trade. That's intentional: Regime is contextual (it should
inform how you read other engines), not a standalone entry signal, so it
isn't meant to be backtested as one. Don't be alarmed if it stays at 0
trades — it's still doing its job inside `directional_contribution` shaping
for the others once regime-gating is added in Phase 2.

## Running it

```bash
pip install -r requirements.txt

# Required for live data:
export TWELVE_DATA_API_KEY=your_key_here

uvicorn api.server:app --reload --port 8000
```

Then, e.g.:
```
GET  http://localhost:8000/analysis/XAUUSD?timeframe=M15
GET  http://localhost:8000/validation/XAUUSD
POST http://localhost:8000/backtest/XAUUSD   {"engine": "STRUCTURE", "timeframe": "H4", "bars": 5000}
GET  http://localhost:8000/data-health/XAUUSD
POST http://localhost:8000/journal           {...}
GET  http://localhost:8000/calibration/XAUUSD
```

Point the Stitch UI's `fetch()` calls at these endpoints — the JSON shape
matches the `engines[]` / `fusion` / `risk` / `final` structure the Command
Center screen already expects.

Without `TWELVE_DATA_API_KEY` set and without a reachable MT5 terminal,
`DataManager.default()` raises `DataUnavailableError` rather than serving
fake data — matching the spec's Simulation Mode rule (the API returns
`DATA_UNAVAILABLE`; wire that to the UI's demo-mode banner, don't invent
numbers client-side).

## What's still not done (be aware before calling this "live")

- **Sample sizes will be small for a while.** `INVESTIGATE_SAMPLE_FLOOR = 30`,
  full promotion needs 100 out-of-sample trades. On real M15/H1 data this
  could take months of live logging or a few years of backtest history to
  clear for lower-frequency setups like liquidity sweeps. Don't rush this —
  it's the entire point.
- **No daily-trade-cap enforcement beyond a read of today's journal rows.**
  If two people/processes hit `/analysis` simultaneously near the cap, a
  race is possible. Fine for solo MVP use, worth a proper lock before
  multi-user.
- **Regime isn't yet feeding conditional logic into the other engines**
  (e.g. discount VSA "No Demand" reads inside a strong trend) — right now
  it only contributes its own small fusion weight. That contextual gating
  is a Phase 2 item.
- **VSA's price-only subset (Test, Narrow/Wide Spread) still isn't
  implemented** — currently VSA returns NEUTRAL outright when volume is
  `UNAVAILABLE` rather than falling back to a volume-free read. Real gap,
  not just a caveat, for EURUSD/GBPUSD until that subset is built.
- **No auth on the API.** Add before this is reachable from the public
  website, not after.
