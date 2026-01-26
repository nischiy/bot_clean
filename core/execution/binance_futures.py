from __future__ import annotations
import time, hmac, hashlib, os, threading
from urllib.parse import urlencode
from typing import Dict, Any, Optional
import requests
from core.config import settings

BINANCE_FAPI_BASE = (
    settings.get_str("BASE_URL_TESTNET", "https://testnet.binancefuture.com")
    if str(settings.get_str("BINANCE_TESTNET", "0")).strip().lower() in {"1", "true"}
    else settings.get_str("BASE_URL_MAINNET", "https://fapi.binance.com")
)

API_KEY = settings.get_str("BINANCE_FAPI_KEY", "")
API_SECRET = settings.get_str("BINANCE_FAPI_SECRET", "")
_ORDER_MUTATING_PATHS = {
    "/fapi/v1/order",
    "/fapi/v1/allOpenOrders",
    "/fapi/v1/batchOrders",
    "/fapi/v1/leverage",
    "/fapi/v1/marginType",
}

class TimeSync:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset_ms = 0
        self._last_sync_ts = 0.0
        self._server_ms: Optional[int] = None
        self._local_ms: Optional[int] = None

    def refresh(self, *, force: bool = False) -> int:
        ttl_sec = max(1, settings.get_int("BINANCE_TIME_OFFSET_TTL_SEC", 60))
        now_ts = time.time()
        if not force and self._last_sync_ts and (now_ts - self._last_sync_ts) < ttl_sec:
            return self._offset_ms
        with self._lock:
            now_ts = time.time()
            if not force and self._last_sync_ts and (now_ts - self._last_sync_ts) < ttl_sec:
                return self._offset_ms
            try:
                local_ms = _local_time_ms()
                server_ms = _fetch_server_time_ms()
                self._server_ms = int(server_ms)
                self._local_ms = int(local_ms)
                self._offset_ms = int(server_ms - local_ms)
                self._last_sync_ts = now_ts
            except Exception:
                if not self._last_sync_ts:
                    self._offset_ms = 0
                    self._last_sync_ts = now_ts
        return self._offset_ms

    def get_timestamp_ms(self) -> int:
        offset_ms = self.refresh(force=False)
        return int(_local_time_ms() + offset_ms)

    def snapshot(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        if force_refresh:
            self.refresh(force=True)
        local_ms = _local_time_ms()
        ttl_sec = max(1, settings.get_int("BINANCE_TIME_OFFSET_TTL_SEC", 60))
        age_ms = (
            int(max(0.0, time.time() - self._last_sync_ts) * 1000)
            if self._last_sync_ts
            else None
        )
        return {
            "server_time_ms": self._server_ms,
            "local_time_ms": self._local_ms,
            "offset_ms": self._offset_ms,
            "ttl_sec": ttl_sec,
            "age_ms": age_ms,
            "request_ts": int(local_ms + self._offset_ms),
        }


_TIME_SYNC = TimeSync()


def _local_time_ms() -> int:
    return int(time.time() * 1000)


def _fetch_server_time_ms() -> int:
    resp = requests.get(BINANCE_FAPI_BASE + "/fapi/v1/time", timeout=settings.get_int("HTTP_TIMEOUT_SEC"))
    resp.raise_for_status()
    data = resp.json()
    return int(data.get("serverTime"))


def get_time_sync_snapshot(*, force_refresh: bool = False) -> Dict[str, Any]:
    return _TIME_SYNC.snapshot(force_refresh=force_refresh)


def _ts() -> int:
    return _TIME_SYNC.get_timestamp_ms()


def _canonical_qs(params: Dict[str, Any]) -> str:
    return urlencode(sorted(params.items()), doseq=True)


def _redacted_query(canonical_qs: str) -> str:
    return canonical_qs


class BinanceRequestError(RuntimeError):
    def __init__(self, message: str, diagnostics: Dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def _build_error_diagnostics(
    *,
    response: requests.Response,
    path: str,
    method: str,
    canonical_qs: str,
    request_url: str,
) -> Dict[str, Any]:
    code = None
    msg = None
    response_text_trim = None
    content_type = response.headers.get("Content-Type")
    try:
        data = response.json()
        if isinstance(data, dict):
            code = data.get("code")
            msg = data.get("msg")
    except Exception:
        response_text_trim = (response.text or "")[:500]
    if msg is None and response_text_trim is None:
        response_text_trim = (response.text or "")[:500]
    return {
        "binance_error_code": code,
        "binance_error_msg": msg,
        "response_text_trim": response_text_trim,
        "content_type": content_type,
        "request_endpoint": path,
        "request_method": method,
        "signed_params_snapshot": {
            "query_string_no_signature": _redacted_query(canonical_qs),
        },
        "time_sync_snapshot": get_time_sync_snapshot(),
        "http_status": response.status_code,
        "request_url": request_url,
    }

def _extract_error(response: requests.Response) -> tuple[Optional[int], Optional[str]]:
    code = None
    msg = None
    try:
        data = response.json()
        if isinstance(data, dict):
            code = data.get("code")
            msg = data.get("msg")
    except Exception:
        msg = (response.text or "")[:500]
    return code, msg

def _is_timestamp_error(error_code: Optional[int], error_msg: Optional[str]) -> bool:
    msg = str(error_msg or "").lower()
    return error_code == -1021 or ("timestamp" in msg and "recvwindow" in msg)

def _request(method: str, path: str, params: Dict[str, Any], *, private: bool, retry_time_sync: bool = True) -> Any:
    url = BINANCE_FAPI_BASE + path
    req_params = dict(params)
    canonical_qs = ""
    headers = None
    if private:
        req_params["timestamp"] = int(_TIME_SYNC.get_timestamp_ms())
        req_params["recvWindow"] = int(settings.get_int("BINANCE_RECV_WINDOW", 5000))
        canonical_qs = _canonical_qs(req_params)
        signature = _sign(canonical_qs)
        request_url = f"{url}?{canonical_qs}&signature={signature}"
        headers = _headers()
    else:
        canonical_qs = _canonical_qs(req_params) if req_params else ""
        request_url = f"{url}?{canonical_qs}" if canonical_qs else url

    timeout = settings.get_int("HTTP_TIMEOUT_SEC")
    if method == "GET":
        r = requests.get(request_url, headers=headers, timeout=timeout)
    elif method == "POST":
        r = requests.post(request_url, headers=headers, timeout=timeout)
    elif method == "DELETE":
        r = requests.delete(request_url, headers=headers, timeout=timeout)
    else:
        raise ValueError(f"unsupported_method:{method}")

    status_code = getattr(r, "status_code", 200)
    if status_code >= 400:
        error_code, error_msg = _extract_error(r)
        if private and retry_time_sync and _is_timestamp_error(error_code, error_msg):
            _TIME_SYNC.refresh(force=True)
            return _request(method, path, params, private=private, retry_time_sync=False)
        diagnostics = _build_error_diagnostics(
            response=r,
            path=path,
            method=method,
            canonical_qs=canonical_qs,
            request_url=request_url,
        )
        raise BinanceRequestError(f"{method} {path} failed", diagnostics)
    return r.json()

def _sign(canonical_qs: str) -> str:
    if not API_SECRET:
        raise RuntimeError("BINANCE_FAPI_SECRET is not set")
    return hmac.new(API_SECRET.encode(), canonical_qs.encode(), hashlib.sha256).hexdigest()

def _headers() -> Dict[str, str]:
    if not API_KEY:
        raise RuntimeError("BINANCE_FAPI_KEY is not set")
    return {"X-MBX-APIKEY": API_KEY, "Content-Type": "application/x-www-form-urlencoded"}

def _block_order_mutation(path: str, *, method: Optional[str] = None) -> None:
    is_mutation = path in _ORDER_MUTATING_PATHS
    if method:
        is_mutation = True
    if is_mutation and (
        settings.get_bool("LIVE_READONLY")
        or settings.get_bool("SAFE_RUN")
        or settings.get_bool("DRY_RUN_ONLY")
    ):
        raise RuntimeError("order_submission_blocked")

def _get(path: str, params: Dict[str, Any], private: bool=False) -> Any:
    return _request("GET", path, params, private=private)

def _post(path: str, params: Dict[str, Any]) -> Any:
    _block_order_mutation(path, method="POST")
    return _request("POST", path, params, private=True)

def _delete(path: str, params: Dict[str, Any]) -> Any:
    _block_order_mutation(path, method="DELETE")
    return _request("DELETE", path, params, private=True)

# ----- Public helpers -----
def ping() -> bool:
    try:
        requests.get(BINANCE_FAPI_BASE + "/fapi/v1/ping", timeout=settings.get_int("HTTP_TIMEOUT_SEC"))
        return True
    except Exception:
        return False

def exchange_info(symbol: str) -> Any:
    return _get("/fapi/v1/exchangeInfo", {"symbol": symbol}, private=False)

def klines(symbol: str, interval: str, limit: int=500) -> Any:
    return _get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}, private=False)

