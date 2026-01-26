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

    binance_futures._TIME_OFFSET_MS = 0
    binance_futures._TIME_OFFSET_FETCH_TS = 0.0

    binance_futures._get("/fapi/v2/balance", {}, private=True)

    request_url = captured["url"]
    assert captured["params"] is None
    assert "recvWindow=5000" in request_url
    assert "timestamp=1700000005000" in request_url
