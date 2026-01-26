"""
Tests for decision engine.
"""
import copy
import numbers

import pytest
from app import run
from app.strategy.decision_engine import make_decision

try:
    import jsonschema  # noqa: F401
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False


def _ensure_required_fields(payload):
    features = payload.setdefault("features_ltf", {})
    context = payload.setdefault("context_htf", {})
    close = features.get("close", 0.0) or 0.0
    features.setdefault("high", close)
    features.setdefault("low", close)
    features.setdefault("consec_close_above_donchian_20", 0.0)
    features.setdefault("consec_close_below_donchian_20", 0.0)
    features.setdefault("consec_above_ema50", 0.0)
    features.setdefault("consec_below_ema50", 0.0)
    features.setdefault("consec_above_ema50_prev", 0.0)
    features.setdefault("consec_below_ema50_prev", 0.0)
    features.setdefault("close_max_n", close)
    features.setdefault("close_min_n", close)
    features.setdefault("time_exit_bars", 12)

    ema200 = context.get("ema200", 0.0) or 0.0
    context.setdefault("ema200_prev_n", ema200)
    context.setdefault("ema200_slope_norm", 0.05)
    context.setdefault("consec_above_ema200", 0.0)
    context.setdefault("consec_below_ema200", 0.0)
    context.setdefault("consec_higher_close", 0.0)
    context.setdefault("consec_lower_close", 0.0)
    return payload


@pytest.fixture
def valid_payload_long():
    """Valid payload for LONG signal."""
    payload = {
        "price_snapshot": {
            "last": 1100.0,
            "mark": 1100.0,
            "bid": 1099.0,
            "ask": 1101.0,
        },
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
        },
        "context_htf": {
            "ema200": 950.0,
            "close": 1010.0,
            "atr14": 100.0,
            "trend": "up",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.5
        },
        "position_state": {
            "side": None,
            "qty": 0.0
        }
    }
    payload["context_htf"]["ema200_prev_n"] = 900.0
    payload["context_htf"]["ema200_slope_norm"] = 0.06
    payload["context_htf"]["consec_above_ema200"] = 5
    payload["context_htf"]["consec_higher_close"] = 5
    payload["features_ltf"]["consec_below_ema50_prev"] = 2
    return _ensure_required_fields(payload)


@pytest.fixture
def valid_payload_short():
    """Valid payload for SHORT signal."""
    payload = {
        "price_snapshot": {
            "last": 900.0,
            "mark": 900.0,
            "bid": 899.0,
            "ask": 901.0,
        },
        "features_ltf": {
            "close": 900.0,
            "close_prev": 1010.0,
            "high": 1015.0,
            "low": 890.0,
            "ema50": 1000.0,
            "ema120": 1000.0,
            "donchian_high_240": 1100.0,
            "donchian_low_240": 920.0,
            "donchian_high_20": 1020.0,
            "donchian_low_20": 930.0,
            "consec_close_above_donchian_20": 0,
            "consec_close_below_donchian_20": 2,
            "atr14": 100.0,
            "atr14_sma20": 90.0,
            "bb_upper": 1100.0,
            "bb_lower": 900.0,
            "bb_mid": 1000.0,
            "volume_ratio": 1.5,
            "candle_body_ratio": 0.7,
            "rsi14": 65.0,
            "rsi14_prev": 65.0,
            "consec_above_ema50": 0,
            "consec_below_ema50": 2,
            "consec_above_ema50_prev": 2,
            "consec_below_ema50_prev": 0,
            "close_max_n": 1015.0,
            "close_min_n": 880.0,
            "time_exit_bars": 12,
        },
        "context_htf": {
            "ema200": 1050.0,
            "close": 990.0,
            "atr14": 100.0,
            "trend": "down",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.5
        },
        "position_state": {
            "side": None,
            "qty": 0.0
        }
    }
    payload["context_htf"]["ema200_prev_n"] = 1150.0
    payload["context_htf"]["ema200_slope_norm"] = 0.06
    payload["context_htf"]["consec_below_ema200"] = 5
    payload["context_htf"]["consec_lower_close"] = 5
    payload["features_ltf"]["consec_above_ema50_prev"] = 2
    return _ensure_required_fields(payload)


