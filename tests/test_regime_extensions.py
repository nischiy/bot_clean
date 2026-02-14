from __future__ import annotations

import copy

import pytest

from app.strategy import decision_engine as de


def _base_payload_short() -> dict:
    payload = {
        "market_identity": {"timestamp_closed": 1},
        "price_snapshot": {"last": 98.0, "mark": 98.0, "bid": 97.9, "ask": 98.1},
        "features_ltf": {
            "open": 100.0,
            "open_prev": 100.0,
            "close": 98.0,
            "close_prev": 102.0,
            "high": 102.0,
            "low": 96.0,
            "high_prev": 101.0,
            "low_prev": 90.0,
            "ema50": 105.0,
            "ema50_prev_12": 110.0,
            "ema120": 110.0,
            "donchian_high_240": 140.0,
            "donchian_low_240": 90.0,
            "donchian_high_20": 120.0,
            "donchian_low_20": 100.0,
            "donchian_high_k": 120.0,
            "donchian_low_k": 100.0,
            "atr14": 10.0,
            "atr14_sma20": 10.0,
            "bb_upper": 130.0,
            "bb_lower": 80.0,
            "bb_mid": 105.0,
            "bb_width": 200.0,
            "bb_width_prev": 210.0,
            "volume_ratio": 1.1,
            "volume": 1000.0,
            "volume_prev": 900.0,
            "candle_body_ratio": 0.6,
            "rsi14": 45.0,
            "rsi14_prev": 45.0,
            "consec_close_above_donchian_20": 0,
            "consec_close_below_donchian_20": 2,
            "consec_above_ema50": 0,
            "consec_below_ema50": 2,
            "consec_above_ema50_prev": 0,
            "consec_below_ema50_prev": 2,
            "close_max_n": 102.0,
            "close_min_n": 96.0,
            "time_exit_bars": 12,
            "stability_n": 20,
            "trend_candles_below_ema50": 20,
            "trend_candles_above_ema50": 0,
            "wick_ratio_count": 0,
            "swing_high_m": 110.0,
            "swing_low_m": 90.0,
        },
        "context_htf": {
            "ema200": 1000.0,
            "ema_fast": 980.0,
            "close": 900.0,
            "atr14": 100.0,
            "trend": "down",
            "timeframe": "1h",
            "ema200_prev_n": 1100.0,
            "ema200_slope_norm": 0.06,
            "consec_below_ema200": 5,
            "consec_lower_close": 5,
            "consec_above_ema200": 0,
            "consec_higher_close": 0,
            "rsi14": 45.0,
            "rsi14_prev": 45.0,
        },
        "risk_policy": {"min_rr": 1.0},
        "position_state": {"side": None, "qty": 0.0},
    }
    return payload


def test_stability_score_soft_band():
    score, stable_ok, stable_soft, stable_block, reason, metrics = de._compute_stability(
        direction="UP",
        stability_n=20,
        trend_candles_below=0,
        trend_candles_above=14,
        wick_ratio_count=4,
        dist50=1.0,
    )
    assert reason == ""
    assert stable_soft is True
    assert stable_ok is False
    assert stable_block is False
    assert score == pytest.approx(0.674, abs=0.01)
    assert metrics["R"] == pytest.approx(0.7)


def test_confirmation_two_bar_continuation_short():
    ctype, metrics, ok = de._continuation_confirmation(
        direction="DOWN",
        close_ltf=90.0,
        open_ltf=100.0,
        high_ltf=102.0,
        low_ltf=89.0,
        close_prev=92.0,
        open_prev=100.0,
        high_prev=101.0,
        low_prev=90.0,
        atr14=10.0,
        ema50_ltf=100.0,
        bb_mid=100.0,
        volume_ratio=1.2,
        swing_high_m=110.0,
        swing_low_m=80.0,
        donchian_high_k=110.0,
        donchian_low_k=95.0,
    )
    assert ok is True
    assert ctype == "TWO_BAR_CONTINUATION"
    assert metrics.get("body_ratio_last") is not None


def test_confirmation_retest_reject_short():
    ctype, _, ok = de._continuation_confirmation(
        direction="DOWN",
        close_ltf=99.0,
        open_ltf=100.2,
        high_ltf=100.2,
        low_ltf=98.0,
        close_prev=99.5,
        open_prev=100.0,
        high_prev=101.0,
        low_prev=98.0,
        atr14=10.0,
        ema50_ltf=100.0,
        bb_mid=100.0,
        volume_ratio=1.0,
        swing_high_m=110.0,
        swing_low_m=80.0,
        donchian_high_k=110.0,
        donchian_low_k=95.0,
    )
    assert ok is True
    assert ctype == "RETEST_REJECT"


def test_confirmation_lower_high_break_short():
    ctype, _, ok = de._continuation_confirmation(
        direction="DOWN",
        close_ltf=95.0,
        open_ltf=100.0,
        high_ltf=105.0,
        low_ltf=94.0,
        close_prev=100.0,
        open_prev=100.0,
        high_prev=101.0,
        low_prev=98.0,
        atr14=10.0,
        ema50_ltf=120.0,
        bb_mid=120.0,
        volume_ratio=1.0,
        swing_high_m=110.0,
        swing_low_m=90.0,
        donchian_high_k=120.0,
        donchian_low_k=100.0,
    )
    assert ok is True
    assert ctype == "LOWER_HIGH_BREAK"


