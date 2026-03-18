# Log Diagnostics

`app.analysis.log_diagnostics` is a predictive-first runtime log analyzer for trading decisions. It parses mixed runtime logs, merges `decision_reject` + `decision_candle` + `decision_clean` into one row per candle, backfills finalized analytics labels to the original candle, and emits a compact diagnostic report for strategy/debug review.

## Run

Analyze the live runtime log:

```bash
python -m app.analysis.log_diagnostics --log logs/runtime.log --last 500
```

Analyze a whole logs directory, including archived session logs:

```bash
python -m app.analysis.log_diagnostics --log logs --last 800 --recent 50 --csv-out diagnostics --json-out diagnostics/summary.json
```

Wrapper script:

```bash
python scripts/analyze_logs.py --log logs/runtime.log --only-predictive --since-ts 1773800000
```

Supported filters:

- `--log`: file, glob, or logs directory; repeatable
- `--last N`: keep only the last `N` merged decision candles
- `--since-ts UNIX_TS`: keep only candles at or after a Unix timestamp
- `--csv-out DIR`: export parsed candle rows and missed opportunities to CSV
- `--json-out FILE`: export summary/tables as JSON
- `--only-holds`: analyze only `HOLD` / `HOLD_*` rows
- `--only-predictive`: analyze only directional predictive rows
- `--recent N`: size of the recent-cases table

## Read The Report

- **Overview summary** shows the sample size, predictive coverage, hold/open split, confirmation quality, finalized-label coverage, and the main blocker-family counts.
- **Blocker frequency** shows which gates dominate overall, inside `HOLD` rows, and inside directional predictive rows.
- **Predictive state / transition tables** show whether the new state machine is early, useful, or still firing after the move.
- **Missed opportunity / late detection / validator conflict** isolate the most actionable architectural problems: profitable directional calls that never traded, calls that were already late, and cases where legacy validators suppressed a good predictive hypothesis.
- **Event analysis** separates chaotic event blocks from directional event cases that may deserve more selective execution logic.
