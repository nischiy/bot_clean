# State and Side Effects

## Import-Time Side Effects
- **None**: all modules are import-safe. No filesystem writes, network calls, or directory creation at import time.
- Enforcement: `tests/test_imports_no_side_effects.py` verifies no directories created during imports.

## Runtime Side Effects (Filesystem)

### Logs
- `LOG_DIR/`: created on first log write (skipped in pytest).
  - `LOG_DIR/runtime.log` (runtime log in `app.run._setup_logging()`).
  - `LOG_DIR/sessions/{YYYYMMDD_HHMMSS}_{pid}.log` (per-run session log).
  - `LOG_DIR/bootstrap.log` (bootstrap log in `app.bootstrap._ensure_logging()`).
  - `LOG_DIR/health/<date>/health.jsonl` (health logs in `core.telemetry.health.log_health()` and `core.risk_guard.log_event()`).

### State Persistence (New)
- `run/state/`: created by `app.state.state_manager` for restart safety.
  - `run/state/candle_gate.json`: Last processed closed candle timestamp
  - `run/state/daily_YYYY-MM-DD.json`: Daily risk state (starting equity, realized PnL, consecutive losses)
  - `run/state/trade_ids.json`: Last 1000 trade identifiers (clientOrderId tracking for idempotency)
  - `run/state/position_state.json`: Last known position snapshot (for reconciliation)
- `run/state/trade_cooldown.json`: Last trade attempt timestamps (cooldown)

### Trade Ledger (New)
- `run/ledger/ledger_YYYY-MM-DD.jsonl`: append-only trade ledger written at runtime via `app/core/trade_ledger.py`.
  - Records decision, trade_plan, execution, sltp, and kill-switch events with deterministic hashes.

### Kill Switch
- `run/TRADE_KILLED.flag`: created when `core.risk_guard.kill()` is called.

## Runtime Side Effects (Network)
- Market data retrieval: `app.services.market_data.HttpMarketData.get_klines()`.
- Price/filters retrieval: `app.run._read_preflight()` via `HttpMarketData` and cached `binance_futures.exchange_info`.
- Account snapshot: `core.exchange_private.fetch_futures_private()`.
- Order submission: `app.services.execution_service.ExecutionService.execute_trade_plan()` (OPEN/CLOSE actions; blocked in SAFE_RUN/DRY_RUN_ONLY/LIVE_READONLY).

## State (in-memory)
- `TraderApp._last_closed_candle_ts`: last processed closed candle timestamp (seconds, Unix epoch).
  - **Note:** This is now also persisted to `run/state/candle_gate.json` for restart safety.
- `TraderApp` references to services: `md`.

## Ephemeral / Derived
- Market data DataFrames in memory per tick (not persisted).
- Decision dicts per tick (not persisted).
- Preflight snapshot dicts per tick (not persisted).
- **New:** `payload.json`, `decision.json`, `trade_plan.json` objects in memory (not persisted, but validated).
 - **New:** Market data validation rejects (in-memory only).
 - **New:** Time-exit produces CLOSE intent (still routed through trade_plan).
- **Time-exit is derived:** computed each tick from closed LTF candles (`close_max_n`/`close_min_n` over `TIME_EXIT_BARS`) and current position snapshot; no extra persisted time-exit state.

## Restart Behavior

### Candle Gating (Now Restart-Safe)
- **Before:** `TraderApp._last_closed_candle_ts` was lost on restart; latest closed candle would be processed again.
- **Now:** `app.state.state_manager.save_last_closed_candle_ts()` persists timestamp to `run/state/candle_gate.json`.
- **On restart:** `load_last_closed_candle_ts()` restores timestamp; candles with timestamp ≤ last_closed_candle_ts are skipped.

### Daily State (Now Persisted)
- **Before:** Daily risk metrics (starting equity, PnL, consecutive losses) were lost on restart.
- **Now:** `app.state.state_manager.save_daily_state()` persists to `run/state/daily_YYYY-MM-DD.json`.
- **On restart:** `load_daily_state()` restores daily metrics for current UTC date.

### Trade Identifiers (Idempotency)
- **Before:** No tracking of executed orders; duplicate execution possible on restart.
- **Now:** `app.state.state_manager.save_trade_identifier()` persists clientOrderId to `run/state/trade_ids.json`.
- **On restart:** `has_trade_identifier()` prevents duplicate order execution.

### Kill-Switch (Already Restart-Safe)
- `run/TRADE_KILLED.flag` persists across restarts; `core.risk_guard.is_killed()` reads from disk.

## Restart Safety

### ✅ RESTART-SAFE Components:
1. **Candle gating:** `run/state/candle_gate.json` prevents duplicate candle processing.
2. **Daily state:** `run/state/daily_YYYY-MM-DD.json` preserves risk metrics.
3. **Trade identifiers:** `run/state/trade_ids.json` prevents duplicate order execution.
4. **Kill-switch:** `run/TRADE_KILLED.flag` persists across restarts.

### ⚠️ NOT RESTART-SAFE (Ephemeral):
- In-memory filters cache (rebuilt on first use).
- Market data DataFrames - re-fetched on restart.
- Decision objects - regenerated from payload on restart.
- Time-exit signal is recalculated from payload each tick; no persisted time-exit state is required.

## Reconciliation on Startup

### Position Reconciliation
- **Module:** `app.state.state_manager.reconcile_positions()`
- **Purpose:** On startup, reconcile exchange positions vs local state.
- **Checks:**
  - Positions without SL → STOP TRADING
  - Multiple positions → STOP TRADING
- **Fail-closed:** If a position exists without SL/TP, trading is stopped until manual intervention.

### Daily State Initialization
- **Module:** `app.state.state_manager.initialize_daily_state()`
- **Purpose:** Initialize daily state for new trading day.
- **Boundary:** UTC date rollover.
- **Behavior:** If state exists for current date, preserves it; otherwise initializes with current equity.

## State File Locations

```
run/
├── TRADE_KILLED.flag          # Kill-switch flag
└── state/
    ├── candle_gate.json        # Last processed candle timestamp
    ├── daily_2024-01-01.json   # Daily risk state (one per day)
    └── trade_ids.json          # Trade identifiers (last 1000)
```

## Best Practices

1. **State Persistence:** Always use `app.state.state_manager` functions for state operations.
2. **Idempotency:** Always check `has_trade_identifier()` before executing orders.
3. **Candle Gating:** Always check `load_last_closed_candle_ts()` before processing candles.
4. **Reconciliation:** Always call `reconcile_positions()` on startup.
5. **Daily State:** Always initialize daily state on startup with `initialize_daily_state(equity)`.