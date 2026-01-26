"""
Position Sizing: Risk-based position sizing with leverage caps.
"""
from __future__ import annotations

import math
from typing import Dict, Any, Tuple, Optional, List


def calculate_position_size(
    equity: float,
    entry: float,
    sl: float,
    risk_per_trade: float,
    step_size: float,
    min_qty: float,
    max_leverage: int = 5
) -> Tuple[Optional[float], Optional[int], List[str]]:
    """
    Calculate position size based on risk.
    
    Formula:
        risk_usd = equity * risk_per_trade
        qty = risk_usd / abs(entry - sl)
        qty must respect step_size and min_qty
        leverage ≤ max_leverage, isolated margin
    
    Returns:
        (qty: Optional[float], leverage: Optional[int], errors: List[str])
    """
    errors = []
    
    if equity <= 0:
        errors.append("invalid_equity")
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
    
    # Calculate risk USD
    risk_usd = equity * risk_per_trade
    
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
        errors.append(f"qty_below_min: {qty} < {min_qty}")
        return None, None, errors
    
    # Calculate notional
    notional = qty * entry
    
    # Calculate leverage (isolated margin)
    # margin = notional / leverage
    # We want margin <= equity * risk_per_trade (but we already used that for qty)
    # Actually, for isolated margin, we need to ensure margin is available
    # Let's use a conservative approach: leverage = min(5, ceil(notional / (equity * 0.2)))
    margin_required = notional / max_leverage
    if margin_required > equity * 0.2:  # Use max 20% of equity as margin
        # Adjust leverage to fit
        leverage = math.ceil(notional / (equity * 0.2))
        leverage = min(leverage, max_leverage)
    else:
        leverage = max_leverage
    
    # Final check: ensure we have enough margin
    margin_needed = notional / leverage
    if margin_needed > equity:
        errors.append(f"insufficient_margin: {margin_needed} > {equity}")
        return None, None, errors
    
    return qty, leverage, []


def round_to_step(value: float, step: float) -> float:
    """Round value to nearest step."""
    if step <= 0:
        return value
    return math.floor(value / step) * step
