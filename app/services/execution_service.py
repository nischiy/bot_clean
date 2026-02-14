"""
Execution Service: Idempotent execution layer that consumes ONLY trade_plan.json.
All orders MUST use clientOrderId. Entry → confirm fill → immediately place SL and TP.
"""
from __future__ import annotations

import logging
import time
import json
import hashlib
from typing import Dict, Any, Optional, List, Tuple

from core.config import settings
from core.runtime_mode import get_runtime_settings
from app.core.validation import validate_trade_plan
from app.state.state_manager import has_trade_identifier, save_trade_identifier, get_trade_identifier
from app.services import notifications as net
import core.exchange_private as exchange_private
from app.services.exit_adapter import preview_exits
from core.risk_guard import kill

# Import cancel_order_via_rest
try:
    from app.services.notifications import cancel_order_via_rest
except ImportError:
    # Fallback if not available
    def cancel_order_via_rest(*args, **kwargs):
        raise NotImplementedError("cancel_order_via_rest not available")

def _retry_call(fn, *, log: logging.Logger, label: str) -> Any:
    attempts = settings.get_int("EXECUTION_RETRY_ATTEMPTS")
    base_delay = settings.get_float("EXECUTION_RETRY_BASE_DELAY_SEC")
    max_delay = settings.get_float("EXECUTION_RETRY_MAX_DELAY_SEC")
    delay = base_delay
    last_exc: Optional[BaseException] = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i < attempts:
                log.warning("%s — retry %d/%d in %.2fs (err: %s)", label, i, attempts, delay, e)
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
    assert last_exc is not None
    raise last_exc


