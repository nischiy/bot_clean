from __future__ import annotations
import json
import logging
import time
from typing import Dict, Any, List, Optional, Tuple

import requests
from core.config import settings

logger = logging.getLogger(__name__)

def _bool_env(name: str, default: bool=False) -> bool:
    v = settings.get_str(name, None)
    if v is None: return default
    return v.strip().lower() in {"1","true","yes","y","on"}

def _request_timestamp_ms() -> int:
    """Centralized time source for signed requests."""
    try:
        from core.execution import binance_futures
        return int(binance_futures._ts())
    except Exception:
        return int(time.time() * 1000)


def _parse_binance_error(err: Exception) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[Dict[str, Any]]]:
    http_status = None
    code = None
    msg = None
    diagnostics = None
    try:
        from core.execution.binance_futures import BinanceRequestError
    except Exception:
        BinanceRequestError = None
    if BinanceRequestError and isinstance(err, BinanceRequestError):
        diagnostics = getattr(err, "diagnostics", None)
        if isinstance(diagnostics, dict):
            http_status = diagnostics.get("http_status")
            code = diagnostics.get("binance_error_code")
            msg = diagnostics.get("binance_error_msg")
        return http_status, code, msg, diagnostics
    resp = getattr(err, "response", None)
    if resp is not None:
        try:
            http_status = int(resp.status_code)
        except Exception:
            http_status = None
        try:
            data = resp.json()
            if isinstance(data, dict):
                code = data.get("code")
                msg = data.get("msg")
        except Exception:
            try:
                data = json.loads(getattr(resp, "text", "") or "{}")
                if isinstance(data, dict):
                    code = data.get("code")
                    msg = data.get("msg")
            except Exception:
                msg = getattr(resp, "text", None)
    return http_status, code, msg, diagnostics


def _classify_error(
    *,
    http_status: Optional[int],
    error_code: Optional[int],
    error_msg: Optional[str],
    exc: Exception,
) -> Tuple[str, str]:
    msg = str(error_msg or "").lower()
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return "TRANSIENT_ERROR", "network_failure"
    if isinstance(exc, requests.RequestException) and getattr(exc, "response", None) is None:
        return "TRANSIENT_ERROR", "network_failure"
    if error_code == -1021 or "timestamp" in msg and "recvwindow" in msg:
        return "TRANSIENT_ERROR", "timestamp_out_of_sync"
    if "futures" in msg and "enable" in msg:
        return "CONFIG_ERROR", "futures_not_enabled"
    if error_code in (-2015, -2014) or "invalid api-key" in msg or "permission" in msg:
        return "CONFIG_ERROR", "invalid_api_permissions"
    if http_status is not None and http_status >= 500:
        return "TRANSIENT_ERROR", "exchange_error"
    if error_code is not None or error_msg:
        return "TRANSIENT_ERROR", "binance_error"
    return "TRANSIENT_ERROR", "unknown_error"


def _log_snapshot_error(payload: Dict[str, Any]) -> None:
    try:
        logger.error(json.dumps({"event": "account_snapshot_error", **payload}, separators=(",", ":"), sort_keys=True))
    except Exception:
        logger.error("account_snapshot_error: %s", payload)


def _log_snapshot_warning(payload: Dict[str, Any]) -> None:
    try:
        logger.warning(json.dumps({"event": "account_snapshot_warning", **payload}, separators=(",", ":"), sort_keys=True))
    except Exception:
        logger.warning("account_snapshot_warning: %s", payload)


