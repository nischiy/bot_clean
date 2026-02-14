# Trading Bot - Production-Grade Architecture

A production-ready trading bot with strict JSON contracts, fail-closed validation, and restart safety.

## Architecture Overview

The system enforces **three JSON contracts** between modules:

1. **payload.json** → Market data, account state, features, risk policy
2. **decision.json** → Trading intent (LONG/SHORT/HOLD/CLOSE/UPDATE_SLTP) - **INTENT ONLY**
3. **trade_plan.json** → Executable order instructions - **ONLY executable orders**

### Key Features

- ✅ **Fail-closed validation**: Invalid payload → HARD FAIL → HOLD
- ✅ **Intent-only decisions**: Strategy cannot place executable orders
- ✅ **Risk Manager is final authority**: Only produces trade_plan if ALL checks pass
- ✅ **Restart-safe**: State persistence prevents duplicate processing
- ✅ **Idempotent execution**: clientOrderId tracking prevents duplicate orders
- ✅ **Closed-candle gating**: Decisions made ONLY on closed 5m candles
- ✅ **UTC daily reset**: Daily state resets at UTC midnight
- ✅ **Hard SL/TP enforcement**: Missing protective orders triggers kill-switch
- ✅ **LIVE_READONLY paper mirror**: Real market + account/position snapshots with zero mutations
- ✅ **Explainable logs**: `decision_clean` includes funds + sizing + routing evidence
- ✅ **TimeSync recovery**: Automatic server-time sync with retry-on-`-1021`

## Quick Start

### Installation

```bash
# Create virtual environment (Windows)
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies (optional)
pip install -r requirements-dev.txt
```

JSON schema validation requires `jsonschema` (included in `requirements.txt`).

### Configuration

Create a `.env` file with your configuration:

```env
# Safe readonly (paper mirror) defaults
LIVE_READONLY=1
SAFE_RUN=1
DRY_RUN_ONLY=1
TRADE_ENABLED=0

SYMBOL=BTCUSDT
INTERVAL=5m
HTF_INTERVAL=1h
RISK_PER_TRADE_PCT=5.0
RISK_MAX_DD_PCT_DAY=3.0
RISK_MAX_CONSEC_LOSSES=3
MIN_RR=1.5
LEVERAGE=5
```

Key safety defaults:
- **Funds base:** sizing uses `funds_base = min(availableBalance, totalMarginBalance)` when both exist (from `payload_builder.py:441`)
- **Fail-closed:** missing/nonpositive funds → HOLD with explicit reject reason (`payload_builder.py:451-454`)
- **Time sync:** signed endpoints use server time offset with retry-on-`-1021` (`core/execution/binance_futures.py`)

### Running

```bash
# Run in paper trading mode (safe defaults)
python main.py

# Run tests (Windows)
py -m pytest tests/ -v

# Run critical safety tests only
py -m pytest -m critical -v
```

## Architecture Flow

```
Market Data → Market Data Validator → Payload Builder → Decision Engine → Risk Manager → Execution Service
                (fail-closed)          (payload.json)    (decision.json)  (trade_plan.json)   (readonly/blocked)
```

**Runtime Ordering (strict per tick, from `app/run.py:_run_once_contracts`):**
1. Position snapshot (`_get_position_snapshot`) → save to `position_{SYMBOL}.json`
2. Position reconciliation (`reconcile_positions`) → **HARD KILL** if missing SL/TP or multiple positions
3. Kill-switch flag check (`is_killed()`) → skip all trading if active
4. Preflight (`_read_preflight`) → account + filters + price snapshots → **HOLD** if rejects
5. Market data validation (`validate_market_data`) → **HOLD** if invalid
6. Payload building (`build_payload`) → **HOLD** if fails validation
7. Decision making (`make_decision`) → intent only, no execution authority
8. Trade plan creation (`create_trade_plan`) → **FINAL AUTHORITY**, only if ALL checks pass
9. Execution (`execute_trade_plan`) → idempotent, blocked in LIVE_READONLY/SAFE_RUN/DRY_RUN_ONLY

### 0. Market Data Validator
- **Module:** `app/data/market_data_validator.py`
- **Function:** `validate_market_data(df_ltf, df_htf)`
- **Fail-closed checks:**
  - Required columns: `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume` (`market_data_validator.py:23-26`)
  - Empty DataFrame (`market_data_validator.py:31-32`)
  - Minimum bars: LTF < `MD_MIN_BARS_1H` (default 260), HTF < `MD_MIN_BARS_4H` (default 220) (`market_data_validator.py:39-43`)
  - Non-monotonic timestamps (`market_data_validator.py:48-49`)
  - Missing timezone (`market_data_validator.py:51-52`)
  - Stale data: `now_ts - last_ts > MD_MAX_AGE_SECONDS` (default 7200) (`market_data_validator.py:55-57`)
  - Time gaps: `gap > MD_MAX_GAP_SECONDS` (default 7200) (`market_data_validator.py:59-61`)
  - NaNs in critical columns: `close`, `high`, `low`, `volume` within lookback window (`market_data_validator.py:64-68`)
