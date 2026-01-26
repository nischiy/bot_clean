from __future__ import annotations

import json
import sys
import requests


def test_fetch_futures_private_uses_rest(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")

    def _fake_get(path, params, private=False):
        if path == "/fapi/v2/balance":
            return [{"asset": "USDT", "balance": "123.45"}]
        if path == "/fapi/v2/positionRisk":
            return [{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        raise AssertionError(f"unexpected path: {path}")

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    from core.exchange_private import fetch_futures_private
    snapshot = fetch_futures_private()

    assert snapshot["mode"] == "PRIVATE_OK"
    assert snapshot["balances"]["USDT"] == 123.45
    assert snapshot["positions"][0]["symbol"] == "BTCUSDT"
    assert "binance" not in sys.modules


def _http_error(status_code: int, code: int, msg: str) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps({"code": code, "msg": msg}).encode("utf-8")
    return requests.HTTPError(response=resp)


def test_fetch_futures_private_futures_disabled(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")

    def _fake_get(path, params, private=False):
        if path == "/fapi/v2/balance":
            raise _http_error(400, -2015, "Futures not enabled")
        raise AssertionError(f"unexpected path: {path}")

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    from core.exchange_private import fetch_futures_private
    snapshot = fetch_futures_private()

    assert snapshot["mode"] == "PUBLIC_ONLY"
    assert snapshot["error_category"] == "CONFIG_ERROR"
    assert snapshot["error_reason"] == "futures_not_enabled"


def test_fetch_futures_private_invalid_permissions(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")

    def _fake_get(path, params, private=False):
        if path == "/fapi/v2/balance":
            raise _http_error(400, -2015, "Invalid API-key, IP, or permissions for action.")
        raise AssertionError(f"unexpected path: {path}")

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    from core.exchange_private import fetch_futures_private
    snapshot = fetch_futures_private()

    assert snapshot["mode"] == "PUBLIC_ONLY"
    assert snapshot["error_category"] == "CONFIG_ERROR"
    assert snapshot["error_reason"] == "invalid_api_permissions"


def test_fetch_futures_private_timestamp_drift(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")

    def _fake_get(path, params, private=False):
        if path == "/fapi/v2/balance":
            raise _http_error(400, -1021, "Timestamp for this request is outside of the recvWindow.")
        raise AssertionError(f"unexpected path: {path}")

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    from core.exchange_private import fetch_futures_private
    snapshot = fetch_futures_private()

    assert snapshot["mode"] == "PUBLIC_ONLY"
    assert snapshot["error_category"] == "TRANSIENT_ERROR"
    assert snapshot["error_reason"] == "timestamp_out_of_sync"
