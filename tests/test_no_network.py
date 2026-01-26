from __future__ import annotations

import importlib
import os
import socket
import sys
import types

import pytest

class NetworkBlocked(RuntimeError):
    pass

def _block_requests(monkeypatch):
    try:
        import requests
    except Exception:
        return

    def _fail(*_args, **_kwargs):
        raise NetworkBlocked("Network access blocked during tests")

    monkeypatch.setattr(requests, "get", _fail, raising=True)
    monkeypatch.setattr(requests, "post", _fail, raising=True)
    monkeypatch.setattr(requests, "delete", _fail, raising=True)
    monkeypatch.setattr(requests.sessions.Session, "request", _fail, raising=True)

def _block_urllib(monkeypatch):
    import urllib.request

    def _fail(*_args, **_kwargs):
        raise NetworkBlocked("Network access blocked during tests")

    monkeypatch.setattr(urllib.request, "urlopen", _fail, raising=True)

def _block_optional_clients(monkeypatch):
    try:
        import httpx
        monkeypatch.setattr(httpx, "request", lambda *_a, **_k: (_ for _ in ()).throw(NetworkBlocked()))
        monkeypatch.setattr(httpx.Client, "request", lambda *_a, **_k: (_ for _ in ()).throw(NetworkBlocked()))
    except Exception:
        pass
    try:
        import aiohttp
        monkeypatch.setattr(aiohttp.ClientSession, "_request", lambda *_a, **_k: (_ for _ in ()).throw(NetworkBlocked()))
    except Exception:
        pass

def _block_socket(monkeypatch):
    class BlockedSocket(socket.socket):
        def connect(self, *_args, **_kwargs):
            raise NetworkBlocked("Network access blocked during tests")

    monkeypatch.setattr(socket, "socket", BlockedSocket, raising=True)
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (_ for _ in ()).throw(NetworkBlocked()), raising=True)

def _block_binance_client(monkeypatch):
    # If binance is not installed, inject a stub that fails on Client usage
    mod = types.ModuleType("binance")
    client_mod = types.ModuleType("binance.client")

    class _Client:
        def __init__(self, *_a, **_k):
            raise NetworkBlocked("Binance client blocked during tests")

    client_mod.Client = _Client
    mod.client = client_mod
    monkeypatch.setitem(sys.modules, "binance", mod)
    monkeypatch.setitem(sys.modules, "binance.client", client_mod)
    try:
        import websockets
        monkeypatch.setattr(websockets, "connect", lambda *_a, **_k: (_ for _ in ()).throw(NetworkBlocked()))
    except Exception:
        pass

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_no_network_on_imports_or_bootstrap(monkeypatch):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("DRY_RUN_ONLY", "1")
    monkeypatch.setenv("TRADE_ENABLED", "0")
    monkeypatch.setenv("OFFLINE_MODE", "1")

    _block_requests(monkeypatch)
    _block_urllib(monkeypatch)
    _block_optional_clients(monkeypatch)
    _block_socket(monkeypatch)
    _block_binance_client(monkeypatch)

    # Re-import key modules under network blockade
    importlib.reload(importlib.import_module("core.config.loader"))
    importlib.reload(importlib.import_module("app.bootstrap"))

    from core.config.loader import get_config
    from app.bootstrap import compose_trader_app

    cfg = get_config()
    app = compose_trader_app(cfg)
    assert app is not None
