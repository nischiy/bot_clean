# Architecture

## Production-Grade Architecture Overview

The system enforces strict JSON contracts between modules with fail-closed validation:

1. **payload.json** → Market data, account state, features, risk policy
2. **decision.json** → Trading intent (LONG/SHORT/HOLD) - INTENT ONLY
3. **trade_plan.json** → Executable order instructions - ONLY executable orders

## Runtime flow

`main.py → app.bootstrap.compose_trader_app → app.run.TraderApp → JSON contracts → execution`

1. `main.py` loads config via `core.config.loader.get_config()`.
2. `app.bootstrap.compose_trader_app()` coerces config, sets logging, and wires services/adapters.
3. `app.run.TraderApp` manages the loop and calls services on each tick.
4. **New Architecture Flow:**
   - Build `payload.json` from market data and account state
   - Make `decision.json` from payload (strategy intent only)
   - Create `trade_plan.json` from decision (risk manager - final authority)
   - Execute `trade_plan.json` (idempotent execution)
5. Decisions run only once per new closed 5m candle (persisted state gating) with HTF=1h trend context.

Runtime ordering per tick (strict):
1. Position reconciliation
2. SL/TP enforcement (hard kill if missing)
3. Kill-switch flag check
4. Preflight (account + filters + price)
5. Market data validation
6. Payload → decision → trade_plan
7. Execution

## JSON Contract Flow

### Stage 1: Payload Builder
- **Module:** `app.data.payload_builder`
- **Input:** Market data (LTF/HTF), account snapshot, position snapshot, price/filters
- **Output:** `payload.json` (validated against `app/core/schemas/payload.schema.json`)
- **Fail-closed:** Missing/NaN/stale data → payload fails → HOLD
- **Includes:**
  - Market identity, price snapshot, fees
  - Account state, position state
- LTF features (EMA50/EMA120, ATR, RSI14, close_prev, volume ratio)
  - HTF context (EMA200, trend)
  - Risk policy, market meta, exchange limits

### Stage 2: Decision Engine
- **Module:** `app.strategy.decision_engine`
- **Input:** `payload.json`
- **Output:** `decision.json` (validated against `app/core/schemas/decision.schema.json`)
- **Authority:** INTENT ONLY - no execution authority
- **Strategy Rules (5m LTF, 1h HTF):**
  - LONG: HTF trend up, RSI14 <= 40, pullback_atr <= 0.8, reclaim EMA50 (prev close <= EMA50 or prev RSI <= 40)
  - SHORT: HTF trend down, RSI14 >= 60, pullback_atr <= 0.8, reclaim EMA50 (prev close >= EMA50 or prev RSI >= 60)
  - SL = entry ± 1.6 * ATR, TP = entry ± 2.4 * ATR
  - Cooldown: block new trade_plan within 15 minutes of the last trade_plan
- **Fail-closed:** Invalid decision → HOLD with reject_reasons

### Stage 3: Risk Manager
- **Module:** `app.risk.risk_manager`
- **Input:** `payload.json`, `decision.json`, daily state, exchange positions
- **Output:** `trade_plan.json` (validated against `app/core/schemas/trade_plan.schema.json`)
- **Authority:** FINAL AUTHORITY - only produces trade_plan if ALL checks pass
- **Kill-switch Checks:**
  - Daily drawdown exceeded
  - Consecutive losses exceeded
  - Multiple positions
  - Spread too wide
  - Abnormal ATR spike
  - Stale data
- Exit enforcement handled before risk evaluation (missing SL/TP → kill-switch)
- **Position Sizing:** `app.risk.position_sizing`
  - Formula: `risk_usd = equity * risk_per_trade`, `qty = risk_usd / abs(entry - sl)`
  - Respects step_size, min_qty, leverage ≤ 5x
- **Fail-closed:** Any check fails → HOLD with rejections

### Stage 4: Execution Service
- **Module:** `app.services.execution_service`
- **Input:** `trade_plan.json` ONLY
- **Output:** Execution result with entry/SL/TP order status
- **Idempotency:** All orders use clientOrderId, duplicate check prevents re-execution
- **Process:** Entry order → fill confirmation → SL/TP orders immediately
- **LIVE_READONLY:** execution is blocked and logged (trade_plan is still created)