def test_make_decision_long(valid_payload_long):
    """Test LONG decision."""
    decision = make_decision(valid_payload_long)

    if not _JSONSCHEMA_AVAILABLE:
        assert decision["intent"] == "HOLD"
        assert "jsonschema_not_installed" in decision["reject_reasons"]
        return

    assert decision["intent"] == "LONG"
    assert "entry" in decision
    assert "sl" in decision
    assert "tp" in decision
    assert "rr" in decision
    assert decision["rr"] >= valid_payload_long["risk_policy"]["min_rr"]
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"


def test_make_decision_short(valid_payload_short):
    """Test SHORT decision."""
    decision = make_decision(valid_payload_short)

    if not _JSONSCHEMA_AVAILABLE:
        assert decision["intent"] == "HOLD"
        assert "jsonschema_not_installed" in decision["reject_reasons"]
        return

    assert decision["intent"] == "SHORT"
    assert "entry" in decision
    assert "sl" in decision
    assert "tp" in decision
    assert decision["rr"] >= valid_payload_short["risk_policy"]["min_rr"]
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"


def test_decision_candle_explainability_fields(valid_payload_long):
    if not _JSONSCHEMA_AVAILABLE:
        pytest.skip("jsonschema required for decision explainability test")

    valid_payload_long["account_state"] = {
        "funds_base": 100.0,
        "funds_source": "available_balance",
        "leverage": 5,
    }
    valid_payload_long["exchange_limits"] = {
        "step_size": 0.001,
    }
    valid_payload_long["risk_policy"]["risk_per_trade"] = 0.05

    decision = make_decision(valid_payload_long)
    explain_fields = run._build_explain_fields(valid_payload_long, decision)
    decision_log = run._build_decision_log(
        latest_closed_ts=1234567890,
        interval="5m",
        payload=valid_payload_long,
        explain_fields=explain_fields,
        trade_plan=None,
        all_rejects=list(decision.get("reject_reasons") or []),
        cooldown_active=False,
        time_exit_signal=False,
    )

    assert decision_log["pullback_atr_long"] is not None
    assert decision_log["pullback_atr_short"] is not None
    assert isinstance(decision_log["pullback_atr_long"], numbers.Real)
    assert isinstance(decision_log["pullback_atr_short"], numbers.Real)
    assert isinstance(decision_log["reclaim_long"], bool)
    assert isinstance(decision_log["reclaim_short"], bool)
    assert isinstance(decision_log["prev_reclaim_long"], bool)
    assert isinstance(decision_log["prev_reclaim_short"], bool)
    assert isinstance(decision_log["prev_rsi_long"], bool)
    assert isinstance(decision_log["prev_rsi_short"], bool)
    assert isinstance(decision_log["prev_close"], numbers.Real)
    assert isinstance(decision_log["prev_rsi"], numbers.Real)
    assert isinstance(decision_log["ema50_5m"], numbers.Real)
    assert isinstance(decision_log["atr_ratio"], numbers.Real)
    assert isinstance(decision_log["candle_body_ratio"], numbers.Real)
    assert isinstance(decision_log["volume_ratio_5m"], numbers.Real)
    assert isinstance(decision_log["breakout_long"], bool)
    assert isinstance(decision_log["breakout_short"], bool)
    assert decision_log["regime_used_for_routing"] == decision_log["regime_detected"]
    assert isinstance(decision_log["risk_usd"], numbers.Real)
    assert isinstance(decision_log["qty_before_rounding"], numbers.Real)
    assert isinstance(decision_log["qty_after_rounding"], numbers.Real)
    assert isinstance(decision_log["required_margin"], numbers.Real)
    assert decision_log["leverage_used"] == 5


