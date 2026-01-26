# Updates (AI-Oriented)

## Summary

The system now includes:
- **Canonical Trade Ledger** (append-only JSONL with deterministic hashes)
- **Central Runtime Mode** (single source of truth for runtime behavior)
- **Market Data Validation** (fail-closed before payload build)
- **SAFE_RUN** (paper mirror, orders blocked)
- **LIVE_READONLY** (paper mirror, mutations blocked)
- **Deterministic 5m Regime Router** (COMPRESSION, BREAKOUT_EXPANSION, TREND_CONTINUATION, PULLBACK, RANGE)
- **HTF Trend Stability Gate** (EMA200 slope + persistence)
- **HTF Trend Structure Gate** (optional consecutive higher/lower closes)
- **Breakout Acceptance** (2-phase impulse/acceptance + retest)
- **Pullback Reentry** (min depth, min pullback bars, reclaim confirmation)
- **Adaptive RR by regime**
- **Time-exit CLOSE intent**
- **Replay Runner** (deterministic historical pipeline)
- **Critical Test Gating** for CI (`pytest -m critical`)

## Trade Ledger
- Module: `app/core/trade_ledger.py`
- Storage: `run/ledger/ledger_YYYY-MM-DD.jsonl`
- Hashes: canonical JSON serialization with sorted keys
- Events include:
  - `decision_created`
  - `trade_plan_created`
  - `execution_attempted`
  - `execution_submitted`
  - `sltp_submitted`
  - `position_closed`
  - `kill_switch_triggered`

## Runtime Mode
- Module: `core/runtime_mode.py`
- Modes: `TEST`, `OFFLINE`, `PAPER`, `LIVE`, `REPLAY`
- Override: `RUNTIME_MODE=<mode>`
- Used by runtime and tests to ensure consistent behavior

## Market Data Validation
- Module: `app/data/market_data_validator.py`
- Checks: required columns, monotonic timestamps, gaps, NaNs, staleness, min bars
- Fail-closed: invalid data → HOLD/STOP

## SAFE_RUN
- Env: `SAFE_RUN=1`
- Pipeline runs normally but execution is blocked
- REST order endpoints raise if SAFE_RUN is active

## LIVE_READONLY
- Env: `LIVE_READONLY=1`
- Pipeline runs normally with real data but all mutations are blocked
- REST order endpoints raise even if SAFE_RUN/DRY_RUN_ONLY are off

## Replay
- Module: `app/replay.py`
- CLI: `python -m app.replay --from YYYY-MM-DD --to YYYY-MM-DD`
- Uses real pipeline; SAFE_RUN enforced
- Produces summary and ledger events

## Critical Tests
## Strategy & Logs
- Regime detection uses 5m Donchian width, dist50 (EMA50 vs ATR), and breakout/volume checks
- Trend stability uses HTF EMA200 slope norm + consecutive closes on EMA200 side
- Trend structure optionally enforces consecutive higher/lower HTF closes
- Breakout uses impulse + acceptance (consecutive closes or wick reject), optional retest
- Pullback reentry uses min pullback bars + reclaim confirmation + fake-reclaim filter
- Strategy priority order: TIME_EXIT → BREAKOUT → PULLBACK → CONTINUATION → RANGE → HOLD
- decision_candle logs include stability, regime, routing, time-exit, and acceptance fields
- Log analytics tool writes daily summaries under `reports/daily/YYYY-MM-DD/`
- Marker: `@pytest.mark.critical`
- CI stages:
  - `pytest -m critical -v`
  - `pytest -v`
