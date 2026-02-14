# Configuration

This project relies on environment variables (via `.env` or process env). `core.config.loader.get_config()` normalizes aliases and applies defaults from `core/config/settings.py`.

JSON schema validation requires `jsonschema` to be installed (runtime dependency).

Windows venv install:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Required vs non-required

- **Required (only when trading live in production)**:
  - `BINANCE_API_KEY` / `BINANCE_API_SECRET` (or `API_KEY` / `API_SECRET`, or `BINANCE_FAPI_KEY` / `BINANCE_FAPI_SECRET`)
  - Condition: `ENV=production`, `TRADE_ENABLED=1`, and `DRY_RUN_ONLY=0`.
- **Non-required**: everything else; defaults are used when not set (from `core/config/settings.py:DEFAULTS`).

## Core runtime

- `ENV` (str, default: `production`) — environment mode (`production` vs `local`/`test`)
- `RUNTIME_MODE` (str, default: empty) — override runtime mode (`TEST`, `OFFLINE`, `PAPER`, `LIVE`, `REPLAY`)
  - Detection order (`core/runtime_mode.py:68-79`): override → PYTEST/CI → REPLAY_MODE → OFFLINE_MODE → PAPER/SAFE_RUN → LIVE
- `DOTENV_DISABLE` (bool, default: `0`) — disable `.env` loading
- `PAPER_TRADING` (bool, default: `1`) — paper mode (forces `DRY_RUN_ONLY=1` via `bootstrap.py:52`)
- `TRADE_ENABLED` (bool, default: `0`) — enable trading logic
- `DRY_RUN_ONLY` (bool, default: `1`) — never submit orders; used by `execution_service`
- `SAFE_RUN` (bool, default: `1`) — hard block for live order submission (paper mirror)
- `LIVE_READONLY` (bool, default: `0`) — allow read-only exchange calls but block all mutations (paper mirror)
- `REPLAY_MODE` (bool, default: `0`) — enable replay mode
- `OFFLINE_MODE` (bool, default: `0`) — force offline mode (still fail-closed)
- `FORCE_RECONCILE` (bool, default: `0`) — force position reconciliation even in test mode
- `APP_RUN_ONESHOT` (bool, default: `0`) — run a single tick (for testing)
- `LOG_DIR` (str, default: `logs`) — logs output directory
- `LOG_LEVEL` (str, default: `INFO`) — logging level for bootstrap
- `LOOP_SLEEP_SEC` (float, default: `5` in `app.run`, `1` in bootstrap) — sleep between ticks
- `LOG_SKIP_THROTTLE_SEC` (int, default: `30`) — throttle skip tick_summary when unchanged
- `LOG_SKIP_AGG_SEC` (int, default: `60`) — emit skip aggregation window
  - In non-production, `core.config.loader.normalize_env()` forces `DRY_RUN_ONLY=1`

## State / Ledger

- `STATE_DIR` (str, default: `run/state`) — state persistence directory
  - Stores: `candle_gate.json`, `daily_YYYY-MM-DD.json`, `trade_ids.json`, `decision_{SYMBOL}.json`, `position_{SYMBOL}.json`
- `LEDGER_DIR` (str, default: `run/ledger`) — trade ledger directory
  - Stores: `ledger_YYYY-MM-DD.jsonl` (append-only event log)
- `LEDGER_ENABLED` (bool, default: `1`) — enable append-only trade ledger

## Market / strategy

- `SYMBOL` (str, default: `BTCUSDT`) — trading symbol
- `INTERVAL` (str, default: `5m`) — decision timeframe (LTF)
- `HTF_INTERVAL` (str, default: `1h`) — higher timeframe context (HTF)
- `QUOTE_ASSET` (str, default: `USDT`) — quote asset for the symbol
- `ROUTING_REGIME_OVERRIDE` (str, default: empty) — force routing regime for verification (non-production/test/offline/replay only)
  - Valid values: `BREAKOUT_EXPANSION`, `SQUEEZE_BREAK`, `TREND_ACCEL`, `TREND_CONTINUATION`, `PULLBACK`, `RANGE`, `COMPRESSION`, `EVENT`
  - Checked in `decision_engine.py:1455` — only allowed if `get_runtime_settings().is_test or is_offline or is_replay or env != "production"`

## Exchange endpoints and modules

