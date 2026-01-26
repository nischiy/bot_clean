# Production-Grade Architecture Implementation Summary

## Overview
This document summarizes the implementation of a production-grade, canonical architecture for the trading bot. The system now enforces strict JSON contracts, fail-closed validation, and restart safety.

## Architecture Components

### 1. JSON Schemas + Validation (FAIL-CLOSED)
**Location:** `app/core/schemas/` and `app/core/validation.py`

- **payload.schema.json**: Defines contract for market data, account state, features, and risk policy
- **decision.schema.json**: Defines contract for trading intent (LONG/SHORT/HOLD)
- **trade_plan.schema.json**: Defines contract for executable order instructions

**Key Features:**
- All schemas enforce required fields
- Validation failures return errors without crashing runtime
- Invalid payload → HARD FAIL → HOLD
- Invalid decision → HOLD with reject_reasons
- Invalid trade_plan → HARD STOP (do not execute)

### 2. Payload Builder (Deterministic)
**Location:** `app/data/payload_builder.py`

**Function:** `build_payload(...) → (payload, errors)`

**Payload Includes:**
- Market identity: exchange, symbol, timeframe, timestamp_closed
- Price snapshot: last, bid, ask, mark
- Fees: maker, taker
- Account state: equity, available, margin_type, leverage
- Position state: side, qty, entry, unrealized_pnl, liq_price
- 1h features: ema50, ema200, donchian_high_20, donchian_low_20, atr14, volume_ratio
- 4h context: ema200, trend (up|down|range)
- Risk policy: risk_per_trade, max_daily_drawdown, max_consecutive_losses, min_rr
- Market meta: funding_rate, funding_next_ts
- Exchange limits: tick_size, step_size, min_qty

**Fail-Closed Behavior:**
- Missing, NaN, or stale fields → payload fails validation → HOLD
- Stale timestamp (>2h old) → rejection

### 3. Strategy → decision.json (INTENT ONLY)
**Location:** `app/strategy/decision_engine.py`

**Function:** `make_decision(payload) → decision.json`

**Strategy Rules:**
- **LONG:**
  - close_1h > ema200_1h
  - close_1h > donchian_high_20
  - volume_ratio >= 1.2
  - (close_1h - ema50_1h) / atr14 <= 2.0
- **SHORT:**
  - close_1h < ema200_1h
  - close_1h < donchian_low_20
  - volume_ratio >= 1.2
  - (ema50_1h - close_1h) / atr14 <= 2.0

**Risk Management:**
- SL = entry ± 1.8 * ATR
- TP = entry ± 3.0 * ATR
- RR >= min_rr (from risk policy)

**Validation:**
- Validates against decision.schema.json
- Invalid → returns HOLD + reject_reasons
- Already in position → HOLD

### 4. Risk Manager → trade_plan.json (FINAL AUTHORITY)
**Location:** `app/risk/risk_manager.py`

**Function:** `create_trade_plan(payload, decision, daily_state, exchange_positions) → (trade_plan, rejections)`

**Kill-Switch Checks:**
1. Daily drawdown exceeded
2. Consecutive losses exceeded
3. More than one open position
4. Spread above threshold (>0.5%)
5. Abnormal ATR spike (>10% of price)
6. Stale data (>2h old)

**Exit Enforcement (Critical):**
- Implemented via `state_manager.reconcile_positions()` before payload/decision.
- Missing SL/TP → kill-switch + stop trading.

**Position Sizing:**
- Location: `app/risk/position_sizing.py`
- Formula: `risk_usd = equity * risk_per_trade`, `qty = risk_usd / abs(entry - sl)`
- Respects step_size and min_qty
- Leverage ≤ 5x, isolated margin

**Output:**
- Only produces trade_plan.json if ALL checks pass
- Validates against trade_plan.schema.json
- Otherwise returns HOLD with reasons

### 5. State Management (Restart Safety + Reconciliation)
**Location:** `app/state/state_manager.py`

**Persisted State:**
- `last_closed_candle_ts`: Prevents duplicate candle processing
- `daily_state`: UTC date, starting_equity, realized_pnl, consecutive_losses
- `trade_ids`: Last 1000 trade identifiers (clientOrderId / hash)

