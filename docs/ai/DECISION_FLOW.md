# Decision Flow (AI-Oriented) - Production Architecture

## New Production Flow (JSON Contracts)

## Predictive-First Flow

Decision generation now uses a three-stage deterministic path:

1. `Predictive inference`
   - `app.strategy.predictive_engine` classifies early directional opportunity, failure transitions, directional events vs chaotic events, and market-state transitions.
   - outputs `predictive_bias`, `predictive_state`, `confidence_tier`, trigger reasons, invalidations, and state-machine transition fields.
2. `Legacy validation`
   - existing strategies still run every candle.
   - they now contribute supporting/opposing evidence, confirmation quality, and analytics labels instead of acting as the only trade source.
3. `Execution decision`
   - maps predictive bias plus validation quality into `OPEN_*_EARLY`, `OPEN_*_CONFIRMED`, or hold variants.
   - keeps legacy `intent` for runtime compatibility.

The predictive layer is explainable by construction: each branch is rule-based, named, logged, and restart-safe.

### Stage 0: Market Data Validation (Fail-Closed)
- **Input:** LTF/HTF DataFrames (default 5m/1h)
- **Source:** `app.data.market_data_validator.validate_market_data()`
- **Output:** `(ok: bool, errors: List[str])` reject list
- **Deterministic:** Yes (given same data)
- **Side effects:** None
- **Fail-closed checks** (`market_data_validator.py:23-68`):
  - Missing required columns → `missing_col_ltf:{col}`
  - Empty DataFrame → `empty_df_ltf`
  - Insufficient bars: LTF < `MD_MIN_BARS_1H` (default 260), HTF < `MD_MIN_BARS_4H` (default 220)
  - Invalid/non-monotonic timestamps → `invalid_close_time`, `non_monotonic_close_time`
  - Missing timezone → `close_time_not_tz_aware`
  - Stale data: `now_ts - last_ts > MD_MAX_AGE_SECONDS` (default 7200) → `stale_market_data` (skipped in replay)
  - Time gaps: `gap > MD_MAX_GAP_SECONDS` (default 7200) → `time_gap_exceeded`
  - NaNs in critical columns (`close`, `high`, `low`, `volume`) within lookback window → `nan_in_{col}`
- **Fail-closed:** invalid/NaN/gapped/stale → HOLD

### Stage 1: Payload Building
- **Input:** Market data (LTF/HTF DataFrames), account snapshot, position snapshot, price/filters
- **Source:** `app.data.payload_builder.build_payload()`
- **Output:** `(payload: Optional[Dict], errors: List[str])` - validated against `app/core/schemas/payload.schema.json`
- **Deterministic:** Yes (given same inputs, produces same payload)
- **Side effects:** None (pure function)
- **Fail-closed conditions** (`payload_builder.py:449-454`):
  - `equity is None or equity <= 0` → `missing_or_invalid_equity`
  - `funds_base is None` → `funds_source_missing`
  - `funds_base <= 0` → `funds_nonpositive`
- **Funds base calculation** (`payload_builder.py:438-445`):
  - If both `available` and `total_margin` exist: `funds_base = min(available, total_margin)`, `funds_source = "min(available_balance,total_margin_balance)"`
  - Else if `available` exists: `funds_base = available`, `funds_source = "available_balance"`
  - Else: `funds_base = None` → fail-closed

**Payload includes:**
- Market identity: exchange, symbol, timeframe, timestamp_closed
- Price snapshot: last, bid, ask, mark (all required)
- Fees: maker, taker (defaults from `FEE_MAKER_BPS`, `FEE_TAKER_BPS`)
- Account state: equity, available, funds_base, funds_source, margin_type, leverage
- Position state: side, qty, entry, unrealized_pnl, liq_price
- LTF features: close, close_prev, ema50, ema120, donchian_high_240, donchian_low_240, atr14, atr14_sma20, donchian_high_20, donchian_low_20, consec_close_above_donchian_20, consec_close_below_donchian_20, bb_upper, bb_lower, bb_mid, candle_body_ratio, volume_ratio, rsi14, rsi14_prev, consec_above_ema50, consec_below_ema50, consec_above_ema50_prev, consec_below_ema50_prev, close_max_n, close_min_n, time_exit_bars
- HTF context: ema200, ema200_prev_n, ema200_slope_norm, consec_above_ema200, consec_below_ema200, consec_higher_close, consec_lower_close, close, trend (up|down|range), timeframe, atr14
- Risk policy: risk_per_trade, max_daily_drawdown, max_consecutive_losses, min_rr
- Market meta: funding_rate, funding_next_ts
- Exchange limits: tick_size, step_size, min_qty

