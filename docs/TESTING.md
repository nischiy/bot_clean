# Testing

## Run tests

```bash
# Run all tests
py -m pytest tests/ -v

# Run safety-critical tests only (CI Stage 1)
py -m pytest -m critical -v

# Run specific test file
py -m pytest tests/test_decision_engine.py -v

# Run with coverage (if configured)
py -m pytest --cov=app --cov=core tests/ -v
```

## Test Structure

Tests are organized in `tests/` directory:
- Safety-critical tests are marked with `@pytest.mark.critical`
- CI runs critical tests first, then full suite

## What is mocked and why

- Network libraries (`requests`, `urllib.request`, `socket`, plus `httpx`, `aiohttp`, `websockets`) are blocked in `tests/test_no_network.py`.
- REST calls are mocked/stubbed in unit tests where needed.
- This guarantees imports and app wiring do not attempt real network calls.

## Network access prevention

- `test_no_network.py` raises immediately if any network client is used.
- Tests run with:
  - `ENV=local`
  - `DRY_RUN_ONLY=1`
  - `TRADE_ENABLED=0`
  - `OFFLINE_MODE=1`

These settings ensure only offline fallbacks are used.

## Safety tests (critical)

- `test_imports_no_side_effects.py`: imports every module and asserts no runtime dirs (`run/`, `logs/`) are created.
- `test_bootstrap_fail_fast.py`: verifies missing critical services raise `RuntimeError`.
- `test_closed_candle_gating.py`: ensures decisions run once per closed 5m candle (persisted state gating).
- `test_config_validation.py`: enforces production key requirements and local safe mode.
- `test_schema_validation.py`: validates JSON schema contracts (payload, decision, trade_plan).
- `test_decision_engine.py`: tests decision engine logic and regime routing.
- `test_golden_replay_defaults_off.py`: golden replay — decision signatures match baseline when new flags are OFF.
- `test_risk_manager.py`: tests risk manager kill-switch checks and position sizing.
- `test_state_manager.py`: tests state persistence and restart safety.
- `test_execution_idempotency.py`: verifies clientOrderId tracking prevents duplicate orders.
- `test_execution_consolidation.py`: tests execution service order consolidation.
- `test_payload_builder.py`: tests payload building and fail-closed validation.
- `test_market_data_validator.py`: tests market data validation checks.

## Test Environment

Tests automatically set safe defaults:
- `ENV=local`
- `DRY_RUN_ONLY=1`
- `TRADE_ENABLED=0`
- `OFFLINE_MODE=1`
- `PYTEST_CURRENT_TEST` is set (detected by runtime to skip side effects)

These ensure tests never make real network calls or create runtime artifacts.

## Golden replay test: behavior unchanged when flags OFF

When new feature flags are **OFF** (defaults: `ADAPTIVE_SOFT_STABILITY_ENABLED=0`, `RANGE_IN_TREND_ENABLED=0`, `PULLBACK_RECLAIM_TOL_ABS=0`, `REAL_MARKET_TUNING=0`), decision signatures must match a committed baseline. This proves no behavior change by default.

- **Run:** `py -m pytest tests/test_golden_replay_defaults_off.py -v`
- **Regenerate baseline** (only after an intentional logic change): set `UPDATE_GOLDEN=1` and run the same test. The test writes `tests/fixtures/baselines/golden_signatures_defaults_off.json` and skips the comparison. Commit the new baseline, then run again without `UPDATE_GOLDEN` to confirm.

Example (Windows): `set UPDATE_GOLDEN=1 && py -m pytest tests/test_golden_replay_defaults_off.py -v`

The test uses fixture data in `tests/fixtures/golden_bars.json` and does not write any file unless `UPDATE_GOLDEN=1`.
