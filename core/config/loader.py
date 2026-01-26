from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional
from core.config.env import parse_bool, get_env, load_dotenv_once
from core.runtime_mode import get_runtime_settings

# =====================
# Config dataclass
# =====================
@dataclass
class Config:
    ENV: str = "production"
    PAPER_TRADING: bool = True
    TRADE_ENABLED: bool = False
    BINANCE_TESTNET: bool = False

    EXCHANGE: str = "binance_futures"
    SYMBOL: str = "BTCUSDT"
    INTERVAL: str = "1m"
    HTF_INTERVAL: str = "15m"
    QUOTE_ASSET: str = "USDT"

    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""

    LOG_DIR: str = "logs"

# ---- Centralized ENV normalization & singleton config ----
_SYNONYMS = {
    "BINANCE_API_KEY": ["BINANCE_API_KEY", "API_KEY", "BINANCE_FAPI_KEY"],
    "BINANCE_API_SECRET": ["BINANCE_API_SECRET", "API_SECRET", "BINANCE_FAPI_SECRET"],
    "BINANCE_FAPI_KEY": ["BINANCE_FAPI_KEY", "BINANCE_API_KEY", "API_KEY"],
    "BINANCE_FAPI_SECRET": ["BINANCE_FAPI_SECRET", "BINANCE_API_SECRET", "API_SECRET"],
    "PAPER_TRADING": ["PAPER_TRADING"],
    "TRADE_ENABLED": ["TRADE_ENABLED"],
    "BINANCE_TESTNET": ["BINANCE_TESTNET"],
    "SYMBOL": ["SYMBOL"],
    "INTERVAL": ["INTERVAL"],
    "LOG_DIR": ["LOG_DIR"],
}

def normalize_env(environ: Optional[dict] = None) -> None:
    """
    Normalize environment variables to a canonical schema in os.environ.
    This is the ONLY place that 'analyzes' .env — other modules should just read canonical keys.
    """
    e = dict(os.environ)
    if environ:
        e.update(environ)

    # Forward mapping: set canonical keys from the first non-empty alias
    for canonical, keys in _SYNONYMS.items():
        val = None
        for k in keys:
            v = e.get(k)
            if v is not None and str(v).strip() != "":
                val = str(v)
                break
        if val is not None:
            os.environ[canonical] = val

    # Canonical defaults (only if not present)
    os.environ.setdefault("PAPER_TRADING", "1")
    os.environ.setdefault("TRADE_ENABLED", "0")
    os.environ.setdefault("BINANCE_TESTNET", "0")
    os.environ.setdefault("SYMBOL", "BTCUSDT")
    os.environ.setdefault("INTERVAL", "5m")
    os.environ.setdefault("LOG_DIR", "logs")

    # Non-production defaults: force dry-run to prevent real trading
    if str(os.environ.get("ENV", "production")).lower() != "production":
        os.environ["DRY_RUN_ONLY"] = "1"

# Singleton holder for cached Config
_CONFIG_SINGLETON: Optional[Config] = None

def load_config() -> Config:
    """Build Config from (already normalized) environment."""
    return Config(
        ENV=get_env("ENV", "production"),
        PAPER_TRADING=parse_bool(get_env("PAPER_TRADING", "1")),
        TRADE_ENABLED=parse_bool(get_env("TRADE_ENABLED", "0")),
        BINANCE_TESTNET=parse_bool(get_env("BINANCE_TESTNET", "0")),

        EXCHANGE=get_env("EXCHANGE", "binance_futures"),
        SYMBOL=get_env("SYMBOL", "BTCUSDT"),
        INTERVAL=get_env("INTERVAL", "5m"),
        HTF_INTERVAL=get_env("HTF_INTERVAL", "1h"),
        QUOTE_ASSET=get_env("QUOTE_ASSET", "USDT"),

        BINANCE_API_KEY=get_env("BINANCE_API_KEY", ""),
        BINANCE_API_SECRET=get_env("BINANCE_API_SECRET", ""),

        LOG_DIR=get_env("LOG_DIR", "logs"),
    )

def get_config() -> Config:
    global _CONFIG_SINGLETON
    if _CONFIG_SINGLETON is None:
        load_dotenv_once()
        normalize_env()
        _CONFIG_SINGLETON = load_config()
        _validate_config(_CONFIG_SINGLETON)
    return _CONFIG_SINGLETON

def _validate_config(cfg: Config) -> None:
    """Validate production-critical settings without logging secrets."""
    env_name = str(cfg.ENV or "production").lower()
    trade_enabled = bool(cfg.TRADE_ENABLED)
    dry_run = parse_bool(get_env("DRY_RUN_ONLY", "1"))
    runtime = get_runtime_settings()
    if env_name == "production" and trade_enabled and (not dry_run or runtime.is_live):
        api_key = get_env("BINANCE_API_KEY") or get_env("API_KEY") or get_env("BINANCE_FAPI_KEY")
        api_secret = get_env("BINANCE_API_SECRET") or get_env("API_SECRET") or get_env("BINANCE_FAPI_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Missing required API keys for production trading. "
                "Set BINANCE_API_KEY/BINANCE_API_SECRET (or API_KEY/API_SECRET) "
                "or enable DRY_RUN_ONLY=1."
            )
        required = {
            "EXCHANGE": get_env("EXCHANGE"),
            "SYMBOL": get_env("SYMBOL"),
            "INTERVAL": get_env("INTERVAL"),
            "LOG_DIR": get_env("LOG_DIR"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError(f"Missing required production settings: {','.join(missing)}")

__all__ = ["Config", "normalize_env", "load_config", "get_config"]
