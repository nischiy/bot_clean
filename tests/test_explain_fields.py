"""
Unit tests for explainability fields in decision logs.

Tests verify that explain_pullback, explain_range, and explain_anti_reversal
fields are correctly generated with numeric values and thresholds.
"""
import pytest
from app.run import (
    _build_explain_pullback,
    _build_explain_range,
    _build_explain_anti_reversal,
    _build_explain_main,
)


def test_explain_pullback_with_dist50_blocker():
    """Test explain_pullback contains dist50_prev, thresholds, and correct pass/fail booleans."""
    signal = {
        "regime_detected": "PULLBACK",
        "dist50": 0.5,
        "dist50_prev": 0.8,
        "trend": "up",
        "close_ltf": 50000.0,
        "ema50_ltf": 49900.0,
        "reclaim_long": True,
        "reclaim_short": False,
        "volume_ratio": 1.2,
        "stability_score": 0.75,
    }
    blockers = ["P:dist50_prev"]
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is not None
    assert explain["dist50_prev"] == 0.8
    assert explain["dist50_curr"] == 0.5
    assert explain["dist50_min"] == 0.3  # PULLBACK_REENTRY_DIST50_MIN
    assert explain["dist50_max"] == 1.5  # PULLBACK_REENTRY_DIST50_MAX
    assert explain["dist50_prev_ok"] is True  # 0.8 is within [0.3, 1.5]
    assert explain["dist50_curr_ok"] is True  # 0.5 <= 1.5
    assert explain["reclaim_required"] == "long"
    assert explain["reclaim_ok"] is True
    assert explain["volume_ratio"] == 1.2
    assert explain["vol_min"] == 1.0  # PULLBACK_REENTRY_VOL_MIN
    assert explain["vol_ok"] is True  # 1.2 >= 1.0
    assert explain["stability_score"] == 0.75
    assert explain["stable_ok"] is True  # 0.75 >= 0.70 (STABILITY_HARD)


def test_explain_pullback_with_vol_blocker():
    """Test explain_pullback when volume is too low."""
    signal = {
        "regime_detected": "PULLBACK",
        "dist50": 0.4,
        "dist50_prev": 0.6,
        "trend": "down",
        "close_ltf": 50000.0,
        "ema50_ltf": 50100.0,
        "reclaim_long": False,
        "reclaim_short": True,
        "volume_ratio": 0.8,  # Below minimum
        "stability_score": 0.65,
    }
    blockers = ["P:vol"]
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is not None
    assert explain["vol_ok"] is False  # 0.8 < 1.0
    assert explain["reclaim_required"] == "short"
    assert explain["reclaim_ok"] is True


def test_explain_pullback_not_pullback_regime():
    """Test explain_pullback returns None when regime is not PULLBACK and no P: blockers."""
    signal = {
        "regime_detected": "RANGE",
        "dist50": 0.5,
    }
    blockers = ["M:trend"]
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is None


def test_explain_pullback_with_p_blockers():
    """Test explain_pullback returns data when P: blockers present even if regime != PULLBACK."""
    signal = {
        "regime_detected": "RANGE",
        "dist50": 0.5,
        "dist50_prev": 0.8,
        "trend": "up",
        "close_ltf": 50000.0,
        "ema50_ltf": 49900.0,
        "reclaim_long": True,
        "volume_ratio": 1.2,
        "stability_score": 0.75,
    }
    blockers = ["P:dist50"]
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is not None
    assert explain["dist50_curr"] == 0.5


def test_explain_pullback_with_confirm_blocker():
    """Test explain_pullback.confirm exists when P:confirm blocker is present."""
    signal = {
        "regime_detected": "PULLBACK",
        "dist50": 0.4,
        "dist50_prev": 0.6,
        "trend": "up",
        "close_ltf": 50000.0,
        "close_prev_ltf": 49900.0,
        "ema50_ltf": 49900.0,
        "reclaim_long": True,
        "volume_ratio": 1.2,
        "stability_score": 0.75,
        "candle_body_ratio": 0.4,  # Below minimum
        "consec_below_ema50_prev": 3,
    }
    blockers = ["P:confirm"]
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is not None
    assert "confirm" in explain
    confirm = explain["confirm"]
    assert confirm["body_ratio"] == 0.4
    assert confirm["body_min"] == 0.5  # PULLBACK_REENTRY_CONFIRM_BODY_MIN
    assert confirm["body_ok"] is False  # 0.4 < 0.5
    assert confirm["min_bars"] == 2  # PULLBACK_REENTRY_MIN_BARS
    assert confirm["bars_since_signal"] == 3  # consec_below_ema50_prev for up trend
    assert confirm["bars_ok"] is True  # 3 >= 2
    assert confirm["confirmation_type"] in ("BODY", "CLOSE_DIRECTION", "MISSING_DATA", "BARS", "OK")
    assert confirm["confirm_ok"] is False  # body_ok is False