def test_anti_reversal_blocks_on_htf_ema_reclaim():
    blocked, reason = de._anti_reversal_filter(
        entry_side="SHORT",
        close_htf=105.0,
        ema_fast_htf=100.0,
        rsi_htf=None,
        rsi_htf_prev=None,
        wick_ratio_ltf=1.0,
    )
    assert blocked is True
    assert reason == "HTF_EMA_RECLAIM"


def test_anti_reversal_blocks_on_rsi_slope():
    blocked, reason = de._anti_reversal_filter(
        entry_side="SHORT",
        close_htf=None,
        ema_fast_htf=None,
        rsi_htf=55.0,
        rsi_htf_prev=50.0,
        wick_ratio_ltf=3.0,
    )
    assert blocked is True
    assert reason == "HTF_RSI_SLOPE"


def test_event_cooldown_blocks_entries():
    payload = _base_payload_short()
    payload["market_identity"]["timestamp_closed"] = 1000
    payload["features_ltf"]["high"] = 140.0
    payload["features_ltf"]["low"] = 80.0
    payload["features_ltf"]["close_prev"] = 100.0
    decision = de.make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "E:event" in decision["reject_reasons"]

    state_update = decision.get("state_update") or {}
    payload_next = copy.deepcopy(payload)
    payload_next["market_identity"]["timestamp_closed"] = 1300
    payload_next["features_ltf"]["high"] = 102.0
    payload_next["features_ltf"]["low"] = 96.0
    payload_next["features_ltf"]["close_prev"] = 99.0
    decision_next = de.make_decision(payload_next, decision_state=state_update)
    assert decision_next["intent"] == "HOLD"
    assert any(r.startswith("E:event_cooldown") for r in decision_next["reject_reasons"])


def test_pending_entry_idempotent_and_confirmed():
    payload = _base_payload_short()
    payload["market_identity"]["timestamp_closed"] = 2000
    payload["features_ltf"]["trend_candles_below_ema50"] = 12
    payload["features_ltf"]["wick_ratio_count"] = 4
    payload["features_ltf"]["open"] = 108.0
    payload["features_ltf"]["close"] = 98.0
    payload["features_ltf"]["high"] = 110.0
    payload["features_ltf"]["low"] = 97.0
    payload["features_ltf"]["open_prev"] = 108.0
    payload["features_ltf"]["close_prev"] = 96.0
    payload["features_ltf"]["high_prev"] = 109.0
    payload["features_ltf"]["low_prev"] = 95.0
    decision = de.make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert decision.get("signal", {}).get("pending_entry_status") == "SET"

    decision_repeat = de.make_decision(payload, decision_state=decision.get("state_update"))
    assert decision_repeat["intent"] == "HOLD"
    assert decision_repeat.get("signal", {}).get("pending_entry_status") == "SET"

    payload_next = copy.deepcopy(payload)
    payload_next["market_identity"]["timestamp_closed"] = 2300
    decision_next = de.make_decision(payload_next, decision_state=decision.get("state_update"))
    assert decision_next["intent"] == "SHORT"
    assert decision_next.get("signal", {}).get("pending_entry_status") == "CONFIRMED"


def test_ev_gate_default_off_allows_trade():
    payload = _base_payload_short()
    payload["market_identity"]["timestamp_closed"] = 3000
    decision = de.make_decision(payload)
    assert decision.get("signal", {}).get("ev_gate_enabled") is False
    assert decision["intent"] == "SHORT"


def test_ev_gate_blocks_low_ev(monkeypatch):
    monkeypatch.setenv("EV_GATE_ENABLED", "1")
    monkeypatch.setenv("EV_TP_R", "0.5")
    monkeypatch.setenv("EV_SL_R", "2.0")
    payload = _base_payload_short()
    payload["market_identity"]["timestamp_closed"] = 3100
    decision = de.make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "EV:low" in decision["reject_reasons"]


def test_event_regime_priority():
    regime = de.compute_regime_5m({
        "close_ltf": 100.0,
        "ema50_ltf": 105.0,
        "atr14": 10.0,
        "atr_ratio": 1.2,
        "donchian_high_20": 120.0,
        "donchian_low_20": 90.0,
        "volume_ratio": 2.0,
        "candle_body_ratio": 0.7,
        "trend": "down",
        "cont_body_min": 0.5,
        "cont_vol_min": 1.0,
        "breakout_vol_min": 1.2,
        "compression_width_atr_max": 2.0,
        "compression_vol_max": 1.0,
        "trend_dist50_max": 1.0,
        "bb_width_atr": 1.0,
        "bb_width_prev": 1.1,
        "squeeze_bb_width_th": 1.2,
        "trend_accel_vol_mult": 1.4,
        "squeeze_break_long": False,
        "squeeze_break_short": True,
        "event_detected": True,
    })
    assert regime == "EVENT"
