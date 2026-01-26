from __future__ import annotations

from app.bootstrap import compose_trader_app
from core.config.loader import get_config

def test_compose_trader_app_wiring():
    cfg = get_config()
    app = compose_trader_app(cfg)
    assert hasattr(app, "start") and callable(app.start)
