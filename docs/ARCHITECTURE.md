# Architecture

## Production-Grade Architecture Overview

## Predictive-First Decision Architecture

The decision engine now runs as a deterministic 3-stage pipeline:

1. `Stage 1: Predictive inference`
   - `app.strategy.predictive_engine.infer_predictive_layer()`
   - infers `predictive_bias`, `predictive_state`, `confidence_tier`, named triggers, invalidations, and an explicit market-state transition before any legacy strategy is selected.
   - persists restart-safe state in `decision_state_{SYMBOL}.json` via `market_state`, `predictive_memory`, `last_predictive_bias`, and `last_transition`.
2. `Stage 2: Legacy strategy validation`
   - existing strategies remain intact: `PULLBACK_REENTRY`, `CONTINUATION`, `TREND_ACCEL`, `BREAKOUT_EXPANSION`, `SQUEEZE_BREAK`, `RANGE_MEANREV`.
   - they now act as validators, confidence amplifiers, and analytics labels rather than the only source of directional intent.
3. `Stage 3: Execution decision`
   - maps predictive inference + legacy validation into `OPEN_LONG_EARLY`, `OPEN_LONG_CONFIRMED`, `OPEN_SHORT_EARLY`, `OPEN_SHORT_CONFIRMED`, `HOLD_LATE`, `HOLD_EVENT`, `HOLD_LOW_QUALITY`, or `HOLD`.
   - keeps legacy `intent` compatibility for the runtime and risk manager.

Risk management remains the final authority. Predictive logic never bypasses kill-switches, spread checks, stale-data guards, cooldowns, drawdown controls, reconciliation, or sizing limits.

The system enforces strict JSON contracts between modules with fail-closed validation:

1. **payload.json** → Market data, account state, features, risk policy
2. **decision.json** → Trading intent (LONG/SHORT/HOLD/CLOSE/UPDATE_SLTP) - INTENT ONLY
3. **trade_plan.json** → Executable order instructions - ONLY executable orders

## Runtime flow

`main.py → app.bootstrap.compose_trader_app → app.run.TraderApp → JSON contracts → execution`

1. `main.py` loads config via `core.config.loader.get_config()` and composes the trading app.
2. `app.bootstrap.compose_trader_app()` coerces config, sets logging, and wires services/adapters (MarketDataAdapter).
3. `app.run.TraderApp.start()` manages the main loop and calls `run_once()` on each tick.
4. **Architecture Flow (per tick, from `app/run.py:_run_once_contracts`):**
   - Position snapshot (`_get_position_snapshot`) → save to `position_{SYMBOL}.json`
   - Position reconciliation (`reconcile_positions`) → **HARD KILL** if missing SL/TP or multiple positions
   - Kill-switch flag check (`is_killed()`) → skip all trading if active
   - Preflight (`_read_preflight`) → account + filters + price snapshots → **HOLD** if rejects
   - Market data validation (`validate_market_data`) → **HOLD** if invalid
   - Build `payload.json` from market data and account state
   - Make `decision.json` from payload (strategy intent only)
   - Create `trade_plan.json` from decision (risk manager - final authority)
   - Execute `trade_plan.json` (idempotent execution, blocked in LIVE_READONLY/SAFE_RUN)
5. Decisions run only once per new closed 5m candle (persisted state gating) with HTF=1h trend context.

Runtime ordering per tick (strict, enforced in `app.run._run_once_contracts`):
1. **Position snapshot** (`_get_position_snapshot`) → save to `position_{SYMBOL}.json` (`run.py:264-265`)
2. **Position reconciliation** (`reconcile_positions`) → **HARD KILL** if missing SL/TP or multiple positions (`run.py:293-328`)
3. **Kill-switch flag check** (`is_killed()`) → skip all trading if active (`run.py:330-342`)
4. **Preflight** (`_read_preflight`) → account + filters + price snapshots → **HOLD** if rejects (`run.py:397-423`)
5. **Market data validation** (`validate_market_data`) → **HOLD** if invalid (`run.py:425-440`)
6. **Payload building** (`build_payload`) → **HOLD** if fails validation (`run.py:441-467`)
7. **Decision making** (`make_decision`) → intent only (`run.py:473-474`)
8. **Trade plan creation** (`create_trade_plan`) → final authority (`run.py:564`)
9. **Execution** (`execute_trade_plan`) → idempotent, blocked in readonly modes (`run.py:628-671`)

