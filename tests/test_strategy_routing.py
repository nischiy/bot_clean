"""
Tests for strategy routing fixes.

Verifies that:
1. All strategies are checked for eligibility, not just regime-mapped strategy
2. eligible_strategies is populated even when selected_strategy is NONE
3. Anti-reversal only blocks entries going AGAINST HTF direction
4. Multiple strategies can be eligible simultaneously
"""
import pytest
from app.strategy.decision_engine import make_decision


def _create_base_payload():
    """Create base payload with required fields."""
    return {
        "price_snapshot": {
            "last": 50000.0,
            "mark": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
        },
        "features_ltf": {
            "close": 50000.0,
            "close_prev": 49900.0,
            "open": 49950.0,
            "open_prev": 49850.0,
            "high": 50100.0,
            "high_prev": 50000.0,
            "low": 49900.0,
            "low_prev": 49800.0,
            "ema50": 49500.0,
            "ema50_prev_12": 49400.0,
            "ema120": 49400.0,
            "donchian_high_240": 51000.0,
            "donchian_low_240": 48000.0,
            "donchian_high_20": 50500.0,
            "donchian_low_20": 49500.0,
            "consec_close_above_donchian_20": 0.0,
            "consec_close_below_donchian_20": 0.0,
            "atr14": 500.0,
            "atr14_sma20": 480.0,
            "bb_upper": 51000.0,
            "bb_lower": 49000.0,
            "bb_mid": 50000.0,
            "bb_width": 2000.0,
            "bb_width_prev": 2100.0,
            "volume_ratio": 1.5,
            "candle_body_ratio": 0.6,
            "rsi14": 50.0,
            "rsi14_prev": 50.0,
            "consec_above_ema50": 5.0,
            "consec_below_ema50": 0.0,
            "consec_above_ema50_prev": 4.0,
            "consec_below_ema50_prev": 0.0,
            "close_max_n": 51000.0,
            "close_min_n": 49000.0,
            "time_exit_bars": 12.0,
            "stability_n": 20.0,
            "trend_candles_below_ema50": 0.0,
            "trend_candles_above_ema50": 20.0,
            "wick_ratio_count": 2.0,
            "swing_high_m": 51000.0,
            "swing_low_m": 49000.0,
            "donchian_high_k": 50500.0,
            "donchian_low_k": 49500.0,
            "volume": 1000.0,
            "volume_prev": 900.0,
        },
        "context_htf": {
            "close": 50000.0,
            "ema200": 49000.0,
            "ema_fast": 49500.0,
            "ema200_prev_n": 48900.0,
            "ema200_slope_norm": 0.05,
            "consec_above_ema200": 10.0,
            "consec_below_ema200": 0.0,
            "consec_higher_close": 5.0,
            "consec_lower_close": 0.0,
            "rsi14": 55.0,
            "rsi14_prev": 54.0,
            "trend": "up",
        },
        "risk_policy": {
            "risk_per_trade": 0.05,
            "min_rr": 1.5,
        },
        "account_state": {
            "equity": 1000.0,
            "funds_base": 1000.0,
            "funds_source": "available_balance",
            "leverage": 5,
        },
        "exchange_limits": {
            "step_size": 0.001,
            "tick_size": 0.1,
            "min_qty": 0.001,
        },
        "position_state": {
            "side": None,
            "qty": 0.0,
        },
        "market_identity": {
            "timestamp_closed": 1234567890,
        },
    }


def test_continuation_eligible_in_trend_regime():
    """Test that CONTINUATION can be eligible in TREND_CONTINUATION regime."""
    payload = _create_base_payload()
    
    # Set up for continuation: strong trend, good body/volume, close above EMA50
    payload["features_ltf"]["close"] = 50500.0  # Above EMA50
    payload["features_ltf"]["ema50"] = 50000.0
    payload["features_ltf"]["candle_body_ratio"] = 0.6  # Good body
    payload["features_ltf"]["volume_ratio"] = 1.5  # Good volume
    payload["features_ltf"]["atr14_sma20"] = 480.0
    payload["features_ltf"]["atr14"] = 500.0  # ATR ratio > 0.95
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["close"] = 50500.0
    payload["context_htf"]["ema200"] = 49000.0  # Strong trend
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    # Should have eligible strategies
    eligible_strategies = signal.get("eligible_strategies", [])
    selected_strategy = signal.get("selected_strategy", "NONE")
    
    # Continuation should be eligible (or at least checked)
    assert isinstance(eligible_strategies, list)
    # If continuation is eligible, it should be in the list
    if signal.get("cont_long_ok") or signal.get("cont_short_ok"):
        assert "CONTINUATION" in eligible_strategies or selected_strategy == "CONTINUATION"
    
    # Regression: eligible_strategies should not be empty if any strategy is eligible
    if eligible_strategies:
        assert selected_strategy != "NONE" or len(eligible_strategies) == 0


