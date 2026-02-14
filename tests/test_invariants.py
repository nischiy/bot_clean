"""
Tests for runtime invariant enforcement.

Ensures documented invariants are enforced with explicit error codes.
"""
import pytest
from core.invariants import (
    check_decision_has_valid_payload,
    check_trade_plan_passed_risk,
    check_execution_has_sl,
    check_payload_has_equity,
    check_decision_validated,
    enforce_invariants,
    InvariantViolation,
)


class TestInvariantEnforcement:
    """Test runtime invariant enforcement."""
    
    def test_check_decision_has_valid_payload_valid(self):
        """Test decision with valid payload passes."""
        decision = {"intent": "HOLD"}
        payload = {
            "market_identity": {
                "symbol": "BTCUSDT",
                "timestamp_closed": 1700000000,
            }
        }
        is_valid, error = check_decision_has_valid_payload(decision, payload)
        assert is_valid
        assert error == ""
    
    def test_check_decision_has_valid_payload_no_payload(self):
        """Test decision without payload fails."""
        decision = {"intent": "LONG"}
        is_valid, error = check_decision_has_valid_payload(decision, None)
        assert not is_valid
        assert error == "INV_DECISION_NO_PAYLOAD"
    
    def test_check_trade_plan_passed_risk_valid(self):
        """Test trade_plan with no rejections passes."""
        trade_plan = {"action": "OPEN", "symbol": "BTCUSDT"}
        is_valid, error = check_trade_plan_passed_risk(trade_plan, [])
        assert is_valid
        assert error == ""
    
    def test_check_trade_plan_passed_risk_with_rejections(self):
        """Test trade_plan with rejections fails."""
        trade_plan = {"action": "OPEN", "symbol": "BTCUSDT"}
        rejections = ["risk_reject: daily_drawdown"]
        is_valid, error = check_trade_plan_passed_risk(trade_plan, rejections)
        assert not is_valid
        assert "INV_TRADE_PLAN_WITH_REJECTIONS" in error
    
    def test_check_execution_has_sl_valid(self):
        """Test execution with SL passes."""
        trade_plan = {
            "action": "OPEN",
            "stop_loss": {"price": 49000.0},
        }
        is_valid, error = check_execution_has_sl(trade_plan, "LONG")
        assert is_valid
        assert error == ""
    
    def test_check_execution_has_sl_missing(self):
        """Test execution without SL fails."""
        trade_plan = {"action": "OPEN"}
        is_valid, error = check_execution_has_sl(trade_plan, "LONG")
        assert not is_valid
        assert error == "INV_EXECUTION_NO_SL"
    
    def test_check_payload_has_equity_valid(self):
        """Test payload with equity passes."""
        payload = {
            "account_state": {
                "equity": 1000.0,
            }
        }
        is_valid, error = check_payload_has_equity(payload)
        assert is_valid
        assert error == ""
    
    def test_check_payload_has_equity_missing(self):
        """Test payload without equity fails."""
        payload = {"account_state": {}}
        is_valid, error = check_payload_has_equity(payload)
        assert not is_valid
        assert error == "INV_PAYLOAD_MISSING_EQUITY"
    
    def test_check_decision_validated_valid(self):
        """Test validated decision passes."""
        decision = {
            "intent": "HOLD",
            "reject_reasons": [],
            "signal": {},
        }
        is_valid, error = check_decision_validated(decision)
        assert is_valid
        assert error == ""
    
    def test_check_decision_validated_invalid_intent(self):
        """Test decision with invalid intent fails."""
        decision = {
            "intent": "INVALID",
            "reject_reasons": [],
            "signal": {},
        }
        is_valid, error = check_decision_validated(decision)
        assert not is_valid
        assert "INV_DECISION_INVALID_INTENT" in error
    
    def test_enforce_invariants_all_pass(self):
        """Test enforce_invariants when all checks pass."""
        decision = {"intent": "HOLD", "reject_reasons": [], "signal": {}}
        payload = {"account_state": {"equity": 1000.0}, "market_identity": {"symbol": "BTCUSDT", "timestamp_closed": 1700000000}}
        trade_plan = {"action": "OPEN", "stop_loss": {"price": 49000.0}}
        
        # Should not raise
        enforce_invariants(decision=decision, payload=payload, trade_plan=trade_plan, intent="LONG")
    
    def test_enforce_invariants_violation_raises(self):
        """Test enforce_invariants raises on violation."""
        decision = {"intent": "LONG", "reject_reasons": [], "signal": {}}
        payload = None  # Missing payload
        
        with pytest.raises(InvariantViolation) as exc_info:
            enforce_invariants(decision=decision, payload=payload)
        
        assert exc_info.value.error_code.startswith("INV_")