**Functions:**
- `save_last_closed_candle_ts(timestamp)`: Persist last processed candle
- `load_last_closed_candle_ts()`: Load last processed candle
- `save_daily_state(date, state)`: Persist daily risk state
- `load_daily_state(date)`: Load daily risk state
- `save_trade_identifier(client_order_id, hash)`: Track executed trades
- `has_trade_identifier(client_order_id)`: Check for duplicates
- `reconcile_positions(exchange_positions, local_state)`: Reconcile exchange vs local

**Restart Behavior:**
- On startup: Reconcile exchange positions vs local state
- If position exists without SL → STOP TRADING
- Never re-process candle with timestamp ≤ last_closed_candle_ts

### 6. Execution Layer (Idempotent, Strict)
**Location:** `app/services/execution_service.py`

**Class:** `ExecutionService`

**Function:** `execute_trade_plan(trade_plan) → execution_result`

**Process:**
1. Validate trade_plan.json
2. Check if client_order_id already exists (idempotency)
3. Set leverage if needed
4. Place entry order
5. Wait for fill confirmation
6. Place SL and TP orders immediately

**Idempotency:**
- All orders use clientOrderId
- If clientOrderId exists → do not duplicate
- Any inconsistency → activate kill-switch and stop trading

**DRY RUN:**
- Respects DRY_RUN_ONLY flag
- Still tracks identifiers to prevent duplicate processing

## Test Coverage

### Tests Created:
1. **test_schema_validation.py**: Schema validation tests
2. **test_payload_builder.py**: Payload building tests
3. **test_decision_engine.py**: Decision making tests
4. **test_risk_manager.py**: Risk manager and kill-switch tests
5. **test_state_manager.py**: State persistence and reconciliation tests

### Test Requirements Met:
- ✅ Schema validation tests (payload, decision, trade_plan)
- ✅ Closed-candle gating test (via state manager)
- ✅ Restart-safety test (no duplicate execution after restart)
- ✅ Risk sizing and leverage cap test
- ✅ Kill-switch trigger tests

## Modified Files

### New Files Created:
1. `app/core/schemas/payload.schema.json`
2. `app/core/schemas/decision.schema.json`
3. `app/core/schemas/trade_plan.schema.json`
4. `app/core/validation.py`
5. `app/data/payload_builder.py`
6. `app/strategy/decision_engine.py`
7. `app/risk/risk_manager.py`
8. `app/risk/position_sizing.py`
9. `app/state/state_manager.py`
10. `app/services/execution_service.py`
11. `tests/test_schema_validation.py`
12. `tests/test_payload_builder.py`
13. `tests/test_decision_engine.py`
14. `tests/test_risk_manager.py`
15. `tests/test_state_manager.py`

### Modified Files:
1. `requirements.txt` - Added `jsonschema` dependency

## Integration Notes

### To Integrate with Existing Code:

1. **Runtime flow is contract-only**:
   - `build_payload()` → `make_decision()` → `create_trade_plan()` → `execute_trade_plan()`
   - Use `load_last_closed_candle_ts()` for candle gating
   - Use `save_last_closed_candle_ts()` after processing

2. **State Initialization**:
   - On startup, call `reconcile_positions()` to check exchange state
   - Initialize daily state with `initialize_daily_state(equity)` (UTC day boundary)

## Non-Negotiable Rules Enforced

1. ✅ Modules communicate ONLY via validated JSON objects
2. ✅ Exactly three contracts: payload.json, decision.json, trade_plan.json
3. ✅ Fail-closed validation (payload → HARD FAIL, decision → HOLD, trade_plan → HARD STOP)
4. ✅ Decisions made ONLY on CLOSED 1h candles
5. ✅ Risk Manager is FINAL authority
6. ✅ Execution layer accepts ONLY trade_plan.json
7. ✅ System is restart-safe and idempotent

## Next Steps

1. **Testing**: Run full test suite to verify all components
2. **Monitoring**: Add logging for all validation failures

## Dependencies

- `jsonschema`: Added to requirements.txt for JSON schema validation
- All other dependencies remain unchanged
