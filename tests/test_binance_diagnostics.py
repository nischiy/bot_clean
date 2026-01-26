from __future__ import annotations

import logging

import pytest


def _setup_env(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")


def test_preflight_diagnostics_includes_code_and_classifies_config(monkeypatch, caplog):
    _setup_env(monkeypatch)
    from core.execution.binance_futures import BinanceRequestError

    diagnostics = {
        "http_status": 400,
        "binance_error_code": -2015,
        "binance_error_msg": "Invalid API-key, IP, or permissions for action.",
        "response_text_trim": None,
        "content_type": "application/json",
        "request_method": "GET",
        "request_endpoint": "/fapi/v2/balance",
        "signed_params_snapshot": {"query_string_no_signature": "recvWindow=5000&timestamp=123"},
        "time_sync_snapshot": {"server_time_ms": 1, "local_time_ms": 2, "offset_ms": -1, "ttl_sec": 60, "age_ms": 10},
    }

    def _fake_get(path, params, private=False):
        raise BinanceRequestError("HTTP 400", diagnostics)

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    caplog.set_level(logging.ERROR)
    from core.exchange_private import fetch_futures_private

    snapshot = fetch_futures_private()
    assert snapshot["error_category"] == "CONFIG_ERROR"
    assert snapshot["error_reason"] == "invalid_api_permissions"
    assert snapshot["binance_error_code"] == -2015
    assert "Invalid API-key" in (snapshot.get("binance_error_msg") or "")
    assert "signature=" not in caplog.text


def test_preflight_diagnostics_captures_non_json_body(monkeypatch, caplog):
    _setup_env(monkeypatch)
    from core.execution.binance_futures import BinanceRequestError

    diagnostics = {
        "http_status": 400,
        "binance_error_code": None,
        "binance_error_msg": None,
        "response_text_trim": "<html>WAF</html>",
        "content_type": "text/html",
        "request_method": "GET",
        "request_endpoint": "/fapi/v2/balance",
        "signed_params_snapshot": {"query_string_no_signature": "recvWindow=5000&timestamp=123"},
        "time_sync_snapshot": {"server_time_ms": 1, "local_time_ms": 2, "offset_ms": -1, "ttl_sec": 60, "age_ms": 10},
    }

    def _fake_get(path, params, private=False):
        raise BinanceRequestError("HTTP 400", diagnostics)

    from core.execution import binance_futures
    monkeypatch.setattr(binance_futures, "_get", _fake_get)

    caplog.set_level(logging.ERROR)
    from core.exchange_private import fetch_futures_private

    snapshot = fetch_futures_private()
    assert snapshot["binance_error_code"] is None
    assert snapshot["binance_error_msg"] is None
    assert snapshot["response_text_trim"] == "<html>WAF</html>"
    assert snapshot["content_type"] == "text/html"
    assert "signature=" not in caplog.text