- **Returns:** `(ok: bool, errors: List[str])`
- **Invalid data → HOLD** (runtime saves candle timestamp and skips processing)

### 1. Payload Builder
- **Module:** `app/data/payload_builder.py`
- **Function:** `build_payload(symbol, df_ltf, df_htf, account_snapshot, position_snapshot, price_snapshot, filters_snapshot, timestamp_closed, timeframe, htf_timeframe)`
- **Fail-closed conditions** (`payload_builder.py:449-454`):
  - `equity is None or equity <= 0` → `missing_or_invalid_equity`
  - `funds_base is None` → `funds_source_missing`
  - `funds_base <= 0` → `funds_nonpositive`
- **Funds base calculation** (`payload_builder.py:438-445`):
  - If both `available` and `total_margin` exist: `funds_base = min(available, total_margin)`, `funds_source = "min(available_balance,total_margin_balance)"`
  - Else if `available` exists: `funds_base = available`, `funds_source = "available_balance"`
  - Else: `funds_base = None` → fail-closed
- **Required schema fields:** See `app/core/schemas/payload.schema.json` - all top-level objects are required
- **Returns:** `(payload: Optional[Dict], errors: List[str])`
- **Payload fails → HOLD** (runtime saves candle timestamp and skips processing)

### 2. Decision Engine
- **Module:** `app/strategy/decision_engine.py`
- **Function:** `make_decision(payload, daily_state, decision_state)`
- **Authority:** INTENT ONLY - no execution authority
- **Regime Detection Order** (top-down, exclusive, from `_detect_regime` in `decision_engine.py:353-383`):
  1. **EVENT** → if `event_detected=True` → **BLOCKS** entries (routes to "NONE" strategy)
  2. **SQUEEZE_BREAK** → if `squeeze_break_long or squeeze_break_short`
  3. **BREAKOUT_EXPANSION** → if `(brk_up or brk_dn) and volume_ratio >= breakout_vol_min`
  4. **COMPRESSION** → if `dc_width_atr <= compression_width_atr_max and volume_ratio <= compression_vol_max` → **BLOCKS** entries
  5. **TREND_ACCEL** → if `trend_bias and vol_expansion and dist50 <= trend_dist50_max`
  6. **TREND_CONTINUATION** → if `trend_bias and dist50 <= trend_dist50_max and impulse_ok and (brk_up or brk_dn)`
  7. **PULLBACK** → if `trend_bias and dist50 > trend_dist50_max`
  8. **RANGE** → default fallback
- **Strategy Routing** (`select_strategy_by_regime` in `decision_engine.py:386-401`):
  - BREAKOUT_EXPANSION → "BREAKOUT_EXPANSION" (if `breakout_expansion_long_ok` or `breakout_expansion_short_ok`)
  - SQUEEZE_BREAK → "SQUEEZE_BREAK" (if `squeeze_break_long_ok` or `squeeze_break_short_ok`)
  - TREND_ACCEL → "TREND_ACCEL" (if `trend_accel_long_ok` or `trend_accel_short_ok`)
  - TREND_CONTINUATION → "CONTINUATION" (if `cont_long_ok` or `cont_short_ok`)
  - PULLBACK → "PULLBACK_REENTRY" (if `pullback_reentry_long_ok` or `pullback_reentry_short_ok`)
  - RANGE → "RANGE_MEANREV" (if `range_meanrev_long_ok` or `range_meanrev_short_ok`)
  - EVENT/COMPRESSION → "NONE" (blocks entries)
- **Time-Exit Precedence:** If `time_exit_signal=True`, emit `intent=CLOSE` and skip all entry logic
- **Stability Scoring:** Hard gate (≥STABILITY_HARD), soft gate (≥STABILITY_SOFT requires confirmation), block (<STABILITY_SOFT)
- **Cooldown:** Blocks new trade_plan within `TRADE_COOLDOWN_MINUTES` (default 15) of last trade_plan
- **Returns:** `decision.json` (validated against `app/core/schemas/decision.schema.json`)
- **Invalid decision → HOLD** with `reject_reasons`