def fetch_futures_private() -> Dict[str, Any]:
    """Read futures balances and positions via REST.
    Returns dict with keys: mode, balances, positions, error.
    - mode: 'PRIVATE_OK' if success, 'PUBLIC_ONLY' otherwise.
    """
    api_key = settings.get_str("BINANCE_API_KEY") or settings.get_str("BINANCE_FAPI_KEY") or settings.get_str("API_KEY")
    api_secret = settings.get_str("BINANCE_API_SECRET") or settings.get_str("BINANCE_FAPI_SECRET") or settings.get_str("API_SECRET")
    if not api_key or not api_secret:
        return {
            "mode": "PUBLIC_ONLY",
            "balances": None,
            "positions": None,
            "error": "account_snapshot_failed: missing_api_keys",
            "error_category": "CONFIG_ERROR",
            "error_reason": "missing_api_keys",
            "endpoint": "/fapi/v2/account",
            "http_status": None,
            "binance_error_code": None,
            "binance_error_msg": None,
        }
    request_ts = _request_timestamp_ms()
    try:
        from core.execution import binance_futures

        def _flt(val: Any) -> Optional[float]:
            try:
                return float(val)
            except Exception:
                return None

        def _fetch_account_info() -> Dict[str, Any]:
            endpoints = ("/fapi/v3/account", "/fapi/v2/account")
            last_error: Optional[Exception] = None
            for endpoint in endpoints:
                try:
                    payload = binance_futures._get(endpoint, {}, private=True)
                    if isinstance(payload, dict):
                        return {
                            "available_balance": _flt(payload.get("availableBalance")),
                            "total_wallet_balance": _flt(payload.get("totalWalletBalance")),
                            "total_margin_balance": _flt(payload.get("totalMarginBalance")),
                            "update_time": payload.get("updateTime"),
                            "endpoint": endpoint,
                        }
                except Exception as exc:
                    last_error = exc
                    continue
            if last_error:
                raise last_error
            return {}

        bals: Dict[str, float] = {}
        account_payload = _fetch_account_info()
        balances = binance_futures._get("/fapi/v2/balance", {}, private=True)
        if isinstance(balances, list):
            for b in balances:
                try:
                    val = float(b.get("balance", 0.0))
                    if val > 0:
                        bals[b.get("asset", "?")] = val
                except Exception:
                    continue
        positions_raw = binance_futures._get("/fapi/v2/positionRisk", {}, private=True)
        pos = positions_raw if isinstance(positions_raw, list) else []
        return {
            "mode": "PRIVATE_OK",
            "balances": bals,
            "account": account_payload,
            "positions": pos,
            "error": None,
            "error_category": None,
            "error_reason": None,
            "endpoint": account_payload.get("endpoint") if isinstance(account_payload, dict) else "/fapi/v2/account",
            "http_status": None,
            "binance_error_code": None,
            "binance_error_msg": None,
            "request_ts": request_ts,
        }
    except Exception as e:
        http_status, code, msg, diagnostics = _parse_binance_error(e)
        category, reason = _classify_error(
            http_status=http_status,
            error_code=code,
            error_msg=msg,
            exc=e,
        )
        if isinstance(diagnostics, dict):
            time_sync_snapshot = diagnostics.get("time_sync_snapshot")
            response_text_trim = diagnostics.get("response_text_trim")
            content_type = diagnostics.get("content_type")
            request_method = diagnostics.get("request_method")
            request_endpoint = diagnostics.get("request_endpoint")
            signed_params_snapshot = diagnostics.get("signed_params_snapshot")
        else:
            time_sync_snapshot = None
            response_text_trim = None
            content_type = None
            request_method = None
            request_endpoint = None
            signed_params_snapshot = None
        payload = {
            "endpoint": request_endpoint or "/fapi/v2/account",
            "http_status": http_status,
            "binance_error_code": code,
            "binance_error_msg": msg or str(e),
            "response_text_trim": response_text_trim,
            "content_type": content_type,
            "request_method": request_method,
            "request_endpoint": request_endpoint,
            "signed_params_snapshot": signed_params_snapshot,
            "time_sync_snapshot": time_sync_snapshot,
            "category": category,
            "reason": reason,
            "request_ts": request_ts,
        }
        _log_snapshot_error(payload)
        if reason == "timestamp_out_of_sync":
            try:
                from core.execution import binance_futures
                time_sync = binance_futures.get_time_sync_snapshot()
            except Exception:
                time_sync = None
            _log_snapshot_warning({
                "endpoint": "/fapi/v2/balance",
                "category": category,
                "reason": "timestamp_out_of_sync",
                "recommendation": "sync_system_clock_with_ntp",
                "request_ts": request_ts,
                "time_sync": time_sync,
            })
        return {
            "mode": "PUBLIC_ONLY",
            "balances": None,
            "positions": None,
            "error": f"account_snapshot_failed: {e}",
            "error_category": category,
            "error_reason": reason,
            "endpoint": request_endpoint or "/fapi/v2/account",
            "http_status": http_status,
            "binance_error_code": code,
            "binance_error_msg": msg,
            "response_text_trim": response_text_trim,
            "content_type": content_type,
            "request_method": request_method,
            "request_endpoint": request_endpoint,
            "signed_params_snapshot": signed_params_snapshot,
            "time_sync_snapshot": time_sync_snapshot,
            "request_ts": request_ts,
        }
