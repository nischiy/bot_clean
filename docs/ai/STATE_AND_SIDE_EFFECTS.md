# State and Side Effects

## Import-Time Side Effects
- **None**: all modules are import-safe. No filesystem writes, network calls, or directory creation at import time.
- Enforcement: `tests/test_imports_no_side_effects.py` verifies no directories created during imports.

## Runtime Side Effects (Filesystem)

### Logs
- `LOG_DIR/`: created on first log write (skipped when `PYTEST_CURRENT_TEST` is set).
  - `LOG_DIR/runtime.log` (runtime log in `app.core.logging.ensure_runtime_logging()`).
  - `LOG_DIR/sessions/{YYYYMMDD_HHMMSS}_{pid}.log` (per-run session log).
  - `LOG_DIR/sessions_clean/{YYYYMMDD_HHMMSS}_{pid}.log` (filtered session log).
  - `LOG_DIR/bootstrap.log` (bootstrap log in `app.bootstrap._ensure_logging()`, skipped in pytest).
  - `LOG_DIR/health/<date>/health.jsonl` (health logs in `core.risk_guard.log_event()`).

### State Persistence
- `run/state/`: created by `app.state.state_manager._ensure_state_dir()` for restart safety.
  - `run/state/candle_gate.json`: Last processed closed candle timestamp (`state_manager.py:34-38`)
  - `run/state/daily_YYYY-MM-DD.json`: Daily risk state (UTC date, starting equity, realized PnL, consecutive losses, extreme_snapback_ts) (`state_manager.py:54-83`)
  - `run/state/trade_ids.json`: Last 1000 trade identifiers (clientOrderId tracking for idempotency) (`state_manager.py:86-106`)
  - `run/state/decision_{SYMBOL}.json`: Decision state (pending entries, event cooldown) (`state_manager.py:177-198`)
  - `run/state/position_{SYMBOL}.json`: Last known position snapshot (for reconciliation) (`state_manager.py:135-143`)
  - `run/state/trade_cooldown.json`: Last trade attempt timestamps (cooldown) (`state_manager.py:201-208`)

### Trade Ledger
- `run/ledger/ledger_YYYY-MM-DD.jsonl`: append-only trade ledger written at runtime via `app.core.trade_ledger.append_event()` (if `LEDGER_ENABLED=1`).
  - Records: `decision_created`, `trade_plan_created`, `execution_attempted`, `execution_submitted`, `sltp_submitted`, `position_closed`, `kill_switch_triggered`, `exit_reconcile_failed`
  - Hashes use canonical JSON serialization with sorted keys (`trade_ledger.py:hash_json`)

### Kill Switch
- `run/TRADE_KILLED.flag`: created when `core.risk_guard.kill()` is called (`risk_guard.py:96-105`).

## Runtime Side Effects (Network)
- Market data retrieval: `app.services.market_data.HttpMarketData.get_klines()` (`run.py:355,362`).
- Price/filters retrieval: `app.run._read_preflight()` via `HttpMarketData` and cached `binance_futures.exchange_info()` (`run.py:397-398`).
- Account snapshot: `core.exchange_private.fetch_futures_private()` (`run.py:483-486`).
- Order submission: `app.services.execution_service.ExecutionService.execute_trade_plan()` (OPEN/CLOSE/UPDATE_SLTP actions; blocked in SAFE_RUN/DRY_RUN_ONLY/LIVE_READONLY) (`execution_service.py:97-101`).

## State (in-memory)
- `TraderApp._last_closed_candle_ts`: last processed closed candle timestamp (seconds, Unix epoch) (`run.py:61,260`).
  - **Note:** Also persisted to `run/state/candle_gate.json` for restart safety.
- `TraderApp.md`: MarketDataAdapter instance (wired in bootstrap) (`bootstrap.py:254`).
- `TraderApp._skip_state_key`, `_skip_agg_count`, `_skip_agg_start`: Skip aggregation state (`run.py:62-65`).

## Ephemeral / Derived
- Market data DataFrames in memory per tick (not persisted).
- Decision dicts per tick (not persisted, but logged to ledger).
- Preflight snapshot dicts per tick (not persisted).
- Payload, decision, trade_plan JSON objects in memory (not persisted, but validated and logged).

## Restart Behavior

### Candle Gating (Restart-Safe)
- **Persisted:** `run/state/candle_gate.json` (`state_manager.py:34-38`)
- **On restart:** `load_last_closed_candle_ts()` restores timestamp (`state_manager.py:41-51`)
- **Enforcement:** `if latest_closed_ts <= last_processed_ts: skip` (`run.py:383-394`)
- **Purpose:** Prevent duplicate candle processing

### Daily State (Persisted)
- **Persisted:** `run/state/daily_YYYY-MM-DD.json` (`state_manager.py:54-83`)
- **On restart:** `load_or_initialize_daily_state()` restores daily metrics for current UTC date (`state_manager.py:328-339`)
- **Reset:** At UTC midnight (date changes)
- **Fields:** `date` (UTC), `starting_equity`, `realized_pnl`, `consecutive_losses`, `extreme_snapback_ts` (optional)

