from __future__ import annotations

import os

import pytest

from core.config import loader as cfg_loader
from core.runtime_mode import reset_runtime_settings

def _reset_config():
    cfg_loader._CONFIG_SINGLETON = None
    reset_runtime_settings()

def test_config_validation_production_requires_keys(monkeypatch):
    _reset_config()
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "API_KEY", "API_SECRET", "BINANCE_FAPI_KEY", "BINANCE_FAPI_SECRET"):
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(RuntimeError):
        cfg_loader.get_config()

def test_config_validation_local_allows_missing_keys(monkeypatch):
    _reset_config()
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "API_KEY", "API_SECRET", "BINANCE_FAPI_KEY", "BINANCE_FAPI_SECRET"):
        monkeypatch.delenv(k, raising=False)

    cfg = cfg_loader.get_config()
    assert cfg is not None
    assert os.environ.get("DRY_RUN_ONLY") == "1"