## JSON Contract Flow

### Stage 0: Market Data Validator
- **Module:** `app.data.market_data_validator`
- **Function:** `validate_market_data(df_ltf, df_htf, now_ts=None)`
- **Input:** LTF DataFrame (5m), HTF DataFrame (1h), optional current timestamp
- **Output:** `(ok: bool, errors: List[str])` tuple
- **Fail-closed checks** (`market_data_validator.py:23-68`):
  - Required columns: `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume` → `missing_col_ltf:{col}`
  - Empty DataFrame → `empty_df_ltf`
  - Minimum bars: `len(df_ltf) < MD_MIN_BARS_1H` (default 260) → `insufficient_bars_ltf:{len}`
  - Minimum bars HTF: `len(df_htf) < MD_MIN_BARS_4H` (default 220) → `insufficient_bars_htf:{len}`
  - Invalid close_time (NaT) → `invalid_close_time`
  - Non-monotonic timestamps → `non_monotonic_close_time`
  - Missing timezone → `close_time_not_tz_aware`
  - Stale data: `now_ts - last_ts > MD_MAX_AGE_SECONDS` (default 7200) → `stale_market_data` (skipped in replay mode)
  - Time gaps: `gap > MD_MAX_GAP_SECONDS` (default 7200) → `time_gap_exceeded`
  - NaNs in critical columns (`close`, `high`, `low`, `volume`) within lookback window → `nan_in_{col}`
- **Deterministic:** Yes (given same data)
- **Side effects:** None
- **Invalid data → HOLD** (runtime saves candle timestamp and skips processing)

### Stage 1: Payload Builder
- **Module:** `app.data.payload_builder`
- **Function:** `build_payload(symbol, df_ltf, df_htf, account_snapshot, position_snapshot, price_snapshot, filters_snapshot, timestamp_closed, timeframe, htf_timeframe)`
- **Input:** Market data (LTF/HTF DataFrames), account snapshot, position snapshot, price/filters
- **Output:** `(payload: Optional[Dict], errors: List[str])` - validated against `app/core/schemas/payload.schema.json`
- **Fail-closed conditions** (`payload_builder.py:449-454`):
  - `equity is None or equity <= 0` → `missing_or_invalid_equity`
  - `funds_base is None` → `funds_source_missing`
  - `funds_base <= 0` → `funds_nonpositive`
- **Funds base calculation** (`payload_builder.py:438-445`):
  - If both `available` and `total_margin` exist: `funds_base = min(available, total_margin)`, `funds_source = "min(available_balance,total_margin_balance)"`
  - Else if `available` exists: `funds_base = available`, `funds_source = "available_balance"`
  - Else: `funds_base = None` → fail-closed
