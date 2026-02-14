# Project Map (AI-Oriented)

## Authority Hierarchy

**Critical components** (required at runtime):
- `app.run.TraderApp` - main runtime loop and orchestration
- `app.bootstrap.compose_trader_app` - service composition and wiring
- `app.bootstrap.MarketDataAdapter` - market data access (backed by `app.services.market_data`)
- `app.data.market_data_validator.validate_market_data` - market data validation (fail-closed)
- `app.data.payload_builder.build_payload` - payload construction
- `app.strategy.decision_engine.make_decision` - decision making (intent only)
- `app.risk.risk_manager.create_trade_plan` - trade plan creation (final authority)
- `app.services.execution_service.ExecutionService` - order execution (idempotent)
- `app.state.state_manager` - state persistence (for restart safety)

**Optional components**: 
- `app.core.trade_ledger` - append-only event logging (if `LEDGER_ENABLED=1`)

## `main.py`
- Responsibility: entrypoint that loads config and composes the trading app, then starts it.
- Authority: calls `core.config.loader.get_config()` and `app.bootstrap.compose_trader_app()`, then `TraderApp.start()`.
- Allowed: call config loader, compose app, start runtime.
- MUST NOT: perform network calls or long-running work itself.
- Network access: no.
- Filesystem access: no.
- Environment access: indirect via config loader.
- Import-time side effects: none.
- **Note:** `get_config()` is called but result may not be passed to `compose_trader_app()`; bootstrap will read from environment if needed.

## `app/`

### `app/bootstrap.py`
- Responsibility: compose and wire the runtime (`TraderApp`) with adapters and services.
- Authority: final authority on service wiring; raises `RuntimeError` if critical components fail.
- Allowed: import modules, construct objects, set environment defaults, configure logging.
- MUST NOT: perform network calls, execute trading logic, or run loops.
- Network access: no.
- Filesystem access: logging directory creation at runtime (skipped when `PYTEST_CURRENT_TEST` is set).
- Environment access: yes (reads and writes keys via `_coerce_to_appconfig`).
- Import-time side effects: none (logging setup runs only when `_ensure_logging()` is called).

### `app/run.py`
- Responsibility: core runtime loop (`TraderApp.start`) and single-tick logic (`run_once`).
- Authority: orchestrates the decision pipeline; enforces closed-candle gating; final authority on loop control.
- Allowed: orchestrate market data → payload → decision → trade_plan → execution; enforce candle gating and exit reconciliation.
- MUST NOT: bypass risk manager or execute without trade_plan.
- Network access: yes (preflight snapshots via `HttpMarketData` and `binance_futures`).
- Filesystem access: logging to `logs/` at runtime (skipped when `_is_testish_env()` returns True).
- Environment access: yes (mode flags, symbol, sleep interval).
- Import-time side effects: none.

### `app/decision.py`
- Responsibility: normalize decision structure and side/action.
- Allowed: pure transformation.
- MUST NOT: side effects.
- Network access: no.
- Filesystem access: no.
- Environment access: no.
- Import-time side effects: none.

### `app/data/payload_builder.py` (NEW)
- Responsibility: build deterministic payload.json from market data, account state, and features.
- Authority: fail-closed validation - missing/NaN/stale data → payload fails → HOLD.
- Allowed: compute indicators (EMA, Donchian, ATR, volume ratio), HTF stability metrics, LTF streak stats, validate payload.
- MUST NOT: side effects, network calls.
- Network access: no.
- Filesystem access: no.
- Environment access: yes (risk policy defaults).
- Import-time side effects: none.
 - Notes: derives `funds_base = min(availableBalance, totalMarginBalance)` when both exist.

### `app/data/market_data_validator.py` (NEW)
- Responsibility: validate market data integrity before payload building.
- Authority: fail-closed validation for NaNs, gaps, stale data, missing columns.
- Allowed: read DataFrame and return errors list.
- MUST NOT: side effects or network calls.
- Network access: no.
- Filesystem access: no.
- Environment access: yes (validation thresholds).
- Import-time side effects: none.

### `app/strategy/decision_engine.py` (NEW)
- Responsibility: convert payload.json to decision.json (INTENT ONLY, no execution authority).
- Authority: strategy rules enforcement, stability gating (slope/persistence/structure), SL/TP calculation, adaptive RR validation, time-exit CLOSE intent.
- Allowed: pure transformation from payload to decision.
- MUST NOT: side effects, network calls, execution authority.
- Network access: no.
- Filesystem access: no.
- Environment access: yes (reads thresholds and routing override via settings).
- Import-time side effects: none.

