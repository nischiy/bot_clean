# app/bootstrap.py
from __future__ import annotations

import os
import logging
import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union, Callable, Tuple, Type
from core.config.env import parse_bool, get_env
from core.config import settings


# ----------------------------- Config & utils ---------------------------------

@dataclass(frozen=True)
class AppConfig:
    symbol: str
    paper_trading: bool
    trade_enabled: bool
    dry_run_only: bool
    loop_sleep_sec: float
    binance_fapi_base: Optional[str] = None
    log_dir: str = "logs"
    log_level: str = "INFO"


def _to_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _get(obj: Any, name: str, default: Any) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _coerce_to_appconfig(cfg: Optional[Any], env: Optional[Dict[str, str]] = None) -> AppConfig:
    env = env or os.environ

    symbol = _get(cfg, "SYMBOL", get_env("SYMBOL", "BTCUSDT", env=env))

    paper = parse_bool(_get(cfg, "PAPER_TRADING", get_env("PAPER_TRADING", None, env=env)), default=True)
    trade_enabled = parse_bool(_get(cfg, "TRADE_ENABLED", get_env("TRADE_ENABLED", None, env=env)), default=False)
    dry_run = parse_bool(_get(cfg, "DRY_RUN_ONLY", get_env("DRY_RUN_ONLY", None, env=env)), default=True)
    if paper:
        dry_run = True  # політика: paper ⇒ dry-run

    loop_sleep = _to_float(_get(cfg, "LOOP_SLEEP_SEC", get_env("LOOP_SLEEP_SEC", None, env=env)), 1.0)
    binance_fapi_base = _get(cfg, "BINANCE_FAPI_BASE", get_env("BINANCE_FAPI_BASE", None, env=env))
    log_dir = _get(cfg, "LOG_DIR", get_env("LOG_DIR", "logs", env=env))

    raw_level = _get(cfg, "LOG_LEVEL", get_env("LOG_LEVEL", "INFO", env=env))
    log_level = str(raw_level).upper() if not isinstance(raw_level, int) else {
        v: k for k, v in logging.__dict__.items() if isinstance(v, int)
    }.get(raw_level, "INFO")

    # Виставляємо назад в ENV для сервісів, що читають os.environ
    os.environ["PAPER_TRADING"] = "1" if paper else "0"
    os.environ["TRADE_ENABLED"] = "1" if trade_enabled else "0"
    os.environ["DRY_RUN_ONLY"] = "1" if dry_run else "0"
    os.environ.setdefault("SYMBOL", symbol)
    if binance_fapi_base:
        os.environ.setdefault("BINANCE_FAPI_BASE", binance_fapi_base)
    os.environ.setdefault("LOG_DIR", log_dir)
    os.environ.setdefault("LOG_LEVEL", log_level)
    os.environ.setdefault("LOOP_SLEEP_SEC", str(loop_sleep))

    return AppConfig(
        symbol=symbol,
        paper_trading=paper,
        trade_enabled=trade_enabled,
        dry_run_only=dry_run,
        loop_sleep_sec=loop_sleep,
        binance_fapi_base=binance_fapi_base,
        log_dir=log_dir,
        log_level=log_level,
    )


def resolve_runtime_mode(env: Optional[Dict[str, str]] = None) -> AppConfig:
    return _coerce_to_appconfig(cfg=None, env=env)


def _ensure_logging(cfg: AppConfig) -> logging.Logger:
    level = getattr(logging, str(cfg.log_level).upper(), None)
    if not isinstance(level, int):
        try:
            level = int(cfg.log_level)
        except Exception:
            level = logging.INFO

    log = logging.getLogger("Bootstrap")
    log.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in log.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
        log.addHandler(sh)
    try:
        if "PYTEST_CURRENT_TEST" not in os.environ:
            os.makedirs(cfg.log_dir, exist_ok=True)
            fp = os.path.join(cfg.log_dir, "bootstrap.log")
            if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
                fh = logging.FileHandler(fp, encoding="utf-8")
                fh.setLevel(level)
                fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
                log.addHandler(fh)
    except Exception as e:
        log.warning("Cannot init file logging: %s", e)
    return log


def _import_module(name: str):
    return importlib.import_module(name)


def _construct_with_filtered_kwargs(cls: Type, base_kwargs: Dict[str, Any]):
    try:
        sig = inspect.signature(cls.__init__)
        allowed = {k: v for k, v in base_kwargs.items() if k in sig.parameters}
    except (ValueError, TypeError):
        allowed = base_kwargs
    return cls(**allowed) if allowed else cls()


# ------------------------ Thin adapters (function or class) --------------------

def _resolve_callable(mod, names: Tuple[str, ...]) -> Optional[Callable[..., Any]]:
    for n in names:
        obj = getattr(mod, n, None)
        if callable(obj):
            return obj
    return None


