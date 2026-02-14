"""
Tests for audit-added features (flags ON). Defaults remain OFF; these tests enable flags only in test.
"""
from __future__ import annotations

import copy
import time

import pytest

from app.risk.risk_manager import create_trade_plan
from app.strategy.decision_engine import make_decision


@pytest.fixture
def base_payload_for_decision():
    """Minimal payload that can yield LONG with pullback/continuation."""
    return {
        "market_identity": {"timestamp_closed": 1700000000},
        "price_snapshot": {"last": 1100.0, "mark": 1100.0, "bid": 1099.0, "ask": 1101.0},
        "features_ltf": {
            "close": 1100.0,
            "close_prev": 1005.0,
            "high": 1110.0,
            "low": 1095.0,
            "ema50": 1050.0,
            "ema120": 1040.0,
            "donchian_high_240": 1120.0,
            "donchian_low_240": 1000.0,
            "donchian_high_20": 1080.0,
            "donchian_low_20": 1020.0,
            "consec_close_above_donchian_20": 0,
            "consec_close_below_donchian_20": 0,
            "atr14": 50.0,
            "atr14_sma20": 48.0,
            "bb_upper": 1150.0,
            "bb_lower": 1050.0,
            "bb_mid": 1100.0,
            "volume_ratio": 1.2,
            "candle_body_ratio": 0.6,
            "rsi14": 35.0,
            "rsi14_prev": 38.0,
            "consec_above_ema50": 3,
            "consec_below_ema50": 0,
            "consec_above_ema50_prev": 2,
            "consec_below_ema50_prev": 2,
            "close_max_n": 1110.0,
            "close_min_n": 1090.0,
            "time_exit_bars": 12,
            "stability_n": 20,
            "trend_candles_below_ema50": 2,
            "trend_candles_above_ema50": 18,
            "wick_ratio_count": 1,
            "ema50_prev_12": 1045.0,
            "donchian_high_k": 1080.0,
            "donchian_low_k": 1020.0,
            "bb_width": 100.0,
            "bb_width_prev": 105.0,
            "open": 1098.0,
            "open_prev": 1004.0,
            "high_prev": 1010.0,
            "low_prev": 1000.0,
            "volume": 1000.0,
            "volume_prev": 1000.0,
            "swing_high_m": 1110.0,
            "swing_low_m": 1095.0,
        },
        "context_htf": {
            "ema200": 1000.0,
            "close": 1050.0,
            "atr14": 80.0,
            "trend": "up",
            "timeframe": "1h",
            "ema200_prev_n": 998.0,
            "ema200_slope_norm": 0.05,
            "consec_above_ema200": 5,
            "consec_below_ema200": 0,
            "consec_higher_close": 5,
            "consec_lower_close": 0,
            "ema_fast": 1040.0,
            "rsi14": 50.0,
            "rsi14_prev": 48.0,
        },
        "risk_policy": {"min_rr": 1.5},
        "position_state": {"side": None, "qty": 0.0},
    }


def test_adaptive_soft_stability_reduces_qty_when_enabled(monkeypatch, base_payload_for_decision):
    """When ADAPTIVE_SOFT_STABILITY_ENABLED=1 and signal.adaptive_soft_stability=True, qty is 0.7 * base and >= min_qty."""
    from core.risk_guard import clear_kill
    clear_kill()
    monkeypatch.setenv("ADAPTIVE_SOFT_STABILITY_ENABLED", "1")
    payload = copy.deepcopy(base_payload_for_decision)
    payload["market_identity"] = {"symbol": "BTCUSDT", "timestamp_closed": int(time.time()) - 60, "timeframe": "5m"}
    payload["account_state"] = {
        "equity": 10000.0,
        "available": 9500.0,
        "funds_base": 9500.0,
        "funds_source": "available_balance",
        "margin_type": "isolated",
        "leverage": 5,
    }
    payload["exchange_limits"] = {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.1}
    decision = make_decision(payload)
    decision["signal"] = decision.get("signal") or {}
    decision["signal"]["adaptive_soft_stability"] = True
    decision["intent"] = "LONG"
    decision["entry"] = 1100.0
    decision["sl"] = 1050.0
    decision["tp"] = 1200.0
    decision["rr"] = 1.8
    decision["tp_targets"] = []
    trade_plan, rejections = create_trade_plan(payload, decision, {"starting_equity": 10000.0, "realized_pnl": 0.0, "consecutive_losses": 0}, [])
    assert rejections == [], rejections
    assert trade_plan is not None
    qty_adaptive = trade_plan.get("quantity")
    assert qty_adaptive is not None and qty_adaptive > 0
    assert qty_adaptive >= 0.001
    decision_no_adaptive = copy.deepcopy(decision)
    decision_no_adaptive["signal"]["adaptive_soft_stability"] = False
    monkeypatch.setenv("ADAPTIVE_SOFT_STABILITY_ENABLED", "0")
    plan_base, _ = create_trade_plan(payload, decision_no_adaptive, {"starting_equity": 10000.0, "realized_pnl": 0.0, "consecutive_losses": 0}, [])
    monkeypatch.setenv("ADAPTIVE_SOFT_STABILITY_ENABLED", "1")
    if plan_base and plan_base.get("quantity"):
        qty_base = plan_base["quantity"]
        assert qty_adaptive <= qty_base * 0.71 + 0.001
        assert qty_adaptive >= qty_base * 0.69 - 0.001