- `EXCHANGE` (str, default: `binance_futures`)
- `BINANCE_TESTNET` (bool, default: `0`)
- `BINANCE_FAPI_BASE` (str, default: `https://fapi.binance.com`)
- `BASE_URL_MAINNET` (str, default: `https://fapi.binance.com`)
- `BASE_URL_TESTNET` (str, default: `https://testnet.binancefuture.com`)
- `MARKET_DATA_MODULE` (str, default: `app.services.market_data`)
- `BINANCE_RECV_WINDOW` (int, default: `5000`) — receive window for signed requests (`binance_futures.py:173`)
- `BINANCE_TIME_OFFSET_TTL_SEC` (int, default: `60`) — TimeSync refresh TTL (`binance_futures.py:33,62`)

## API keys and aliases

- `BINANCE_API_KEY`, `BINANCE_API_SECRET` (str, default: empty)
- `BINANCE_FAPI_KEY`, `BINANCE_FAPI_SECRET` (str, default: empty)
- `API_KEY`, `API_SECRET` (str, default: empty)

All are normalized into canonical keys. In production live trading, at least one pair must be set.

## Wallet / sizing

- `WALLET_USDT` (float, default: `1000`) — fallback wallet value for offline mode
- `ORDER_QTY_USD` (float, default: `0`) — fixed order size override (0 = disabled, uses risk-based sizing)
- `RISK_MARGIN_FRACTION` (float, default: `0.2`)
- `PREFERRED_MAX_LEVERAGE` (int, default: `5`)
- `LEVERAGE` (int, default: `5`) — leverage used for position sizing
- `MAX_LEVERAGE` (int, default: `5`)
- `MAX_MARGIN_UTIL_PCT` (float, default: `30`) — max margin utilization percentage
- `MIN_SL_TICKS` (int, default: `10`) — minimum SL distance in ticks
- `SIZER_EXTRA_BUFFER_PCT` (float, default: `0.02`) — extra buffer percentage for margin checks
  - Sizing uses `funds_base = min(availableBalance, totalMarginBalance)` when both exist (`payload_builder.py:440-441`)
  - `equity_usd` is required for payload validity; missing/invalid equity fails closed (`payload_builder.py:449-450`)

## Risk guard

- `RISK_PER_TRADE_PCT` (float, default: `5.0`) — risk per trade as percentage of funds_base
- `RISK_MAX_DD_PCT_DAY` (float, default: `3.0`) — maximum daily drawdown percentage
- `RISK_MAX_LOSS_USD_DAY` (float, default: `10.0`) — maximum daily loss in USD
- `RISK_MAX_POS_USD` (float, default: `100.0`) — maximum position size in USD
- `RISK_MIN_EQUITY_USD` (float, default: `10.0`) — minimum equity required
- `RISK_MAX_OPEN_RISK_USD` (float, default: `100.0`) — maximum open risk in USD
- `RISK_MAX_TRADES_PER_DAY` (int, default: `50`) — maximum trades per day
- `RISK_MAX_CONSEC_LOSSES` (int, default: `3`) — maximum consecutive losses before kill-switch

## SL/TP behavior

- `SL_MODE` (str, default: `atr`; `atr|none|percent`)
- `SL_ATR_MULT` (float, default: `1.5`)
- `TP_R_MULT` (float, default: `1.5`)
- `SL_PCT` (float, default: `0.5`) — used if `SL_MODE=percent`
- `TP_PCT` (float, default: `1.0`) — used if `SL_MODE=percent`
- `TP1_FRACTION` (float, default: `0.4`) — fraction of position for TP1 (40%), remainder goes to TP2 (60%)
  - Used when decision includes multiple TP targets (`tp_targets` array)
  - Applies to regimes with `partial_fixed` or `trailing` exit behavior when `rr_target >= 2.0`
  - TP1 at 60% of target RR, TP2 at 100% target RR (`decision_engine.py:1415`)

## ATR Budget sizing (optional)

- `PS_K_BASE` (float, default: `1.0`)
- `PS_K_MIN` (float, default: `0.25`)
- `PS_K_MAX` (float, default: `1.0`)
- `PS_K_SLOPE` (float, default: `0.04`)
- `PS_RECOVERY_DD_PCT` (float, default: `0.5`)

## Offline / test mode

- `OFFLINE_MODE` (bool, default: `0`) — forces runtime mode to OFFLINE (still fail-closed).

## Strategy thresholds (decision engine)