- **Includes:**
  - Market identity: exchange, symbol, timeframe, timestamp_closed
  - Price snapshot: last, bid, ask, mark (all required)
  - Fees: maker, taker (defaults from `FEE_MAKER_BPS`, `FEE_TAKER_BPS`)
  - Account state: equity, available, funds_base, funds_source, margin_type, leverage
  - Position state: side (LONG/SHORT/null), qty, entry, unrealized_pnl, liq_price
  - LTF features: close, close_prev, high, low, ema50, ema120, donchian_high_240, donchian_low_240, atr14, atr14_sma20, donchian_high_20, donchian_low_20, consec_close_above_donchian_20, consec_close_below_donchian_20, bb_upper, bb_lower, bb_mid, candle_body_ratio, volume_ratio, rsi14, rsi14_prev, consec_above_ema50, consec_below_ema50, consec_above_ema50_prev, consec_below_ema50_prev, close_max_n, close_min_n, time_exit_bars
  - HTF context: ema200, ema200_prev_n, ema200_slope_norm, consec_above_ema200, consec_below_ema200, consec_higher_close, consec_lower_close, close, trend (up/down/range), timeframe, atr14
  - Risk policy: risk_per_trade, max_daily_drawdown, max_consecutive_losses, min_rr
  - Market meta: funding_rate, funding_next_ts
  - Exchange limits: tick_size, step_size, min_qty
- **Deterministic:** Yes (given same inputs, produces same payload)
- **Side effects:** None (pure function)
- **Fail-closed:** Missing/NaN/stale data → payload fails → HOLD

### Stage 2: Decision Engine
- **Module:** `app.strategy.decision_engine`
- **Function:** `make_decision(payload, daily_state, decision_state)`
- **Input:** `payload.json`, daily state, decision state
- **Output:** `decision.json` (validated against `app/core/schemas/decision.schema.json`)
- **Authority:** INTENT ONLY - no execution authority
- **Deterministic:** Yes (given same payload and state)
- **Side effects:** Updates decision state (pending entries, cooldowns, predictive state machine, analytics queue) via `state_update` field

**Predictive layer outputs:**
- `predictive_bias`: `LONG|SHORT|NEUTRAL`
- `predictive_state`: `EARLY_LONG|EARLY_SHORT|LONG_FAILURE|SHORT_FAILURE|BREAKDOWN_RISK|BREAKOUT_RISK|EVENT_DIRECTIONAL|CHOP|NEUTRAL`
- `confidence_tier`: `LOW|MEDIUM|HIGH`
- `market_state_prev`, `market_state_next`, `transition_name`
- `trigger_candidates`, `invalidation_reasons`
- `execution_decision`, `entry_mode`

**Predictive state machine:**
- directional health and pullback states:
  - `UPTREND_HEALTHY`, `PULLBACK_ACTIVE`, `RECLAIM_PENDING`, `RECLAIM_FAILED`, `BEARISH_TRANSITION`, `BREAKDOWN_CONFIRMED`
  - `DOWNTREND_HEALTHY`, `SHORT_PULLBACK_ACTIVE`, `SHORT_RECLAIM_PENDING`, `SHORT_RECLAIM_FAILED`, `BULLISH_TRANSITION`, `BREAKOUT_CONFIRMED`
- neutral/event states:
  - `RANGE_BALANCED`, `EVENT_DIRECTIONAL`, `EVENT_CHAOTIC`

**Execution modes:**
- `EARLY` entries use reduced size and predictive invalidation-based SL placement.
- `CONFIRMED` entries use the normal legacy strategy sizing/risk profile.
- both are still subject to the same risk-manager veto path.

**Analytics labels:**
- `app.strategy.analytics_labels.update_analytics_labels()` keeps a restart-safe rolling queue and finalizes labels after `PREDICTIVE_LABEL_HORIZON_CANDLES`.
- logs realized move labels, early-signal correctness, confirmed-signal lateness, event-block misses, reclaim-conversion misses, and other missed-opportunity telemetry.

