# Trading Bot - Production-Grade Architecture

A production-ready trading bot with strict JSON contracts, fail-closed validation, and restart safety.

## Architecture Overview

The system enforces **three JSON contracts** between modules:

1. **payload.json** → Market data, account state, features, risk policy
2. **decision.json** → Trading intent (LONG/SHORT/HOLD) - **INTENT ONLY**
3. **trade_plan.json** → Executable order instructions - **ONLY executable orders**

### Key Features

- ✅ **Fail-closed validation**: Invalid payload → HARD FAIL → HOLD
- ✅ **Intent-only decisions**: Strategy cannot place executable orders
- ✅ **Risk Manager is final authority**: Only produces trade_plan if ALL checks pass
- ✅ **Restart-safe**: State persistence prevents duplicate processing
- ✅ **Idempotent execution**: clientOrderId tracking prevents duplicate orders
- ✅ **Closed-candle gating**: Decisions made ONLY on closed 5m candles
- ✅ **UTC daily reset**: Daily state resets at UTC midnight
- ✅ **Hard SL/TP enforcement**: Missing protective orders triggers kill-switch
- ✅ **LIVE_READONLY paper mirror**: Real market + account/position snapshots with zero mutations
- ✅ **Explainable logs**: Single-line `tick_summary` + `decision_candle` with reasons

## Quick Start

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

JSON schema validation requires `jsonschema` (included in `requirements.txt`).

### Configuration

Create a `.env` file with your configuration:

```env
# Safe readonly (paper mirror) defaults
LIVE_READONLY=1
SAFE_RUN=1
DRY_RUN_ONLY=1
TRADE_ENABLED=0

SYMBOL=BTCUSDT
INTERVAL=5m
HTF_INTERVAL=1h
RISK_PER_TRADE_PCT=5.0
RISK_MAX_DD_PCT_DAY=3.0
RISK_MAX_CONSEC_LOSSES=3
MIN_RR=1.8
LEVERAGE=5
```

### Running

```bash
# Run in paper trading mode
python main.py

# Run tests
pytest tests/ -v
```

## Architecture Flow

```
Market Data → Payload Builder → Decision Engine → Risk Manager → Execution Service
                (payload.json)    (decision.json)  (trade_plan.json)   (readonly/blocked)
```

### 1. Payload Builder
- **Module:** `app.data.payload_builder`
- Builds validated `payload.json` from market data, account state, and features
- Fail-closed: Missing/NaN/stale data → HOLD

### 2. Decision Engine
- **Module:** `app.strategy.decision_engine`
- Produces validated `decision.json` with trading intent
- **Authority:** INTENT ONLY - no execution authority
- Strategy rules: HTF EMA200 trend + LTF EMA50 pullback/reclaim, RSI14, ATR-based SL/TP

### 3. Risk Manager
- **Module:** `app.risk.risk_manager`
- Produces validated `trade_plan.json` from decision
- **Authority:** FINAL AUTHORITY - only if ALL kill-switch checks pass
- Kill-switch checks: drawdown, consecutive losses, spread, ATR, stale data, etc.

### 4. Execution Service
- **Module:** `app.services.execution_service`
- Consumes ONLY `trade_plan.json`
- Idempotent: clientOrderId tracking prevents duplicates
- Process: Entry → Fill → SL/TP orders immediately

## State Management

State is persisted for restart safety:

- `run/state/candle_gate.json`: Last processed closed candle timestamp
- `run/state/daily_YYYY-MM-DD.json`: Daily risk state
- `run/state/trade_ids.json`: Trade identifiers (idempotency)

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_schema_validation.py -v
pytest tests/test_payload_builder.py -v
pytest tests/test_decision_engine.py -v
pytest tests/test_risk_manager.py -v
pytest tests/test_state_manager.py -v
```

## Runtime Logs

- Default path: `logs/runtime.log` (or `LOG_DIR/runtime.log`)
- Session logs: `LOG_DIR/sessions/{YYYYMMDD_HHMMSS}_{pid}.log`
- Clean session logs: `LOG_DIR/sessions_clean/{YYYYMMDD_HHMMSS}_{pid}.log`
- Windows tail:

```
Get-Content -Path .\logs\runtime.log -Wait
```

## Log Analytics

```
python tools/log_stats/analyze_logs.py --runtime-log logs/runtime.log --out reports
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System architecture and flow
- [Decision Flow](docs/ai/DECISION_FLOW.md) - Detailed decision flow
- [State and Side Effects](docs/ai/STATE_AND_SIDE_EFFECTS.md) - State management
- [Project Map](docs/ai/PROJECT_MAP.md) - Module responsibilities
- [Implementation Summary](IMPLEMENTATION_SUMMARY.md) - Implementation details

## Non-Negotiable Rules

1. **Modules communicate ONLY via validated JSON objects**
2. **Exactly three contracts exist** - payload.json, decision.json, trade_plan.json
3. **Fail-closed validation** - Invalid data → HOLD/STOP
4. **Decisions made ONLY on CLOSED 5m candles (HTF=1h trend filter)**
5. **Risk Manager is FINAL AUTHORITY**
6. **Execution accepts ONLY trade_plan.json**
7. **Runtime ordering is strict (reconcile → exits → kill → preflight → data)**
8. **System is restart-safe and idempotent**

## License

[Your License Here]
