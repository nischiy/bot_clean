# Decision Flow (AI-Oriented) - Production Architecture

## New Production Flow (JSON Contracts)

### Stage 0: Market Data Validation (Fail-Closed)
- **Input:** LTF/HTF DataFrames (default 5m/1h)
- **Source:** `app.data.market_data_validator.validate_market_data()`
- **Output:** `(ok, errors)` reject list
- **Deterministic:** Yes (given same data)
- **Side effects:** None
- **Fail-closed:** invalid/NaN/gapped/stale → HOLD

### Stage 1: Payload Building
- **Input:** Market data (LTF/HTF DataFrames), account snapshot, position snapshot, price/filters
- **Source:** `app.data.payload_builder.build_payload()`
- **Output:** `payload.json` (validated against `app/core/schemas/payload.schema.json`)
- **Deterministic:** Yes (given same inputs, produces same payload)
- **Side effects:** None (pure function)
- **Fail-closed:** Missing/NaN/stale data → payload fails → HOLD

**Payload includes:**
- Market identity: exchange, symbol, timeframe, timestamp_closed
- Price snapshot: last, bid, ask, mark
- Fees: maker, taker
- Account state: equity, available, funds_base, funds_source, margin_type, leverage
- Position state: side, qty, entry, unrealized_pnl, liq_price
- LTF features: close, close_prev, ema50, ema120, atr14, rsi14, rsi14_prev, volume_ratio
- HTF context: ema200, close, trend (up|down|range), timeframe
- Risk policy: risk_per_trade, max_daily_drawdown, max_consecutive_losses, min_rr
- Market meta: funding_rate, funding_next_ts
- Exchange limits: tick_size, step_size, min_qty

### Stage 2: Decision Making (INTENT ONLY)
- **Input:** `payload.json`
- **Source:** `app.strategy.decision_engine.make_decision()`
- **Output:** `decision.json` (validated against `app/core/schemas/decision.schema.json`)
- **Deterministic:** Yes (given same payload, produces same decision)
- **Side effects:** None (pure function)
- **Authority:** INTENT ONLY - no execution authority

**Regime Detection (5m):**
- Each closed 5m candle is classified into exactly one regime:
  - COMPRESSION, BREAKOUT_EXPANSION, TREND_CONTINUATION, PULLBACK, RANGE
- Top-down classification (first match wins):
  - BREAKOUT_EXPANSION: (close > donchian_high_20 or close < donchian_low_20) and volume_ratio >= 1.2
  - COMPRESSION: donchian_width_atr <= 2.0 and volume_ratio <= 1.0
  - TREND_CONTINUATION: trend in {up, down} and impulse_ok and dist50 <= 1.0 and breakout
  - PULLBACK: trend in {up, down} and dist50 > 1.0
  - RANGE: otherwise

**Strategy Routing (deterministic, non-overlapping):**
- **Time-exit precedence:** if `time_exit_signal=True`, emit `intent=CLOSE` and skip all entry logic.
- **Routing:** `regime_used_for_routing` maps to a single strategy (BREAKOUT_EXPANSION, CONTINUATION, PULLBACK_REENTRY, RANGE_MEANREV).
- **Compression:** COMPRESSION always blocks entries → HOLD.
 - **Override (verification):** `ROUTING_REGIME_OVERRIDE` can force routing only in non-production/test/offline/replay modes.

**HTF Trend Stability Gate (1h):**
- Uses **closed HTF candles only**.
- EMA200 slope over N candles (ATR-normalized):
  - `ema200_slope_norm = abs(ema200_htf - ema200_prev_n) / (atr14_htf * HTF_TREND_SLOPE_N)`
- Consecutive closes on trend side of EMA200 (persistence counts are deterministic, bounded by history)
- Optional structure persistence: consecutive higher/lower closes
- LONG requires trend=up, slope_norm >= min, consec_above >= min, structure >= min (if enabled)
- SHORT requires trend=down, slope_norm >= min, consec_below >= min, structure >= min (if enabled)

**Continuation Rules (unchanged, gated by regime):**
- **LONG:**
  - HTF trend up (close_htf > ema200_htf)
  - RSI14 <= 40
  - pullback_atr = (ema50_ltf - close_ltf)/atr14 <= 0.8
  - reclaim EMA50: close_ltf > ema50_ltf AND (prev close <= ema50_ltf OR prev RSI <= 40)
  - spread_pct <= SPREAD_MAX_PCT
- **SHORT:**
  - HTF trend down (close_htf < ema200_htf)
  - RSI14 >= 60
  - pullback_atr = (close_ltf - ema50_ltf)/atr14 <= 0.8
  - reclaim EMA50: close_ltf < ema50_ltf AND (prev close >= ema50_ltf OR prev RSI >= 60)
  - spread_pct <= SPREAD_MAX_PCT

**Breakout Expansion (2-phase):**
- Impulse: Donchian breakout + volume_ratio >= 1.2
- Acceptance:
  - `BREAKOUT_ACCEPT_BARS > 1`: requires consecutive closes beyond level
  - `BREAKOUT_ACCEPT_BARS == 1`: requires wick acceptance (no reject wick vs level, ATR-based)
- Optional retest:
  - LONG: `close_prev > donchian_high_20` AND `close_ltf > donchian_high_20` AND `low_ltf <= donchian_high_20 + BREAKOUT_RETEST_ATR * atr14`
  - SHORT: `close_prev < donchian_low_20` AND `close_ltf < donchian_low_20` AND `high_ltf >= donchian_low_20 - BREAKOUT_RETEST_ATR * atr14`
