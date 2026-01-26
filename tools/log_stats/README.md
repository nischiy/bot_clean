# Log Stats Tools

Generate decision-only logs and near-miss analytics from `logs/runtime.log` and session logs.

## Usage

runtime.log:
```
python tools/log_stats/analyze_logs.py --runtime-log logs/runtime.log --out reports
```

sessions logs:
```
python tools/log_stats/analyze_logs.py --sessions-glob "logs/sessions/*.log" --out reports
```

optional ledger:
```
python tools/log_stats/analyze_logs.py --runtime-log logs/runtime.log --ledger-glob "run/ledger/*.jsonl" --out reports
```

## Outputs

When run without `--only-decisions`, the script writes under `reports/daily/YYYY-MM-DD/`:

- `decisions/decisions.jsonl` (only `decision_candle` events)
- `decisions/decisions.csv`
- `human_logs/human.log`
- `stats/summary.md`
- `stats/decision_counts.csv`
- `stats/strategy_counts.csv`
- `stats/reject_reasons_top.csv`
- `stats/cont_reject_codes_top.csv`
- `stats/near_miss_1_condition.csv`
- `stats/near_miss_2_conditions.csv`

With `--only-decisions`, only `decisions/decisions.jsonl` is created.

When multiple inputs are provided, outputs are written under:
`reports/daily/YYYY-MM-DD/per_file/<input_basename>/`.
