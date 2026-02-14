"""
Tests for decision determinism guarantees.

Ensures same payload.json → same decision.json with no randomness or time-based drift.
"""
import pytest
import json
from app.strategy.decision_engine import make_decision
from app.core.validation import validate_decision


def _create_minimal_payload() -> dict:
    """Create minimal valid payload for testing."""
    return {
        "market_identity": {
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "timestamp_closed": 1700000000,
        },
        "price_snapshot": {
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "mark": 50000.0,
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
            "volume": 1000.0,
            "volume_prev": 900.0,
            "ema50": 49900.0,
            "ema50_prev_12": 49800.0,
            "ema120": 49800.0,
            "donchian_high_240": 51000.0,
            "donchian_low_240": 49000.0,
            "donchian_high_20": 50500.0,
            "donchian_low_20": 49500.0,
            "consec_close_above_donchian_20": 0.0,
            "consec_close_below_donchian_20": 0.0,
            "consec_above_ema50": 0.0,
            "consec_below_ema50": 0.0,
            "consec_above_ema50_prev": 0.0,
            "consec_below_ema50_prev": 0.0,
            "atr14": 200.0,
            "atr14_sma20": 180.0,
            "bb_upper": 50200.0,
            "bb_lower": 49800.0,
            "bb_mid": 50000.0,
            "bb_width": 400.0,
            "bb_width_prev": 380.0,
            "candle_body_ratio": 0.5,
            "volume_ratio": 1.1,
            "rsi14": 50.0,
            "rsi14_prev": 48.0,
            "close_max_n": 50500.0,
            "close_min_n": 49500.0,
            "time_exit_bars": 12.0,
            "stability_n": 20.0,
            "trend_candles_below_ema50": 10.0,
            "trend_candles_above_ema50": 0.0,
            "wick_ratio_count": 5.0,
            "swing_high_m": 50500.0,
            "swing_low_m": 49500.0,
            "donchian_high_k": 50500.0,
            "donchian_low_k": 49500.0,
        },
        "context_htf": {
            "close": 50000.0,
            "ema200": 49000.0,
            "ema200_prev_n": 48900.0,
            "ema200_slope_norm": 0.05,
            "consec_above_ema200": 0.0,
            "consec_below_ema200": 10.0,
            "consec_higher_close": 0.0,
            "consec_lower_close": 5.0,
            "atr14": 300.0,
            "ema_fast": 49500.0,
            "rsi14": 45.0,
            "rsi14_prev": 43.0,
            "trend": "down",
        },
        "account_state": {
            "equity": 1000.0,
            "funds_base": 1000.0,
            "funds_source": "available_balance",
            "leverage": 5,
            "margin_type": "isolated",
        },
        "position_state": {
            "side": None,
            "qty": 0.0,
            "entry": 0.0,
        },
        "exchange_limits": {
            "step_size": 0.001,
            "min_qty": 0.001,
        },
        "risk_policy": {
            "risk_per_trade": 0.05,
            "min_rr": 1.5,
        },
        "fees": {
            "maker": 0.0002,
            "taker": 0.0004,
        },
    }


class TestDecisionDeterminism:
    """Test decision determinism guarantees."""
    
    def test_same_payload_produces_same_decision(self):
        """Test that identical payload produces identical decision."""
        payload = _create_minimal_payload()
        
        # Make decision twice
        decision1 = make_decision(payload)
        decision2 = make_decision(payload)
        
        # Compare critical fields (excluding timestamps that may differ)
        assert decision1["intent"] == decision2["intent"]
        assert decision1.get("entry") == decision2.get("entry")
        assert decision1.get("sl") == decision2.get("sl")
        assert decision1.get("tp") == decision2.get("tp")
        assert decision1.get("rr") == decision2.get("rr")
        assert decision1.get("reject_reasons") == decision2.get("reject_reasons")
        assert decision1.get("signal", {}).get("selected_strategy") == decision2.get("signal", {}).get("selected_strategy")
    
    def test_decision_no_randomness(self):
        """Test that decision making uses no random values."""
        import inspect
        import ast
        
        # Read decision_engine source
        import app.strategy.decision_engine as de_module
        source = inspect.getsource(de_module)
        
        # Check for random imports/usage
        tree = ast.parse(source)
        has_random = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "random":
                        has_random = True
            elif isinstance(node, ast.ImportFrom):
                if node.module == "random":
                    has_random = True
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "random":
                        has_random = True
        
        assert not has_random, "Decision engine must not use random module"
    
    def test_decision_no_time_based_logic(self):
        """Test that decision making doesn't use current time (except candle timestamp)."""
        import inspect
        import ast
        
        import app.strategy.decision_engine as de_module
        source = inspect.getsource(de_module)
        tree = ast.parse(source)
        
        # Check for time.time() or datetime.now() calls (except in comments/docstrings)
        has_time_logic = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "time" and node.func.attr == "time":
                            has_time_logic = True
                        elif node.func.value.id == "datetime" and node.func.attr == "now":
                            has_time_logic = True
        
        assert not has_time_logic, "Decision engine must not use time.time() or datetime.now()"
    
    def test_decision_deterministic_with_dict_iteration(self):
        """Test that dict iteration order doesn't affect decision."""
        payload1 = _create_minimal_payload()
        payload2 = _create_minimal_payload()
        
        # Shuffle dict order (Python 3.7+ preserves insertion order)
        # Create new dicts with different key order
        payload1_shuffled = {}
        payload2_shuffled = {}
        
        # Add keys in different orders
        keys = list(payload1.keys())
        for key in reversed(keys):
            payload1_shuffled[key] = payload1[key]
        for key in keys:
            payload2_shuffled[key] = payload2[key]
        
        decision1 = make_decision(payload1_shuffled)
        decision2 = make_decision(payload2_shuffled)
        
        # Decisions should be identical regardless of dict key order
        assert decision1["intent"] == decision2["intent"]
        assert decision1.get("entry") == decision2.get("entry")
    
    def test_decision_validates_against_schema(self):
        """Test that decisions are validated against schema."""
        payload = _create_minimal_payload()
        decision = make_decision(payload)
        
        # Decision should be valid
        is_valid, errors = validate_decision(decision)
        assert is_valid, f"Decision must be valid: {errors}"
