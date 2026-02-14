# Runtime Flow - Exact Per-Tick Execution Order

This document describes the **exact** runtime flow per tick as implemented in `app/run.py:_run_once_contracts()`.

## Entry Point

- **File:** `main.py`
- **Function:** `main()`
- **Flow:** `get_config()` → `compose_trader_app()` → `TraderApp.start()`

## Per-Tick Execution Order

Each tick executes `TraderApp.run_once()` which calls `_run_once_contracts()` (`run.py:236-684`).

### Step 1: Position Snapshot (`run.py:264-265`)
- **Function:** `_get_position_snapshot(symbol)`
- **Action:** Fetch current position from exchange via `ExecutionService.get_position_snapshot()`
- **Side effect:** Save to `run/state/position_{SYMBOL}.json` via `save_position_state()`
- **Purpose:** Track position state for reconciliation

### Step 2: Position Reconciliation (`run.py:267-328`)
- **Function:** `reconcile_positions(exchange_positions, open_orders, local_state, require_tp=True)`
- **Source:** `app.state.state_manager.reconcile_positions()`
- **Checks:**
  1. Local state consistency (if provided)
  2. Positions without SL → `position_without_sl:{side} qty={qty}`
  3. Positions without TP (if `EXIT_REQUIRE_TP=1`, default) → `position_without_tp:{side} qty={qty}`
  4. Multiple positions (>1) → `multiple_positions:{count}`
- **Fail-closed:** If `ok=False` → **HARD KILL** via `kill("exit_reconcile_failed")` → raise `RuntimeError` → skip all trading
- **Side effect:** Creates kill-switch flag, logs to ledger

### Step 3: Kill-Switch Check (`run.py:330-342`)
- **Function:** `is_killed()`
- **Source:** `core.risk_guard.is_killed()`
- **Check:** `run/TRADE_KILLED.flag` exists
- **Fail-closed:** If active → skip all trading, emit `tick_summary` with `skip_reason="killed"`, return early
- **Side effect:** None (read-only check)

### Step 4: Market Data Fetch (`run.py:351-366`)
- **Function:** `app.md.get_klines(symbol, interval, limit=1000)` for LTF, `limit=500` for HTF
- **Action:** Fetch market data, filter to closed candles only (`_filter_closed_candles`)
- **Fail-closed:** If exception → `df_ltf = None` or `df_htf = None` → will fail at validation step
- **Side effect:** Network call (unless cached)

### Step 5: Closed Candle Detection (`run.py:368-394`)
- **Function:** `_latest_closed_candle_ts(df_ltf)`
- **Check:** Extract latest closed candle timestamp from DataFrame
- **Fail-closed:** If `latest_closed_ts is None` → skip processing, emit `tick_summary` with `skip_reason="no_closed_candle"`, return early
- **Gating:** If `latest_closed_ts <= last_processed_ts` → skip processing, emit `tick_summary` with `skip_reason="already_processed"`, return early
- **Purpose:** Prevent duplicate candle processing (restart safety)

### Step 6: Preflight (`run.py:397-423`)
- **Function:** `_read_preflight(symbol, wallet_usdt)`
- **Source:** `app.run._read_preflight()` → `_build_preflight_snapshot()`
- **Actions:**
  1. Fetch account snapshot (`_fetch_account_snapshot`) → `core.exchange_private.fetch_futures_private()`
  2. Fetch price snapshot (`_fetch_price_snapshot`) → `HttpMarketData.get_latest_price()` + `binance_futures._get("/fapi/v1/ticker/bookTicker")`
  3. Fetch filters snapshot (`_fetch_filters_snapshot`) → `binance_futures.exchange_info()` (cached for `FILTERS_CACHE_TTL_SEC`)
- **Validation:** `_validate_preflight()` checks:
  - Account errors (CONFIG_ERROR, TRANSIENT_ERROR) → `preflight_config_error` or `preflight_transient_error`
  - Missing equity → `missing_account_equity`
  - Missing available → `missing_account_available`
  - Missing wallet → `missing_wallet_usdt`
  - Missing price → `missing_price`
  - Missing step_size → `missing_filter_step_size`
  - Missing min_qty → `missing_filter_min_qty`
  - Missing tick_size → `missing_filter_tick_size`
