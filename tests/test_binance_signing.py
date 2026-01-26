from __future__ import annotations

import hmac
import hashlib
from types import SimpleNamespace


def test_canonical_query_string_used_for_signing(monkeypatch):
    from core.execution import binance_futures

    binance_futures.API_KEY = "k"
    binance_futures.API_SECRET = "secret"

    captured = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    monkeypatch.setattr(binance_futures.requests, "get", _fake_get)
    monkeypatch.setattr(binance_futures._TIME_SYNC, "get_timestamp_ms", lambda: 1700000005000)

    binance_futures._get("/fapi/v2/balance", {}, private=True)

    request_url = captured["url"]
    assert captured["params"] is None
    assert "signature=" in request_url
    assert "recvWindow=" in request_url
    assert "timestamp=1700000005000" in request_url

    base = request_url.split("?", 1)[1]
    canonical_qs, signature = base.split("&signature=", 1)
    expected = hmac.new(b"secret", canonical_qs.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
