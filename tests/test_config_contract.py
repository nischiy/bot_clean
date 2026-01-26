from __future__ import annotations

from core.config.loader import get_config

def test_config_contract():
    cfg = get_config()
    assert cfg is not None

    required_fields = [
        "ENV",
        "PAPER_TRADING",
        "TRADE_ENABLED",
        "BINANCE_TESTNET",
        "EXCHANGE",
        "SYMBOL",
        "INTERVAL",
        "HTF_INTERVAL",
        "QUOTE_ASSET",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "LOG_DIR",
    ]
    for name in required_fields:
        assert hasattr(cfg, name), f"Config missing field: {name}"