def test_explain_pullback_confirm_passes():
    """Test explain_pullback.confirm when all confirmation checks pass."""
    signal = {
        "regime_detected": "PULLBACK",
        "dist50": 0.4,
        "dist50_prev": 0.6,
        "trend": "up",
        "close_ltf": 50000.0,
        "close_prev_ltf": 49900.0,  # close_ltf > close_prev for up trend
        "ema50_ltf": 49900.0,
        "reclaim_long": True,
        "volume_ratio": 1.2,
        "stability_score": 0.75,
        "candle_body_ratio": 0.6,  # Above minimum
        "consec_below_ema50_prev": 3,
    }
    blockers = []
    
    explain = _build_explain_pullback(signal, blockers)
    
    assert explain is not None
    assert "confirm" in explain
    confirm = explain["confirm"]
    assert confirm["body_ok"] is True  # 0.6 >= 0.5
    assert confirm["bars_ok"] is True  # 3 >= 2
    assert confirm["confirmation_type"] == "OK"
    assert confirm["confirm_ok"] is True


def test_explain_range_with_vol_blocker():
    """Test explain_range contains volume_ratio vs vol_max and correct pass/fail."""
    signal = {
        "regime_detected": "RANGE",
        "trend": "range",
        "trend_strength": 0.3,
        "volume_ratio": 1.5,  # Above maximum
        "rsi14_ltf": 50.0,
        "close_ltf": 50000.0,
        "atr": 500.0,
        "donchian_high_20": 51000.0,
        "donchian_low_20": 49000.0,
        "wick_ratio": 1.5,
    }
    blockers = ["M:vol"]
    
    explain = _build_explain_range(signal, blockers)
    
    assert explain is not None
    assert explain["trend"] == "range"
    assert explain["trend_ok"] is True  # trend == "range"
    assert explain["volume_ratio"] == 1.5
    assert explain["vol_max"] == 1.0  # RANGE_MEANREV_VOL_MAX
    assert explain["vol_ok"] is False  # 1.5 >= 1.0
    assert explain["rsi"] == 50.0
    assert explain["rsi_long_max"] == 35.0  # RANGE_RSI_LONG_MAX
    assert explain["rsi_short_min"] == 65.0  # RANGE_RSI_SHORT_MIN
    assert explain["rsi_long_ok"] is False  # 50.0 > 35.0
    assert explain["rsi_short_ok"] is False  # 50.0 < 65.0
    assert explain["edge_atr"] is not None
    assert explain["edge_atr_min"] == 0.2  # RANGE_MEANREV_EDGE_ATR


def test_explain_range_with_trend_blocker():
    """Test explain_range when trend is not range."""
    signal = {
        "regime_detected": "RANGE",
        "trend": "up",
        "trend_strength": 0.8,  # Above threshold
        "volume_ratio": 0.8,
        "rsi14_ltf": 40.0,
        "close_ltf": 50000.0,
        "atr": 500.0,
        "donchian_high_20": 51000.0,
        "donchian_low_20": 49000.0,
    }
    blockers = ["M:trend"]
    
    explain = _build_explain_range(signal, blockers)
    
    assert explain is not None
    assert explain["trend"] == "up"
    assert explain["trend_ok"] is False  # trend != "range" and trend_strength >= threshold
    assert "trend_block_reason" in explain
    assert explain["vol_ok"] is True  # 0.8 < 1.0


def test_explain_range_not_range_regime():
    """Test explain_range returns None when regime is not RANGE and no M: blockers."""
    signal = {
        "regime_detected": "PULLBACK",
        "volume_ratio": 1.2,
    }
    blockers = ["P:dist50"]
    
    explain = _build_explain_range(signal, blockers)
    
    assert explain is None


def test_explain_range_with_m_blockers():
    """Test explain_range returns data when M: blockers present even if regime != RANGE."""
    signal = {
        "regime_detected": "PULLBACK",
        "trend": "range",
        "volume_ratio": 0.8,
        "rsi14_ltf": 50.0,
        "close_ltf": 50000.0,
        "atr": 500.0,
        "donchian_high_20": 51000.0,
        "donchian_low_20": 49000.0,
    }
    blockers = ["M:vol"]
    
    explain = _build_explain_range(signal, blockers)
    
    assert explain is not None
    assert explain["vol_ok"] is True


def test_explain_anti_reversal_when_blocked():
    """Test explain_anti_reversal emits required fields when anti_reversal_block is true."""
    signal = {
        "anti_reversal_block": True,
        "anti_reversal_reason": "HTF_EMA_RECLAIM",
        "close_htf": 51000.0,
        "ema200_htf": 50000.0,
        "ema_fast_htf": 50500.0,
        "direction": "DOWN",
    }
    
    explain = _build_explain_anti_reversal(signal)
    
    assert explain is not None
    assert explain["evaluated"] is True
    assert explain["blocked"] is True  # anti_reversal_block is True
    assert explain["condition_ok"] is False  # condition_ok == (not blocked)
    assert explain["reason"] == "HTF_EMA_RECLAIM"
    assert explain["close_htf"] == 51000.0
    assert explain["ema200_htf"] == 50000.0
    assert explain["ema_fast_htf"] == 50500.0
    assert explain["htf_reclaim_level"] == 50500.0


