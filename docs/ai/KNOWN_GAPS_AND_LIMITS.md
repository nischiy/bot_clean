# Known Gaps and Limits

## State Persistence
- **RESOLVED**: `last_closed_candle_ts` is persisted via `run/state/candle_gate.json` (restart-safe gating).
- **RESOLVED**: daily state persistence exists via `run/state/daily_YYYY-MM-DD.json`.
- **RESOLVED**: trade ledger persistence exists via `run/ledger/ledger_YYYY-MM-DD.jsonl`.

## Schema and Validation
- **RESOLVED**: JSON schemas enforced for `payload.json`, `decision.json`, `trade_plan.json` via `app/core/validation.py`.
- **RESOLVED**: market data validation layer exists via `app/data/market_data_validator.py`.

## Risk Management
- **RESOLVED**: daily state persistence and UTC day rollover reset in `app.state.state_manager`.

## Missing Dependencies
- **RESOLVED**: account snapshots use REST; no `python-binance` dependency required.

## Missing Modules
- **RESOLVED**: `app.run._try_get_main()` no longer imports missing modules.

## Runtime Mode
- **RESOLVED**: runtime mode is centralized in `core/runtime_mode.py`.

## Execution Architecture
- **RESOLVED**: execution is unified in `app.services.execution_service` (trade_plan-only).
- **RESOLVED**: exit enforcement is always applied via `state_manager.reconcile_positions()`; missing SL/TP triggers kill-switch.
- **RESOLVED**: trade_plan supports CLOSE action for time-exit intent.
- **RESOLVED**: trade_plan supports UPDATE_SLTP action for trailing SL after TP1 fill.

## Runtime Safety (Current Limits)
- **Paper-only enforcement**: live execution is intentionally disabled; production use requires explicit enablement after validation on real data.
- **TP1 fill detection**: Current implementation uses simplified check (`run.py:524-543`): if `move_sl_to_be_after_tp1=True` and TP2 still open, assumes TP1 filled. Full implementation would track original position qty and TP1 qty when trade executes, then compare current position qty with expected qty after TP1 fill to detect exact fill.
- **Single-position only**: no pyramiding or scaling; more than one open position triggers kill-switch (`risk_manager.py:58-60`).
- **No partial exits via decision**: time-exit issues CLOSE intents only (full position close); partial exits handled via TP1/TP2 split at trade_plan level.

## Strategy Limits (Hardcoded Thresholds)

These thresholds are hardcoded in `decision_engine.py` and NOT configurable via environment variables:

### Continuation Strategy Filters (`decision_engine.py:909-916`)
- `cont_slope_atr_max = -0.15` — max EMA50 slope (ATR-normalized) for SHORT continuation
- `cont_slope_atr_min = 0.15` — min EMA50 slope (ATR-normalized) for LONG continuation
- `cont_k_max = 2.2` — max overextension proxy (dist50 * k) for continuation
- `cont_rsi_min_short = 30.0` — min RSI14 for SHORT continuation
- `cont_rsi_max_long = 70.0` — max RSI14 for LONG continuation
- `cont_body_min = 0.50` — min candle body ratio for continuation
- `cont_vol_min = 1.0` — min volume ratio for continuation
- `cont_atr_ratio_min = 0.95` — min ATR ratio for continuation

### Stability Scoring Weights (`decision_engine.py:93`)
- Formula: `score = 0.55 * R + 0.25 * (1 - W) + 0.20 * (1 - X)`
- Weights are hardcoded: R=0.55, W=0.25, X=0.20 (not configurable)

### Trend Strength Threshold (`decision_engine.py:641`)
- `trend_strength_min = 0.6` — hardcoded threshold for TREND vs RANGE classification
- Formula: `trend_strength = abs(close_htf - ema200_htf) / atr14_htf`

### Volatility State Threshold (`decision_engine.py:675`)
- `atr_ratio >= 1.3` → "VOL_EXPANSION", else "NORMAL" (hardcoded)

### Pullback Threshold (`decision_engine.py:416,424`)
- Pullback detection: `pullback_atr <= 0.8` (hardcoded)

### BB Width Expansion (`decision_engine.py:370`)
- `bb_width_atr >= bb_width_prev * 1.2` → volatility expansion (hardcoded multiplier)

### TP1 RR Fraction (`decision_engine.py:1415`)
- `tp1_rr = rr_target * 0.6` — TP1 at 60% of target RR (hardcoded, not configurable)

### Other Hardcoded Values (`decision_engine.py:1834-1858`)
- `sl_atr_cont = 1.2`, `tp_atr_cont = 1.8` — continuation SL/TP (hardcoded, not used in current flow)
- `trend_rsi_long_max = 45`, `trend_rsi_short_min = 55` — trend RSI limits (hardcoded)
- `range_rsi_long_max = 35`, `range_rsi_short_min = 65` — range RSI limits (hardcoded)
- `extreme_rsi_long_min = 82`, `extreme_rsi_short_max = 18` — extreme RSI limits (hardcoded)
- `pullback_atr_max = 1.0` — max pullback depth (hardcoded)
- `breakout_volume_min = 1.8`, `breakout_body_min = 0.65`, `breakout_atr_ratio_min = 1.2`, `breakout_chop_min = 1.1` — breakout thresholds (hardcoded)
- `sl_atr_trend = 1.6`, `tp_atr_trend = 2.6` — trend SL/TP (hardcoded, not used)
- `sl_atr_breakout = 1.0`, `tp_atr_breakout = 1.8` — breakout SL/TP (hardcoded, not used)
- `sl_atr_range = 1.2`, `tp_atr_range = 1.8` — range SL/TP (hardcoded, not used)
- `bb_period = 20`, `bb_std = 2.0` — Bollinger Bands parameters (hardcoded)
- `ema_period = 50`, `atr_period = 14`, `donchian_n = 20` — indicator periods (hardcoded)

**Note**: Many hardcoded values in the `thresholds` dict (`decision_engine.py:1825-1858`) are logged but not actively used in the current decision flow. The actual decision logic uses configurable thresholds from environment variables.

## Testing and Replay
- **RESOLVED**: deterministic replay runner exists via `python -m app.replay`.