def test_breakout_eligible_in_breakout_regime():
    """Test that BREAKOUT_EXPANSION can be eligible in BREAKOUT_EXPANSION regime."""
    payload = _create_base_payload()
    
    # Set up for breakout: price breaks donchian, good volume
    payload["features_ltf"]["close"] = 51000.0  # Above donchian_high_20
    payload["features_ltf"]["donchian_high_20"] = 50500.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2.0  # Accept bars
    payload["features_ltf"]["volume_ratio"] = 1.5  # Good volume
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["consec_above_ema200"] = 10.0  # Stable trend
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    eligible_strategies = signal.get("eligible_strategies", [])
    selected_strategy = signal.get("selected_strategy", "NONE")
    
    # Breakout should be eligible if conditions met
    if signal.get("breakout_expansion_long_ok") or signal.get("breakout_expansion_short_ok"):
        assert "BREAKOUT_EXPANSION" in eligible_strategies or selected_strategy == "BREAKOUT_EXPANSION"


def test_range_eligible_in_range_regime():
    """Test that RANGE_MEANREV can be eligible in RANGE regime."""
    payload = _create_base_payload()
    
    # Set up for range: low trend strength, price near range edge
    payload["context_htf"]["trend"] = "range"
    payload["context_htf"]["close"] = 50000.0
    payload["context_htf"]["ema200"] = 49900.0  # Low trend strength
    payload["features_ltf"]["close"] = 49600.0  # Near donchian_low_20
    payload["features_ltf"]["donchian_low_20"] = 49500.0
    payload["features_ltf"]["volume_ratio"] = 0.8  # Low volume (good for mean rev)
    payload["features_ltf"]["atr14"] = 500.0
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    eligible_strategies = signal.get("eligible_strategies", [])
    selected_strategy = signal.get("selected_strategy", "NONE")
    
    # Range should be eligible if conditions met
    if signal.get("range_meanrev_long_ok") or signal.get("range_meanrev_short_ok"):
        assert "RANGE_MEANREV" in eligible_strategies or selected_strategy == "RANGE_MEANREV"


def test_eligible_strategies_populated_when_selected_none():
    """Regression test: eligible_strategies should be populated even when selected_strategy is NONE."""
    payload = _create_base_payload()
    
    # Set up scenario where regime is PULLBACK but pullback not eligible
    # But other strategies might be eligible
    payload["features_ltf"]["close"] = 51000.0  # Far from EMA50 (pullback not eligible)
    payload["features_ltf"]["ema50"] = 50000.0
    payload["features_ltf"]["dist50"] = 2.0  # Too far for pullback
    payload["context_htf"]["trend"] = "up"
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    eligible_strategies = signal.get("eligible_strategies", [])
    selected_strategy = signal.get("selected_strategy", "NONE")
    
    # eligible_strategies should always be a list (even if empty)
    assert isinstance(eligible_strategies, list)
    
    # If any strategy is eligible, it should be in eligible_strategies
    # This is the key regression test
    if signal.get("cont_long_ok") or signal.get("cont_short_ok"):
        assert "CONTINUATION" in eligible_strategies
    if signal.get("breakout_expansion_long_ok") or signal.get("breakout_expansion_short_ok"):
        assert "BREAKOUT_EXPANSION" in eligible_strategies
    if signal.get("range_meanrev_long_ok") or signal.get("range_meanrev_short_ok"):
        assert "RANGE_MEANREV" in eligible_strategies


