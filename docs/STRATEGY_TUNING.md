# Strategy Real-Market Tuning

## Overview

`REAL_MARKET_TUNING=1` relaxes empirically dominant blockers that prevent entries under real-market conditions. When `REAL_MARKET_TUNING=0` (default), behavior is identical to the original strict settings.

## What REAL_MARKET_TUNING Does

When enabled, the following thresholds are relaxed:

| Setting | Default | REAL value |
|---------|---------|------------|
| PULLBACK_REENTRY_DIST50_MAX | 1.5 | 2.5 |
| PULLBACK_REENTRY_VOL_MIN | 1.0 | 0.70 |
| CONT_VOL_MIN | 1.0 | 0.70 |
| STABILITY_SOFT | 0.58 | 0.50 |
| STABILITY_HARD | 0.70 | 0.65 |
| HTF_EMA_RECLAIM_ATR_BUFFER | 0 | 0.10 |
| PULLBACK_RECLAIM_TOL_ATR | 0 | 0.10 |

- **dist50 / vol / stability**: Aligns with observed medians (e.g. dist50 ~2.14, volume_ratio ~0.70, stability_score ~0.355 in logs).
- **EMA reclaim buffer**: Adds hysteresis so small HTF deviations from the fast EMA do not block entries.
- **Pullback reclaim tolerance**: Allows reclaim to pass when price is within `PULLBACK_RECLAIM_TOL_ATR * atr14` of EMA50.

## Why These Thresholds

Based on `tools/log_stats/parse_decision_candles.py` output:

- dist50 median ~2.14 vs PULLBACK_REENTRY_DIST50_MAX=1.5
- volume_ratio median ~0.70 vs PULLBACK_REENTRY_VOL_MIN=1.0
- stability_score median ~0.355 vs STABILITY_SOFT=0.58

The REAL values are chosen to allow entries around typical market values while still filtering weak setups.

## Safe Rollout

1. **Paper mirror first**: Enable `REAL_MARKET_TUNING=1` with `PAPER_TRADING=1` and `TRADE_ENABLED=0`.
2. **Monitor decision_clean**: Track decision frequency and reject distribution.
3. **Watch reject codes**: Ensure P:dist50, P:vol, P:stability_block decrease; no new dominant blockers.
4. **Gradual enable**: Only consider live trading after extended paper validation.

## Environment Variables

```bash
REAL_MARKET_TUNING=1
PULLBACK_REENTRY_DIST50_MAX_REAL=2.5
PULLBACK_REENTRY_VOL_MIN_REAL=0.70
CONT_VOL_MIN_REAL=0.70
STABILITY_SOFT_REAL=0.50
STABILITY_HARD_REAL=0.65
HTF_EMA_RECLAIM_ATR_BUFFER=0.10
PULLBACK_RECLAIM_TOL_ATR=0.10
```
