from __future__ import annotations

from types import SimpleNamespace


def test_private_get_uses_offset_and_recvwindow(monkeypatch):
    from core.execution import binance_futures
    from core.config import settings

    binance_futures.API_KEY = "k"
    binance_futures.API_SECRET = "s"

    monkeypatch.setattr(binance_futures, "_local_time_ms", lambda: 1_700_000_000_000)
    monkeypatch.setattr(binance_futures, "_fetch_server_time_ms", lambda: 1_700_000_005_000)

    captured = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    monkeypatch.setattr(binance_futures.requests, "get", _fake_get)
    monkeypatch.setattr(settings, "get_int", lambda name, default=None: 5000 if name == "BINANCE_RECV_WINDOW" else default or 0)

    binance_futures._TIME_SYNC._offset_ms = 0
    binance_futures._TIME_SYNC._last_sync_ts = 0.0

    binance_futures._get("/fapi/v2/balance", {}, private=True)

    request_url = captured["url"]
    assert captured["params"] is None
    assert "recvWindow=5000" in request_url
    assert "timestamp=1700000005000" in request_url


def test_retry_on_timestamp_out_of_sync(monkeypatch):
    from core.execution import binance_futures

    binance_futures.API_KEY = "k"
    binance_futures.API_SECRET = "s"

    calls = {"refresh": 0, "private": 0}

    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.headers = {"Content-Type": "application/json"}
            self.text = ""

        def json(self):
            return self._payload

    def _fake_get(url, params=None, headers=None, timeout=None):
        if "/fapi/v2/balance" in url:
            calls["private"] += 1
            if calls["private"] == 1:
                return _Resp(400, {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."})
            return _Resp(200, {"ok": True})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(binance_futures.requests, "get", _fake_get)
    monkeypatch.setattr(binance_futures, "_local_time_ms", lambda: 1_700_000_000_000)
    def _fake_fetch():
        calls["refresh"] += 1
        return 1_700_000_005_000

    monkeypatch.setattr(binance_futures, "_fetch_server_time_ms", _fake_fetch)

    binance_futures._TIME_SYNC._offset_ms = 0
    binance_futures._TIME_SYNC._last_sync_ts = 0.0

    res = binance_futures._get("/fapi/v2/balance", {}, private=True)
    assert res == {"ok": True}
    assert calls["private"] == 2
    assert calls["refresh"] >= 1


def test_stale_offset_refresh(monkeypatch):
    from core.execution import binance_futures
    from core.config import settings

    binance_futures.API_KEY = "k"
    binance_futures.API_SECRET = "s"

    monkeypatch.setattr(settings, "get_int", lambda name, default=None: 1 if name == "BINANCE_TIME_OFFSET_TTL_SEC" else (5000 if name == "BINANCE_RECV_WINDOW" else default or 0))
    monkeypatch.setattr(binance_futures, "_local_time_ms", lambda: 1_700_000_000_000)
    monkeypatch.setattr(binance_futures, "_fetch_server_time_ms", lambda: 1_700_000_010_000)

    captured = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    monkeypatch.setattr(binance_futures.requests, "get", _fake_get)

    binance_futures._TIME_SYNC._offset_ms = 0
    binance_futures._TIME_SYNC._last_sync_ts = 0.0

    binance_futures._get("/fapi/v2/balance", {}, private=True)

    request_url = captured["url"]
    assert "timestamp=1700000010000" in request_url