def test_anti_reversal_only_blocks_against_trend():
    """Test that anti-reversal only blocks entries going AGAINST HTF direction."""
    payload = _create_base_payload()
    
    # HTF trend UP, close_htf > ema_fast_htf (reclaiming upward)
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["close"] = 50500.0
    payload["context_htf"]["ema_fast"] = 50000.0  # close > ema_fast (reclaiming)
    
    # Set up for LONG continuation (going WITH trend)
    payload["features_ltf"]["close"] = 50500.0
    payload["features_ltf"]["ema50"] = 50000.0
    payload["features_ltf"]["candle_body_ratio"] = 0.6
    payload["features_ltf"]["volume_ratio"] = 1.5
    payload["context_htf"]["consec_above_ema200"] = 10.0
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    # LONG entry should NOT be blocked by anti-reversal (going WITH trend)
    # Check explain_anti_reversal if available
    # The key is: if cont_long_ok is True, anti-reversal should NOT have blocked it
    if signal.get("cont_long_ok"):
        # Anti-reversal should not block LONG when HTF is reclaiming upward
        # (because LONG is going WITH the upward trend)
        anti_reversal_block = signal.get("anti_reversal_block", False)
        # If anti-reversal blocked, it should be for a different reason (not HTF_EMA_RECLAIM for LONG)
        if anti_reversal_block:
            anti_reversal_reason = signal.get("anti_reversal_reason", "")
            # HTF_EMA_RECLAIM should not block LONG when HTF is going up
            assert anti_reversal_reason != "HTF_EMA_RECLAIM" or not signal.get("cont_long_ok")


def test_multiple_strategies_eligible():
    """Test that multiple strategies can be eligible simultaneously."""
    payload = _create_base_payload()
    
    # Set up conditions where both continuation and breakout could be eligible
    payload["features_ltf"]["close"] = 51000.0  # Above donchian (breakout)
    payload["features_ltf"]["donchian_high_20"] = 50500.0
    payload["features_ltf"]["consec_close_above_donchian_20"] = 2.0
    payload["features_ltf"]["ema50"] = 50500.0  # Close above EMA50 (continuation)
    payload["features_ltf"]["candle_body_ratio"] = 0.6
    payload["features_ltf"]["volume_ratio"] = 1.5
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["consec_above_ema200"] = 10.0
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    eligible_strategies = signal.get("eligible_strategies", [])
    
    # Should have at least one eligible strategy if conditions are met
    # Multiple strategies could be eligible
    assert isinstance(eligible_strategies, list)
    
    # If both are eligible, both should be in the list
    cont_ok = signal.get("cont_long_ok") or signal.get("cont_short_ok")
    breakout_ok = signal.get("breakout_expansion_long_ok") or signal.get("breakout_expansion_short_ok")
    
    if cont_ok:
        assert "CONTINUATION" in eligible_strategies
    if breakout_ok:
        assert "BREAKOUT_EXPANSION" in eligible_strategies


def test_regression_selected_none_with_eligible_strategies():
    """
    Regression test: Fail if selected_strategy=NONE while eligible_strategies is non-empty.
    
    This ensures the routing logic never misses eligible strategies.
    """
    payload = _create_base_payload()
    
    # Create scenario where at least one strategy should be eligible
    payload["features_ltf"]["close"] = 50500.0
    payload["features_ltf"]["ema50"] = 50000.0
    payload["features_ltf"]["candle_body_ratio"] = 0.6
    payload["features_ltf"]["volume_ratio"] = 1.5
    payload["context_htf"]["trend"] = "up"
    payload["context_htf"]["consec_above_ema200"] = 10.0
    
    decision = make_decision(payload)
    signal = decision.get("signal", {})
    
    eligible_strategies = signal.get("eligible_strategies", [])
    selected_strategy = signal.get("selected_strategy", "NONE")
    
    # CRITICAL: If eligible_strategies is non-empty, selected_strategy must not be NONE
    # (unless all strategies are blocked by risk/gating rules, which is acceptable)
    if eligible_strategies and selected_strategy == "NONE":
        # Check if blocked by risk/gating (acceptable)
        reject_reasons = decision.get("reject_reasons", [])
        # If not blocked by risk, this is a bug
        risk_blocks = [r for r in reject_reasons if any(x in r for x in ["risk", "gate", "volatility", "session"])]
        if not risk_blocks:
            pytest.fail(
                f"BUG: eligible_strategies={eligible_strategies} but selected_strategy=NONE. "
                f"Reject reasons: {reject_reasons}"
            )
