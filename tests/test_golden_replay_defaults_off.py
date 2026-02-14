"""
Golden replay test: behavior unchanged when new flags are OFF.

With ADAPTIVE_SOFT_STABILITY_ENABLED=0, RANGE_IN_TREND_ENABLED=0, PULLBACK_RECLAIM_TOL_ABS=0
(and REAL_MARKET_TUNING=0 so reclaim tolerance is not applied), decision signatures must match
a committed baseline. Ensures no behavior change by default.

- Run: pytest tests/test_golden_replay_defaults_off.py -v
- Regenerate baseline (after intentional logic change): UPDATE_GOLDEN=1 pytest tests/test_golden_replay_defaults_off.py -v
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from app.strategy.decision_engine import make_decision

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BASELINES_DIR = FIXTURES_DIR / "baselines"
GOLDEN_BARS_PATH = FIXTURES_DIR / "golden_bars.json"
BASELINE_PATH = BASELINES_DIR / "golden_signatures_defaults_off.json"


def _base_payload():
    """Full valid payload (mirrors valid_payload_long) for merging bar overrides."""
    payload = {
        "market_identity": {"timestamp_closed": 0},
        "price_snapshot": {"last": 1100.0, "mark": 1100.0, "bid": 1099.0, "ask": 1101.0},
        "features_ltf": {
            "close": 1100.0,
            "close_prev": 990.0,
            "high": 1110.0,
            "low": 980.0,
            "ema50": 1000.0,
            "ema120": 1000.0,
            "donchian_high_240": 1080.0,
            "donchian_low_240": 900.0,
            "donchian_high_20": 1080.0,
            "donchian_low_20": 950.0,
            "consec_close_above_donchian_20": 2,
            "consec_close_below_donchian_20": 0,
            "atr14": 100.0,
            "atr14_sma20": 90.0,
            "bb_upper": 1200.0,
            "bb_lower": 900.0,
            "bb_mid": 1050.0,
            "volume_ratio": 1.5,
            "candle_body_ratio": 0.7,
            "rsi14": 35.0,
            "rsi14_prev": 35.0,
            "consec_above_ema50": 2,
            "consec_below_ema50": 0,
            "consec_above_ema50_prev": 1,
            "consec_below_ema50_prev": 2,
            "close_max_n": 1120.0,
            "close_min_n": 980.0,
            "time_exit_bars": 12,
            "stability_n": 20,
            "trend_candles_below_ema50": 20,
            "trend_candles_above_ema50": 20,
            "wick_ratio_count": 0,
            "ema50_prev_12": 995.0,
            "donchian_high_k": 1080.0,
            "donchian_low_k": 950.0,
            "bb_width": 200.0,
            "bb_width_prev": 210.0,
            "open": 1100.0,
            "open_prev": 990.0,
            "high_prev": 1090.0,
            "low_prev": 985.0,
            "volume": 1000.0,
            "volume_prev": 1000.0,
            "swing_high_m": 1110.0,
            "swing_low_m": 980.0,
        },
        "context_htf": {
            "ema200": 950.0,
            "close": 1010.0,
            "atr14": 100.0,
            "trend": "up",
            "timeframe": "1h",
            "ema200_prev_n": 900.0,
            "ema200_slope_norm": 0.06,
            "consec_above_ema200": 5,
            "consec_below_ema200": 0,
            "consec_higher_close": 5,
            "consec_lower_close": 0,
            "ema_fast": 1000.0,
            "rsi14": 50.0,
            "rsi14_prev": 50.0,
        },
        "risk_policy": {"min_rr": 1.5},
        "position_state": {"side": None, "qty": 0.0},
    }
    return payload


def _bar_to_overrides(bar: dict) -> dict:
    """Map golden_bars entry to features_ltf and context_htf overrides."""
    return {
        "features_ltf": {
            "close": float(bar["close"]),
            "ema50": float(bar["ema50"]),
            "atr14": float(bar["atr14"]),
            "donchian_high_20": float(bar["d_high"]),
            "donchian_low_20": float(bar["d_low"]),
            "volume_ratio": float(bar["vol_ratio"]),
            "rsi14": float(bar["rsi14"]),
        },
        "context_htf": {"trend": bar["trend"]},
    }


def _signature(decision: dict) -> dict:
    """Compact decision signature for baseline comparison."""
    signal = decision.get("signal") or {}
    reject_reasons = decision.get("reject_reasons") or []
    return {
        "decision": decision.get("intent", "HOLD"),
        "selected_strategy": signal.get("selected_strategy", "NONE"),
        "main_blocker": reject_reasons[0] if reject_reasons else None,
        "strategy_block_reason": signal.get("strategy_block_reason"),
        "regime_detected": signal.get("regime_detected"),
    }


def _run_golden_replay(monkeypatch) -> list:
    """Force defaults OFF, run decision engine bar-by-bar, return list of signatures."""
    monkeypatch.setenv("ADAPTIVE_SOFT_STABILITY_ENABLED", "0")
    monkeypatch.setenv("RANGE_IN_TREND_ENABLED", "0")
    monkeypatch.setenv("PULLBACK_RECLAIM_TOL_ABS", "0")
    monkeypatch.setenv("REAL_MARKET_TUNING", "0")
    with open(GOLDEN_BARS_PATH) as f:
        bars = json.load(f)
    base = _base_payload()
    signatures = []
    for i, bar in enumerate(bars):
        payload = copy.deepcopy(base)
        payload["market_identity"] = {"timestamp_closed": 1700000000 + i * 300}
        overrides = _bar_to_overrides(bar)
        for key, val in overrides.get("features_ltf", {}).items():
            payload["features_ltf"][key] = val
        for key, val in overrides.get("context_htf", {}).items():
            payload["context_htf"][key] = val
        payload["price_snapshot"]["last"] = payload["features_ltf"]["close"]
        payload["price_snapshot"]["mark"] = payload["features_ltf"]["close"]
        payload["price_snapshot"]["bid"] = payload["features_ltf"]["close"] - 1
        payload["price_snapshot"]["ask"] = payload["features_ltf"]["close"] + 1
        decision = make_decision(payload)
        signatures.append(_signature(decision))
    return signatures


def test_golden_replay_signatures_match_baseline_when_defaults_off(monkeypatch):
    """With new flags OFF, decision signatures must exactly match committed baseline."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    signatures = _run_golden_replay(monkeypatch)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        with open(BASELINE_PATH, "w") as f:
            json.dump(signatures, f, indent=2)
        pytest.skip("UPDATE_GOLDEN=1: baseline written; run without it to compare")
    assert BASELINE_PATH.exists(), "Baseline missing; run with UPDATE_GOLDEN=1 to create"
    with open(BASELINE_PATH) as f:
        baseline = json.load(f)
    assert len(signatures) == len(baseline), (
        f"Signature count mismatch: got {len(signatures)}, baseline {len(baseline)}"
    )
    for i, (got, expected) in enumerate(zip(signatures, baseline)):
        assert got == expected, f"Bar {i}: got {got} != expected {expected}"