- `DECISION_VOLUME_RATIO_MIN` (float, default: `1.5`)
- `DECISION_EMA50_ATR_MAX` (float, default: `0.8`) — max EMA50 distance in ATR for trend strategies
- `DECISION_BREAKOUT_ATR_MIN` (float, default: `0.15`)
- `DECISION_SL_ATR_MULT` (float, default: `1.6`) — SL multiplier for decision engine
- `DECISION_TP_ATR_MULT` (float, default: `2.4`) — TP multiplier for decision engine
- `DECISION_MIN_RR` (float, default: `1.5`)
- `TRADE_COOLDOWN_MINUTES` (int, default: `15`) — cooldown after any trade_plan creation
- `REGIME_BREAKOUT_VOL_MIN` (float, default: `1.2`) — min volume ratio for breakout regime
- `REGIME_COMPRESSION_WIDTH_ATR_MAX` (float, default: `2.0`) — max Donchian width in ATR for compression
- `REGIME_COMPRESSION_VOL_MAX` (float, default: `1.0`) — max volume ratio for compression
- `REGIME_TREND_DIST50_MAX` (float, default: `1.0`) — max dist50 in ATR for trend regimes
- `VOLATILITY_PERCENTILE_MIN` (float, default: `20.0`) — min HTF ATR percentile for continuation/accel strategies

## Regime-specific thresholds

- `BREAKOUT_ACCEPT_BARS` (int, default: `2`) — consecutive closes required for breakout acceptance
- `BREAKOUT_REJECT_WICK_ATR` (float, default: `0.3`) — wick rejection threshold in ATR for breakout
- `BREAKOUT_RETEST_ATR` (float, default: `0.2`) — retest tolerance in ATR for breakout
- `BREAKOUT_SL_ATR` (float, default: `1.0`) — SL distance in ATR for breakout strategy
- `BREAKOUT_RR_TARGET` (float, default: `3.0`) — target RR for breakout strategy
- `CONTINUATION_SL_ATR` (float, default: `1.2`) — SL distance in ATR for continuation strategy
- `CONTINUATION_RR_TARGET` (float, default: `1.6`) — target RR for continuation strategy
- `PULLBACK_REENTRY_DIST50_MIN` (float, default: `0.3`) — min dist50 in ATR for pullback
- `PULLBACK_REENTRY_DIST50_MAX` (float, default: `1.5`) — max dist50 in ATR for pullback
- `PULLBACK_REENTRY_MIN_BARS` (int, default: `2`) — min bars against trend for pullback
- `PULLBACK_REENTRY_CONFIRM_BODY_MIN` (float, default: `0.5`) — min body ratio for pullback confirmation
- `PULLBACK_REENTRY_RECLAIM_VOL_MIN` (float, default: `1.0`) — min volume ratio for EMA reclaim
- `PULLBACK_REENTRY_SL_ATR` (float, default: `1.6`) — SL distance in ATR for pullback strategy
- `PULLBACK_REENTRY_RR_TARGET` (float, default: `2.2`) — target RR for pullback strategy
- `PULLBACK_REENTRY_VOL_MIN` (float, default: `1.0`) — min volume ratio for pullback entry
- `RANGE_MEANREV_EDGE_ATR` (float, default: `0.2`) — edge distance in ATR for range mean reversion
- `RANGE_MEANREV_VOL_MAX` (float, default: `1.0`) — max volume ratio for range
- `RANGE_MEANREV_SL_ATR` (float, default: `1.2`) — SL distance in ATR for range strategy
- `RANGE_MEANREV_RR_TARGET` (float, default: `1.6`) — target RR for range strategy
- `HTF_TREND_SLOPE_N` (int, default: `8`) — candles for HTF trend slope calculation
- `HTF_TREND_SLOPE_MIN` (float, default: `0.04`) — min HTF trend slope (ATR-normalized)
- `HTF_TREND_PERSIST_MIN` (int, default: `4`) — min consecutive closes on EMA200 side
- `HTF_TREND_STRUCTURE_MIN` (int, default: `0`) — min consecutive higher/lower closes (0 = disabled)
- `TIME_EXIT_BARS` (int, default: `12`) — bars without progress to trigger time exit
- `TIME_EXIT_PROGRESS_ATR` (float, default: `0.5`) — min progress in ATR to reset time exit counter

## Regime extensions

- `TREND_ACCEL_VOL_MULT` (float, default: `1.4`) — ATR expansion multiplier for trend acceleration
- `SQUEEZE_BB_WIDTH_TH` (float, default: `1.2`) — Bollinger width expansion threshold
- `EVENT_TR_ATR` (float, default: `3.0`) — true range ratio for event detection
- `EVENT_COOLDOWN_CANDLES` (int, default: `4`) — cooldown candles after event