def _resolve_class_with_methods(
    mod,
    method_pairs: Tuple[Tuple[str, ...], Tuple[str, ...]],
    *,
    prefer_suffixes: Tuple[str, ...] = ("service", "client", "provider"),
) -> Optional[Type]:
    """
    Шукає клас у модулі, який має два необхідні методи (з вказаних варіантів назв).
    method_pairs: ((methodA_names...), (methodB_names...))
    """
    need_a, need_b = method_pairs
    candidates: list[Type] = []
    for attr_name in dir(mod):
        cls = getattr(mod, attr_name, None)
        if not inspect.isclass(cls):
            continue
        methods = dir(cls)
        ok_a = any(m in methods for m in need_a)
        ok_b = any(m in methods for m in need_b)
        if ok_a and ok_b:
            candidates.append(cls)
    if not candidates:
        return None
    # якщо є кілька — пріоритезуємо за суфіксами
    lower = {c: c.__name__.lower() for c in candidates}
    for suf in prefer_suffixes:
        for c in candidates:
            if lower[c].endswith(suf):
                return c
    return candidates[0]


class MarketDataAdapter:
    """
    Працює з:
      - модульними функціями: get_klines(...), get_latest_price(...)
      - або з класом у модулі, що має відповідні методи (напр., MarketDataClient, MarketDataService)
    """
    FN_KLINES = ("get_klines", "fetch_klines", "klines")
    FN_PRICE = ("get_latest_price", "latest_price", "get_price", "price")

    def __init__(self, *, module_name: str = "app.services.market_data", cfg=None, symbol: str | None = None, logger=None, **_):
        self.cfg = cfg
        self.symbol = symbol
        self.log = logger or logging.getLogger("MarketData")
        mod = _import_module(module_name)

        # 1) спроба: модульні функції
        fn_kl = _resolve_callable(mod, self.FN_KLINES)
        fn_pr = _resolve_callable(mod, self.FN_PRICE)

        if fn_kl and fn_pr:
            self._kind = "module_functions"
            self._fn_klines = fn_kl
            self._fn_price = fn_pr
            self._cls_inst = None
        else:
            # 2) спроба: знайти клас із потрібними методами
            cls = _resolve_class_with_methods(mod, (self.FN_KLINES, self.FN_PRICE))
            if not cls:
                raise ImportError("market_data: neither functions nor class with required methods found")
            self._kind = "class_instance"
            self._cls_inst = _construct_with_filtered_kwargs(cls, {"cfg": cfg, "symbol": symbol, "logger": logger})
            self._fn_klines = None
            self._fn_price = None

    def get_klines(self, symbol: str | None = None, interval: str = "1m", limit: int = 500, **kwargs):
        sym = symbol or self.symbol
        if not sym:
            raise ValueError("symbol is required for get_klines()")
        if self._kind == "module_functions":
            return self._fn_klines(sym, interval=interval, limit=limit, **kwargs)
        # class_instance
        # шукаємо доступну назву методу
        for name in self.FN_KLINES:
            if hasattr(self._cls_inst, name):
                return getattr(self._cls_inst, name)(sym, interval=interval, limit=limit, **kwargs)
        raise AttributeError("market_data class instance lost klines method")

    def get_latest_price(self, symbol: str | None = None, **kwargs) -> float:
        sym = symbol or self.symbol
        if not sym:
            raise ValueError("symbol is required for get_latest_price()")
        if self._kind == "module_functions":
            return self._fn_price(sym, **kwargs)
        for name in self.FN_PRICE:
            if hasattr(self._cls_inst, name):
                return getattr(self._cls_inst, name)(sym, **kwargs)
        raise AttributeError("market_data class instance lost price method")


# -------------------------------- Composition ----------------------------------

def compose_trader_app(cfg: Optional[Union[AppConfig, Dict[str, Any], Any]] = None):
    app_cfg = cfg if isinstance(cfg, AppConfig) else _coerce_to_appconfig(cfg)
    log = _ensure_logging(app_cfg)

    # 1) TraderApp
    try:
        TraderApp = getattr(_import_module("app.run"), "TraderApp")
    except Exception as e:
        raise RuntimeError(f"Critical component app.run.TraderApp missing: {e}") from e
    app_logger = logging.getLogger("TraderApp")
    trader_app = _construct_with_filtered_kwargs(TraderApp, {
        "logger": app_logger,
        "symbol": app_cfg.symbol,
        "cfg": cfg if cfg is not None else app_cfg,
    })

    # 2) Wire adapters
    try:
        md_module = settings.get_str("MARKET_DATA_MODULE", "app.services.market_data")
        setattr(trader_app, "md", MarketDataAdapter(module_name=md_module, cfg=cfg or app_cfg, symbol=app_cfg.symbol, logger=logging.getLogger("MarketData")))
    except Exception as e:
        raise RuntimeError(f"Critical component MarketDataAdapter failed: {e}") from e
    log.info("Wired MarketDataAdapter -> trader_app.md")

    log.info(
        "Mode: PAPER=%s, TRADE_ENABLED=%s, DRY_RUN_ONLY=%s, SYMBOL=%s",
        app_cfg.paper_trading, app_cfg.trade_enabled, app_cfg.dry_run_only, app_cfg.symbol
    )

    return trader_app