- **Fail-closed:** If `preflight_rejects` → save candle timestamp, emit `tick_summary` with `skip_reason="preflight_reject"`, return early
- **Side effect:** Network calls (unless cached), creates `run/` directory if needed

### Step 7: Market Data Validation (`run.py:425-440`)
- **Function:** `validate_market_data(df_ltf, df_htf)`
- **Source:** `app.data.market_data_validator.validate_market_data()`
- **Checks:** See `docs/ARCHITECTURE.md` Stage 0 for complete list
- **Fail-closed:** If `ok=False` → save candle timestamp, emit `tick_summary` with `skip_reason="md_invalid"`, return early
- **Side effect:** None (pure function)

### Step 8: Payload Building (`run.py:441-467`)
- **Function:** `build_payload(symbol, df_ltf, df_htf, account_snapshot, position_snapshot, price_snapshot, filters_snapshot, timestamp_closed, timeframe, htf_timeframe)`
- **Source:** `app.data.payload_builder.build_payload()`
- **Fail-closed conditions:** See `docs/ARCHITECTURE.md` Stage 1
- **Fail-closed:** If `payload is None` → save candle timestamp, emit `tick_summary` with `skip_reason="payload_invalid"`, return early
- **Side effect:** None (pure function)

### Step 9: Decision Making (`run.py:471-479`)
- **Function:** `make_decision(payload, daily_state, decision_state)`
- **Source:** `app.strategy.decision_engine.make_decision()`
- **Inputs:**
  - `daily_state` from `load_or_initialize_daily_state()` (UTC date-based)
  - `decision_state` from `load_decision_state(symbol)` (pending entries, cooldowns, event timestamps)
- **Output:** `decision.json` (validated against schema)
- **Side effect:** Updates decision state via `state_update` field → `save_decision_state()` if present
- **Purpose:** Generate trading intent (LONG/SHORT/HOLD/CLOSE/UPDATE_SLTP)

### Step 10: Decision Logging (`run.py:498-520`)
- **Action:** Log `decision_created` event to ledger, build `decision_log` dict
- **Side effect:** Ledger write (if `LEDGER_ENABLED=1`)

### Step 11: UPDATE_SLTP Check (`run.py:522-543`)
- **Condition:** If `decision.get("move_sl_to_be_after_tp1")` and `has_open_position`
- **Check:** If TP2 still open (TP1 likely filled) → modify decision to `intent="UPDATE_SLTP"` with break-even SL
- **Note:** This is a placeholder implementation - full TP1 fill detection would track original qty and TP1 qty
- **Side effect:** Modifies decision dict in-place

### Step 12: Intent Check (`run.py:545-562`)
- **Check:** If `intent not in ("LONG", "SHORT", "CLOSE", "UPDATE_SLTP")`
- **Action:** Log decision, emit `tick_summary` with `skip_reason="no_trade_signal"`, save candle timestamp, return early
- **Purpose:** Skip trade plan creation if no actionable intent

### Step 13: Trade Plan Creation (`run.py:564`)
- **Function:** `create_trade_plan(payload, decision, daily_state, exchange_positions)`
- **Source:** `app.risk.risk_manager.create_trade_plan()`
- **Fail-closed:** If `trade_plan is None` → log decision with rejections, emit `tick_summary` with `skip_reason="risk_reject"` or `"decision_invalid"`, save candle timestamp, return early
- **Side effect:** None (pure function, but may update daily_state for extreme_snapback_ts)

### Step 14: Trade Plan Validation (`run.py:623-626`)
- **Function:** `validate_trade_plan(trade_plan)`
- **Source:** `app.core.validation.validate_trade_plan()`
- **Fail-closed:** If `ok=False` → raise `RuntimeError` (hard stop, should never happen if risk manager is correct)
- **Side effect:** None (pure function)