## Stability scoring

- `STABILITY_N` (int, default: `20`) — LTF candles used for scoring
- `WICK_TH` (float, default: `2.5`) — wick ratio threshold per candle
- `XMAX` (float, default: `1.8`) — dist50 ATR cap for overextension proxy
- `STABILITY_HARD` (float, default: `0.70`) — hard stability gate
- `STABILITY_SOFT` (float, default: `0.58`) — soft stability gate (requires extra confirmation)
- `ADAPTIVE_SOFT_STABILITY_ENABLED` (bool, default: `0`) — when ON, allow entry in soft stability zone with 30% size reduction (respects min_qty)
- **Hardcoded weights** (`decision_engine.py:93`): `score = 0.55 * R + 0.25 * (1 - W) + 0.20 * (1 - X)` (not configurable)

## Continuation confirmation

- `CONFIRM_MIN_BODY_RATIO` (float, default: `0.55`) — body ratio for two-bar continuation
- `CONFIRM_MIN_BODY_RATIO_RETEST` (float, default: `0.45`) — body ratio for retest-reject
- `CONFIRM_MAX_CLOSE_POS_SHORT` (float, default: `0.25`) — close position limit for shorts
- `CONFIRM_MAX_CLOSE_POS_LONG` (float, default: `0.25`) — close position limit for longs
- `CONFIRM_RETEST_TOL_ATR` (float, default: `0.25`) — retest tolerance in ATR
- `CONFIRM_SWING_M` (int, default: `12`) — swing window for lower-high / higher-low
- `CONFIRM_DONCHIAN_K` (int, default: `10`) — Donchian window for break confirmation
- `CONFIRM_BREAK_DELTA_ATR` (float, default: `0.15`) — ATR buffer for breaks

## Anti-reversal (HTF)

- `HTF_EMA_PERIOD` (int, default: `20`) — EMA period for HTF reclaim filter
- `HTF_RSI_PERIOD` (int, default: `14`) — HTF RSI period
- `HTF_RSI_SLOPE_MIN` (float, default: `0.5`) — min RSI slope to trigger reversal block
- `ANTI_REV_WICK_TH` (float, default: `2.0`) — LTF wick ratio threshold for reversal block

## Pending entry confirmation

- `PENDING_CONFIRM_CANDLES` (int, default: `1`) — required confirming candles after signal
- `PENDING_EXPIRE_CANDLES` (int, default: `1`) — expiry window for pending entries

## Optional feature flags (audit, defaults OFF)

- `RANGE_IN_TREND_ENABLED` (bool, default: `0`) — when ON, allows RANGE_IN_TREND_LONG in trend-up with RSI ≤ RANGE_RSI_LONG_MAX, Donchian edge, atr_ratio &lt; 1 (same risk as RANGE_MEANREV)
- `PULLBACK_RECLAIM_TOL_ABS` (float, default: `0.0`) — absolute reclaim tolerance (price units). Effective tolerance = max(PULLBACK_RECLAIM_TOL_ABS, PULLBACK_RECLAIM_TOL_ATR × ATR) when REAL_MARKET_TUNING=1
- `PULLBACK_RECLAIM_TOL_ATR` (float, default: `0.10`) — used when REAL_MARKET_TUNING=1 for ATR-scaled reclaim tolerance
- `REAL_MARKET_TUNING` (bool, default: `0`) — when ON, enables relaxed thresholds (e.g. reclaim tolerance, stability soft/hard real values)

## EV gate (optional)

- `EV_GATE_ENABLED` (bool, default: `0`) — enable EV gate for soft stability
- `CONFIRM_BONUS` (float, default: `0.05`) — bonus to expected value for confirmed entries
- `EV_TP_R` (float, default: `1.5`) — EV gate TP multiple
- `EV_SL_R` (float, default: `1.0`) — EV gate SL multiple

## Live readonly (paper mirror)

Recommended profile:

```
LIVE_READONLY=1
SAFE_RUN=1
DRY_RUN_ONLY=1
TRADE_ENABLED=0
```

This keeps market/account/position reads live while hard-blocking all mutations.

## Logging (high-signal)

Single-line structured events (JSON):

- `startup_banner`: ENV, runtime flags, symbol/interval, mutation status
- `tick_summary`: now, interval, latest_closed_ts, last_processed_ts, will_process, skip_reason
- `decision_candle`: price/bid/ask/spread, HTF trend, EMA50/ATR/RSI, pullback/reclaim, decision + reject_reasons
- `decision_clean`: regime routing + blockers + funds/sizing evidence