**Regime Detection (5m, deterministic, exclusive, top-down, from `_detect_regime` in `decision_engine.py:310-383`):**
1. **EVENT** → if `event_detected=True` (true_range >= EVENT_TR_ATR * atr14) → **BLOCKS** entries (routes to "NONE" strategy)
2. **SQUEEZE_BREAK** → if `squeeze_break_long or squeeze_break_short` (BB width expansion + Donchian break)
3. **BREAKOUT_EXPANSION** → if `(brk_up or brk_dn) and volume_ratio >= breakout_vol_min`
4. **COMPRESSION** → if `dc_width_atr <= compression_width_atr_max and volume_ratio <= compression_vol_max` → **BLOCKS** entries
5. **TREND_ACCEL** → if `trend_bias and vol_expansion and dist50 <= trend_dist50_max`
6. **TREND_CONTINUATION** → if `trend_bias and dist50 <= trend_dist50_max and impulse_ok and (brk_up or brk_dn)`
7. **PULLBACK** → if `trend_bias and dist50 > trend_dist50_max`
8. **RANGE** → default fallback

**Strategy Routing** (`select_strategy_by_regime` in `decision_engine.py:386-401`):
- BREAKOUT_EXPANSION → "BREAKOUT_EXPANSION" (if `breakout_expansion_long_ok` or `breakout_expansion_short_ok`)
- SQUEEZE_BREAK → "SQUEEZE_BREAK" (if `squeeze_break_long_ok` or `squeeze_break_short_ok`)
- TREND_ACCEL → "TREND_ACCEL" (if `trend_accel_long_ok` or `trend_accel_short_ok`)
- TREND_CONTINUATION → "CONTINUATION" (if `cont_long_ok` or `cont_short_ok`)
- PULLBACK → "PULLBACK_REENTRY" (if `pullback_reentry_long_ok` or `pullback_reentry_short_ok`)
- RANGE → "RANGE_MEANREV" (if `range_meanrev_long_ok` or `range_meanrev_short_ok`)
- EVENT/COMPRESSION → "NONE" (blocks entries)
- Override: `ROUTING_REGIME_OVERRIDE` can force routing (non-production/test/offline/replay only)

**Time-Exit Precedence:**
- If `time_exit_signal=True` (from `time_exit_bars` and `time_exit_progress_atr`), emit `intent=CLOSE` and skip all entry logic.

**HTF Trend Stability Gate (1h):**
- Uses closed HTF candles only.
- EMA200 slope (ATR-normalized) + consecutive closes on EMA200 side.
- Optional structure persistence (consecutive higher/lower closes).

**LTF Stability Scoring (trend-following only, from `_compute_stability` in `decision_engine.py:65-100`):**
- Computed from EMA50 trend candles ratio, wick ratio frequency, and dist50 overextension.
- Formula: `score = 0.55 * R + 0.25 * (1 - W) + 0.20 * (1 - X)` where:
  - `R = trend_candles / STABILITY_N`
  - `W = wick_ratio_count / STABILITY_N`
  - `X = clamp(dist50 / XMAX, 0, 1)`
- Hard gate (≥STABILITY_HARD, default 0.70): allow
- Soft gate (≥STABILITY_SOFT, default 0.58): requires continuation confirmation + HTF anti-reversal filter
- Block (<STABILITY_SOFT): HOLD
- Applied to TREND_CONTINUATION, PULLBACK_REENTRY, and TREND_ACCEL.

**Continuation Confirmation (soft stability only, from `_continuation_confirmation` in `decision_engine.py:103-216`):**
- Any of: two-bar continuation, EMA50/BB mid retest-reject, or lower-high/higher-low break.

**HTF Anti-Reversal Filter (soft stability only):**
- Blocks entries on HTF EMA reclaim or RSI slope + LTF wick ratio.

**Pending Entry Confirmation:**
- Signals may enter a pending state (`pending_entry_status`) that must confirm within `PENDING_CONFIRM_CANDLES` or expires after `PENDING_EXPIRE_CANDLES`.

**Optional EV Gate (disabled by default):**
- Computes simple EV score from stability + confirmation; blocks low/negative EV entries when `EV_GATE_ENABLED=1`.

**Cooldown:**
- Blocks new trade_plan within `TRADE_COOLDOWN_MINUTES` (default 15) of the last trade_plan.

