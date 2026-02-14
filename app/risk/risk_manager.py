"""
Risk Manager: Final authority that produces trade_plan.json.
Applies kill-switch checks and position sizing.
Only produces trade_plan.json if ALL checks pass.
"""
from __future__ import annotations

import time
import uuid
import math
from typing import Dict, Any, List, Tuple, Optional

from core.risk_guard import is_killed, evaluate
from core.config import settings
from app.core.validation import validate_trade_plan
from app.risk.position_sizing import calculate_position_size, round_to_step
from app.state.state_manager import load_trade_cooldown_state


def check_kill_switches(
    payload: Dict[str, Any],
    decision: Dict[str, Any],
    daily_state: Dict[str, Any],
    exchange_positions: list
) -> List[str]:
    """
    Apply kill-switch checks.
    
    Returns:
        List of rejection reasons (empty if all checks pass)
    """
    rejections = []
    
    # 1. Kill-switch flag
    if is_killed():
        rejections.append("kill_switch_engaged")
    
    # 2. Daily drawdown exceeded
    daily_pnl = daily_state.get("realized_pnl", 0.0)
    starting_equity = daily_state.get("starting_equity", 0.0)
    if starting_equity > 0:
        drawdown_pct = abs(min(0, daily_pnl)) / starting_equity * 100.0
        max_dd = payload.get("risk_policy", {}).get(
            "max_daily_drawdown", settings.get_float("RISK_MAX_DD_PCT_DAY") / 100.0
        ) * 100.0
        if drawdown_pct >= max_dd:
            rejections.append(f"daily_drawdown_exceeded: {drawdown_pct:.2f}% >= {max_dd:.2f}%")
    
    # 3. Consecutive losses exceeded
    consec_losses = daily_state.get("consecutive_losses", 0)
    max_consec = payload.get("risk_policy", {}).get(
        "max_consecutive_losses", settings.get_int("RISK_MAX_CONSEC_LOSSES")
    )
    if consec_losses >= max_consec:
        rejections.append(f"consecutive_losses_exceeded: {consec_losses} >= {max_consec}")
    
    # 4. More than one open position
    open_positions = [p for p in exchange_positions if float(p.get("positionAmt", 0) or 0) != 0.0]
    if len(open_positions) > 1:
        rejections.append(f"multiple_positions: {len(open_positions)} > 1")
    
    # 5. Spread above threshold
    price_snapshot = payload.get("price_snapshot", {})
    bid = price_snapshot.get("bid", 0.0)
    ask = price_snapshot.get("ask", 0.0)
    last = price_snapshot.get("last", 0.0)
    if last > 0:
        spread_pct = abs(ask - bid) / last * 100.0
        spread_max_pct = settings.get_float("SPREAD_MAX_PCT")
        if spread_pct > spread_max_pct:
            rejections.append(f"spread_too_wide: {spread_pct:.2f}%")
    
    # 6. Abnormal ATR spike
    atr14 = payload.get("features_ltf", {}).get("atr14", 0.0)
    if atr14 <= 0:
        rejections.append("invalid_atr")
    else:
        atr_spike_pct = settings.get_float("ATR_SPIKE_MAX_PCT")
        if atr14 > last * atr_spike_pct:
            rejections.append(f"abnormal_atr_spike: {atr14} > {last * atr_spike_pct}")
    
    # 7. Stale data check
    timestamp_closed = payload.get("market_identity", {}).get("timestamp_closed", 0)
    now_ts = int(time.time())
    max_age = settings.get_int("DATA_MAX_AGE_SECONDS")
    if timestamp_closed < now_ts - max_age:
        rejections.append("stale_data")
    
    # 8. Execution desync (check if we have unexecuted orders)
    # This would require order tracking - simplified for now
    
    # 9. Open position without SL
    for pos in open_positions:
        pos_side = pos.get("side", "").upper()
        if pos_side in ("LONG", "SHORT"):
            # Check if position has stop loss order
            # This would require order tracking - simplified for now
            pass
    
    return rejections