### Stage 2: Decision Making (INTENT ONLY)
- **Input:** `payload.json`, daily state, decision state
- **Source:** `app.strategy.decision_engine.make_decision()`
- **Output:** `decision.json` (validated against `app/core/schemas/decision.schema.json`)
- **Deterministic:** Yes (given same payload and state)
- **Side effects:** Updates decision state (pending entries, cooldowns, event timestamps) via `state_update` field
- **Authority:** INTENT ONLY - no execution authority

**Execution decision contract additions:**
- top-level:
  - `execution_decision`
  - `entry_mode`
- `signal.predictive`
  - predictive bias/state/confidence
  - transition fields
  - trigger and invalidation reasons
- `signal.validation`
  - `confirmation_quality`
  - supporting/opposing strategies
  - validator reject map
- `signal.execution_profile`
  - size multiplier
  - event hold flag
- `signal.analytics`
  - pending queue size
  - finalized labels
  - latest missed-opportunity telemetry

**Early-entry rules:**
- allowed only when predictive bias is directional, confidence is at least `MEDIUM`, the thesis is not late/chaotic, and no structural validator reject or risk veto is active.
- uses smaller size (`EARLY_ENTRY_SIZE_MULTIPLIER`) and tighter invalidation handling.

**Confirmed-entry rules:**
- preserves the legacy confirmed strategy path and standard sizing.
- the predictive layer can strengthen confidence, but a neutral predictive read does not erase an otherwise valid legacy confirmed trade.

**Regime Detection (5m, deterministic, exclusive, top-down, from `_detect_regime` in `decision_engine.py:310-383`):**
- Each closed 5m candle is classified into exactly one regime:
  1. **EVENT** → if `event_detected=True` (true_range >= `EVENT_TR_ATR` * atr14, default 3.0) → **BLOCKS** entries (routes to "NONE" strategy)
  2. **SQUEEZE_BREAK** → if `squeeze_break_long or squeeze_break_short` (BB width expansion + Donchian break)
  3. **BREAKOUT_EXPANSION** → if `(brk_up or brk_dn) and volume_ratio >= REGIME_BREAKOUT_VOL_MIN` (default 1.2)
  4. **COMPRESSION** → if `dc_width_atr <= REGIME_COMPRESSION_WIDTH_ATR_MAX` (default 2.0) `and volume_ratio <= REGIME_COMPRESSION_VOL_MAX` (default 1.0) → **BLOCKS** entries
  5. **TREND_ACCEL** → if `trend_bias and vol_expansion and dist50 <= REGIME_TREND_DIST50_MAX` (default 1.0)
  6. **TREND_CONTINUATION** → if `trend_bias and dist50 <= REGIME_TREND_DIST50_MAX and impulse_ok and (brk_up or brk_dn)`
  7. **PULLBACK** → if `trend_bias and dist50 > REGIME_TREND_DIST50_MAX`
  8. **RANGE** → default fallback
- Top-down classification (first match wins, exclusive)

**Strategy Routing (deterministic, non-overlapping, from `select_strategy_by_regime` in `decision_engine.py:386-401`):**
- **Time-exit precedence:** if `time_exit_signal=True`, emit `intent=CLOSE` and skip all entry logic.
- **Routing:** `regime_used_for_routing` maps to a single strategy:
  - BREAKOUT_EXPANSION → "BREAKOUT_EXPANSION" (if `breakout_expansion_long_ok` or `breakout_expansion_short_ok`)
  - SQUEEZE_BREAK → "SQUEEZE_BREAK" (if `squeeze_break_long_ok` or `squeeze_break_short_ok`)
  - TREND_ACCEL → "TREND_ACCEL" (if `trend_accel_long_ok` or `trend_accel_short_ok`)
  - TREND_CONTINUATION → "CONTINUATION" (if `cont_long_ok` or `cont_short_ok`)
  - PULLBACK → "PULLBACK_REENTRY" (if `pullback_reentry_long_ok` or `pullback_reentry_short_ok`)
  - RANGE → "RANGE_MEANREV" (if `range_meanrev_long_ok` or `range_meanrev_short_ok`)
  - EVENT/COMPRESSION → "NONE" (blocks entries)
- **Override (verification):** `ROUTING_REGIME_OVERRIDE` can force routing only in non-production/test/offline/replay modes (`decision_engine.py:1455`).

**HTF Trend Stability Gate (1h):**
- Uses **closed HTF candles only**.
- EMA200 slope over N candles (ATR-normalized) + consecutive closes on EMA200 side.
- Optional structure persistence (consecutive higher/lower closes).