**Regime Exit Behavior Mapping** (`decision_engine.py:1511-1519`):
- BREAKOUT_EXPANSION → "trailing" (TP1 + trailing SL after TP1 fill)
- TREND_ACCEL → "trailing"
- SQUEEZE_BREAK → "trailing"
- CONTINUATION → "partial_fixed" (TP1 + TP2, fixed SL)
- PULLBACK_REENTRY → "partial_fixed"
- RANGE_MEANREV → "fixed" (single TP)

**Partial Position Closure (TP1/TP2):**
- When `regime_exit_behavior in ("trailing", "partial_fixed")` and `rr_target >= 2.0` (`decision_engine.py:1413`):
  - TP1: 60% of target RR, quantity = `TP1_FRACTION` (default 0.4 = 40% of position)
  - TP2: 100% of target RR, quantity = `1 - TP1_FRACTION` (default 0.6 = 60% of position)
  - Creates `tp_targets` array in decision with both TP prices and quantity fractions
- For `trailing` behavior: sets `move_sl_to_be_after_tp1=True` to trigger UPDATE_SLTP after TP1 fill

**Fail-closed:** Invalid decision → HOLD with reject_reasons

### Stage 3: Risk Manager
- **Module:** `app.risk.risk_manager`
- **Function:** `create_trade_plan(payload, decision, daily_state, exchange_positions)`
- **Input:** `payload.json`, `decision.json`, daily state, exchange positions
- **Output:** `(trade_plan: Optional[Dict], rejections: List[str])` - validated against `app/core/schemas/trade_plan.schema.json`
- **Authority:** FINAL AUTHORITY - only produces trade_plan if ALL checks pass
- **Deterministic:** Yes (given same inputs, produces same trade plan)
- **Side effects:** None (pure function)

**Kill-Switch Checks** (`check_kill_switches` in `risk_manager.py:20-100`):
1. Kill-switch flag (`is_killed()`) → `kill_switch_engaged`
2. Daily drawdown: `abs(min(0, daily_pnl)) / starting_equity * 100 >= max_daily_drawdown` → `daily_drawdown_exceeded:{pct}% >= {max}%`
3. Consecutive losses: `consec_losses >= max_consecutive_losses` → `consecutive_losses_exceeded:{count} >= {max}`
4. Multiple positions: `len(open_positions) > 1` → `multiple_positions:{count} > 1`
5. Spread too wide: `abs(ask - bid) / last * 100 > SPREAD_MAX_PCT` (default 0.5) → `spread_too_wide:{pct}%`
6. Abnormal ATR spike: `atr14 > last * ATR_SPIKE_MAX_PCT` (default 0.1) → `abnormal_atr_spike:{atr} > {threshold}`
7. Stale data: `timestamp_closed < now_ts - DATA_MAX_AGE_SECONDS` (default 7200) → `stale_data`

**Cooldown Check** (`risk_manager.py:122-132`):
- Blocks if `intent in ("LONG", "SHORT")` and `(ts - last_ts) < TRADE_COOLDOWN_MINUTES * 60`
- Returns `cooldown_active:{elapsed}s<{required}s`

**Position Sizing** (`calculate_position_size` in `app/risk/position_sizing.py:10-95`):
- Formula: `risk_usd = funds_base * risk_per_trade` (`position_sizing.py:61`)
- Formula: `qty_raw = risk_usd / abs(entry - sl)` (`position_sizing.py:70`)
- Rounding: `qty = floor(qty_raw / step_size) * step_size` (`position_sizing.py:73`)
- Validation: `qty >= min_qty` (`position_sizing.py:76-80`)
- Margin check: `required_margin = (qty * entry) / leverage` (`position_sizing.py:90`)
- Fail-closed: `margin_needed > funds_base` → `insufficient_margin:{needed} > {available}` (`position_sizing.py:91-93`)

