# Updates Summary

This document summarizes the latest production-hardening updates.

## New Capabilities

### Regime-Based Multi-Strategy (BTCUSDT 5m)
- **Strategies:** CONTINUATION, BREAKOUT_EXPANSION, PULLBACK_REENTRY, RANGE_MEANREV
- **Trend stability gate:** HTF EMA200 slope norm + persistence on EMA200 side
- **Breakout acceptance:** impulse + acceptance (consecutive closes or wick reject) + optional retest
- **Adaptive RR:** TP derived from regime RR target * SL distance
- **Time-exit:** emits CLOSE intent when progress stalls
- **Regime/volatility:** `regime`, `direction`, `trend_strength`, `atr_ratio`, `volatility_state`
- **Decision candle fields:** `ema50_5m`, `rsi14_5m`, `atr14_5m`, `bb_upper/lower/mid`,
  `donchian_high_20/low_20`, `volume_ratio_5m`, `candle_body_ratio`, `pullback_atr_*`, `reclaim_*`,
  `reentry_*`, `breakout_*`, `selected_strategy`, `cont_short_ok`, `cont_long_ok`, `slope_atr`,
  `k_overextension`, `break_level`, `break_delta_atr`, `cont_reject_codes`

### TREND_CONTINUATION (5m)
- **Goal:** enter stable trends without pullbacks, with anti-bounce/chop filters
- **Setup:** EMA50 slope (ATR-normalized), Donchian break with ATR buffer, RSI guards, body/volume/ATR ratio filters
- **Risk:** SL = 1.2 * ATR14_5m, TP = RR_TARGET * SL distance, RR >= MIN_RR

### Trade Ledger (append-only)
- **Module:** `app/core/trade_ledger.py`
- **Storage:** `run/ledger/ledger_YYYY-MM-DD.jsonl`
- **Events:** `decision_created`, `trade_plan_created`, `execution_attempted`, `execution_submitted`,
  `sltp_submitted`, `position_closed`, `kill_switch_triggered`
- **Hashes:** canonical JSON serialization with sorted keys

### Central Runtime Mode
- **Module:** `core/runtime_mode.py`
- **Modes:** `TEST`, `OFFLINE`, `PAPER`, `LIVE`, `REPLAY`
- **Overrides:** `RUNTIME_MODE=` can force a mode
- **Single source of truth** for runtime behavior

### Market Data Validation
- **Module:** `app/data/market_data_validator.py`
- **Fail-closed** checks: required columns, monotonic time, NaNs, gaps, staleness, min bars
- **Used before** payload building

### SAFE_RUN (Paper Mirror)
- **Env:** `SAFE_RUN=1`
- **Behavior:** full pipeline with real data; **orders blocked**
- **Hard guard:** REST placement functions raise if SAFE_RUN is active

### LIVE_READONLY (Paper Mirror)
- **Env:** `LIVE_READONLY=1`
- **Behavior:** full pipeline with real data; **mutations blocked**
- **Hard guard:** REST placement functions raise even if SAFE_RUN/DRY_RUN_ONLY are off

### Deterministic Replay
- **CLI:** `python -m app.replay --from YYYY-MM-DD --to YYYY-MM-DD [--symbol BTCUSDT]`
- **Uses:** real pipeline, SAFE_RUN enforced, ledger + state writes
- **Summary:** holds / trade intents / blocked trades / drawdown / streaks

### CI Critical Tests
- **Marker:** `@pytest.mark.critical`
- **Stage 1:** `pytest -m critical -v`
- **Stage 2:** `pytest -v`

## Key Environment Variables

- Runtime: `ENV`, `RUNTIME_MODE`, `SAFE_RUN`, `LIVE_READONLY`, `REPLAY_MODE`
- State/Ledger: `STATE_DIR`, `LEDGER_DIR`, `LEDGER_ENABLED`
- Data validation: `MD_MIN_BARS_1H`, `MD_MIN_BARS_4H`, `MD_MAX_GAP_SECONDS`, `MD_MAX_AGE_SECONDS`
- Risk/Execution: `SPREAD_MAX_PCT`, `ATR_SPIKE_MAX_PCT`, `EXIT_REQUIRE_TP`

See `.env.example` for the full list.

## How to Run

```
pytest -m critical -v
pytest -v

# SAFE_RUN (paper mirror)
SAFE_RUN=1 TRADE_ENABLED=1 PAPER_TRADING=1 DRY_RUN_ONLY=0 python main.py

# LIVE_READONLY (paper mirror, read-only)
LIVE_READONLY=1 TRADE_ENABLED=1 PAPER_TRADING=0 DRY_RUN_ONLY=0 python main.py

# Replay
python -m app.replay --from 2024-01-01 --to 2024-03-01 --symbol BTCUSDT
```

## Logging & Reports

- **Session logs:** `logs/sessions/{YYYYMMDD_HHMMSS}_{pid}.log`
- **Clean session logs:** `logs/sessions_clean/{YYYYMMDD_HHMMSS}_{pid}.log` (filters tick skip noise, includes decision_clean)
- **Decision analytics:** `python tools/log_stats/analyze_logs.py --runtime-log logs/runtime.log --out reports`
- **Daily outputs:** `reports/daily/YYYY-MM-DD/{decisions,stats,human_logs,raw_extract}/`