def test_make_decision_hold():
    """Test HOLD decision when conditions not met."""
    payload = {
        "price_snapshot": {
            "last": 1000.0,
            "mark": 1000.0,
            "bid": 999.0,
            "ask": 1001.0,
        },
        "features_ltf": {
            "close": 1000.0,
            "close_prev": 1000.0,
            "ema50": 1000.0,
            "ema120": 1000.0,
            "donchian_high_240": 1100.0,
            "donchian_low_240": 900.0,
            "donchian_high_20": 1050.0,
            "donchian_low_20": 950.0,
            "atr14": 100.0,
            "atr14_sma20": 90.0,
            "bb_upper": 1100.0,
            "bb_lower": 900.0,
            "bb_mid": 1000.0,
            "volume_ratio": 0.8,
            "candle_body_ratio": 0.5,
            "rsi14": 50.0,
            "rsi14_prev": 50.0
        },
        "context_htf": {
            "ema200": 950.0,
            "close": 1000.0,
            "atr14": 100.0,
            "trend": "range",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.8
        },
        "position_state": {
            "side": None,
            "qty": 0.0
        }
    }
    payload = _ensure_required_fields(payload)
    decision = make_decision(payload)
    
    assert decision["intent"] == "HOLD"
    assert len(decision["reject_reasons"]) > 0