**Partial Position Closure (TP1/TP2):**
- When decision includes `tp_targets` array with 2+ targets (`risk_manager.py:271`):
  - Split quantity: `qty_tp1 = qty * TP1_FRACTION` (default 40%), `qty_tp2 = qty * (1 - TP1_FRACTION)` (default 60%)
  - Both quantities rounded to step_size, validated against min_qty (`risk_manager.py:278-286`)
  - Creates `tp_orders` array in trade_plan with separate TP1 and TP2 orders (`risk_manager.py:304-319`)
  - Each TP order has its own clientOrderId for idempotency

**UPDATE_SLTP Action:**
- Triggered when `intent == "UPDATE_SLTP"` (`risk_manager.py:158-200`)
- Requires valid position (side, qty > 0) and new SL price
- Creates trade_plan with action="UPDATE_SLTP" and stop_loss object

**Fail-closed:** Any check fails → HOLD with rejections

### Stage 4: Execution Service
- **Module:** `app.services.execution_service`
- **Function:** `execute_trade_plan(trade_plan)`
- **Input:** `trade_plan.json` ONLY
- **Output:** Execution result with entry/SL/TP order status
- **Idempotency:** All orders use clientOrderId, duplicate check via `has_trade_identifier()` prevents re-execution (`execution_service.py:119-140`)
- **Process** (`execution_service.py:159-320`):
  1. Validate trade_plan schema
  2. Check idempotency (`has_trade_identifier`)
  3. Persist identifier (`save_trade_identifier`) before any live submission
  4. Set leverage if needed
  5. Place entry order → wait for fill confirmation
  6. Place SL and TP orders immediately after fill
  7. For multiple TP orders (`tp_orders` array), place TP1 and TP2 separately (`execution_service.py:255-291`)
- **UPDATE_SLTP** (`execution_service.py:509-605`):
  - Cancels existing SL order
  - Places new SL at break-even (entry ± fees)
  - Idempotent via `clientOrderId` check
- **Execution Blocking** (checked in order, `execution_service.py:97-101`):
  1. `LIVE_READONLY=1` → returns `_live_readonly_response()` (no orders placed)
  2. `SAFE_RUN=1` → returns `_safe_run_response()` (no orders placed)
  3. `DRY_RUN_ONLY=1` or `PAPER_TRADING=1` → returns dry-run response (no orders placed)
- **LIVE_READONLY:** execution is blocked and logged (trade_plan is still created)

## State Management (Restart Safety)

- **Module:** `app.state.state_manager`
- **Persisted State:**
  - `last_closed_candle_ts`: Prevents duplicate candle processing (`run/state/candle_gate.json`, `state_manager.py:34-38`)
  - `daily_state`: UTC date, starting equity, realized PnL, consecutive losses, extreme_snapback_ts (reset at UTC midnight, `run/state/daily_YYYY-MM-DD.json`, `state_manager.py:54-83`)
  - `trade_ids`: Last 1000 trade identifiers (clientOrderId tracking, `run/state/trade_ids.json`, `state_manager.py:86-106`)
  - `decision_state`: Pending entries, cooldowns, event timestamps (`run/state/decision_{SYMBOL}.json`, `state_manager.py:177-198`)
  - `position_state`: Position snapshot (`run/state/position_{SYMBOL}.json`, `state_manager.py:135-143`)
- **Reconciliation:** On startup (if `TRADE_ENABLED=1` and not test), reconcile exchange positions vs local state (`run.py:101-106`)
- **Reconciliation Logic** (`reconcile_positions` in `state_manager.py:260-311`):
  - Checks local state consistency if provided
  - Checks for positions without SL/TP (if `require_tp=True`, default)
  - Checks for multiple positions (>1 triggers error)
  - Returns `(ok: bool, errors: List[str])`
- **Restart Safety:** Never re-process candle with timestamp ≤ last_closed_candle_ts (`run.py:383-394`)
- **State Directory:** Configurable via `STATE_DIR` (default: `run/state`)

## Active services and responsibilities

