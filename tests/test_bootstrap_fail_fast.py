from __future__ import annotations

from app.bootstrap import compose_trader_app
from core.config import loader as cfg_loader

def _reset_config():
    cfg_loader._CONFIG_SINGLETON = None

def test_compose_trader_app_minimal_services():
    _reset_config()
    cfg = cfg_loader.get_config()
    app = compose_trader_app(cfg)
    assert app is not None
    assert hasattr(app, "md")