def test_hold_rejects_use_routed_regime(monkeypatch, valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["ema50"] = -1.0
    payload["features_ltf"]["bb_upper"] = -1.0
    payload["features_ltf"]["bb_lower"] = -1.0
    payload["features_ltf"]["bb_mid"] = -1.0
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    rejects = decision.get("reject_reasons") or []
    assert len(rejects) > 0
    assert all(not code.startswith("M:regime") for code in rejects)


def test_strategy_ineligible_only_for_routed_regime(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close"] = 1300.0
    payload["features_ltf"]["close_prev"] = 1200.0
    payload["features_ltf"]["ema50"] = 1000.0
    payload["features_ltf"]["donchian_high_20"] = 2000.0
    payload["features_ltf"]["donchian_low_20"] = 900.0
    payload["features_ltf"]["volume_ratio"] = 0.5
    payload["price_snapshot"]["last"] = 1300.0
    payload["price_snapshot"]["mark"] = 1300.0
    payload["price_snapshot"]["bid"] = 1299.0
    payload["price_snapshot"]["ask"] = 1301.0
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime_detected") == "PULLBACK"
    rejects = decision.get("reject_reasons") or []
    assert "P:strategy_ineligible" in rejects
    assert all(not code.startswith("M:strategy_ineligible") for code in rejects)


def test_regime_mismatch_emits_m_regime(monkeypatch, valid_payload_long):
    monkeypatch.setenv("ROUTING_REGIME_OVERRIDE", "RANGE")
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close"] = 1300.0
    payload["features_ltf"]["close_prev"] = 1200.0
    payload["features_ltf"]["ema50"] = 1000.0
    payload["features_ltf"]["donchian_high_20"] = 2000.0
    payload["features_ltf"]["donchian_low_20"] = 900.0
    payload["features_ltf"]["volume_ratio"] = 0.5
    payload["price_snapshot"]["last"] = 1300.0
    payload["price_snapshot"]["mark"] = 1300.0
    payload["price_snapshot"]["bid"] = 1299.0
    payload["price_snapshot"]["ask"] = 1301.0
    decision = make_decision(payload)
    rejects = decision.get("reject_reasons") or []
    assert "M:regime" in rejects


def test_routing_override_blocked_in_production(monkeypatch, valid_payload_long):
    from core.runtime_mode import reset_runtime_settings

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RUNTIME_MODE", "live")
    monkeypatch.setenv("ROUTING_REGIME_OVERRIDE", "RANGE")
    reset_runtime_settings()

    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close"] = 1300.0
    payload["features_ltf"]["close_prev"] = 1200.0
    payload["features_ltf"]["ema50"] = 1000.0
    payload["features_ltf"]["donchian_high_20"] = 2000.0
    payload["features_ltf"]["donchian_low_20"] = 900.0
    payload["features_ltf"]["volume_ratio"] = 0.5
    payload["price_snapshot"]["last"] = 1300.0
    payload["price_snapshot"]["mark"] = 1300.0
    payload["price_snapshot"]["bid"] = 1299.0
    payload["price_snapshot"]["ask"] = 1301.0

    decision = make_decision(payload)
    signal = decision.get("signal", {})
    rejects = decision.get("reject_reasons") or []
    assert signal.get("regime_used_for_routing") == signal.get("regime_detected")
    assert all(not code.startswith("M:regime") for code in rejects)


def test_make_decision_already_in_position():
    """Test HOLD when already in position."""
    payload = {
        "price_snapshot": {
            "last": 1100.0,
            "mark": 1100.0,
            "bid": 1099.0,
            "ask": 1101.0,
        },
        "features_ltf": {
            "close": 1100.0,
            "close_prev": 990.0,
            "ema50": 1000.0,
            "ema120": 1000.0,
            "donchian_high_240": 1080.0,
            "donchian_low_240": 900.0,
            "donchian_high_20": 1080.0,
            "donchian_low_20": 950.0,
            "atr14": 100.0,
            "atr14_sma20": 90.0,
            "bb_upper": 1200.0,
            "bb_lower": 900.0,
            "bb_mid": 1050.0,
            "volume_ratio": 1.5,
            "candle_body_ratio": 0.7,
            "rsi14": 35.0,
            "rsi14_prev": 35.0
        },
        "context_htf": {
            "ema200": 950.0,
            "close": 1000.0,
            "atr14": 100.0,
            "trend": "up",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.8
        },
        "position_state": {
            "side": "LONG",
            "qty": 0.1
        }
    }
    payload = _ensure_required_fields(payload)
    decision = make_decision(payload)
    
    assert decision["intent"] == "HOLD"
    assert "already_in_position" in str(decision["reject_reasons"])

def test_spread_pct_from_bid_ask():
    payload = {
        "price_snapshot": {
            "last": 1000.0,
            "mark": 1000.0,
            "bid": 999.0,
            "ask": 1001.0,
        },
        "features_ltf": {
            "close": 1000.0,
            "close_prev": 1000.0,
            "ema50": 1000.0,
            "ema120": 1000.0,
            "donchian_high_240": 1100.0,
            "donchian_low_240": 900.0,
            "atr14": 100.0,
            "bb_upper": 1100.0,
            "bb_lower": 900.0,
            "bb_mid": 1000.0,
            "volume_ratio": 0.8,
            "rsi14": 50.0,
            "rsi14_prev": 50.0,
            "donchian_high_20": 1050.0,
            "donchian_low_20": 950.0,
            "atr14_sma20": 90.0,
            "candle_body_ratio": 0.5,
        },
        "context_htf": {
            "ema200": 950.0,
            "close": 1000.0,
            "atr14": 100.0,
            "trend": "range",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.8
        },
        "position_state": {
            "side": None,
            "qty": 0.0
        }
    }
    payload = _ensure_required_fields(payload)
    decision = make_decision(payload)
    spread_pct = decision.get("signal", {}).get("spread_pct")
    assert spread_pct is not None
    assert spread_pct > 0

@pytest.mark.critical
def test_make_decision_deterministic(valid_payload_long):
    """Decision must be deterministic for the same payload."""
    payload = dict(valid_payload_long)
    payload["market_identity"] = {"timestamp_closed": 1704067200}
    d1 = make_decision(payload)
    d2 = make_decision(payload)
    assert d1 == d2


def test_regime_detection_boundaries(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200"] = 100.0
    payload["context_htf"]["close"] = 160.0
    payload["context_htf"]["atr14"] = 100.0
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime") == "TREND"

    payload["context_htf"]["close"] = 159.0
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime") == "RANGE"


def test_trend_down_short_trade(valid_payload_short):
    payload = copy.deepcopy(valid_payload_short)
    payload["context_htf"]["ema200"] = 1050.0
    payload["context_htf"]["close"] = 900.0
    payload["context_htf"]["atr14"] = 100.0
    payload["features_ltf"]["close"] = 900.0
    payload["features_ltf"]["close_prev"] = 1000.0
    payload["features_ltf"]["ema50"] = 1000.0
    payload["features_ltf"]["atr14"] = 100.0
    payload["features_ltf"]["rsi14"] = 55.0
    payload["features_ltf"]["rsi14_prev"] = 55.0

    decision = make_decision(payload)
    assert decision["intent"] == "SHORT"
    assert decision.get("entry") is not None
    assert decision.get("sl") is not None
    assert decision.get("tp") is not None
    assert decision.get("rr") is not None
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"


def test_range_long_trade(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200"] = 100.0
    payload["context_htf"]["close"] = 101.0
    payload["context_htf"]["atr14"] = 200.0
    payload["context_htf"]["trend"] = "range"
    payload["price_snapshot"]["last"] = 91.0
    payload["price_snapshot"]["mark"] = 91.0
    payload["price_snapshot"]["bid"] = 90.9
    payload["price_snapshot"]["ask"] = 91.1
    payload["features_ltf"]["close_prev"] = 95.0
    payload["features_ltf"]["close"] = 91.0
    payload["features_ltf"]["donchian_low_20"] = 90.0
    payload["features_ltf"]["donchian_high_20"] = 150.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["volume_ratio"] = 0.8
    payload["features_ltf"]["bb_lower"] = 85.0
    payload["features_ltf"]["bb_upper"] = 155.0
    payload["features_ltf"]["bb_mid"] = 120.0
    payload["features_ltf"]["rsi14"] = 45.0
    payload["features_ltf"]["rsi14_prev"] = 45.0
    payload["risk_policy"]["min_rr"] = 1.0

    decision = make_decision(payload)
    assert decision["intent"] == "LONG"
    assert decision.get("entry") is not None
    assert decision.get("sl") is not None
    assert decision.get("tp") is not None
    assert decision.get("rr") is not None
    assert decision.get("signal", {}).get("selected_strategy") == "RANGE_MEANREV"


def test_breakout_long_trade(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200"] = 1000.0
    payload["context_htf"]["close"] = 1010.0
    payload["context_htf"]["atr14"] = 100.0
    payload["features_ltf"]["close"] = 120.0
    payload["features_ltf"]["high"] = 125.0
    payload["features_ltf"]["low"] = 108.0
    payload["features_ltf"]["donchian_high_20"] = 110.0
    payload["features_ltf"]["donchian_low_20"] = 90.0
    payload["features_ltf"]["donchian_low_20"] = 90.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2
    payload["features_ltf"]["volume_ratio"] = 2.0
    payload["features_ltf"]["candle_body_ratio"] = 0.7
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["atr14_sma20"] = 7.0
    payload["features_ltf"]["rsi14"] = 50.0
    payload["features_ltf"]["rsi14_prev"] = 50.0
    payload["risk_policy"]["min_rr"] = 1.0

    decision = make_decision(payload)
    assert decision["intent"] == "LONG"
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"


def test_strategy_selection_priority(monkeypatch, valid_payload_long):
    monkeypatch.setenv("REGIME_COMPRESSION_VOL_MAX", "1.3")
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200"] = 100.0
    payload["context_htf"]["close"] = 160.0
    payload["context_htf"]["atr14"] = 100.0
    payload["features_ltf"]["close_prev"] = 90.0
    payload["features_ltf"]["close"] = 110.0
    payload["features_ltf"]["ema50"] = 100.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["atr14_sma20"] = 7.0
    payload["features_ltf"]["rsi14"] = 40.0
    payload["features_ltf"]["rsi14_prev"] = 40.0
    payload["features_ltf"]["donchian_high_20"] = 105.0
    payload["features_ltf"]["donchian_low_20"] = 95.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2
    payload["features_ltf"]["volume_ratio"] = 1.2
    payload["features_ltf"]["candle_body_ratio"] = 0.7

    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime_detected") == "BREAKOUT_EXPANSION"


def test_sentinel_fields_fail_closed(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["ema50"] = -1.0
    payload["features_ltf"]["bb_upper"] = -1.0
    payload["features_ltf"]["bb_lower"] = -1.0
    payload["features_ltf"]["bb_mid"] = -1.0
    payload["features_ltf"]["donchian_high_20"] = -1.0
    payload["features_ltf"]["donchian_low_20"] = -1.0
    payload["features_ltf"]["atr14_sma20"] = -1.0

    decision = make_decision(payload)

    assert decision["intent"] == "HOLD"
    assert decision.get("signal", {}).get("selected_strategy") == "NONE"
    assert "T:insufficient_history:ema50_5m" in decision["reject_reasons"]
    assert "B:insufficient_history:donchian_20" in decision["reject_reasons"]
    assert "B:insufficient_history:atr14_sma20" in decision["reject_reasons"]
    assert "M:insufficient_history:bb_20" in decision["reject_reasons"]


def test_atr_ratio_guard_requires_sma(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["atr14_sma20"] = 0.0
    payload["features_ltf"]["rsi14"] = 50.0
    payload["features_ltf"]["rsi14_prev"] = 50.0
    payload["features_ltf"]["close"] = 120.0
    payload["features_ltf"]["donchian_high_20"] = 110.0
    payload["features_ltf"]["volume_ratio"] = 2.0
    payload["features_ltf"]["candle_body_ratio"] = 0.7

    decision = make_decision(payload)

    assert decision["intent"] == "HOLD"
    assert "B:insufficient_history:atr14_sma20" in decision["reject_reasons"]


def _cont_short_payload():
    payload = {
        "price_snapshot": {
            "last": 98.0,
            "mark": 98.0,
            "bid": 97.9,
            "ask": 98.1,
        },
        "features_ltf": {
            "close": 98.0,
            "close_prev": 102.0,
            "ema50": 105.0,
            "ema50_prev_12": 110.0,
            "ema120": 110.0,
            "donchian_high_240": 140.0,
            "donchian_low_240": 90.0,
            "donchian_high_20": 120.0,
            "donchian_low_20": 100.0,
            "atr14": 10.0,
            "atr14_sma20": 10.0,
            "bb_upper": 130.0,
            "bb_lower": 80.0,
            "bb_mid": 105.0,
            "volume_ratio": 1.1,
            "candle_body_ratio": 0.6,
            "rsi14": 45.0,
            "rsi14_prev": 45.0,
        },
        "context_htf": {
            "ema200": 1000.0,
            "close": 900.0,
            "atr14": 100.0,
            "trend": "down",
            "timeframe": "1h",
        },
        "risk_policy": {
            "min_rr": 1.0
        },
        "position_state": {
            "side": None,
            "qty": 0.0
        }
    }
    payload["context_htf"]["ema200_prev_n"] = 1100.0
    payload["context_htf"]["ema200_slope_norm"] = 0.06
    payload["context_htf"]["consec_below_ema200"] = 5
    payload["context_htf"]["consec_lower_close"] = 5
    payload["features_ltf"]["consec_above_ema50_prev"] = 0
    return _ensure_required_fields(payload)


def test_trend_continuation_short_trade():
    payload = _cont_short_payload()
    decision = make_decision(payload)

    if not _JSONSCHEMA_AVAILABLE:
        assert decision["intent"] == "HOLD"
        assert "jsonschema_not_installed" in decision["reject_reasons"]
        return

    assert decision["intent"] == "SHORT"
    assert decision.get("signal", {}).get("selected_strategy") == "CONTINUATION"
    assert decision.get("entry") == pytest.approx(98.0)
    assert decision.get("sl") == pytest.approx(110.0)
    assert decision.get("tp") == pytest.approx(78.8)


def test_pullback_regime_blocks_continuation():
    payload = _cont_short_payload()
    payload["features_ltf"]["close"] = 70.0
    payload["price_snapshot"]["last"] = 70.0
    payload["price_snapshot"]["mark"] = 70.0
    payload["price_snapshot"]["bid"] = 69.9
    payload["price_snapshot"]["ask"] = 70.1
    decision = make_decision(payload)

    if not _JSONSCHEMA_AVAILABLE:
        assert decision["intent"] == "HOLD"
        assert "jsonschema_not_installed" in decision["reject_reasons"]
        return

    assert decision.get("signal", {}).get("regime_detected") == "PULLBACK"
    assert decision.get("signal", {}).get("selected_strategy") == "NONE"
    assert "P:dist50" in decision["reject_reasons"]


def test_trend_continuation_slope_reject():
    payload = _cont_short_payload()
    payload["features_ltf"]["ema50_prev_12"] = 106.0
    decision = make_decision(payload)

    if not _JSONSCHEMA_AVAILABLE:
        assert decision["intent"] == "HOLD"
        assert "jsonschema_not_installed" in decision["reject_reasons"]
        return

    assert decision["intent"] == "HOLD"
    assert "C:slope" in decision["reject_reasons"]


def test_regime_detection_exclusive(valid_payload_long):
    decision = make_decision(valid_payload_long)
    regime = decision.get("signal", {}).get("regime_detected")
    assert regime in {"COMPRESSION", "BREAKOUT_EXPANSION", "TREND_CONTINUATION", "PULLBACK", "RANGE"}


def test_compression_holds(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["trend"] = "range"
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["donchian_high_20"] = 110.0
    payload["features_ltf"]["donchian_low_20"] = 95.0
    payload["features_ltf"]["close"] = 100.0
    payload["features_ltf"]["volume_ratio"] = 0.9
    payload["features_ltf"]["candle_body_ratio"] = 0.4

    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime_detected") == "COMPRESSION"
    assert decision.get("signal", {}).get("selected_strategy") == "NONE"
    assert decision["intent"] == "HOLD"


def test_pullback_vs_continuation_dist50_threshold(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["trend"] = "down"
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["ema50"] = 100.0
    payload["features_ltf"]["donchian_low_20"] = 95.0
    payload["features_ltf"]["donchian_high_20"] = 120.0
    payload["features_ltf"]["close"] = 112.0
    payload["features_ltf"]["volume_ratio"] = 1.1
    payload["features_ltf"]["candle_body_ratio"] = 0.6

    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime_detected") == "PULLBACK"

    payload["features_ltf"]["close"] = 94.0
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("regime_detected") == "TREND_CONTINUATION"


def test_strategy_gating_no_overlap(valid_payload_long):
    decision = make_decision(valid_payload_long)
    eligible = decision.get("signal", {}).get("eligible_strategies") or []
    selected = decision.get("signal", {}).get("selected_strategy")
    assert len(eligible) <= 1
    assert selected == "NONE" or selected in eligible


def test_trend_stability_gate_blocks_breakout(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200_slope_norm"] = 0.01
    payload["context_htf"]["consec_above_ema200"] = 1
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "B:stability" in decision["reject_reasons"]

def test_trend_stability_gate_passes(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200_slope_norm"] = 0.08
    payload["context_htf"]["consec_above_ema200"] = 6
    payload["context_htf"]["consec_higher_close"] = 6
    decision = make_decision(payload)
    assert decision["intent"] == "LONG"
    assert decision.get("signal", {}).get("trend_stable_long") is True

def test_trend_stability_nan_fails_closed(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["ema200_slope_norm"] = float("nan")
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "T:insufficient_history:ema200_slope_norm" in decision["reject_reasons"]


def test_breakout_acceptance_requires_consecutive_closes(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["consec_close_above_donchian_20"] = 1
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "B:accept" in decision["reject_reasons"]


def test_breakout_acceptance_passes(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2
    decision = make_decision(payload)
    assert decision["intent"] == "LONG"
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"

def test_breakout_acceptance_wick_reject(monkeypatch, valid_payload_long):
    monkeypatch.setenv("BREAKOUT_ACCEPT_BARS", "1")
    monkeypatch.setenv("HTF_TREND_SLOPE_MIN", "0.01")
    monkeypatch.setenv("HTF_TREND_PERSIST_MIN", "2")
    monkeypatch.setenv("HTF_TREND_STRUCTURE_MIN", "2")
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close"] = 120.0
    payload["features_ltf"]["close_prev"] = 100.0
    payload["features_ltf"]["high"] = 122.0
    payload["features_ltf"]["low"] = 100.0
    payload["features_ltf"]["donchian_high_20"] = 110.0
    payload["features_ltf"]["donchian_low_20"] = 90.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 1
    payload["features_ltf"]["atr14"] = 10.0
    payload["context_htf"]["ema200_slope_norm"] = 0.08
    payload["context_htf"]["consec_above_ema200"] = 6
    payload["context_htf"]["consec_higher_close"] = 6
    payload["price_snapshot"]["last"] = 120.0
    payload["price_snapshot"]["mark"] = 120.0
    payload["price_snapshot"]["bid"] = 119.9
    payload["price_snapshot"]["ask"] = 120.1
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "B:accept" in decision["reject_reasons"]


def test_pullback_reentry_passes(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["trend"] = "up"
    payload["features_ltf"]["ema50"] = 100.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["close_prev"] = 90.0
    payload["features_ltf"]["close"] = 112.0
    payload["features_ltf"]["donchian_high_20"] = 130.0
    payload["features_ltf"]["donchian_low_20"] = 80.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 0
    payload["features_ltf"]["consec_close_below_donchian_20"] = 0
    payload["features_ltf"]["candle_body_ratio"] = 0.6
    payload["features_ltf"]["consec_below_ema50_prev"] = 2
    payload["features_ltf"]["consec_above_ema50"] = 2
    payload["features_ltf"]["volume_ratio"] = 1.2
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("selected_strategy") == "PULLBACK_REENTRY"


def test_pullback_reentry_fake_reclaim_blocks(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["trend"] = "up"
    payload["features_ltf"]["ema50"] = 100.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["close_prev"] = 90.0
    payload["features_ltf"]["close"] = 112.0
    payload["features_ltf"]["donchian_high_20"] = 130.0
    payload["features_ltf"]["donchian_low_20"] = 80.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 0
    payload["features_ltf"]["consec_close_below_donchian_20"] = 0
    payload["features_ltf"]["candle_body_ratio"] = 0.6
    payload["features_ltf"]["consec_below_ema50_prev"] = 2
    payload["features_ltf"]["consec_above_ema50"] = 1
    payload["features_ltf"]["volume_ratio"] = 0.8
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "P:fake_reclaim" in decision["reject_reasons"]


def test_adaptive_rr_min_rr_blocks_range(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["context_htf"]["trend"] = "range"
    payload["context_htf"]["ema200"] = 100.0
    payload["context_htf"]["close"] = 101.0
    payload["context_htf"]["atr14"] = 200.0
    payload["features_ltf"]["close"] = 91.0
    payload["features_ltf"]["close_prev"] = 95.0
    payload["features_ltf"]["donchian_low_20"] = 90.0
    payload["features_ltf"]["donchian_high_20"] = 150.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["volume_ratio"] = 0.8
    payload["risk_policy"]["min_rr"] = 2.5
    decision = make_decision(payload)
    assert decision["intent"] == "HOLD"
    assert "L:rr" in decision["reject_reasons"]

def test_adaptive_rr_math_breakout(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close"] = 120.0
    payload["features_ltf"]["high"] = 125.0
    payload["features_ltf"]["low"] = 118.0
    payload["features_ltf"]["donchian_high_20"] = 110.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["volume_ratio"] = 1.5
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["ema200_slope_norm"] = 0.08
    payload["context_htf"]["consec_above_ema200"] = 6
    decision = make_decision(payload)
    assert decision["intent"] == "LONG"
    entry = decision.get("entry")
    sl = decision.get("sl")
    tp = decision.get("tp")
    rr = decision.get("rr")
    assert entry is not None and sl is not None and tp is not None and rr is not None
    assert tp == pytest.approx(entry + rr * abs(entry - sl))


def test_time_exit_intent_close(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["position_state"]["side"] = "LONG"
    payload["position_state"]["qty"] = 0.5
    payload["position_state"]["entry"] = 100.0
    payload["features_ltf"]["atr14"] = 10.0
    payload["features_ltf"]["close_max_n"] = 104.0
    payload["features_ltf"]["close_min_n"] = 95.0
    decision = make_decision(payload)
    assert decision["intent"] == "CLOSE"
    assert decision.get("signal", {}).get("selected_strategy") == "TIME_EXIT"


def test_time_exit_precedes_entry(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["position_state"]["side"] = "LONG"
    payload["position_state"]["qty"] = 0.5
    payload["position_state"]["entry"] = 1100.0
    payload["features_ltf"]["atr14"] = 100.0
    payload["features_ltf"]["close_max_n"] = 1120.0
    payload["features_ltf"]["close_min_n"] = 980.0
    decision = make_decision(payload)
    assert decision["intent"] == "CLOSE"
    assert decision.get("signal", {}).get("selected_strategy") == "TIME_EXIT"


def test_strategy_priority_breakout_over_pullback(valid_payload_long):
    payload = copy.deepcopy(valid_payload_long)
    payload["features_ltf"]["close_prev"] = 1030.0
    decision = make_decision(payload)
    assert decision.get("signal", {}).get("pullback_reentry_long_ok") is True
    assert decision.get("signal", {}).get("selected_strategy") == "BREAKOUT_EXPANSION"
