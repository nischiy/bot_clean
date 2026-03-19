"""
Runtime Invariant Enforcement: Explicit checks for documented invariants.

Centralizes invariant failures with explicit error codes.
"""
from __future__ import annotations

from typing import Dict, Any, Tuple, Optional


FINAL_AUTHORITY_STAGES = {
    "TIME_EXIT",
    "HARD_BLOCK",
    "LEGACY_CONFIRMED",
    "PREDICTIVE_EARLY",
    "QUALITY_REJECT",
    "EVENT_REJECT",
    "LATE_REJECT",
    "ROUTER_REJECT",
}


class InvariantViolation(RuntimeError):
    """Raised when a runtime invariant is violated."""
    def __init__(self, error_code: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.error_code = error_code
        self.details = details or {}
        super().__init__(f"{error_code}: {message}")


def check_decision_has_valid_payload(decision: Dict[str, Any], payload: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Invariant: Decision without valid payload → HOLD.
    
    Returns:
        (is_valid: bool, error_code: str)
    """
    if payload is None:
        return False, "INV_DECISION_NO_PAYLOAD"
    
    # Check payload has required structure
    if not isinstance(payload, dict):
        return False, "INV_PAYLOAD_NOT_DICT"
    
    market_identity = payload.get("market_identity", {})
    if not market_identity.get("symbol"):
        return False, "INV_PAYLOAD_MISSING_SYMBOL"
    
    if not market_identity.get("timestamp_closed"):
        return False, "INV_PAYLOAD_MISSING_TIMESTAMP"
    
    # If decision intent is not HOLD, payload must be valid
    intent = decision.get("intent", "HOLD")
    if intent != "HOLD" and payload is None:
        return False, "INV_DECISION_INTENT_NO_PAYLOAD"
    
    return True, ""


def check_trade_plan_passed_risk(trade_plan: Optional[Dict[str, Any]], rejections: list) -> Tuple[bool, str]:
    """
    Invariant: trade_plan without passed risk checks → REJECT.
    
    Returns:
        (is_valid: bool, error_code: str)
    """
    if trade_plan is None:
        # This is valid - no trade plan means risk checks blocked it
        return True, ""
    
    # If trade_plan exists, it must have passed risk checks (no rejections)
    if rejections:
        return False, f"INV_TRADE_PLAN_WITH_REJECTIONS: {rejections[0]}"
    
    # Validate trade_plan has required fields
    if not isinstance(trade_plan, dict):
        return False, "INV_TRADE_PLAN_NOT_DICT"
    
    action = trade_plan.get("action")
    if action not in ("OPEN", "CLOSE", "UPDATE_SLTP"):
        return False, f"INV_TRADE_PLAN_INVALID_ACTION: {action}"
    
    return True, ""


def check_execution_has_sl(trade_plan: Dict[str, Any], intent: str) -> Tuple[bool, str]:
    """
    Invariant: Execution attempted without SL → HARD STOP.
    
    Returns:
        (is_valid: bool, error_code: str)
    """
    if intent not in ("LONG", "SHORT"):
        # CLOSE/UPDATE_SLTP don't require SL check here
        return True, ""
    
    action = trade_plan.get("action")
    if action == "OPEN":
        stop_loss = trade_plan.get("stop_loss")
        if not stop_loss:
            return False, "INV_EXECUTION_NO_SL"
        
        if not isinstance(stop_loss, dict):
            return False, "INV_EXECUTION_SL_NOT_DICT"
        
        sl_price = stop_loss.get("price")
        if sl_price is None or sl_price <= 0:
            return False, "INV_EXECUTION_INVALID_SL_PRICE"
    
    return True, ""


def check_payload_has_equity(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Invariant: Payload without equity → fail-closed.
    
    Returns:
        (is_valid: bool, error_code: str)
    """
    account_state = payload.get("account_state", {})
    equity = account_state.get("equity")
    
    if equity is None:
        return False, "INV_PAYLOAD_MISSING_EQUITY"
    
    try:
        equity_float = float(equity)
        if equity_float <= 0:
            return False, "INV_PAYLOAD_INVALID_EQUITY"
    except (TypeError, ValueError):
        return False, "INV_PAYLOAD_EQUITY_NOT_NUMERIC"
    
    return True, ""


def check_decision_validated(decision: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Invariant: Decision must be validated against schema.
    
    Returns:
        (is_valid: bool, error_code: str)
    """
    intent = decision.get("intent")
    if intent not in ("LONG", "SHORT", "HOLD", "CLOSE", "UPDATE_SLTP"):
        return False, f"INV_DECISION_INVALID_INTENT: {intent}"
    
    # Check required fields exist
    if "reject_reasons" not in decision:
        return False, "INV_DECISION_MISSING_REJECT_REASONS"
    
    if "signal" not in decision:
        return False, "INV_DECISION_MISSING_SIGNAL"

    final_authority_stage = decision.get("final_authority_stage")
    if final_authority_stage not in FINAL_AUTHORITY_STAGES:
        return False, f"INV_DECISION_INVALID_FINAL_AUTHORITY: {final_authority_stage}"
    
    return True, ""


def check_decision_semantics(decision: Dict[str, Any]) -> Tuple[bool, str]:
    signal = decision.get("signal") or {}
    execution_decision = str(decision.get("execution_decision") or "")
    predictive_bias = str(signal.get("predictive_bias") or "NEUTRAL")
    selected_strategy = str(signal.get("selected_strategy") or "NONE")
    router_candidates = list(signal.get("router_candidates") or [])
    event_hard_block = bool(signal.get("event_hard_block"))
    hard_block_reason = signal.get("hard_block_reason")
    missing_fields = list(decision.get("missing_fields") or [])

    if execution_decision.startswith("OPEN_LONG") and predictive_bias != "LONG":
        return False, "INV_EXECUTION_LONG_BIAS_MISMATCH"
    if execution_decision.startswith("OPEN_SHORT") and predictive_bias != "SHORT":
        return False, "INV_EXECUTION_SHORT_BIAS_MISMATCH"
    if selected_strategy not in ("NONE", "TIME_EXIT") and selected_strategy not in router_candidates:
        return False, "INV_SELECTED_STRATEGY_NOT_IN_ROUTER_CANDIDATES"
    if event_hard_block and not hard_block_reason and not ((signal.get("event_gate") or {}).get("hard_block_reason")):
        return False, "INV_EVENT_HARD_BLOCK_MISSING_REASON"
    if missing_fields and decision.get("intent") != "HOLD":
        return False, "INV_MISSING_FIELDS_NOT_HELD"
    if execution_decision == "HOLD_LOW_QUALITY" and decision.get("hold_reason") not in {
        "NEUTRAL_MARKET",
        "UNCONFIRMED_DIRECTION",
        "PLAN_BUILD_FAILED",
    }:
        return False, "INV_HOLD_LOW_QUALITY_MISSING_REASON"
    return True, ""


def enforce_invariants(
    *,
    decision: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    trade_plan: Optional[Dict[str, Any]] = None,
    rejections: Optional[list] = None,
    intent: Optional[str] = None,
) -> None:
    """
    Enforce all runtime invariants.
    
    Raises InvariantViolation if any invariant is violated.
    """
    errors = []
    
    if decision is not None:
        # Check decision has valid payload
        is_valid, error_code = check_decision_has_valid_payload(decision, payload)
        if not is_valid:
            errors.append((error_code, f"Decision without valid payload: {error_code}"))
        
        # Check decision is validated
        is_valid, error_code = check_decision_validated(decision)
        if not is_valid:
            errors.append((error_code, f"Decision not validated: {error_code}"))

        is_valid, error_code = check_decision_semantics(decision)
        if not is_valid:
            errors.append((error_code, f"Decision semantics invalid: {error_code}"))
    
    if trade_plan is not None:
        # Check trade_plan passed risk
        is_valid, error_code = check_trade_plan_passed_risk(trade_plan, rejections or [])
        if not is_valid:
            errors.append((error_code, f"Trade plan with rejections: {error_code}"))
        
        # Check execution has SL
        if intent:
            is_valid, error_code = check_execution_has_sl(trade_plan, intent)
            if not is_valid:
                errors.append((error_code, f"Execution without SL: {error_code}"))
    
    if payload is not None:
        # Check payload has equity
        is_valid, error_code = check_payload_has_equity(payload)
        if not is_valid:
            errors.append((error_code, f"Payload without equity: {error_code}"))
    
    if errors:
        error_codes = [e[0] for e in errors]
        messages = [e[1] for e in errors]
        raise InvariantViolation(
            error_code=error_codes[0],
            message="; ".join(messages),
            details={"errors": error_codes, "messages": messages}
        )
