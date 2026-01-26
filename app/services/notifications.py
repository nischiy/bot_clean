from __future__ import annotations

from typing import Any, Dict, Optional
from contextlib import contextmanager
from contextvars import ContextVar

from core.execution import binance_futures as _bf
from core.runtime_mode import get_runtime_settings
from core.config import settings

_EXECUTION_CONTEXT: ContextVar[bool] = ContextVar("execution_context", default=False)

@contextmanager
def execution_context() -> Any:
    token = _EXECUTION_CONTEXT.set(True)
    try:
        yield
    finally:
        _EXECUTION_CONTEXT.reset(token)

def _assert_execution_context() -> None:
    runtime = get_runtime_settings()
    should_block = (
        settings.get_bool("TRADE_ENABLED")
        and not settings.get_bool("DRY_RUN_ONLY")
        and not settings.get_bool("SAFE_RUN")
        and not settings.get_bool("PAPER_TRADING")
        and not settings.get_bool("LIVE_READONLY")
    )
    if (runtime.is_live or should_block) and not _EXECUTION_CONTEXT.get():
        raise RuntimeError("direct_order_submission_blocked")

def get_open_orders(symbol: str) -> Any:
    """Return open orders for a symbol via Binance Futures REST."""
    return _bf._get("/fapi/v1/openOrders", {"symbol": symbol}, private=True)

def cancel_order_via_rest(*, symbol: str, orderId: Optional[int] = None, origClientOrderId: Optional[str] = None) -> Any:
    """Cancel a single order by orderId or origClientOrderId."""
    _assert_execution_context()
    params: Dict[str, Any] = {"symbol": symbol}
    if orderId is not None:
        params["orderId"] = int(orderId)
    if origClientOrderId:
        params["origClientOrderId"] = origClientOrderId
    return _bf._delete("/fapi/v1/order", params)

def get_order_via_rest(*, symbol: str, orderId: Optional[int] = None, origClientOrderId: Optional[str] = None) -> Any:
    """Fetch a single order by orderId or origClientOrderId."""
    params: Dict[str, Any] = {"symbol": symbol}
    if orderId is not None:
        params["orderId"] = int(orderId)
    if origClientOrderId:
        params["origClientOrderId"] = origClientOrderId
    return _bf._get("/fapi/v1/order", params, private=True)

def place_order_via_rest(**payload: Any) -> Any:
    """Place a Futures order via REST using binance_futures helpers."""
    _assert_execution_context()
    return _bf._post("/fapi/v1/order", dict(payload))

def set_leverage_via_rest(symbol: str, leverage: int) -> Any:
    """Set leverage for a symbol."""
    _assert_execution_context()
    return _bf._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})
