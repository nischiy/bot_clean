# Testing

## Run tests

```
py -m pytest
```

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

## Safety tests

- `test_imports_no_side_effects.py`: imports every module and asserts no runtime dirs (`run/`, `logs/`) are created.
- `test_bootstrap_fail_fast.py`: verifies missing critical services raise `RuntimeError`.
- `test_closed_candle_gating.py`: ensures decisions run once per closed 5m candle.
- `test_config_validation.py`: enforces production key requirements and local safe mode.