### `app/risk/risk_manager.py` (NEW)
- Responsibility: final authority that produces trade_plan.json from validated payload + decision.
- Authority: kill-switch checks, position sizing, trade plan creation (OPEN/CLOSE).
- Allowed: apply kill-switch checks, call position_sizing, validate trade_plan.
- MUST NOT: side effects, network calls (delegates to execution_service).
- Network access: no.
- Filesystem access: no.
- Environment access: no (uses payload risk_policy).
- Import-time side effects: none.
 - Notes: rejects on missing/nonpositive `funds_base`.

### `app/risk/position_sizing.py` (NEW)
- Responsibility: risk-based position sizing with leverage caps.
- Allowed: calculate qty from risk_usd and SL distance, respect exchange limits.
- MUST NOT: side effects.
- Network access: no.
- Filesystem access: no.
- Environment access: no.
- Import-time side effects: none.
 - Notes: uses funds_base for sizing and re-checks margin after rounding.

### `app/state/state_manager.py` (NEW)
- Responsibility: persist state for restart safety and reconciliation.
- Authority: state persistence, trade identifier tracking, position reconciliation.
- Allowed: read/write state files in `run/state/`, reconcile positions.
- MUST NOT: network calls.
- Network access: no.
- Filesystem access: yes (runtime: `run/state/*.json`).
- Environment access: no.
- Import-time side effects: none (creates directory on first use, not at import).

### `app/core/trade_ledger.py` (NEW)
- Responsibility: append-only trade ledger with deterministic hashes and correlation IDs.
- Authority: record trade lifecycle events to JSONL files.
- Allowed: runtime file writes under `run/ledger/`.
- MUST NOT: write at import time.
- Network access: no.
- Filesystem access: yes (runtime only).
- Environment access: yes (`LEDGER_DIR`, `LEDGER_ENABLED`).
- Import-time side effects: none.

### `core/runtime_mode.py` (NEW)
- Responsibility: centralized runtime mode model and settings.
- Authority: single source of truth for runtime behavior.
- Allowed: read environment and derive `RuntimeSettings`.
- MUST NOT: side effects.
- Network access: no.
- Filesystem access: no.
- Environment access: yes.
- Import-time side effects: none.

### `app/services/`

- `market_data.py`
  - Responsibility: HTTP market data retrieval and DataFrame normalization.
  - Allowed: perform HTTP GET to Binance endpoints, parse to DataFrame.
  - MUST NOT: be called at import time; do not write files.
  - Network access: yes (requests).
  - Filesystem access: no.
  - Environment access: no direct reads; uses parameters.
  - Import-time side effects: none.

- `execution_service.py` (NEW)
  - Responsibility: idempotent execution layer that consumes ONLY trade_plan.json (OPEN/CLOSE).
  - Authority: final authority on order execution (respects `DRY_RUN_ONLY` flag).
  - Allowed: validate trade_plan, check idempotency, place orders via REST, track identifiers.
  - MUST NOT: execute without valid trade_plan.json, duplicate orders.
  - Network access: yes (via `notifications`, `core.execution.binance_futures`).
  - Filesystem access: yes (via `state_manager` for trade identifier tracking).
  - Environment access: yes (mode flags via `get_bool`).
  - Import-time side effects: none.

### `app/replay.py` (NEW)
- Responsibility: deterministic replay runner using historical data through the real pipeline.
- Authority: SAFE_RUN enforced; writes state/ledger.
- Allowed: network for historical market data and read-only account snapshot.
- MUST NOT: place orders.
- Network access: yes (market data, account snapshot).
- Filesystem access: yes (state + ledger).
- Environment access: yes (SAFE_RUN, STATE_DIR).
- Import-time side effects: none.

- `exit_adapter.py`
  - Responsibility: build SL/TP order specs (pure).
  - Allowed: pure transformation.
  - MUST NOT: side effects.
  - Network access: no.
  - Filesystem access: no.
  - Environment access: no.
  - Import-time side effects: none.

- `notifications.py`
  - Responsibility: REST calls for orders and leverage.
  - Allowed: network via `core.execution.binance_futures`.
  - MUST NOT: run at import time.
  - Network access: yes.
  - Filesystem access: no.
  - Environment access: no (uses lower-level module).
  - Import-time side effects: none.