def create_trade_plan(
    payload: Dict[str, Any],
    decision: Dict[str, Any],
    daily_state: Dict[str, Any],
    exchange_positions: list
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Create trade_plan.json from validated payload and decision.
    
    Returns:
        (trade_plan: Optional[Dict], rejections: List[str])
    """
    rejections = []
    
    intent = decision.get("intent")
    if intent not in ("LONG", "SHORT", "CLOSE", "UPDATE_SLTP"):
        rejections.append(f"invalid_intent: {intent}")
        return None, rejections
    
    # Cooldown check (any recent trade plan)
    cooldown_min = settings.get_int("TRADE_COOLDOWN_MINUTES")
    ts = payload.get("market_identity", {}).get("timestamp_closed")
    if intent in ("LONG", "SHORT") and cooldown_min > 0 and ts is not None:
        cooldown_state = load_trade_cooldown_state()
        last_ts = max(
            [v for v in (cooldown_state.get("LONG"), cooldown_state.get("SHORT")) if v is not None],
            default=None,
        )
        if last_ts is not None and int(ts) - int(last_ts) < cooldown_min * 60:
            rejections.append(f"cooldown_active: {int(ts) - int(last_ts)}s<{cooldown_min * 60}s")

    if rejections:
        return None, rejections

    # Apply kill-switch checks
    kill_switch_rejections = check_kill_switches(payload, decision, daily_state, exchange_positions)
    rejections.extend(kill_switch_rejections)
    
    if rejections:
        return None, rejections
    
    # Extract required fields
    symbol = payload.get("market_identity", {}).get("symbol")
    entry = decision.get("entry")
    sl = decision.get("sl")
    tp = decision.get("tp")
    tp_targets = decision.get("tp_targets", [])
    account_state = payload.get("account_state", {})
    exchange_limits = payload.get("exchange_limits", {})
    risk_policy = payload.get("risk_policy", {})
    
    if not symbol:
        rejections.append("missing_symbol")
        return None, rejections
    
    # Handle UPDATE_SLTP action
    if intent == "UPDATE_SLTP":
        pos_state = payload.get("position_state", {})
        pos_side = pos_state.get("side")
        pos_qty = float(pos_state.get("qty", 0.0) or 0.0)
        pos_entry = float(pos_state.get("entry", 0.0) or 0.0)
        new_sl = decision.get("sl")
        
        if pos_side not in ("LONG", "SHORT") or pos_qty <= 0:
            rejections.append("missing_or_invalid_position")
            return None, rejections
        if new_sl is None or new_sl <= 0:
            rejections.append("missing_or_invalid_sl")
            return None, rejections
        
        ts = payload.get("market_identity", {}).get("timestamp_closed")
        if ts is None:
            rejections.append("missing_timestamp_closed")
            return None, rejections
        
        base_id = f"{symbol}-{int(ts)}-updatesltp"
        trade_plan = {
            "symbol": symbol,
            "side": "BUY" if pos_side == "LONG" else "SELL",
            "type": "MARKET",
            "client_order_id": f"{base_id}-sltp",
            "timeframe": payload.get("market_identity", {}).get("timeframe"),
            "action": "UPDATE_SLTP",
            "stop_loss": {
                "price": new_sl,
                "client_order_id": f"{base_id}-sl"
            },
            "leverage": account_state.get("leverage", 1),
            "margin_type": account_state.get("margin_type", "isolated"),
            "timestamp": int(ts)
        }
        
        is_valid, validation_errors = validate_trade_plan(trade_plan)
        if not is_valid:
            rejections.extend(validation_errors)
            return None, rejections
        
        return trade_plan, []
    
    if intent in ("LONG", "SHORT"):
        if entry is None or entry <= 0:
            rejections.append("missing_or_invalid_entry")
            return None, rejections
        if sl is None or sl <= 0:
            rejections.append("missing_or_invalid_sl")
            return None, rejections
        # Allow either tp or tp_targets
        if not tp_targets and (tp is None or tp <= 0):
            rejections.append("missing_or_invalid_tp")
            return None, rejections
    
    if intent in ("LONG", "SHORT"):
        equity = account_state.get("equity", None)
        funds_base = account_state.get("funds_base", None)
        risk_per_trade = risk_policy.get("risk_per_trade", 0.05)
        step_size = exchange_limits.get("step_size", 0.0)
        min_qty = exchange_limits.get("min_qty", 0.0)
        leverage = account_state.get("leverage", None)

        if funds_base is None:
            rejections.append("funds_source_missing")
            return None, rejections
        if float(funds_base or 0.0) <= 0.0:
            rejections.append("funds_nonpositive")
            return None, rejections
        if leverage is None or int(leverage) <= 0:
            rejections.append("invalid_leverage")
            return None, rejections

        qty, leverage, sizing_errors = calculate_position_size(
            equity=float(equity) if equity is not None else 0.0,
            available=float(funds_base),
            entry=entry,
            sl=sl,
            risk_per_trade=risk_per_trade,
            step_size=step_size,
            min_qty=min_qty,
            leverage=int(leverage)
        )

        if qty is None:
            rejections.extend(sizing_errors)
            return None, rejections
        # Optional: reduce size by 30% when adaptive_soft_stability (soft zone + confirm + volume ok)
        adaptive_enabled = settings.get_bool("ADAPTIVE_SOFT_STABILITY_ENABLED", False)
        if adaptive_enabled and decision.get("signal", {}).get("adaptive_soft_stability"):
            qty = round_to_step(float(qty) * 0.7, step_size)
            qty = max(qty, min_qty) if min_qty and qty is not None else qty
    else:
        pos_state = payload.get("position_state", {})
        pos_side = pos_state.get("side")
        pos_qty = float(pos_state.get("qty", 0.0) or 0.0)
        if pos_side not in ("LONG", "SHORT") or pos_qty <= 0:
            rejections.append("missing_or_invalid_position")
            return None, rejections
        qty = abs(pos_qty)
        leverage = account_state.get("leverage", 1)
    
    # Generate client order IDs (deterministic by closed candle timestamp)
    ts = payload.get("market_identity", {}).get("timestamp_closed")
    if ts is None:
        rejections.append("missing_timestamp_closed")
        return None, rejections
    base_id = f"{symbol}-{int(ts)}"
    entry_order_id = f"{base_id}-entry"
    sl_order_id = f"{base_id}-sl"
    
    # Build trade plan
    if intent in ("LONG", "SHORT"):
        # Split quantity if we have tp_targets, otherwise use single TP
        step_size = exchange_limits.get("step_size", 0.0)
        min_qty = exchange_limits.get("min_qty", 0.0)
        
        if tp_targets and len(tp_targets) >= 2:
            # Multiple TP orders: split qty using TP1_FRACTION
            tp1_fraction = settings.get_float("TP1_FRACTION", 0.4)
            qty_tp1_raw = qty * tp1_fraction
            qty_tp2_raw = qty * (1.0 - tp1_fraction)
            
            # Round to step_size
            qty_tp1 = round_to_step(qty_tp1_raw, step_size)
            qty_tp2 = round_to_step(qty_tp2_raw, step_size)
            
            # Ensure minimum quantities
            if qty_tp1 < min_qty or qty_tp2 < min_qty:
                rejections.append(
                    f"min_qty_not_met_after_split: qty_tp1={qty_tp1} qty_tp2={qty_tp2} min_qty={min_qty}"
                )
                return None, rejections
            
            # Adjust to ensure qty_tp1 + qty_tp2 == qty_total after rounding
            qty_total_rounded = qty_tp1 + qty_tp2
            if abs(qty_total_rounded - qty) > step_size:
                # Adjust qty_tp2 to match total
                qty_tp2 = round_to_step(qty - qty_tp1, step_size)
                qty_total_rounded = qty_tp1 + qty_tp2
            
            if abs(qty_total_rounded - qty) > step_size * 0.5:
                rejections.append(
                    f"qty_split_mismatch: qty={qty} qty_tp1={qty_tp1} qty_tp2={qty_tp2} total={qty_total_rounded}"
                )
                return None, rejections
            
            # Use qty_total_rounded as final qty
            qty_final = qty_total_rounded
            
            # Create tp_orders array
            tp_orders = []
            for i, target in enumerate(tp_targets[:2], 1):  # Limit to 2 TPs
                tp_price = target.get("price")
                if tp_price is None or tp_price <= 0:
                    rejections.append(f"invalid_tp{i}_price")
                    return None, rejections
                
                tp_qty = qty_tp1 if i == 1 else qty_tp2
                tp_orders.append({
                    "type": "TAKE_PROFIT_MARKET",
                    "take_price": tp_price,
                    "qty": tp_qty,
                    "reduce_only": True,
                    "client_order_id": f"{base_id}:tp{i}"
                })
            
            trade_plan = {
                "symbol": symbol,
                "side": "BUY" if intent == "LONG" else "SELL",
                "type": "MARKET",
                "quantity": qty_final,
                "client_order_id": entry_order_id,
                "timeframe": payload.get("market_identity", {}).get("timeframe"),
                "action": "OPEN",
                "stop_loss": {
                    "price": sl,
                    "client_order_id": sl_order_id
                },
                "tp_orders": tp_orders,
                "leverage": leverage,
                "margin_type": account_state.get("margin_type", "isolated"),
                "timestamp": int(ts)
            }
        else:
            # Single TP (backward compatible)
            tp_order_id = f"{base_id}-tp"
            trade_plan = {
                "symbol": symbol,
                "side": "BUY" if intent == "LONG" else "SELL",
                "type": "MARKET",
                "quantity": qty,
                "client_order_id": entry_order_id,
                "timeframe": payload.get("market_identity", {}).get("timeframe"),
                "action": "OPEN",
                "stop_loss": {
                    "price": sl,
                    "client_order_id": sl_order_id
                },
                "take_profit": {
                    "price": tp,
                    "client_order_id": tp_order_id
                },
                "leverage": leverage,
                "margin_type": account_state.get("margin_type", "isolated"),
                "timestamp": int(ts)
            }
    else:
        close_side = "SELL" if payload.get("position_state", {}).get("side") == "LONG" else "BUY"
        trade_plan = {
            "symbol": symbol,
            "side": close_side,
            "type": "MARKET",
            "quantity": qty,
            "client_order_id": f"{base_id}-close",
            "timeframe": payload.get("market_identity", {}).get("timeframe"),
            "action": "CLOSE",
            "leverage": leverage,
            "margin_type": account_state.get("margin_type", "isolated"),
            "timestamp": int(ts)
        }
    
    # Validate trade plan
    is_valid, validation_errors = validate_trade_plan(trade_plan)
    if not is_valid:
        rejections.extend(validation_errors)
        return None, rejections
    
    return trade_plan, []