`decision_candle` fields:

- `close`, `bid`, `ask`, `spread_pct`: market snapshot
- `trend`, `close_htf`, `ema200_htf`: HTF context
- `ema50_5m`, `atr`, `rsi14_5m`: LTF indicators
- `pullback_atr`, `reclaim`: pullback metrics
- `decision`, `reject_reasons`, `cooldown_active`
- `time_exit_signal`: log-only time exit condition

`decision_clean` fields:

- `equity`, `funds_base`, `funds_source`, `risk_usd`, `qty_before_rounding`, `qty_after_rounding`
- `required_margin`, `leverage_used`
- `regime_detected`, `regime_used_for_routing`
- `eligible_strategies`, `selected_strategy`
- `blockers`, `blocker_categories`
- `stability_mode_used` (hard | soft | block)
- `gating_summary` (first failing blocker)
- `router_debug.compact` (when present) — one-line router state
- `reclaim_debug.compact` (when present) — one-line reclaim level/tolerance/distance

Decision logging is additive; no JSON schema changes. `decision_candle` and `decision_clean` include optional `router_debug`, `reclaim_level_used`, `effective_tolerance`, `distance_to_reclaim` when applicable.

Runtime log file:

- `LOG_DIR/runtime.log` (default: `logs/runtime.log`)

Session log file:

- `LOG_DIR/sessions/{YYYYMMDD_HHMMSS}_{pid}.log`

Windows tail command:

```
Get-Content -Path .\logs\runtime.log -Wait
```

## Local/test without real exchange access

Recommended settings:

```
ENV=local
TRADE_ENABLED=0
DRY_RUN_ONLY=1
OFFLINE_MODE=1
```

This keeps sizing and preflight in offline mode and prevents any real order submission.

## Cache / network hygiene

- `PRICE_CACHE_TTL_SEC` (float, default: `10`) — price snapshot cache TTL
- `FILTERS_CACHE_TTL_SEC` (float, default: `21600`) — exchange filters cache TTL (6 hours)
- `HTTP_TIMEOUT_SEC` (float, default: `10`) — HTTP request timeout
- `ACCOUNT_SNAPSHOT_TTL_SEC` (float, default: `45`) — account snapshot cache TTL

## Fees / Funding

- `FEE_MAKER_BPS` (float, default: `2.0`) — maker fee in basis points (0.02%)
- `FEE_TAKER_BPS` (float, default: `4.0`) — taker fee in basis points (0.04%)
- `FUNDING_RATE` (float, default: `0`) — current funding rate
- `FUNDING_NEXT_TS` (int, default: `0`) — next funding timestamp
- `FUNDING_INTERVAL_SECONDS` (int, default: `28800`) — funding interval (8 hours)

## Market data validation

- `MD_MIN_BARS_1H` (int, default: `260`) — minimum bars required for 1h timeframe (fail-closed)
- `MD_MIN_BARS_4H` (int, default: `220`) — minimum bars required for 4h timeframe (fail-closed)
- `MD_MAX_GAP_SECONDS` (int, default: `7200`) — maximum gap between candles in seconds (fail-closed)
- `MD_MAX_AGE_SECONDS` (int, default: `7200`) — maximum age of latest candle in seconds (fail-closed, skipped in replay)
- `DATA_MAX_AGE_SECONDS` (int, default: `7200`) — maximum age of data snapshot (used by risk manager)

## Execution safety thresholds

- `SPREAD_MAX_PCT` (float, default: `0.5`) — maximum spread percentage (kill-switch)
- `ATR_SPIKE_MAX_PCT` (float, default: `0.1`) — maximum ATR spike percentage (kill-switch)
- `EXIT_REQUIRE_TP` (bool, default: `1`) — require TP order for open positions (hard kill if missing)

## Execution timing / retries

- `EXECUTION_POLL_INTERVAL_SEC` (float, default: `0.5`) — interval between order status polls
- `EXECUTION_POLL_TIMEOUT_SEC` (float, default: `10`) — timeout for order fill confirmation
- `EXECUTION_POLL_MAX_ATTEMPTS` (int, default: `40`) — max polling attempts
- `EXECUTION_RETRY_ATTEMPTS` (int, default: `3`) — max retry attempts for failed orders
- `EXECUTION_RETRY_BASE_DELAY_SEC` (float, default: `0.5`) — base delay for retries
- `EXECUTION_RETRY_MAX_DELAY_SEC` (float, default: `4.0`) — max delay for retries