## State Management (Restart Safety)

- **Module:** `app.state.state_manager`
- **Persisted State:**
  - `last_closed_candle_ts`: Prevents duplicate candle processing
  - `daily_state`: UTC date, starting equity, realized PnL, consecutive losses (reset at UTC midnight)
  - `trade_ids`: Last 1000 trade identifiers (clientOrderId tracking)
- **Reconciliation:** On startup, reconcile exchange positions vs local state
- **Restart Safety:** Never re-process candle with timestamp ≤ last_closed_candle_ts

## Active services and responsibilities

- `app.bootstrap.MarketDataAdapter`: loads `app.services.market_data` and exposes `get_klines()` / `get_latest_price()`.
- `app.data.payload_builder`: Builds validated payload.json
- `app.strategy.decision_engine`: Produces validated decision.json (intent only)
- `app.risk.risk_manager`: Produces validated trade_plan.json (final authority)
- `app.risk.position_sizing`: Risk-based position sizing
- `app.state.state_manager`: Persists state for restart safety + reconciliation
- `app.services.execution_service`: Idempotent execution of trade_plan.json

## Core modules and responsibilities

- `core.config.*`: configuration parsing, environment normalization, and defaults.
- `core.env_loader`: `.env` discovery and parsing.
- `core.exchange_private`: private account snapshot via REST helpers in `core.execution.binance_futures`.
- `core.execution.binance_futures`: REST helpers for public/private Binance Futures calls.
- `core.risk_guard`: risk guard and kill switch, plus health logging.
- `core.telemetry.health`: JSONL health logging.

## JSON Schema Validation

- **Module:** `app.core.validation`
- **Schemas:** `app/core/schemas/*.schema.json`
- **Functions:**
  - `validate_payload(payload) → (ok, errors)`
  - `validate_decision(decision) → (ok, errors)`
  - `validate_trade_plan(trade_plan) → (ok, errors)`
- **Fail-closed:** Validation failures return errors without crashing runtime

## Bootstrap / wiring

`compose_trader_app()`:

- Coerces config and mirrors selected values into `os.environ`.
- Initializes logging.
- Instantiates `TraderApp` with `cfg`, `symbol`, and logger.
- Wires `MarketDataAdapter` (critical).

## Side effects

- Import-time side effects: none.
- Startup logging:
  - `app.bootstrap` and `app.run` may create `logs/` and write log files (skipped in tests).
  - `app.state.state_manager` creates `run/state/` directory for persistence.
- Runtime:
  - `TraderApp.start()` runs an infinite loop unless `APP_RUN_ONESHOT=1` or test/CI is detected.
  - `core.risk_guard.kill()` / `log_event()` create `run/` and `logs/health/<date>/` on demand.
  - State persistence: `run/state/*.json` files for candle gating, daily state, trade IDs.
- Shutdown:
  - `TraderApp` exits on `KeyboardInterrupt`.

## Critical components (must exist)

- `core.config.loader.get_config`
- `app.run.TraderApp`
- `app.bootstrap.MarketDataAdapter` (backed by `app.services.market_data`)
- `app.services.execution_service.ExecutionService`
- `app.data.payload_builder.build_payload`
- `app.strategy.decision_engine.make_decision`
- `app.risk.risk_manager.create_trade_plan`
- `app.state.state_manager` (for restart safety)

## Non-Negotiable Rules

1. **Modules communicate ONLY via validated JSON objects** (payload.json, decision.json, trade_plan.json)
2. **Exactly three contracts exist** - no other communication paths
3. **Fail-closed validation:**
   - Invalid payload → HARD FAIL → HOLD
   - Invalid decision → HOLD with reject_reasons
   - Invalid trade_plan → HARD STOP (do not execute)
4. **Decisions made ONLY on CLOSED 5m candles** (persisted timestamp gating)
5. **Risk Manager is FINAL AUTHORITY** - Strategy cannot place executable orders
6. **Execution layer accepts ONLY trade_plan.json**
7. **System is restart-safe and idempotent** (state persistence, clientOrderId tracking)
