# Runtime Invariants

1) Decisions are made only on closed INTERVAL candles (default 5m).
- Enforced in: `app.run._run_once_contracts()` using `_filter_closed_candles()` (`run.py:357,364`) and `_latest_closed_candle_ts()` (`run.py:368`), plus persisted timestamp gating (`run.py:383-394`).
- Breakage if violated: signal could be computed on incomplete data, causing double-trigger or unstable outputs.

2) No network calls at import time.
- Enforced by: test `tests/test_imports_no_side_effects.py` and design of modules (network only in functions).
- Breakage if violated: tests fail and runtime may perform unintended network calls during import.

3) Critical services must exist at bootstrap.
- Enforced in: `app.bootstrap.compose_trader_app()` (MarketDataAdapter is required).
- Critical set: `MarketDataAdapter`, `TraderApp`.
- Breakage if violated: app composition raises `RuntimeError` and refuses to run.

4) Execution is isolated from strategy.
- Enforced by: JSON contracts flow (payload → decision → trade_plan → execution) and `ExecutionService` only accepting validated trade_plan.json.
- Breakage if violated: risk/execution could be bypassed; potential unsafe execution.

4a) Exit intents must still use trade_plan.
- Enforced by: `decision.intent=CLOSE` routed through `risk_manager.create_trade_plan()` and `ExecutionService`.
- Breakage if violated: close orders could bypass safety and idempotency checks.

5) Dry-run safety in non-production.
- Enforced in: `core.config.loader.normalize_env()` forces `DRY_RUN_ONLY=1` when `ENV != production`.
- Breakage if violated: tests or local runs could submit live orders.

5a) SAFE_RUN, DRY_RUN_ONLY, and LIVE_READONLY hard-block order mutation.
- Enforced in: `ExecutionService.execute_trade_plan()` and `core.execution.binance_futures._post/_delete()` guards.
- Breakage if violated: orders or leverage could be submitted when mutations must be blocked.

6) No filesystem writes at import time.
- Enforced by: refactor in `core.risk_guard` (no `mkdir` at import) and tests.
- Breakage if violated: side effects on import, tests fail, non-deterministic behavior.

7) Strict per-tick ordering (contracts pipeline).
- Enforced in: `app.run._run_once_contracts()` ordering (`run.py:236-684`): position snapshot → reconcile → kill → preflight → market data validation → payload → decision → trade_plan → execution.
- Exact order: See `docs/ai/RUNTIME_FLOW.md` for step-by-step breakdown with code references.
- Breakage if violated: side effects or decisions occur before safety checks.

8) Preflight and market data can block execution.
- Enforced in: `app.run.TraderApp.run_once()` with `preflight_rejects` and `market_data_validator`.
- Breakage if violated: execution could run without price/filters/account data.

9) App loop is controlled by oneshot/test flags.
- Enforced in: `TraderApp.start()` using `APP_RUN_ONESHOT`, `PYTEST_CURRENT_TEST`, `CI`.
- Breakage if violated: tests can hang or run indefinitely.

10) Config validation in production live mode.
- Enforced in: `core.config.loader._validate_config()` called from `get_config()`.
- Condition: `ENV=production`, `TRADE_ENABLED=1`, `DRY_RUN_ONLY=0`.
- Action: raises `RuntimeError` if API keys are missing.
- Breakage if violated: live mode could run without API keys, causing runtime failures.

11) Strategy determinism.
- Status: ENFORCED.
- Assumption: `app.strategy.decision_engine.make_decision(payload, daily_state, decision_state)` returns identical output for identical inputs.
- Note: Decision state updates via `state_update` field may cause different outputs on subsequent calls with same payload (by design).
- Enforcement: deterministic test in `tests/test_decision_engine.py`.
- Impact if violated: inconsistent decisions under identical data, breaking reproducibility.

12) Explainable logs are single-line and stable.
- Enforced in: `app.run._log_structured()` with `tick_summary`, `decision_candle`, and `decision_clean`.
- Required fields: routing regime, blockers, equity, and funds/sizing evidence in `decision_clean`.
- Impact if violated: trading decisions become non-auditable in real time.

13) Time-exit intent uses only closed candles.
- Enforced in: `app.strategy.decision_engine.make_decision()` with LTF close stats from payload.
- Impact if violated: exit signals could be based on incomplete candle data.

14) Trend stability gate uses closed HTF candles only.
- Enforced in: `app.data.payload_builder.build_payload()` (HTF EMA200 slope/persistence/structure).
- Impact if violated: strategy could trade on incomplete HTF trend data.

15) Strategy priority order and time-exit precedence.
- Enforced in: `app.strategy.decision_engine.make_decision()` - time-exit checked first (`decision_engine.py`), then regime detection (`_detect_regime`), then strategy routing (`select_strategy_by_regime`).
- Regime detection order (top-down, exclusive): EVENT → SQUEEZE_BREAK → BREAKOUT_EXPANSION → COMPRESSION → TREND_ACCEL → TREND_CONTINUATION → PULLBACK → RANGE (`decision_engine.py:353-383`).
- Impact if violated: multiple strategies could trigger in the same candle or entries could bypass exit intents.

16) Equity is required for payload validity.
- Enforced in: `app.data.payload_builder.build_payload()` (missing/invalid equity adds `missing_or_invalid_equity` and fails closed).
- Impact if violated: trades could proceed without an account equity baseline.