def test_explain_anti_reversal_when_not_blocked():
    """Test explain_anti_reversal when anti-reversal evaluates but does not block."""
    signal = {
        "anti_reversal_block": False,
        "anti_reversal_reason": "",
        "close_htf": 50000.0,
        "ema200_htf": 50000.0,
        "ema_fast_htf": 50500.0,
        "direction": "DOWN",
    }
    
    explain = _build_explain_anti_reversal(signal)
    
    assert explain is not None
    assert explain["evaluated"] is True
    assert explain["blocked"] is False  # anti_reversal_block is False
    assert explain["condition_ok"] is True  # condition_ok == (not blocked)
    assert explain["reason"] == ""


def test_explain_anti_reversal_when_not_evaluated():
    """Test explain_anti_reversal returns None when anti-reversal was not evaluated."""
    signal = {
        "anti_reversal_block": None,
        "anti_reversal_reason": None,
    }
    
    explain = _build_explain_anti_reversal(signal)
    
    assert explain is None


def test_explain_anti_reversal_with_rsi_slope():
    """Test explain_anti_reversal with HTF_RSI_SLOPE reason."""
    signal = {
        "anti_reversal_block": True,
        "anti_reversal_reason": "HTF_RSI_SLOPE",
        "close_htf": 50000.0,
        "ema200_htf": 50000.0,
        "ema_fast_htf": 50500.0,
        "direction": "DOWN",
        "rsi14_htf": 60.0,
        "rsi14_htf_prev": 55.0,
        "wick_ratio": 2.5,
    }
    
    explain = _build_explain_anti_reversal(signal)
    
    assert explain is not None
    assert explain["evaluated"] is True
    assert explain["blocked"] is True
    assert explain["condition_ok"] is False  # condition_ok == (not blocked)
    assert explain["reason"] == "HTF_RSI_SLOPE"


def test_explain_main_from_pullback():
    """Test explain_main extracts correct values from explain_pullback."""
    blockers = ["P:dist50_prev"]
    explain_pullback = {
        "dist50_prev": 0.8,
        "dist50_min": 0.3,
        "dist50_max": 1.5,
        "dist50_prev_ok": True,
    }
    
    explain_main = _build_explain_main(blockers, explain_pullback, None, None)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "P:dist50_prev"
    assert explain_main["x"] == 0.8
    assert explain_main["th"] == "0.3-1.5"
    assert explain_main["ok"] is True


def test_explain_main_from_range():
    """Test explain_main extracts correct values from explain_range."""
    blockers = ["M:vol"]
    explain_range = {
        "volume_ratio": 1.5,
        "vol_max": 1.0,
        "vol_ok": False,
    }
    
    explain_main = _build_explain_main(blockers, None, explain_range, None)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "M:vol"
    assert explain_main["x"] == 1.5
    assert explain_main["th"] == 1.0
    assert explain_main["ok"] is False


def test_explain_main_from_anti_reversal():
    """Test explain_main extracts correct values from explain_anti_reversal."""
    blockers = ["anti_reversal_block"]
    explain_anti_reversal = {
        "reason": "HTF_EMA_RECLAIM",
        "condition_ok": False,
    }
    
    explain_main = _build_explain_main(blockers, None, None, explain_anti_reversal)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "anti_reversal_block"
    assert explain_main["x"] == "HTF_EMA_RECLAIM"
    assert explain_main["th"] == "blocked"
    assert explain_main["ok"] is False


def test_explain_main_fallback():
    """Test explain_main fallback when no explain fields match."""
    blockers = ["unknown_blocker"]
    
    explain_main = _build_explain_main(blockers, None, None, None)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "unknown_blocker"
    assert explain_main["x"] is None
    assert explain_main["th"] is None
    assert explain_main["ok"] is None


def test_explain_main_from_pullback_confirm():
    """Test explain_main extracts correct values from explain_pullback.confirm for P:confirm."""
    blockers = ["P:confirm"]
    explain_pullback = {
        "confirm": {
            "body_ratio": 0.4,
            "body_min": 0.5,
            "body_ok": False,
            "min_bars": 2,
            "bars_since_signal": 3,
            "bars_ok": True,
            "confirmation_type": "BODY",
            "confirm_ok": False,
        }
    }
    
    explain_main = _build_explain_main(blockers, explain_pullback, None, None)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "P:confirm"
    assert explain_main["x"] == "BODY"
    assert "body>=" in explain_main["th"]
    assert "bars>=" in explain_main["th"]
    assert explain_main["ok"] is False


def test_explain_main_from_pullback_body():
    """Test explain_main extracts correct values from explain_pullback.confirm for P:body."""
    blockers = ["P:body"]
    explain_pullback = {
        "confirm": {
            "body_ratio": 0.4,
            "body_min": 0.5,
            "body_ok": False,
            "confirmation_type": "BODY",
            "confirm_ok": False,
        }
    }
    
    explain_main = _build_explain_main(blockers, explain_pullback, None, None)
    
    assert explain_main is not None
    assert explain_main["blocker"] == "P:body"
    assert explain_main["x"] == 0.4
    assert explain_main["th"] == 0.5
    assert explain_main["ok"] is False
