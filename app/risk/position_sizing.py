"""
Position Sizing: Risk-based position sizing with leverage caps.
"""
from __future__ import annotations

import math
from typing import Dict, Any, Tuple, Optional, List


def calculate_position_size(
    equity: float,
    available: float,
    entry: float,
    sl: float,
    risk_per_trade: float,
    step_size: float,
    min_qty: float,
    leverage: int
) -> Tuple[Optional[float], Optional[int], List[str]]:
    """
    Calculate position size based on risk.
    
    Formula:
        risk_usd = funds_base * risk_per_trade
        qty = risk_usd / abs(entry - sl)
        qty must respect step_size and min_qty
        leverage ≤ max_leverage, isolated margin
    
    Returns:
        (qty: Optional[float], leverage: Optional[int], errors: List[str])
    """
    errors = []
    
    if equity is None or equity <= 0:
        errors.append("missing_or_invalid_equity")
    if available is None:
        errors.append("funds_source_missing")
    elif available <= 0:
        errors.append("funds_nonpositive")
    if errors:
        return None, None, errors
    
    if entry <= 0:
        errors.append("invalid_entry")
        return None, None, errors
    
    if sl <= 0:
        errors.append("invalid_sl")
        return None, None, errors
    
    if step_size <= 0:
        errors.append("invalid_step_size")
        return None, None, errors
    
    if min_qty <= 0:
        errors.append("invalid_min_qty")
        return None, None, errors
    
    # Canonical funds base = available balance/margin
    funds_base = float(available)
    risk_usd = funds_base * risk_per_trade
    
    # Calculate distance to SL
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        errors.append("invalid_sl_distance")
        return None, None, errors
    
    # Calculate raw quantity
    qty_raw = risk_usd / sl_distance
    
    # Round to step_size
    qty = math.floor(qty_raw / step_size) * step_size
    
    # Ensure minimum quantity
    if qty < min_qty:
        errors.append(
            f"min_qty_not_met_after_rounding: qty={qty} min_qty={min_qty} step={step_size} risk_usd={risk_usd}"
        )
        return None, None, errors
    
    # Calculate notional
    notional = qty * entry
    
    if leverage is None or leverage <= 0:
        errors.append("invalid_leverage")
        return None, None, errors

    # Final check: ensure we have enough available margin
    margin_needed = notional / float(leverage)
    if margin_needed > funds_base:
        errors.append(f"insufficient_margin: {margin_needed} > {funds_base}")
        return None, None, errors
    
    return qty, leverage, []


def round_to_step(value: float, step: float) -> float:
    """Round value to nearest step."""
    if step <= 0:
        return value
    return math.floor(value / step) * step
