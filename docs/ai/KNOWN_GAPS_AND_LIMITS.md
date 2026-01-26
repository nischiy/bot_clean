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

## Runtime Safety (Current Limits)
- **Paper-only enforcement**: live execution is intentionally disabled; production use requires explicit enablement after validation on real data.
- **Close-only exit intent**: time-exit issues CLOSE intents only (no partial exits or SL/TP updates).
- **Single-position only**: no pyramiding or scaling; more than one open position triggers kill-switch.

## Strategy Limits (Hardcoded Thresholds)
- Continuation filters are hardcoded (not in settings):
  - `cont_slope_atr_max=-0.15`, `cont_slope_atr_min=0.15`
  - `cont_k_max=2.2`
  - `cont_rsi_min_short=30`, `cont_rsi_max_long=70`
  - `cont_body_min=0.50`, `cont_vol_min=1.0`, `cont_atr_ratio_min=0.95`

## Testing and Replay
- **RESOLVED**: deterministic replay runner exists via `python -m app.replay`.