### Step 15: Execution (`run.py:628-671`)
- **Function:** `execute_trade_plan(trade_plan)`
- **Source:** `app.services.execution_service.ExecutionService.execute_trade_plan()`
- **Blocking checks** (in order):
  1. `LIVE_READONLY=1` → log `execution_blocked`, return early (no orders placed)
  2. `SAFE_RUN=1` → return `_safe_run_response()` (no orders placed)
  3. `DRY_RUN_ONLY=1` or `PAPER_TRADING=1` → return dry-run response (no orders placed)
- **Process:** See `docs/ARCHITECTURE.md` Stage 4
- **Side effect:** Network calls (if not blocked), state persistence (trade_ids), ledger writes

### Step 16: Candle Timestamp Save (`run.py:683-684`)
- **Function:** `save_last_closed_candle_ts(latest_closed_ts)`
- **Source:** `app.state.state_manager.save_last_closed_candle_ts()`
- **Action:** Persist timestamp to `run/state/candle_gate.json`
- **Purpose:** Prevent duplicate processing on next tick
- **Side effect:** File write

### Step 17: Tick Summary (`run.py:673-682`)
- **Function:** `_emit_tick_summary()` with `will_process=True`
- **Action:** Log `tick_summary` event
- **Side effect:** Log write

## Skip Reasons

The following `skip_reason` values are emitted in `tick_summary`:

- `"reconcile_failed"` — position reconciliation failed (Step 2)
- `"exits_missing"` — position without SL/TP (Step 2)
- `"killed"` — kill-switch active (Step 3)
- `"no_closed_candle"` — no closed candle available (Step 5)
- `"already_processed"` — candle already processed (Step 5)
- `"preflight_reject"` — preflight validation failed (Step 6)
- `"md_invalid"` — market data validation failed (Step 7)
- `"payload_invalid"` — payload building failed (Step 8)
- `"no_trade_signal"` — decision intent is HOLD (Step 12)
- `"risk_reject"` — trade plan creation failed (Step 13)
- `"decision_invalid"` — decision schema validation failed (Step 13)

## Fail-Closed Points

The following steps cause **HARD FAIL** (kill-switch or exception):

1. **Position reconciliation failure** (`run.py:301-328`) → `kill("exit_reconcile_failed")` → raise `RuntimeError`
2. **Exit reconcile error** (`run.py:272-291`) → `kill("exit_reconcile_error")` → raise `RuntimeError`
3. **Trade plan schema validation failure** (`run.py:623-626`) → raise `RuntimeError` (should never happen)

All other failures result in **HOLD** (skip processing, save candle timestamp, continue to next tick).

## Determinism Guarantees

- **Deterministic:** Steps 7-13 (market data validation → trade plan creation) are deterministic given same inputs
- **Non-deterministic:** Steps 1-6, 15 (network calls, exchange state) are non-deterministic
- **Restart-safe:** Candle gating (Step 5) ensures same candle is never processed twice, even after restart

## State Persistence Points

- **Every tick:** Position snapshot (Step 1)
- **On successful processing:** Candle timestamp (Step 16)
- **On decision state update:** Decision state (Step 9)
- **On trade plan creation:** Trade identifiers (Step 15, inside execution)
- **On daily state update:** Daily state (Step 9, if extreme_snapback_ts set)

## Network Calls

- **Step 1:** `ExecutionService.get_position_snapshot()` → exchange REST
- **Step 2:** `ExecutionService.fetch_positions()`, `ExecutionService.get_open_orders()` → exchange REST
- **Step 4:** `app.md.get_klines()` → market data API
- **Step 6:** `fetch_futures_private()`, `get_latest_price()`, `exchange_info()` → exchange REST (cached where applicable)
- **Step 15:** `place_order_via_rest()`, `set_leverage_via_rest()`, `cancel_order_via_rest()` → exchange REST (blocked in readonly modes)

## Error Handling

- **Exceptions in Steps 1-6:** Caught, logged, may trigger kill-switch or skip processing
- **Exceptions in Steps 7-13:** Fail-closed → HOLD (skip processing, save timestamp)
- **Exceptions in Step 15:** Logged, execution result includes errors, does not crash runtime loop