**LTF Stability Scoring (trend-following only, from `_compute_stability` in `decision_engine.py:65-100`):**
- Computed from EMA50 trend candles ratio, wick ratio frequency, and dist50 overextension.
- **Hardcoded formula** (`decision_engine.py:93`): `score = 0.55 * R + 0.25 * (1 - W) + 0.20 * (1 - X)` where:
  - `R = trend_candles / STABILITY_N` (default 20)
  - `W = wick_ratio_count / STABILITY_N`
  - `X = clamp(dist50 / XMAX, 0, 1)` where `XMAX` default is 1.8
- Hard gate (≥STABILITY_HARD, default 0.70): allow
- Soft gate (≥STABILITY_SOFT, default 0.58): requires continuation confirmation + HTF anti-reversal filter
- Block (<STABILITY_SOFT): HOLD
- Applied to TREND_CONTINUATION, PULLBACK_REENTRY, and TREND_ACCEL.

**Continuation Confirmation (soft stability only, from `_continuation_confirmation` in `decision_engine.py:103-216`):**
- Any of: two-bar continuation, EMA50/BB mid retest-reject, or lower-high/higher-low break.

**Anti-Reversal Filter (HTF):**
- HTF EMA reclaim or RSI slope + LTF wick ratio blocks entries.

**Pending Entry Confirmation:**
- Signals may enter a pending state that must confirm within a small candle window or expires.

**Optional EV Gate (disabled by default):**
- Computes a simple EV score from stability + confirmation; blocks low/negative EV entries when `EV_GATE_ENABLED=1`.

**Breakout Expansion (2-phase):**
- Impulse: Donchian breakout + volume expansion.
- Acceptance: consecutive closes or wick acceptance; optional retest confirmation.

**Pullback Reentry:**
- Requires pullback depth within bounds, min bars against trend, reclaim EMA50, and confirmation candle.

**Risk Management:**
- SL = entry ± (regime SL ATR)
- TP = entry ± RR * abs(entry - SL)
- Adaptive RR:
  - Regime RR target is applied per strategy (e.g., BREAKOUT_RR_TARGET, CONTINUATION_RR_TARGET).
  - `RR_final = max(regime_rr_target, risk_policy.min_rr, settings.MIN_RR)`.
  - If `RR_final` would be below the base minimum, decision is rejected with `{L|S}:rr` and intent=HOLD.
- **Partial Position Closure (TP1/TP2):**
  - When `regime_exit_behavior` is `partial_fixed` (CONTINUATION, PULLBACK_REENTRY) or `trailing` (BREAKOUT_EXPANSION, TREND_ACCEL, SQUEEZE_BREAK) and `rr_target >= 2.0` (`decision_engine.py:1413`):
    - TP1: 60% of target RR (`tp1_rr = rr_target * 0.6`, hardcoded), quantity = `TP1_FRACTION` (default 40% of position)
    - TP2: 100% of target RR, quantity = `1 - TP1_FRACTION` (default 60% of position)
    - Creates `tp_targets` array in decision with both TP prices and quantity fractions
  - For `trailing` behavior: sets `move_sl_to_be_after_tp1=True` to trigger UPDATE_SLTP after TP1 fill

**Regime Exit Behavior Mapping** (`decision_engine.py:1511-1519`):
- BREAKOUT_EXPANSION → "trailing" (TP1 + trailing SL after TP1 fill)
- TREND_ACCEL → "trailing"
- SQUEEZE_BREAK → "trailing"
- CONTINUATION → "partial_fixed" (TP1 + TP2, fixed SL)
- PULLBACK_REENTRY → "partial_fixed"
- RANGE_MEANREV → "fixed" (single TP)

**Fail-closed:** Invalid decision → HOLD with reject_reasons

### Stage 3: Risk Manager (FINAL AUTHORITY)
- **Input:** `payload.json`, `decision.json`, daily state, exchange positions
- **Source:** `app.risk.risk_manager.create_trade_plan()`
- **Output:** `(trade_plan: Optional[Dict], rejections: List[str])` - validated against `app/core/schemas/trade_plan.schema.json`
- **Deterministic:** Yes (given same inputs, produces same trade plan)
- **Side effects:** None (pure function)
- **Authority:** FINAL AUTHORITY - only produces trade_plan if ALL checks pass

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

**Partial Exit Handling:**
- If decision includes `tp_targets` array with 2+ targets (`risk_manager.py:271`):
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
- **Input:** `trade_plan.json` ONLY
- **Source:** `app.services.execution_service.ExecutionService.execute_trade_plan()`
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

## Hardcoded Thresholds (Not Configurable)

See `docs/ai/KNOWN_GAPS_AND_LIMITS.md` for complete list of hardcoded values, including:
- Stability scoring weights (0.55, 0.25, 0.20)
- Continuation strategy filters (slope, RSI, body, volume, ATR ratio)
- Trend strength minimum (0.6)
- Volatility expansion threshold (1.3)
- Pullback threshold (0.8)
- TP1 RR fraction (0.6)