class ExecutionService:
    """Idempotent execution service for trade_plan.json."""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger("ExecutionService")
        self.dry_run = settings.get_bool("DRY_RUN_ONLY")
        self.safe_run = settings.get_bool("SAFE_RUN")
        self.live_readonly = settings.get_bool("LIVE_READONLY")
        self.runtime = get_runtime_settings()
    
    def execute_trade_plan(self, trade_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute trade plan (idempotent).
        
        Steps:
        1. Validate trade_plan.json
        2. Check if client_order_id already exists (idempotency)
        3. Set leverage if needed
        4. Place entry order
        5. Wait for fill confirmation
        6. Place SL and TP orders
        
        Returns:
            {
                "executed": bool,
                "reason": str,
                "entry_order": Optional[Dict],
                "sl_order": Optional[Dict],
                "tp_order": Optional[Dict],
                "errors": List[str]
            }
        """
        errors = []

        # 1. Validate trade plan
        is_valid, validation_errors = validate_trade_plan(trade_plan)
        if not is_valid:
            errors.extend(validation_errors)
            return {
                "executed": False,
                "reason": "invalid_trade_plan",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors
            }
        
        action = trade_plan.get("action", "OPEN")
        if self.live_readonly:
            return self._live_readonly_response(trade_plan)

        if self.safe_run:
            return self._safe_run_response(trade_plan)

        symbol = trade_plan.get("symbol")
        client_order_id = trade_plan.get("client_order_id")
        side = trade_plan.get("side")
        quantity = trade_plan.get("quantity")
        leverage = trade_plan.get("leverage")
        margin_type = trade_plan.get("margin_type", "isolated")
        stop_loss = trade_plan.get("stop_loss", {})
        take_profit = trade_plan.get("take_profit", {})
        tp_orders = trade_plan.get("tp_orders", [])
        
        # Handle UPDATE_SLTP action
        if action == "UPDATE_SLTP":
            return self._execute_update_sltp(trade_plan, symbol=symbol, stop_loss=stop_loss)
        
        # 2. Check idempotency
        trade_hash = _trade_plan_hash(trade_plan)
        if has_trade_identifier(client_order_id):
            stored = get_trade_identifier(client_order_id) or {}
            stored_hash = stored.get("hash")
            if stored_hash and stored_hash != trade_hash:
                kill("execution_desync", {"client_order_id": client_order_id})
                return {
                    "executed": False,
                    "reason": "trade_plan_mismatch",
                    "entry_order": None,
                    "sl_order": None,
                    "tp_order": None,
                    "errors": ["client_order_id_hash_mismatch"]
                }
            self.log.warning(f"Trade plan already executed: {client_order_id}")
            return {
                "executed": False,
                "reason": "already_executed",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": ["duplicate_client_order_id"]
            }
        
        # 3. DRY RUN mode
        if self.dry_run or settings.get_bool("PAPER_TRADING") or self.runtime.is_paper:
            self.log.info(f"DRY RUN: Would execute trade plan for {symbol}")
            # Save identifier even in dry run to prevent duplicate processing
            save_trade_identifier(client_order_id, trade_hash)
            return {
                "executed": False,
                "reason": "dry_run",
                "entry_order": {"client_order_id": client_order_id, "dry_run": True, "action": action},
                "sl_order": {"client_order_id": stop_loss.get("client_order_id"), "dry_run": True} if isinstance(stop_loss, dict) and stop_loss else None,
                "tp_order": {"client_order_id": take_profit.get("client_order_id"), "dry_run": True} if isinstance(take_profit, dict) and take_profit else None,
                "errors": []
            }

        if action == "CLOSE":
            return self._execute_close_plan(trade_plan, symbol=symbol, side=side, quantity=quantity, client_order_id=client_order_id)
        
        # 4. Persist identifier before any live submission
        save_trade_identifier(client_order_id, trade_hash)

        # 5. Set leverage
        if leverage:
            try:
                with net.execution_context():
                    _retry_call(
                        lambda: net.set_leverage_via_rest(symbol, leverage),
                        log=self.log,
                        label=f"set_leverage({symbol},{leverage}) failed",
                    )
                self.log.info(f"Set leverage {leverage}x for {symbol}")
            except Exception as e:
                errors.append(f"set_leverage_failed: {e}")
                self.log.error(f"Failed to set leverage: {e}")
        
        # 6. Place entry order
        entry_order = None
        try:
            _log_ledger("execution_submitted", trade_plan)
            with net.execution_context():
                entry_order = _retry_call(
                    lambda: net.place_order_via_rest(
                        symbol=symbol,
                        side=side,
                        type=trade_plan.get("type", "MARKET"),
                        quantity=quantity,
                        newClientOrderId=client_order_id,
                    ),
                    log=self.log,
                    label=f"place_order({symbol},{side},{trade_plan.get('type','MARKET')}) failed",
                )
            self.log.info(f"Entry order placed: {client_order_id}")
            
        except Exception as e:
            errors.append(f"entry_order_failed: {e}")
            self.log.error(f"Failed to place entry order: {e}")
            return {
                "executed": False,
                "reason": "entry_order_failed",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors
            }
        
        # 6. Wait for fill confirmation
        order_id = entry_order.get("orderId") if isinstance(entry_order, dict) else None
        filled, fill_details = self._wait_for_fill(symbol, order_id=order_id, client_order_id=client_order_id)
        if not filled:
            errors.append("entry_not_filled")
            kill("execution_desync", {"client_order_id": client_order_id, "details": fill_details})
            return {
                "executed": False,
                "reason": "entry_not_filled",
                "entry_order": entry_order,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }
        
        # 7/8. Place SL/TP orders (reduceOnly + closePosition)
        sl_order = None
        tp_orders_result = []
        sl_price = stop_loss.get("price") if isinstance(stop_loss, dict) else None
        
        # Place SL order
        if sl_price is not None:
            exit_side = "SELL" if side == "BUY" else "BUY"
            sl_spec = {
                "symbol": symbol,
                "side": exit_side,
                "type": "STOP_MARKET",
                "stopPrice": float(sl_price),
                "closePosition": True,
                "reduceOnly": True,
                "newClientOrderId": stop_loss.get("client_order_id"),
            }
            try:
                _log_ledger("sltp_submitted", trade_plan, {"type": "STOP_MARKET"})
                with net.execution_context():
                    sl_order = _retry_call(
                        lambda: net.place_order_via_rest(**sl_spec),
                        log=self.log,
                        label=f"place_sl_order({symbol}) failed",
                    )
                # Persist SL order ID for idempotency
                if sl_order and isinstance(sl_order, dict):
                    sl_client_id = stop_loss.get("client_order_id")
                    if sl_client_id:
                        save_trade_identifier(sl_client_id, _trade_plan_hash({"sl": sl_price, "client_order_id": sl_client_id}))
            except Exception as e:
                errors.append(f"sl_order_failed: {e}")
                self.log.error(f"Failed to place SL order: {e}")
        
        # Place TP orders (multiple if tp_orders array exists, otherwise single take_profit)
        if tp_orders:
            # Multiple TP orders from tp_orders array
            exit_side = "SELL" if side == "BUY" else "BUY"
            for tp_order_spec in tp_orders:
                tp_client_id = tp_order_spec.get("client_order_id")
                tp_price = tp_order_spec.get("take_price")
                tp_qty = tp_order_spec.get("qty")
                
                if tp_client_id and has_trade_identifier(tp_client_id):
                    self.log.warning(f"TP order already exists: {tp_client_id}")
                    continue
                
                order_spec = {
                    "symbol": symbol,
                    "side": exit_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": float(tp_price),
                    "quantity": float(tp_qty),
                    "reduceOnly": True,
                    "newClientOrderId": tp_client_id,
                }
                try:
                    _log_ledger("sltp_submitted", trade_plan, {"type": "TAKE_PROFIT_MARKET", "tp_index": len(tp_orders_result) + 1})
                    with net.execution_context():
                        resp = _retry_call(
                            lambda: net.place_order_via_rest(**order_spec),
                            log=self.log,
                            label=f"place_tp_order({symbol},tp{len(tp_orders_result)+1}) failed",
                        )
                    tp_orders_result.append(resp)
                    # Persist TP order ID for idempotency
                    if resp and isinstance(resp, dict) and tp_client_id:
                        save_trade_identifier(tp_client_id, _trade_plan_hash({"tp": tp_price, "qty": tp_qty, "client_order_id": tp_client_id}))
                except Exception as e:
                    errors.append(f"tp_order_failed: {e}")
                    self.log.error(f"Failed to place TP order: {e}")
        elif take_profit and isinstance(take_profit, dict):
            # Single TP (backward compatible)
            tp_price = take_profit.get("price")
            tp_client_id = take_profit.get("client_order_id")
            if tp_price is not None:
                exit_plan = preview_exits(symbol, side, None, tp_price)
                for spec in exit_plan.get("orders", []):
                    if spec.get("type") == "TAKE_PROFIT_MARKET":
                        if tp_client_id:
                            spec["newClientOrderId"] = tp_client_id
                        if tp_client_id and has_trade_identifier(tp_client_id):
                            self.log.warning(f"TP order already exists: {tp_client_id}")
                            continue
                        try:
                            _log_ledger("sltp_submitted", trade_plan, {"type": "TAKE_PROFIT_MARKET"})
                            with net.execution_context():
                                resp = _retry_call(
                                    lambda: net.place_order_via_rest(**spec),
                                    log=self.log,
                                    label=f"place_tp_order({symbol}) failed",
                                )
                            tp_orders_result.append(resp)
                            # Persist TP order ID for idempotency
                            if resp and isinstance(resp, dict) and tp_client_id:
                                save_trade_identifier(tp_client_id, _trade_plan_hash({"tp": tp_price, "client_order_id": tp_client_id}))
                        except Exception as e:
                            errors.append(f"tp_order_failed: {e}")
                            self.log.error(f"Failed to place TP order: {e}")
        
        executed = entry_order is not None and len(errors) == 0
        
        # Track original position qty and TP1 qty for TP1 fill detection
        # This is saved to position state after successful execution
        tp1_qty = None
        if tp_orders and len(tp_orders) >= 1:
            # Extract TP1 qty from the first TP order spec (input parameter)
            tp1_qty = float(tp_orders[0].get("qty", 0.0) or 0.0)
        
        return {
            "executed": executed,
            "reason": "executed" if executed else "partial_failure",
            "entry_order": entry_order,
            "sl_order": sl_order,
            "tp_order": tp_orders_result[0] if tp_orders_result else None,
            "tp_orders": tp_orders_result,
            "errors": errors,
            "original_qty": float(quantity) if executed and quantity else None,
            "tp1_qty": tp1_qty if executed and tp1_qty and tp1_qty > 0 else None,
        }

    def _execute_close_plan(
        self,
        trade_plan: Dict[str, Any],
        *,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        close_order = None
        try:
            _log_ledger("execution_submitted", trade_plan, {"action": "CLOSE"})
            with net.execution_context():
                close_order = _retry_call(
                    lambda: net.place_order_via_rest(
                        symbol=symbol,
                        side=side,
                        type=trade_plan.get("type", "MARKET"),
                        quantity=quantity,
                        reduceOnly=True,
                        newClientOrderId=client_order_id,
                    ),
                    log=self.log,
                    label=f"place_close_order({symbol},{side},{trade_plan.get('type','MARKET')}) failed",
                )
            self.log.info("Close order placed: %s", client_order_id)
        except Exception as e:
            errors.append(f"close_order_failed: {e}")
            self.log.error("Failed to place close order: %s", e)
            return {
                "executed": False,
                "reason": "close_order_failed",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }

        order_id = close_order.get("orderId") if isinstance(close_order, dict) else None
        filled, fill_details = self._wait_for_fill(symbol, order_id=order_id, client_order_id=client_order_id)
        if not filled:
            errors.append("close_not_filled")
            kill("execution_desync", {"client_order_id": client_order_id, "details": fill_details})
            return {
                "executed": False,
                "reason": "close_not_filled",
                "entry_order": close_order,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }

        return {
            "executed": True,
            "reason": "executed",
            "entry_order": close_order,
            "sl_order": None,
            "tp_order": None,
            "errors": errors,
        }

    def _safe_run_response(self, trade_plan: Dict[str, Any]) -> Dict[str, Any]:
        symbol = trade_plan.get("symbol")
        client_order_id = trade_plan.get("client_order_id")
        stop_loss = trade_plan.get("stop_loss", {}) if isinstance(trade_plan.get("stop_loss"), dict) else {}
        take_profit = trade_plan.get("take_profit", {}) if isinstance(trade_plan.get("take_profit"), dict) else {}
        action = trade_plan.get("action", "OPEN")
        trade_hash = _trade_plan_hash(trade_plan)
        self.log.info(
            "SAFE_RUN: would place order symbol=%s side=%s qty=%s clientOrderId=%s sl=%s tp=%s",
            symbol,
            trade_plan.get("side"),
            trade_plan.get("quantity"),
            client_order_id,
            stop_loss.get("price"),
            take_profit.get("price"),
        )
        save_trade_identifier(client_order_id, trade_hash)
        _log_ledger("execution_attempted", trade_plan, {"mode": "safe_run"})
        return {
            "executed": False,
            "reason": "safe_run",
            "entry_order": {"client_order_id": client_order_id, "safe_run": True, "action": action},
            "sl_order": {"client_order_id": stop_loss.get("client_order_id"), "safe_run": True} if stop_loss else None,
            "tp_order": {"client_order_id": take_profit.get("client_order_id"), "safe_run": True} if take_profit else None,
            "errors": [],
        }

    def _live_readonly_response(self, trade_plan: Dict[str, Any]) -> Dict[str, Any]:
        symbol = trade_plan.get("symbol")
        client_order_id = trade_plan.get("client_order_id")
        stop_loss = trade_plan.get("stop_loss", {}) if isinstance(trade_plan.get("stop_loss"), dict) else {}
        take_profit = trade_plan.get("take_profit", {}) if isinstance(trade_plan.get("take_profit"), dict) else {}
        action = trade_plan.get("action", "OPEN")
        self.log.info(
            "LIVE_READONLY: would place order symbol=%s side=%s qty=%s clientOrderId=%s sl=%s tp=%s",
            symbol,
            trade_plan.get("side"),
            trade_plan.get("quantity"),
            client_order_id,
            stop_loss.get("price"),
            take_profit.get("price"),
        )
        _log_ledger("execution_attempted", trade_plan, {"mode": "live_readonly", "result": "blocked_readonly"})
        return {
            "executed": False,
            "reason": "blocked_readonly",
            "entry_order": {"client_order_id": client_order_id, "live_readonly": True, "action": action},
            "sl_order": {"client_order_id": stop_loss.get("client_order_id"), "live_readonly": True} if stop_loss else None,
            "tp_order": {"client_order_id": take_profit.get("client_order_id"), "live_readonly": True} if take_profit else None,
            "errors": [],
        }

    def _wait_for_fill(
        self,
        symbol: str,
        *,
        order_id: Optional[int],
        client_order_id: Optional[str],
    ) -> Tuple[bool, Dict[str, Any]]:
        interval = settings.get_float("EXECUTION_POLL_INTERVAL_SEC")
        timeout_sec = settings.get_float("EXECUTION_POLL_TIMEOUT_SEC")
        max_attempts = settings.get_int("EXECUTION_POLL_MAX_ATTEMPTS")
        deadline = time.time() + max(0.0, timeout_sec)
        attempts = 0
        last_error = None
        last_payload = None
        while attempts < max_attempts and time.time() <= deadline:
            attempts += 1
            try:
                payload = net.get_order_via_rest(
                    symbol=symbol, orderId=order_id, origClientOrderId=client_order_id
                )
                last_payload = payload
                status = str(payload.get("status", "")).upper()
                if status == "FILLED":
                    return True, payload
                if status in {"CANCELED", "REJECTED", "EXPIRED"}:
                    return False, payload
            except Exception as e:
                last_error = str(e)
            time.sleep(max(0.0, interval))
        return False, {"last_error": last_error, "last_payload": last_payload}

    def fetch_positions(self) -> list:
        data = exchange_private.fetch_futures_private()
        if isinstance(data, dict):
            return data.get("positions") or []
        return []

    def get_open_orders(self, symbol: str) -> list:
        orders = net.get_open_orders(symbol)
        return orders if isinstance(orders, list) else orders.get("orders") or orders.get("data") or []

    def get_position_snapshot(self, symbol: str) -> Dict[str, Any]:
        positions = self.fetch_positions()
        for pos in positions:
            if pos.get("symbol") != symbol:
                continue
            amt = float(pos.get("positionAmt", 0) or 0.0)
            if amt == 0:
                break
            side = "LONG" if amt > 0 else "SHORT"
            return {
                "side": side,
                "qty": abs(amt),
                "entry": float(pos.get("entryPrice", 0) or 0.0),
                "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0.0),
                "liq_price": float(pos.get("liquidationPrice")) if pos.get("liquidationPrice") is not None else None,
            }
        return {"side": None, "qty": 0.0, "entry": 0.0, "unrealized_pnl": 0.0, "liq_price": None}

    def plan_exit_orders(self, symbol: str, side_entry: str, sl: float | None, tp: float | None) -> Dict[str, Any]:
        return preview_exits(symbol, side_entry, sl, tp)
    
    def _execute_update_sltp(
        self,
        trade_plan: Dict[str, Any],
        *,
        symbol: str,
        stop_loss: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute UPDATE_SLTP action: cancel existing SL and place new one at break-even."""
        errors: List[str] = []
        client_order_id = trade_plan.get("client_order_id")
        new_sl_price = stop_loss.get("price")
        new_sl_client_id = stop_loss.get("client_order_id")
        
        if new_sl_price is None or new_sl_price <= 0:
            errors.append("missing_or_invalid_sl")
            return {
                "executed": False,
                "reason": "invalid_sl",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }
        
        # Check idempotency
        if has_trade_identifier(new_sl_client_id):
            stored = get_trade_identifier(new_sl_client_id) or {}
            stored_hash = stored.get("hash")
            expected_hash = _trade_plan_hash({"sl": new_sl_price, "client_order_id": new_sl_client_id})
            if stored_hash and stored_hash != expected_hash:
                kill("execution_desync", {"client_order_id": new_sl_client_id})
                return {
                    "executed": False,
                    "reason": "trade_plan_mismatch",
                    "entry_order": None,
                    "sl_order": None,
                    "tp_order": None,
                    "errors": ["client_order_id_hash_mismatch"],
                }
            self.log.warning(f"UPDATE_SLTP already executed: {new_sl_client_id}")
            return {
                "executed": False,
                "reason": "already_executed",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": ["duplicate_client_order_id"],
            }
        
        # Get current position to determine side
        position = self.get_position_snapshot(symbol)
        pos_side = position.get("side")
        if pos_side not in ("LONG", "SHORT"):
            errors.append("no_open_position")
            return {
                "executed": False,
                "reason": "no_position",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }
        
        # Cancel existing SL orders
        try:
            open_orders = self.get_open_orders(symbol)
            for order in open_orders:
                order_type = str(order.get("type", "")).upper()
                if order_type == "STOP_MARKET":
                    order_id = order.get("orderId")
                    if order_id:
                        with net.execution_context():
                            _retry_call(
                                lambda: cancel_order_via_rest(symbol=symbol, orderId=order_id),
                                log=self.log,
                                label=f"cancel_sl_order({symbol},{order_id}) failed",
                            )
                        self.log.info(f"Cancelled existing SL order: {order_id}")
        except Exception as e:
            errors.append(f"cancel_sl_failed: {e}")
            self.log.error(f"Failed to cancel existing SL: {e}")
        
        # Place new SL at break-even
        exit_side = "SELL" if pos_side == "LONG" else "BUY"
        sl_spec = {
            "symbol": symbol,
            "side": exit_side,
            "type": "STOP_MARKET",
            "stopPrice": float(new_sl_price),
            "closePosition": True,
            "reduceOnly": True,
            "newClientOrderId": new_sl_client_id,
        }
        
        sl_order = None
        try:
            _log_ledger("sltp_submitted", trade_plan, {"type": "STOP_MARKET", "action": "UPDATE_SLTP"})
            save_trade_identifier(new_sl_client_id, _trade_plan_hash({"sl": new_sl_price, "client_order_id": new_sl_client_id}))
            with net.execution_context():
                sl_order = _retry_call(
                    lambda: net.place_order_via_rest(**sl_spec),
                    log=self.log,
                    label=f"place_update_sl({symbol}) failed",
                )
            self.log.info(f"Updated SL to break-even: {new_sl_price}")
        except Exception as e:
            errors.append(f"update_sl_failed: {e}")
            self.log.error(f"Failed to update SL: {e}")
            return {
                "executed": False,
                "reason": "update_sl_failed",
                "entry_order": None,
                "sl_order": None,
                "tp_order": None,
                "errors": errors,
            }
        
        return {
            "executed": True,
            "reason": "executed",
            "entry_order": None,
            "sl_order": sl_order,
            "tp_order": None,
            "errors": errors,
        }


def _trade_plan_hash(trade_plan: Dict[str, Any]) -> str:
    payload = json.dumps(trade_plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _log_ledger(event_type: str, trade_plan: Dict[str, Any], details: Optional[Dict[str, Any]] = None) -> None:
    try:
        from app.core.trade_ledger import append_event, hash_json
    except Exception:
        return
    trade_plan_hash = hash_json(trade_plan) if isinstance(trade_plan, dict) else None
    timeframe = trade_plan.get("timeframe") if isinstance(trade_plan, dict) else None
    if not timeframe:
        timeframe = settings.get_str("INTERVAL", "5m")
    append_event(
        event_type=event_type,
        symbol=trade_plan.get("symbol", "?"),
        timeframe=timeframe,
        correlation_id=trade_plan.get("client_order_id") or "unknown",
        trade_plan_hash=trade_plan_hash,
        client_order_id=trade_plan.get("client_order_id"),
        details=details or {},
    )