### 3. Risk Manager
- **Module:** `app/risk/risk_manager.py`
- **Function:** `create_trade_plan(payload, decision, daily_state, exchange_positions)`
- **Authority:** FINAL AUTHORITY - only produces trade_plan if ALL checks pass
- **Kill-switch Checks** (`check_kill_switches` in `risk_manager.py:20-100`):
  1. Kill-switch flag (`is_killed()`) → `kill_switch_engaged`
  2. Daily drawdown: `abs(min(0, daily_pnl)) / starting_equity * 100 >= max_daily_drawdown` → `daily_drawdown_exceeded`
  3. Consecutive losses: `consec_losses >= max_consecutive_losses` → `consecutive_losses_exceeded`
  4. Multiple positions: `len(open_positions) > 1` → `multiple_positions`
  5. Spread too wide: `abs(ask - bid) / last * 100 > SPREAD_MAX_PCT` (default 0.5) → `spread_too_wide`
  6. Abnormal ATR spike: `atr14 > last * ATR_SPIKE_MAX_PCT` (default 0.1) → `abnormal_atr_spike`
  7. Stale data: `timestamp_closed < now_ts - DATA_MAX_AGE_SECONDS` (default 7200) → `stale_data`
- **Cooldown Check** (`risk_manager.py:122-132`): Blocks if `intent in ("LONG", "SHORT")` and `(ts - last_ts) < TRADE_COOLDOWN_MINUTES * 60`
- **Position Sizing** (`calculate_position_size` in `app/risk/position_sizing.py:10-95`):
  - Formula: `risk_usd = funds_base * risk_per_trade` (`position_sizing.py:61`)
  - Formula: `qty_raw = risk_usd / abs(entry - sl)` (`position_sizing.py:70`)
  - Rounding: `qty = floor(qty_raw / step_size) * step_size` (`position_sizing.py:73`)
  - Validation: `qty >= min_qty` (`position_sizing.py:76-80`)
  - Margin check: `required_margin = (qty * entry) / leverage` (`position_sizing.py:90`)
  - Fail-closed: `margin_needed > funds_base` → `insufficient_margin` (`position_sizing.py:91-93`)
- **Partial Position Closure (TP1/TP2):**
  - When `tp_targets` array has 2+ targets and `regime_exit_behavior in ("partial_fixed", "trailing")` and `rr_target >= 2.0` (`risk_manager.py:271`)
  - Split: `qty_tp1 = qty * TP1_FRACTION` (default 0.4), `qty_tp2 = qty * (1 - TP1_FRACTION)` (default 0.6) (`risk_manager.py:273-275`)
  - Both rounded to `step_size`, validated against `min_qty` (`risk_manager.py:278-286`)
  - Creates `tp_orders` array with separate TP1 and TP2 orders (`risk_manager.py:304-319`)
- **Returns:** `(trade_plan: Optional[Dict], rejections: List[str])`
- **Any check fails → HOLD** with rejections

### 4. Execution Service
- **Module:** `app/services/execution_service.py`
- **Function:** `execute_trade_plan(trade_plan)`
- **Consumes:** ONLY `trade_plan.json`
- **Idempotency:** All orders use `clientOrderId`, duplicate check via `has_trade_identifier()` prevents re-execution (`execution_service.py:119-140`)
- **Execution Blocking** (checked in order, `execution_service.py:97-101`):
  1. `LIVE_READONLY=1` → returns `_live_readonly_response()` (no orders placed)
  2. `SAFE_RUN=1` → returns `_safe_run_response()` (no orders placed)
  3. `DRY_RUN_ONLY=1` or `PAPER_TRADING=1` → returns dry-run response (no orders placed)
- **Process** (`execution_service.py:159-320`):
  1. Validate trade_plan schema
  2. Check idempotency (`has_trade_identifier`)
  3. Persist identifier (`save_trade_identifier`) before any live submission
  4. Set leverage if needed
  5. Place entry order → wait for fill confirmation
  6. Place SL and TP orders immediately after fill
  7. For multiple TP orders (`tp_orders` array), place TP1 and TP2 separately
- **UPDATE_SLTP Action** (`execution_service.py:509-605`):
  - Cancels existing SL order
  - Places new SL at break-even (entry ± fees)
  - Idempotent via `clientOrderId` check
- **Returns:** Execution result with entry/SL/TP order status

## State Management

State is persisted for restart safety (all in `run/state/` directory, configurable via `STATE_DIR`):

- `candle_gate.json`: Last processed closed candle timestamp (`state_manager.py:34-38`)
  - Prevents duplicate processing: `if latest_closed_ts <= last_processed_ts: skip` (`run.py:383-394`)