- Acceptance failure: breakout strategy is ineligible; reject reason includes `B:accept`, falls back to HOLD if no higher-priority strategy is eligible.

**Pullback Reentry:**
- Requires pullback depth within bounds and min pullback bars against trend
- Reclaim EMA50 + confirmation candle + fake-reclaim filter (1 full candle or volume confirm)

**Risk Management:**
- SL = entry ± (regime SL ATR)
- TP = entry ± RR * abs(entry - SL)
- Adaptive RR:
  - Regime RR target is applied per strategy (e.g., BREAKOUT_RR_TARGET, CONTINUATION_RR_TARGET).
  - `RR_final = max(regime_rr_target, risk_policy.min_rr, settings.MIN_RR)`.
  - If `RR_final` would be below the base minimum, decision is rejected with `{L|S}:rr` and intent=HOLD.

**Fail-closed:** Invalid decision → HOLD with reject_reasons

### Stage 3: Risk Manager (FINAL AUTHORITY)
- **Input:** `payload.json`, `decision.json`, daily state, exchange positions
- **Source:** `app.risk.risk_manager.create_trade_plan()`
- **Output:** `trade_plan.json` (validated against `app/core/schemas/trade_plan.schema.json`)
- **Deterministic:** Yes (given same inputs, produces same trade plan)
- **Side effects:** None (pure function)
- **Authority:** FINAL AUTHORITY - only produces trade_plan if ALL checks pass

**Kill-Switch Checks:**
1. Daily drawdown exceeded
2. Consecutive losses exceeded
3. More than one open position
4. Spread above threshold (>0.5%)
5. Abnormal ATR spike (>10% of price)
6. Stale data (>2h old)

**Exit Enforcement (Critical):**
- Implemented in `app.state.state_manager.reconcile_positions()` before payload/decision.
- If a position exists without SL/TP → kill-switch + stop trading.

**Position Sizing:**
- Module: `app.risk.position_sizing.calculate_position_size()`
- Formula: `risk_usd = funds_base * risk_per_trade`, `qty = risk_usd / abs(entry - sl)`
- Respects step_size, min_qty, and re-checks margin after rounding
- Margin check: `required_margin = (qty * entry) / leverage`
- Equity is mandatory at payload stage; missing/invalid equity fails closed before sizing

**Cooldown:** Trade plan blocked if any trade_plan was created within the last 15 minutes.

**Time-exit (intent):**
- Evaluated **before** any entry strategy.
- Uses only closed LTF candles (`close_max_n`/`close_min_n` over `TIME_EXIT_BARS`).
- After N LTF candles, if price moved < progress_atr * ATR toward TP, emit CLOSE intent.

**Fail-closed:** Any check fails → HOLD with rejections

### Stage 4: Execution (Idempotent)
- **Input:** `trade_plan.json` ONLY
- **Source:** `app.services.execution_service.ExecutionService.execute_trade_plan()`
- **Output:** Execution result with entry/SL/TP order status
- **Deterministic:** No (depends on exchange state, network)
- **Side effects:** Network calls (blocked in SAFE_RUN/DRY_RUN_ONLY/LIVE_READONLY), state persistence, ledger writes
- **Idempotency:** All orders use clientOrderId, duplicate check prevents re-execution

**Process:**
1. Validate trade_plan.json
2. Check if client_order_id already exists (idempotency)
3. Set leverage if needed
4. Place entry order
5. Wait for fill confirmation
6. Place SL and TP orders immediately

**Fail-closed:** Invalid trade_plan → HARD STOP (do not execute)

### Ledger Events (Append-Only)
- **Source:** `app.core.trade_ledger.append_event()`
- **Events:** decision_created, trade_plan_created, execution_attempted, execution_submitted, sltp_submitted, position_closed, kill_switch_triggered
- **Deterministic:** Hashes use canonical JSON serialization

## Preflight Snapshot (Parallel to decision flow)
- Input: `symbol`, `wallet_usdt`
- Source: `app.run._read_preflight()` (account snapshot via `core.exchange_private`, price via `HttpMarketData`, filters via `binance_futures.exchange_info`)
- Output: snapshot dict with account, price, filters, rejects
- Deterministic: no (exchange data)
- Side effects: network calls when not in offline/test mode

## State Management (New)

### Candle Gating
- **Module:** `app.state.state_manager`
- **Function:** `save_last_closed_candle_ts()`, `load_last_closed_candle_ts()`
- **Purpose:** Prevent duplicate candle processing on restart
- **Persisted:** `run/state/candle_gate.json`

### Daily State
- **Module:** `app.state.state_manager`
- **Function:** `save_daily_state()`, `load_daily_state()`
- **Purpose:** Track daily risk metrics (starting equity, realized PnL, consecutive losses)
- **Persisted:** `run/state/daily_YYYY-MM-DD.json`

### Trade Identifiers
- **Module:** `app.state.state_manager`
- **Function:** `save_trade_identifier()`, `has_trade_identifier()`
- **Purpose:** Prevent duplicate order execution (idempotency)
- **Persisted:** `run/state/trade_ids.json`

### Reconciliation
- **Module:** `app.state.state_manager`
- **Function:** `reconcile_positions()`
- **Purpose:** On startup, reconcile exchange positions vs local state
- **Fail-closed:** If position exists without SL → STOP TRADING