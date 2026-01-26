from __future__ import annotations

from typing import Any, Dict, List, Optional

def preview_exits(symbol: str, side_entry: str, sl_target: Optional[float], tp_target: Optional[float]) -> Dict[str, Any]:
    """
    Build exit order specs for SL/TP without performing network calls.
    Returns: {"orders": [ ... ]}
    """
    side = (side_entry or "").strip().upper()
    exit_side = "SELL" if side == "BUY" else "BUY"

    orders: List[Dict[str, Any]] = []
    if sl_target is not None:
        orders.append({
            "symbol": symbol,
            "side": exit_side,
            "type": "STOP_MARKET",
            "stopPrice": float(sl_target),
            "closePosition": True,
            "reduceOnly": True,
        })
    if tp_target is not None:
        orders.append({
            "symbol": symbol,
            "side": exit_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": float(tp_target),
            "closePosition": True,
            "reduceOnly": True,
        })
    return {"orders": orders}