# ----- Private trading -----
def get_balance(asset: str="USDT") -> Optional[float]:
    data = _get("/fapi/v2/balance", {}, private=True)
    for it in data:
        if it.get("asset") == asset:
            # cross wallet + unrealized notional не додаємо - базовий баланс
            return float(it.get("balance", 0.0))
    return None

def get_position(symbol: str) -> Dict[str, Any]:
    arr = _get("/fapi/v2/positionRisk", {"symbol": symbol}, private=True)
    return arr[0] if arr else {}

def place_order(symbol: str, side: str, quantity: float, order_type: str="MARKET",
                reduce_only: bool=False, price: Optional[float]=None, tp: Optional[float]=None, sl: Optional[float]=None,
                time_in_force: str="GTC", position_side: Optional[str]=None) -> Dict[str, Any]:
    """
    side: BUY/SELL
    order_type: MARKET/LIMIT
    tp/sl: optional (attached as separate OCO-like? Binance USDT-M supports TP/SL as STOP/TAKE_PROFIT - тут робимо прості child ордери якщо задано)
    """
    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": order_type.upper(),
        "quantity": f"{quantity}",
        "reduceOnly": "true" if reduce_only else "false"
    }
    if position_side:
        params["positionSide"] = position_side
    if order_type.upper() == "LIMIT":
        if price is None:
            raise ValueError("price is required for LIMIT")
        params["price"] = f"{price}"
        params["timeInForce"] = time_in_force

    res = _post("/fapi/v1/order", params)

    # Best-effort TP/SL (optional)
    if tp is not None:
        try:
            _post("/fapi/v1/order", {
                "symbol": symbol,
                "side": "SELL" if side.upper()=="BUY" else "BUY",
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": f"{tp}",
                "closePosition": "true"
            })
        except Exception:
            pass
    if sl is not None:
        try:
            _post("/fapi/v1/order", {
                "symbol": symbol,
                "side": "SELL" if side.upper()=="BUY" else "BUY",
                "type": "STOP_MARKET",
                "stopPrice": f"{sl}",
                "closePosition": "true"
            })
        except Exception:
            pass

    return res
