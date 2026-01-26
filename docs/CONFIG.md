# Configuration

This project relies on environment variables (via `.env` or process env). `core.config.loader.get_config()` normalizes aliases and applies defaults.

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
- **Non-required**: everything else; defaults are used when not set.

## Core runtime

- `ENV` (str, default: `production`) — environment mode (`production` vs `local`/`test`).
- `DOTENV_DISABLE` (bool, default: `0`) — disable `.env` loading.
- `PAPER_TRADING` (bool, default: `1`) — paper mode.
- `TRADE_ENABLED` (bool, default: `0`) — enable trading logic.
- `DRY_RUN_ONLY` (bool, default: `1`) — never submit orders; used by `execution_service`.
- `SAFE_RUN` (bool, default: `1`) — hard block for live order submission.
- `LIVE_READONLY` (bool, default: `0`) — allow read-only exchange calls but block all mutations (paper mirror).
- `APP_RUN_ONESHOT` (bool, default: `0`) — run a single tick.
- `LOG_DIR` (str, default: `logs`) — logs output directory.
- `LOG_LEVEL` (str, default: `INFO`) — logging level for bootstrap.
- `LOOP_SLEEP_SEC` (float, default: `5` in `app.run`, `1` in bootstrap) — sleep between ticks.
- `LOG_SKIP_THROTTLE_SEC` (int, default: `30`) — throttle skip tick_summary when unchanged.
- `LOG_SKIP_AGG_SEC` (int, default: `60`) — emit skip aggregation window.
  - In non-production, `core.config.loader.normalize_env()` forces `DRY_RUN_ONLY=1`.

## Market / strategy

- `SYMBOL` (str, default: `BTCUSDT`)
- `INTERVAL` (str, default: `5m`) — decision timeframe (LTF).
- `HTF_INTERVAL` (str, default: `1h`) — higher timeframe context (HTF).
- `QUOTE_ASSET` (str, default: `USDT`)

## Exchange endpoints and modules

- `EXCHANGE` (str, default: `binance_futures`)
- `BINANCE_TESTNET` (bool, default: `0`)
- `BINANCE_FAPI_BASE` (str, default: `https://fapi.binance.com`)
- `BASE_URL_MAINNET` (str, default: `https://fapi.binance.com`)
- `BASE_URL_TESTNET` (str, default: `https://testnet.binancefuture.com`)
- `MARKET_DATA_MODULE` (str, default: `app.services.market_data`)

## API keys and aliases

- `BINANCE_API_KEY`, `BINANCE_API_SECRET` (str, default: empty)
- `BINANCE_FAPI_KEY`, `BINANCE_FAPI_SECRET` (str, default: empty)
- `API_KEY`, `API_SECRET` (str, default: empty)

All are normalized into canonical keys. In production live trading, at least one pair must be set.

## Wallet / sizing

- `WALLET_USDT` (float, default: `1000`)
- `ORDER_QTY_USD` (float, default: `0`)
- `RISK_MARGIN_FRACTION` (float, default: `0.2`)
- `PREFERRED_MAX_LEVERAGE` (int, default: `5`)
- `LEVERAGE` (int, default: `5`)
- `MAX_LEVERAGE` (int, default: `5`)
- `MAX_MARGIN_UTIL_PCT` (float, default: `30`)
- `MIN_SL_TICKS` (int, default: `10`)

## Risk guard

- `RISK_PER_TRADE_PCT` (float, default: none)
- `RISK_MAX_DD_PCT_DAY` (float, default: `3`)
- `RISK_MAX_LOSS_USD_DAY` (float, default: `10`)
- `RISK_MAX_POS_USD` (float, default: `100`)
- `RISK_MIN_EQUITY_USD` (float, default: `10`)
- `RISK_MAX_OPEN_RISK_USD` (float, default: `100`)
- `RISK_MAX_TRADES_PER_DAY` (int, default: `50`)
- `RISK_MAX_CONSEC_LOSSES` (int, default: `3`)

## SL/TP behavior

- `SL_MODE` (str, default: `atr`; `atr|none|percent`)
- `SL_ATR_MULT` (float, default: `1.5`)
- `TP_R_MULT` (float, default: `1.5`)
- `SL_PCT` (float, default: `0.5`)
- `TP_PCT` (float, default: `1.0`)

## ATR budget sizing

- `PS_K_BASE` (float, default: `1.0`)
- `PS_K_MIN` (float, default: `0.25`)
- `PS_K_MAX` (float, default: `1.0`)
- `PS_K_SLOPE` (float, default: `0.04`)
- `PS_RECOVERY_DD_PCT` (float, default: `0.5`)

## Offline / test mode

- `OFFLINE_MODE` (bool, default: `0`) — forces runtime mode to OFFLINE (still fail-closed).

## Strategy thresholds (decision engine)

- `DECISION_VOLUME_RATIO_MIN` (float, default: `1.5`)
- `DECISION_EMA50_ATR_MAX` (float, default: `2.0`)
- `DECISION_BREAKOUT_ATR_MIN` (float, default: `0.15`)
- `DECISION_SL_ATR_MULT` (float, default: `2.2`)
- `DECISION_TP_ATR_MULT` (float, default: `3.6`)
- `DECISION_MIN_RR` (float, default: `1.8`)
- `TRADE_COOLDOWN_MINUTES` (int, default: `15`) — cooldown after any trade_plan creation.

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

`decision_candle` fields:

- `close`, `bid`, `ask`, `spread_pct`: market snapshot
- `trend`, `close_htf`, `ema200_htf`: HTF context
- `ema50_5m`, `atr`, `rsi14_5m`: LTF indicators
- `pullback_atr`, `reclaim`: pullback metrics
- `decision`, `reject_reasons`, `cooldown_active`
- `time_exit_signal`: log-only time exit condition

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

- `PRICE_CACHE_TTL_SEC` (float, default: `10`)
- `FILTERS_CACHE_TTL_SEC` (float, default: `21600`) — exchange filters TTL (hours).
