"""
State Manager: Persists state for restart safety and reconciliation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, date
from core.config import settings
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

def _state_dir() -> Path:
    base = settings.get_str("STATE_DIR", "run/state")
    return Path(base)

def _ensure_state_dir() -> Path:
    path = _state_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_file(name: str) -> Path:
    """Get path to state file."""
    return _ensure_state_dir() / f"{name}.json"

def _utc_date(now: Optional[datetime] = None) -> date:
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return current.date()

def current_kyiv_date_str(now: Optional[datetime] = None) -> str:
    """UTC day boundary (legacy name retained)."""
    return _utc_date(now).isoformat()

def save_last_closed_candle_ts(timestamp: int) -> None:
    """Persist last processed closed candle timestamp."""
    state = {"last_closed_candle_ts": timestamp, "updated_at": datetime.now(timezone.utc).isoformat()}
    with open(_state_file("candle_gate"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_last_closed_candle_ts() -> Optional[int]:
    """Load last processed closed candle timestamp."""
    state_file = _state_file("candle_gate")
    if not state_file.exists():
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            return state.get("last_closed_candle_ts")
    except Exception:
        return None


def save_daily_state(date_str: str, state: Dict[str, Any]) -> None:
    """Persist daily risk state."""
    state_file = _state_file(f"daily_{date_str}")
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_daily_state(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Load daily risk state."""
    if date_str is None:
        date_str = current_kyiv_date_str()
    state_file = _state_file(f"daily_{date_str}")
    if not state_file.exists():
        return {
            "date": date_str,
            "starting_equity": 0.0,
            "realized_pnl": 0.0,
            "consecutive_losses": 0
        }
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "date": date_str,
            "starting_equity": 0.0,
            "realized_pnl": 0.0,
            "consecutive_losses": 0
        }