- `daily_YYYY-MM-DD.json`: Daily risk state (`state_manager.py:54-83`)
  - Fields: `date` (UTC), `starting_equity`, `realized_pnl`, `consecutive_losses`, `extreme_snapback_ts` (optional)
  - Resets at UTC midnight (`load_or_initialize_daily_state` checks date)
- `trade_ids.json`: Trade identifiers (idempotency, last 1000 clientOrderIds) (`state_manager.py:86-106`)
  - Format: `{client_order_id: {hash, timestamp}}`
  - Used to prevent duplicate execution
- `decision_{SYMBOL}.json`: Decision state (`state_manager.py:177-198`)
  - Fields: `symbol`, `pending_entry`, `event_cooldown: {remaining, last_ts}`
  - Updated by decision engine via `state_update` field
- `position_{SYMBOL}.json`: Position snapshot state (`state_manager.py:135-143`)
  - Saved every tick before reconciliation
  - Used for reconciliation on startup

## Testing

```bash
# Run all tests
py -m pytest tests/ -v

# Run safety-critical tests only
py -m pytest -m critical -v

# Run specific test suite
pytest tests/test_schema_validation.py -v
pytest tests/test_payload_builder.py -v
pytest tests/test_decision_engine.py -v
pytest tests/test_risk_manager.py -v
pytest tests/test_state_manager.py -v
```

## Runtime Logs

- **Runtime log:** `logs/runtime.log` (or `LOG_DIR/runtime.log`) - all structured events
- **Session logs:** `LOG_DIR/sessions/{YYYYMMDD_HHMMSS}_{pid}.log` - full session output
- **Clean session logs:** `LOG_DIR/sessions_clean/{YYYYMMDD_HHMMSS}_{pid}.log` - filtered (no tick skip noise, includes decision_clean)
- **Trade ledger:** `run/ledger/ledger_YYYY-MM-DD.jsonl` - append-only event log (if `LEDGER_ENABLED=1`)

**Structured Events:**
- `startup_banner`: ENV, runtime flags, symbol/interval, mutation status
- `tick_summary`: now, interval, latest_closed_ts, last_processed_ts, will_process, skip_reason
- `decision_candle`: price/bid/ask/spread, HTF trend, EMA50/ATR/RSI, pullback/reclaim, decision + reject_reasons
- `decision_clean`: regime routing + blockers + funds/sizing evidence
- `execution_attempted`, `execution_blocked`, `execution_skipped`

**Windows tail:**

```powershell
Get-Content -Path .\logs\runtime.log -Wait
```

## Log Analytics

```bash
# Analyze runtime logs and generate reports
python tools/log_stats/analyze_logs.py --runtime-log logs/runtime.log --out reports

# Output includes:
# - reports/daily/YYYY-MM-DD/decisions/
# - reports/daily/YYYY-MM-DD/stats/
# - reports/daily/YYYY-MM-DD/human_logs/
# - reports/daily/YYYY-MM-DD/raw_extract/
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System architecture and flow
- [Configuration](docs/CONFIG.md) - Environment variables and settings
- [Testing](docs/TESTING.md) - Test strategy and safety guards
- [Updates](docs/UPDATES.md) - Latest production-hardening updates
- [Decision Flow](docs/ai/DECISION_FLOW.md) - Detailed decision flow
- [State and Side Effects](docs/ai/STATE_AND_SIDE_EFFECTS.md) - State management
- [Project Map](docs/ai/PROJECT_MAP.md) - Module responsibilities
- [Runtime Invariants](docs/ai/RUNTIME_INVARIANTS.md) - Runtime guarantees
- [Known Gaps and Limits](docs/ai/KNOWN_GAPS_AND_LIMITS.md) - Current limitations

## Non-Negotiable Rules

1. **Modules communicate ONLY via validated JSON objects** (payload.json, decision.json, trade_plan.json)
2. **Exactly three contracts exist** - no other communication paths
3. **Fail-closed validation:**
   - Invalid payload → HARD FAIL → HOLD (candle timestamp saved, no processing)
   - Invalid decision → HOLD with reject_reasons
   - Invalid trade_plan → HARD STOP (do not execute)
4. **Decisions made ONLY on CLOSED 5m candles** (persisted timestamp gating, HTF=1h trend filter)
5. **Risk Manager is FINAL AUTHORITY** - Strategy cannot place executable orders
6. **Execution accepts ONLY trade_plan.json**
7. **Runtime ordering is strict** (reconcile → exits → kill → preflight → data → payload → decision → trade_plan → execution)
8. **System is restart-safe and idempotent** (state persistence, clientOrderId tracking)
9. **Market data validation** happens before payload building
10. **Closed-candle gating** prevents duplicate processing (never re-process candle with timestamp ≤ last_closed_candle_ts)

## License

[Your License Here]