### Trade Identifiers (Idempotency)
- **Persisted:** `run/state/trade_ids.json` (`state_manager.py:86-106`)
- **On restart:** `has_trade_identifier()` prevents duplicate order execution (`state_manager.py:109-119`)
- **Limit:** Last 1000 entries (oldest removed when limit exceeded)
- **Format:** `{client_order_id: {hash, timestamp}}`

### Decision State (Persisted)
- **Persisted:** `run/state/decision_{SYMBOL}.json` (`state_manager.py:177-198`)
- **On restart:** `load_decision_state()` restores pending entries and event cooldown (`state_manager.py:177-189`)
- **Updated:** Via `state_update` field in decision (`run.py:476-478`)

### Position State (Persisted)
- **Persisted:** `run/state/position_{SYMBOL}.json` (`state_manager.py:135-143`)
- **On restart:** `load_position_state()` used for reconciliation (`state_manager.py:146-156`)
- **Updated:** Every tick before reconciliation (`run.py:264-265`)

### Kill-Switch (Restart-Safe)
- `run/TRADE_KILLED.flag` persists across restarts (`risk_guard.py:92-95`)
- `core.risk_guard.is_killed()` reads from disk (`risk_guard.py:92-95`)

## Restart Safety

### ✅ RESTART-SAFE Components:
1. **Candle gating:** `run/state/candle_gate.json` prevents duplicate candle processing.
2. **Daily state:** `run/state/daily_YYYY-MM-DD.json` preserves risk metrics (UTC date-based).
3. **Trade identifiers:** `run/state/trade_ids.json` prevents duplicate order execution.
4. **Decision state:** `run/state/decision_{SYMBOL}.json` preserves pending entries and event cooldown.
5. **Position state:** `run/state/position_{SYMBOL}.json` used for reconciliation.
6. **Kill-switch:** `run/TRADE_KILLED.flag` persists across restarts.

### ⚠️ NOT RESTART-SAFE (Ephemeral):
- In-memory filters cache (`run.py:_FILTERS_CACHE`) - rebuilt on first use, TTL-based.
- Market data DataFrames - re-fetched on restart.
- Decision objects - regenerated from payload on restart.
- Preflight snapshots - re-fetched on restart.

## Reconciliation on Startup

### Position Reconciliation (`run.py:101-106`)
- **Module:** `app.state.state_manager.reconcile_positions()`
- **Trigger:** On startup if `TRADE_ENABLED=1` and not test mode (or `FORCE_RECONCILE=1`)
- **Purpose:** Reconcile exchange positions vs local state.
- **Checks** (`state_manager.py:260-311`):
  - Local state consistency (if provided)
  - Positions without SL → `position_without_sl:{side} qty={qty}`
  - Positions without TP (if `EXIT_REQUIRE_TP=1`, default) → `position_without_tp:{side} qty={qty}`
  - Multiple positions (>1) → `multiple_positions:{count}`
- **Fail-closed:** If `ok=False` → `kill("startup_reconcile_failed")` → raise `RuntimeError` → app does not start

### Daily State Initialization (`state_manager.py:328-339`)
- **Module:** `app.state.state_manager.load_or_initialize_daily_state()`
- **Purpose:** Initialize daily state for new trading day.
- **Boundary:** UTC date rollover (checked via `current_kyiv_date_str()` which returns UTC date).
- **Behavior:** If state exists for current UTC date and `starting_equity > 0`, preserves it; otherwise initializes with current equity.

## State File Locations

```
run/
├── TRADE_KILLED.flag              # Kill-switch flag (risk_guard.py)
└── state/                          # State directory (configurable via STATE_DIR)
    ├── candle_gate.json            # Last processed candle timestamp
    ├── daily_2024-01-01.json       # Daily risk state (one per UTC day)
    ├── trade_ids.json              # Trade identifiers (last 1000)
    ├── decision_BTCUSDT.json       # Decision state (pending, event cooldown)
    ├── position_BTCUSDT.json       # Position snapshot
    └── trade_cooldown.json         # Trade cooldown timestamps

run/ledger/                         # Ledger directory (configurable via LEDGER_DIR)
└── ledger_2024-01-01.jsonl        # Append-only event log (one per UTC day)
```

## Best Practices

1. **State Persistence:** Always use `app.state.state_manager` functions for state operations.
2. **Idempotency:** Always check `has_trade_identifier()` before executing orders (`execution_service.py:119`).
3. **Candle Gating:** Always check `load_last_closed_candle_ts()` before processing candles (`run.py:259-260,383-394`).
4. **Reconciliation:** Always call `reconcile_positions()` on startup if `TRADE_ENABLED=1` (`run.py:101-106`).
5. **Daily State:** Always initialize daily state with `load_or_initialize_daily_state(equity)` (`run.py:471`).