## `core/`

### `core/config/*`
- Responsibility: load, normalize, and validate configuration.
- Authority: final authority on environment normalization and production validation.
- Allowed: read environment, load `.env` files via `core.env_loader`, enforce production validation in `_validate_config()`.
- MUST NOT: perform network calls.
- Network access: no.
- Filesystem access: yes (read `.env` files only when `load_dotenv_once()` is called).
- Environment access: yes (reads and writes canonical keys via `normalize_env()`).
- Import-time side effects: none.

### `app/core/validation.py` (NEW)
- Responsibility: JSON schema validation for payload, decision, and trade_plan contracts.
- Authority: fail-closed validation - validation failures return errors without crashing runtime.
- Allowed: validate JSON objects against schemas in `app/core/schemas/`.
- MUST NOT: side effects, network calls.
- Network access: no.
- Filesystem access: yes (read schema files from `app/core/schemas/`).
- Environment access: no.
- Import-time side effects: none (schemas loaded on demand).

### `core/env_loader.py`
- Responsibility: `.env` discovery and parsing.
- Allowed: read `.env` files and set `os.environ`.
- MUST NOT: network calls.
- Network access: no.
- Filesystem access: yes (read files).
- Environment access: yes.
- Import-time side effects: none.

### `core/exchange_private.py`
- Responsibility: private account snapshot via REST helpers in `core.execution.binance_futures`.
- Allowed: network calls when invoked.
- MUST NOT: run at import time.
- Network access: yes.
- Filesystem access: no.
- Environment access: yes (API keys, testnet flag).
- Import-time side effects: none.
 - Notes: prefers `/fapi/v3/account`, fallback `/fapi/v2/account`.

### `core/execution/binance_futures.py`
- Responsibility: REST helpers for Binance Futures.
- Allowed: HTTP requests using `requests`.
- MUST NOT: run at import time.
- Network access: yes.
- Filesystem access: no.
- Environment access: yes (API keys, testnet flag).
- Import-time side effects: none.
 - Notes: TimeSync used for all signed endpoints; retry-on-`-1021`.

### `core/logic/ema_rsi_atr.py`
- Responsibility: strategy computation.
- Allowed: compute indicators and decision dict.
- MUST NOT: side effects.
- Network access: no.
- Filesystem access: no.
- Environment access: no.
- Import-time side effects: none.

### `core/positions/position_sizer.py`
- Responsibility: compute qty/leverage and fetch price/filters (offline or exchange).
- Allowed: HTTP via `urllib.request` when not offline/test.
- MUST NOT: run at import time.
- Network access: yes.
- Filesystem access: no.
- Environment access: yes.
- Import-time side effects: none.

### `core/risk_guard.py`
- Responsibility: kill-switch, risk checks, and health logging.
- Authority: final authority on kill-switch state (via `is_killed()`); risk evaluation via `evaluate()`.
- Allowed: write flag and health logs when functions are called (via `_ensure_parent()`).
- MUST NOT: create files/directories at import time.
- Network access: no.
- Filesystem access: yes (runtime only: `run/TRADE_KILLED.flag`, `logs/health/<date>/health.jsonl`).
- Environment access: yes (risk thresholds via `_get_float`, `_get_int`).
- Import-time side effects: none.

### `core/telemetry/health.py`
- Responsibility: write health events to JSONL and log.
- Allowed: create `logs/health/...` at runtime.
- MUST NOT: network calls.
- Network access: no.
- Filesystem access: yes (runtime).
- Environment access: no.
- Import-time side effects: none.

## `tests/`
- Responsibility: enforce no network, no import side effects, fail-fast wiring, candle gating, config validation.
- Allowed: monkeypatch, stub network, inspect imports.
- MUST NOT: perform real network calls or create runtime directories.
- Network access: blocked.
- Filesystem access: minimal (pytest cache only, state files in `run/state/` for tests).
- Environment access: yes (monkeypatch).
- Import-time side effects: must be none.

**New Test Files:**
- `test_schema_validation.py`: Tests for JSON schema validation
- `test_payload_builder.py`: Tests for payload building
- `test_decision_engine.py`: Tests for decision making
- `test_risk_manager.py`: Tests for risk manager and kill-switch checks
- `test_state_manager.py`: Tests for state persistence and reconciliation

## `docs/`
- Responsibility: human-readable docs.
- Allowed: no runtime impact.
- MUST NOT: referenced for code execution.