- `app.bootstrap.MarketDataAdapter`: loads `app.services.market_data` and exposes `get_klines()` / `get_latest_price()`.
- `app.data.market_data_validator`: Validates market data before payload building (fail-closed)
- `app.data.payload_builder`: Builds validated payload.json from market data, account state, and features
- `app.strategy.decision_engine`: Produces validated decision.json (intent only, regime routing, stability gates)
- `app.risk.risk_manager`: Produces validated trade_plan.json (final authority, kill-switch checks)
- `app.risk.position_sizing`: Risk-based position sizing with margin checks
- `app.state.state_manager`: Persists state for restart safety + reconciliation
- `app.services.execution_service`: Idempotent execution of trade_plan.json (blocked in LIVE_READONLY/SAFE_RUN)
- `app.core.trade_ledger`: Append-only event logging (if `LEDGER_ENABLED=1`)

## Core modules and responsibilities

- `core.config.*`: configuration parsing, environment normalization, and defaults.
- `core.env_loader`: `.env` discovery and parsing.
- `core.exchange_private`: account snapshot (prefers `/fapi/v3/account`, fallback `/fapi/v2/account`) + balances.
- `core.execution.binance_futures`: REST helpers with TimeSync for all signed endpoints.
- `core.risk_guard`: risk guard and kill switch, plus health logging.
- `core.telemetry.health`: JSONL health logging.
- `core.runtime_mode`: Centralized runtime mode detection (TEST, OFFLINE, PAPER, LIVE, REPLAY).

## JSON Schema Validation

- **Module:** `app.core.validation`
- **Schemas:** `app/core/schemas/*.schema.json`
- **Functions:**
  - `validate_payload(payload) → (ok, errors)`
  - `validate_decision(decision) → (ok, errors)`
  - `validate_trade_plan(trade_plan) → (ok, errors)`
- **Fail-closed:** Validation failures return errors without crashing runtime (`validation.py:59-77`)
- **Dependency:** Requires `jsonschema` package (fail-closed if missing: `validation.py:59-61`)

## Bootstrap / wiring

`compose_trader_app()` (`bootstrap.py:235-264`):

- Coerces config and mirrors selected values into `os.environ`.
- Initializes logging (creates `logs/` directory if not in pytest).
- Instantiates `TraderApp` with `cfg`, `symbol`, and logger.
- Wires `MarketDataAdapter` (critical, raises `RuntimeError` if fails).

## Side effects

- Import-time side effects: none.
- Startup logging:
  - `app.bootstrap` and `app.run` may create `logs/` and write log files (skipped in tests via `PYTEST_CURRENT_TEST`).
  - `app.state.state_manager` creates `run/state/` directory for persistence.
- Runtime:
  - `TraderApp.start()` runs an infinite loop unless `APP_RUN_ONESHOT=1` or test/CI is detected.
  - `core.risk_guard.kill()` / `log_event()` create `run/` and `logs/health/<date>/` on demand.
  - State persistence: `run/state/*.json` files for candle gating, daily state, trade IDs.
- Shutdown:
  - `TraderApp` exits on `KeyboardInterrupt`.

## Critical components (must exist)

- `core.config.loader.get_config` - configuration loading and validation
- `app.run.TraderApp` - main runtime loop and orchestration
- `app.bootstrap.compose_trader_app` - service composition and wiring
- `app.bootstrap.MarketDataAdapter` (backed by `app.services.market_data`) - market data access
- `app.data.market_data_validator.validate_market_data` - market data validation (fail-closed)
- `app.data.payload_builder.build_payload` - payload construction
- `app.strategy.decision_engine.make_decision` - decision making (intent only)
- `app.risk.risk_manager.create_trade_plan` - trade plan creation (final authority)
- `app.services.execution_service.ExecutionService` - order execution (idempotent)
- `app.state.state_manager` - state persistence (for restart safety)

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