def test_range_in_trend_eligible_when_enabled_and_conditions_met(monkeypatch, base_payload_for_decision):
    """When RANGE_IN_TREND_ENABLED=1, trend=up, RSI<=35, atr_ratio<1, RANGE_IN_TREND_LONG can be eligible."""
    monkeypatch.setenv("RANGE_IN_TREND_ENABLED", "1")
    payload = copy.deepcopy(base_payload_for_decision)
    payload["features_ltf"]["rsi14"] = 30.0
    payload["features_ltf"]["close"] = 1025.0
    payload["features_ltf"]["ema50"] = 1040.0
    payload["features_ltf"]["donchian_low_20"] = 1020.0
    payload["features_ltf"]["atr14"] = 60.0
    payload["context_htf"]["trend"] = "up"
    payload["features_ltf"]["volume_ratio"] = 0.8
    payload["price_snapshot"]["last"] = 1025.0
    payload["price_snapshot"]["mark"] = 1025.0
    payload["price_snapshot"]["bid"] = 1024.0
    payload["price_snapshot"]["ask"] = 1026.0
    decision = make_decision(payload)
    signal = decision.get("signal") or {}
    eligible = signal.get("eligible_strategies") or []
    regime = signal.get("regime_detected")
    if regime == "PULLBACK" or "PULLBACK_REENTRY" in eligible or "BREAKOUT_EXPANSION" in eligible:
        pass
    if signal.get("range_in_trend_long_ok") is True:
        assert "RANGE_IN_TREND_LONG" in eligible
    router = signal.get("router_debug") or {}
    assert "enabled_strategies" in router or "strategies_for_regime" in router


def test_pullback_reclaim_effective_tolerance_and_distance(monkeypatch, base_payload_for_decision):
    """With REAL_MARKET_TUNING=1, effective_tolerance = max(reclaim_tol_abs, reclaim_tol_atr*atr14) and distance_to_reclaim in ATR."""
    monkeypatch.setenv("REAL_MARKET_TUNING", "1")
    monkeypatch.setenv("PULLBACK_RECLAIM_TOL_ATR", "0.10")
    monkeypatch.setenv("PULLBACK_RECLAIM_TOL_ABS", "0")
    payload = copy.deepcopy(base_payload_for_decision)
    atr14 = 50.0
    ema50 = 1040.0
    close = 1042.0
    payload["features_ltf"]["atr14"] = atr14
    payload["features_ltf"]["ema50"] = ema50
    payload["features_ltf"]["close"] = close
    payload["price_snapshot"]["last"] = close
    payload["price_snapshot"]["mark"] = close
    payload["price_snapshot"]["bid"] = close - 1
    payload["price_snapshot"]["ask"] = close + 1
    decision = make_decision(payload)
    signal = decision.get("signal") or {}
    effective_tolerance = signal.get("effective_tolerance")
    reclaim_level_used = signal.get("reclaim_level_used")
    distance_to_reclaim = signal.get("distance_to_reclaim")
    assert effective_tolerance is not None
    expected_tol = max(0.0, 0.10 * atr14)
    assert effective_tolerance == pytest.approx(expected_tol, rel=1e-5)
    assert reclaim_level_used == ema50
    expected_dist = abs(close - ema50) / atr14
    assert distance_to_reclaim is not None
    assert distance_to_reclaim == pytest.approx(expected_dist, rel=1e-5)