def save_trade_identifier(client_order_id: str, trade_hash: str) -> None:
    """Persist trade identifier to prevent duplicate execution."""
    state_file = _state_file("trade_ids")
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            trade_ids = json.load(f)
    except Exception:
        trade_ids = {}
    
    trade_ids[client_order_id] = {
        "hash": trade_hash,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Keep only last 1000 entries
    if len(trade_ids) > 1000:
        sorted_ids = sorted(trade_ids.items(), key=lambda x: x[1].get("timestamp", ""))
        trade_ids = dict(sorted_ids[-1000:])
    
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(trade_ids, f, indent=2)


def has_trade_identifier(client_order_id: str) -> bool:
    """Check if trade identifier exists (prevent duplicate execution)."""
    state_file = _state_file("trade_ids")
    if not state_file.exists():
        return False
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            trade_ids = json.load(f)
            return client_order_id in trade_ids
    except Exception:
        return False


def get_trade_identifier(client_order_id: str) -> Optional[Dict[str, Any]]:
    """Get stored trade identifier entry."""
    state_file = _state_file("trade_ids")
    if not state_file.exists():
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            trade_ids = json.load(f)
            return trade_ids.get(client_order_id)
    except Exception:
        return None


def save_position_state(symbol: str, state: Dict[str, Any]) -> None:
    """Persist last known position state for reconciliation."""
    payload = {
        "symbol": symbol,
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(_state_file("position_state"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_position_state(symbol: str) -> Optional[Dict[str, Any]]:
    """Load last known position state for reconciliation."""
    state_file = _state_file("position_state")
    if not state_file.exists():
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
            if payload.get("symbol") != symbol:
                return None
            return payload.get("state")
    except Exception:
        return None


def load_trade_cooldown_state() -> Dict[str, Any]:
    """Load last trade attempt timestamps per side."""
    state_file = _state_file("trade_cooldown")
    if not state_file.exists():
        return {"LONG": None, "SHORT": None}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            return {
                "LONG": state.get("LONG"),
                "SHORT": state.get("SHORT"),
            }
    except Exception:
        return {"LONG": None, "SHORT": None}


def save_trade_cooldown_state(state: Dict[str, Any]) -> None:
    payload = {
        "LONG": state.get("LONG"),
        "SHORT": state.get("SHORT"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(_state_file("trade_cooldown"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def record_trade_attempt(side: str, timestamp: int) -> None:
    """Persist last trade attempt timestamp for a side."""
    side = (side or "").upper()
    if side not in ("LONG", "SHORT"):
        return
    state = load_trade_cooldown_state()
    state[side] = int(timestamp)
    save_trade_cooldown_state(state)


def _is_close_position(order: Dict[str, Any]) -> bool:
    val = order.get("closePosition")
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

def _is_reduce_only(order: Dict[str, Any]) -> bool:
    val = order.get("reduceOnly")
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

def _has_protective_sl(side: str, open_orders: List[Dict[str, Any]]) -> bool:
    if not open_orders:
        return False
    side = (side or "").upper()
    need_side = "SELL" if side == "LONG" else "BUY"
    stop_types = {"STOP", "STOP_MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT"}
    for order in open_orders:
        o_side = str(order.get("side", "")).upper()
        o_type = str(order.get("type", "")).upper()
        if o_side == need_side and o_type in stop_types and _is_close_position(order) and _is_reduce_only(order):
            return True
    return False

def _has_protective_tp(side: str, open_orders: List[Dict[str, Any]]) -> bool:
    if not open_orders:
        return False
    side = (side or "").upper()
    need_side = "SELL" if side == "LONG" else "BUY"
    tp_types = {"TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TAKE_PROFIT_LIMIT"}
    for order in open_orders:
        o_side = str(order.get("side", "")).upper()
        o_type = str(order.get("type", "")).upper()
        if o_side == need_side and o_type in tp_types and _is_close_position(order) and _is_reduce_only(order):
            return True
    return False


def reconcile_positions(
    exchange_positions: list,
    open_orders: Optional[List[Dict[str, Any]]] = None,
    local_state: Optional[Dict[str, Any]] = None,
    *,
    require_tp: bool = True,
) -> Tuple[bool, List[str]]:
    """
    Reconcile exchange positions vs local state.
    
    Returns:
        (ok: bool, errors: List[str])
        - If ok is False, trading should be stopped
    """
    errors = []
    
    # Check local state consistency if provided
    if local_state:
        expected_side = local_state.get("side")
        expected_qty = float(local_state.get("qty", 0.0) or 0.0)
        open_positions = [p for p in exchange_positions if float(p.get("positionAmt", 0) or 0) != 0.0]
        if expected_qty == 0.0 and open_positions:
            errors.append("local_state_mismatch: expected no position, exchange has position")
            return False, errors
        if open_positions and expected_qty != 0.0:
            ex = open_positions[0]
            ex_qty = abs(float(ex.get("positionAmt", 0) or 0.0))
            ex_side = "LONG" if float(ex.get("positionAmt", 0) or 0) > 0 else "SHORT"
            if expected_side and expected_side != ex_side:
                errors.append("local_state_mismatch: side")
                return False, errors
            if abs(ex_qty - expected_qty) > 0:
                errors.append("local_state_mismatch: qty")
                return False, errors

    # Check for positions without SL/TP
    for pos in exchange_positions:
        pos_amt = float(pos.get("positionAmt", 0) or 0)
        if pos_amt != 0:
            pos_side = "LONG" if pos_amt > 0 else "SHORT"
            if not _has_protective_sl(pos_side, open_orders or []):
                errors.append(f"position_without_sl: {pos_side} qty={abs(pos_amt)}")
            if require_tp and not _has_protective_tp(pos_side, open_orders or []):
                errors.append(f"position_without_tp: {pos_side} qty={abs(pos_amt)}")
    
    # Check for multiple positions
    open_positions = [p for p in exchange_positions if float(p.get("positionAmt", 0) or 0) != 0.0]
    if len(open_positions) > 1:
        errors.append(f"multiple_positions: {len(open_positions)}")
        return False, errors
    
    return len(errors) == 0, errors

def initialize_daily_state(equity: float, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Initialize daily state for a new UTC trading day."""
    date_str = current_kyiv_date_str(now)
    existing = load_daily_state(date_str)
    if existing.get("starting_equity", 0) > 0:
        return existing
    state = {
        "date": date_str,
        "starting_equity": equity,
        "realized_pnl": 0.0,
        "consecutive_losses": 0
    }
    save_daily_state(date_str, state)
    return state

def load_or_initialize_daily_state(equity: float, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    date_str = current_kyiv_date_str(now)
    state = load_daily_state(date_str)
    if state.get("date") != date_str or state.get("starting_equity", 0) <= 0:
        state = {
            "date": date_str,
            "starting_equity": float(equity or 0.0),
            "realized_pnl": 0.0,
            "consecutive_losses": 0,
        }
        save_daily_state(date_str, state)
    return state
