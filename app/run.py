from __future__ import annotations
import os
import math
import json
import sys
import time
import logging
import logging.handlers
from datetime import datetime, timezone
from importlib import import_module
from typing import Callable, Optional, Any, Dict, List, Tuple

import pandas as pd

from core.config.env import get_env, get_bool, load_dotenv_once, dotenv_loaded, dotenv_override_report, dotenv_paths
from core.config import settings
from app.core import logging as runtime_logging
from core.runtime_mode import get_runtime_settings

LOG_NAME = "BotRun"
DECISION_INTERVAL = "5m"
_FILTERS_CACHE: Dict[str, Dict[str, Any]] = {}

def _is_pytest_env() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def _is_testish_env() -> bool:
    """Detect test environment for side-effect control (no CI shortcut)."""
    env = os.environ
    return (
        env.get("APP_RUN_ONESHOT", "0") == "1"
        or _is_pytest_env()
        or str(get_env("RUNTIME_MODE", "") or "").strip().lower() == "test"
    )

def _ensure_runtime_logging(logger: logging.Logger) -> Dict[str, Any]:
    return runtime_logging.ensure_runtime_logging(
        logger,
        log_dir=settings.get_str("LOG_DIR", "logs"),
        pytest_env=_is_pytest_env(),
    )


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger(LOG_NAME)
    if logger.handlers:
        _ensure_runtime_logging(logger)
        return logger
    _ensure_runtime_logging(logger)
    return logger

class TraderApp:
    """Фасад життєвого циклу бота, сумісний з тестами."""
    def __init__(self, cfg: Any=None, symbol: Optional[str]=None, interval: Optional[str]=None, logger: Optional[logging.Logger]=None):
        self.log = logger or _setup_logging()
        self._log_setup = _ensure_runtime_logging(self.log)
        self.cfg = cfg
        self.symbol = symbol or settings.get_str("SYMBOL", "BTCUSDT")
        self.interval = interval or settings.get_str("INTERVAL", "1m")
        self._last_closed_candle_ts: Optional[int] = None
        self._skip_state_key: Optional[tuple] = None
        self._skip_last_summary_ts: int = 0
        self._skip_agg_start: Optional[int] = None
        self._skip_agg_count: int = 0
        self._last_tick_log_ts: int = 0
        self._last_will_process: bool = False
        self.paper = get_bool("PAPER_TRADING", True)
        self.trade_enabled = get_bool("TRADE_ENABLED", False)
        self.md = None   # expects get_klines(symbol, interval, limit=...)

    def run_once(self) -> None:
        _run_once_contracts(self)

    def start(self, oneshot: Optional[bool]=None) -> None:
        try:
            from core.config.loader import get_config
            get_config()
        except Exception as e:
            raise RuntimeError(f"config_validation_failed: {e}") from e
        if oneshot is None:
            oneshot = (
                os.environ.get("APP_RUN_ONESHOT", "0") == "1"
                or _is_pytest_env()
                or str(get_env("RUNTIME_MODE", "") or "").strip().lower() == "test"
            )
        runtime_settings = get_runtime_settings()
        self.log.info("TraderApp.start(symbol=%s, interval=%s, paper=%s, trade_enabled=%s, oneshot=%s, mode=%s)",
                      self.symbol, self.interval, self.paper, self.trade_enabled, oneshot, runtime_settings.mode.value)
        _log_structured(self.log, "runtime_logging_ready", {
            "log_dir": self._log_setup.get("log_dir"),
            "runtime_log": self._log_setup.get("runtime_log"),
            "file_handler": self._log_setup.get("file_handler"),
            "pytest_env": self._log_setup.get("pytest_env"),
            "oneshot": oneshot,
        })
        if runtime_settings.safe_run:
            self.log.info("SAFE_RUN active: execution is blocked (paper mirror).")
        if runtime_settings.live_readonly or get_bool("LIVE_READONLY", False):
            self.log.info("MODE: LIVE_READONLY (mutations blocked)")
        if self.trade_enabled and (not _is_testish_env() or get_bool("FORCE_RECONCILE", False)):
            _startup_reconcile(
                self.log,
                self.symbol,
                require_tp=get_bool("EXIT_REQUIRE_TP", True),
            )
        if oneshot:
            try:
                self.run_once()
            except Exception:
                self.log.exception("TraderApp oneshot crashed")
                raise
            return
        i = 0
        try:
            while True:
                i += 1
                try:
                    self.run_once()
                except Exception:
                    self.log.exception("TraderApp loop crashed")
                    raise
                now_ts = int(time.time())
                if self._last_will_process or (now_ts - self._last_tick_log_ts) >= 60:
                    self.log.info("Tick #%d", i)
                    self._last_tick_log_ts = now_ts
                sleep_sec = settings.get_float("LOOP_SLEEP_SEC")
                time.sleep(sleep_sec)
        except KeyboardInterrupt:
            self.log.info("TraderApp shutdown requested.")

def _try_get_main() -> Optional[Callable[..., None]]:
    candidates = [
        ("app.bootstrap", "main"),
    ]
    for mod_name, func_name in candidates:
        try:
            mod = import_module(mod_name)
        except Exception:
            continue
        fn = getattr(mod, func_name, None)
        if callable(fn):
            return fn
    return None

def _print_env(logger: logging.Logger) -> None:
    runtime = get_runtime_settings()
    overrides = dotenv_override_report()
    env_files = dotenv_paths()
    validation_missing = False
    try:
        from app.core import validation as _validation
        validation_missing = _validation.jsonschema is None
    except Exception:
        validation_missing = True
    payload = {
        "env": get_env("ENV"),
        "live_readonly": get_bool("LIVE_READONLY", False),
        "safe_run": get_bool("SAFE_RUN", False),
        "dry_run_only": get_bool("DRY_RUN_ONLY", False),
        "trade_enabled": get_bool("TRADE_ENABLED", False),
        "paper_trading": get_bool("PAPER_TRADING", True),
        "symbol": get_env("SYMBOL"),
        "interval": get_env("INTERVAL"),
        "htf_interval": get_env("HTF_INTERVAL"),
        "log_dir": get_env("LOG_DIR"),
        "runtime_mode": runtime.mode.value,
        "dotenv_loaded": dotenv_loaded(),
        "dotenv_files": env_files,
        "mutations": "BLOCKED" if get_bool("LIVE_READONLY", False) else "ALLOWED",
    }
    if overrides.get("count"):
        payload["dotenv_override_count"] = overrides["count"]
        payload["dotenv_override_keys"] = overrides.get("keys", [])[:20]
    _log_structured(logger, "startup_banner", payload)
    if validation_missing:
        logger.error(
            "jsonschema_missing: install with 'pip install -r requirements.txt'"
        )

def _heartbeat(logger: logging.Logger, oneshot: bool) -> None:
    logger.info("No explicit app main() found. Heartbeat mode%s.", " (oneshot)" if oneshot else "")
    if oneshot:
        logger.info("Heartbeat #1 - exit (test/CI mode).")
        return
    i = 0
    try:
        while True:
            i += 1
            logger.info("Heartbeat #%d - bot idle (set TRADE_ENABLED=1 to trade).", i)
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Shutdown requested. Bye.")

# ================== helpers (тільки для run.py) ==================
def _startup_reconcile(logger: logging.Logger, symbol: str, *, require_tp: bool) -> None:
    try:
        from app.services.execution_service import ExecutionService
        from app.state.state_manager import reconcile_positions, load_position_state
        from core.risk_guard import kill
        from app.core.trade_ledger import append_event
    except Exception as e:
        raise RuntimeError(f"startup_reconcile_import_error: {e}") from e

    exe = ExecutionService(logger=logger)
    positions = exe.fetch_positions()
    try:
        open_orders = exe.get_open_orders(symbol)
    except Exception as e:
        raise RuntimeError(f"startup_reconcile_open_orders_failed: {e}") from e

    local_state = load_position_state(symbol)
    ok, errors = reconcile_positions(
        positions, open_orders=open_orders, local_state=local_state, require_tp=require_tp
    )
    if not ok:
        logger.error("startup_reconcile_failed: %s", errors)
        kill("startup_reconcile_failed", {"errors": errors, "symbol": symbol})
        append_event(
            event_type="kill_switch_triggered",
            symbol=symbol,
            timeframe=settings.get_str("INTERVAL", "5m"),
            correlation_id=f"{symbol}:startup",
            details={"errors": errors},
        )
        raise RuntimeError(f"startup_reconcile_failed: {errors}")

def _get_position_snapshot(symbol: str) -> Dict[str, Any]:
    try:
        from app.services.execution_service import ExecutionService
    except Exception:
        return {"side": None, "qty": 0.0, "entry": 0.0, "unrealized_pnl": 0.0, "liq_price": None}
    exe = ExecutionService()
    return exe.get_position_snapshot(symbol)

def _run_once_contracts(app: "TraderApp") -> None:
    from app.data.payload_builder import build_payload
    from app.data.market_data_validator import validate_market_data
    from app.strategy.decision_engine import make_decision
    from app.risk.risk_manager import create_trade_plan
    from app.core.validation import validate_trade_plan
    from app.core.trade_ledger import append_event, hash_json
    from core.risk_guard import kill, is_killed
    from app.state.state_manager import (
        load_last_closed_candle_ts,
        save_last_closed_candle_ts,
        load_or_initialize_daily_state,
        save_daily_state,
        save_position_state,
        record_trade_attempt,
    )
    from app.services.execution_service import ExecutionService

    now_ts = int(time.time())
    interval = app.interval or settings.get_str("INTERVAL", "5m")
    htf_interval = settings.get_str("HTF_INTERVAL", "1h")
    if app._last_closed_candle_ts is None:
        app._last_closed_candle_ts = load_last_closed_candle_ts()
    last_processed_ts = app._last_closed_candle_ts
    latest_closed_ts: Optional[int] = None

    position_snapshot = _get_position_snapshot(app.symbol)
    save_position_state(app.symbol, position_snapshot)

    exe = ExecutionService(logger=app.log)
    try:
        exchange_positions = exe.fetch_positions()
        has_open_position = any(float(p.get("positionAmt", 0) or 0) != 0.0 for p in exchange_positions)
        open_orders = exe.get_open_orders(app.symbol) if has_open_position else []
    except Exception as e:
        kill("exit_reconcile_error", {"symbol": app.symbol, "error": str(e)})
        append_event(
            event_type="kill_switch_triggered",
            symbol=app.symbol,
            timeframe=settings.get_str("INTERVAL", "5m"),
            correlation_id=f"{app.symbol}:{now_ts}",
            details={"reason": "exit_reconcile_error", "error": str(e)},
        )
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="reconcile_failed",
            killed_flag=is_killed(),
        )
        raise RuntimeError(f"exit_reconcile_error:{e}") from e

    from app.state.state_manager import reconcile_positions
    require_tp = get_bool("EXIT_REQUIRE_TP", True)
    ok_reconcile, errors = reconcile_positions(
        exchange_positions,
        open_orders=open_orders,
        local_state=position_snapshot,
        require_tp=require_tp,
    )
    if not ok_reconcile:
        skip_reason = "exits_missing" if any("position_without_" in err for err in errors) else "reconcile_failed"
        kill("exit_reconcile_failed", {"symbol": app.symbol, "errors": errors})
        append_event(
            event_type="exit_reconcile_failed",
            symbol=app.symbol,
            timeframe=settings.get_str("INTERVAL", "5m"),
            correlation_id=f"{app.symbol}:{now_ts}",
            details={"errors": errors},
        )
        append_event(
            event_type="kill_switch_triggered",
            symbol=app.symbol,
            timeframe=settings.get_str("INTERVAL", "5m"),
            correlation_id=f"{app.symbol}:{now_ts}",
            details={"reason": "exit_reconcile_failed"},
        )
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason=skip_reason,
            killed_flag=is_killed(),
        )
        raise RuntimeError(f"exit_reconcile_failed:{errors}")

    if is_killed():
        app.log.info("contracts: kill-switch active — trading halted")
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="killed",
            killed_flag=is_killed(),
        )
        return
    df_ltf = None
    df_htf = None
    if getattr(app, "md", None) and hasattr(app.md, "get_klines"):
        try:
            df_ltf = app.md.get_klines(app.symbol, interval, limit=1000)
            df_ltf = _ensure_utc_timestamps(df_ltf)
            df_ltf = _filter_closed_candles(df_ltf)
        except Exception as e:
            app.log.exception("md.get_klines(%s) failed: %s", interval, e)
            df_ltf = None
        try:
            df_htf = app.md.get_klines(app.symbol, htf_interval, limit=500)
            df_htf = _ensure_utc_timestamps(df_htf)
            df_htf = _filter_closed_candles(df_htf)
        except Exception:
            df_htf = None

    latest_closed_ts = _latest_closed_candle_ts(df_ltf)
    if latest_closed_ts is None:
        app.log.info("contracts: no closed %s candle available", interval)
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="no_closed_candle",
            killed_flag=is_killed(),
        )
        return

    if last_processed_ts is not None and latest_closed_ts <= last_processed_ts:
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="already_processed",
            killed_flag=is_killed(),
        )
        return


    wallet_usdt = _get_wallet_usdt()
    preflight = _read_preflight(app.symbol, wallet_usdt)
    preflight_rejects = list(preflight.get("rejects") or [])

    if preflight_rejects:
        app.log.info("contracts: preflight rejects: %s", preflight_rejects)
        rejects_meta = list(preflight.get("rejects_meta") or [])
        _log_structured(app.log, "preflight_reject", {
            "reason": preflight_rejects[0] if preflight_rejects else "",
            "category": (rejects_meta[0].get("category") if rejects_meta else None),
            "endpoint": (rejects_meta[0].get("endpoint") if rejects_meta else None),
            "http_status": (rejects_meta[0].get("http_status") if rejects_meta else None),
            "rejects": rejects_meta,
        })
        save_last_closed_candle_ts(latest_closed_ts)
        app._last_closed_candle_ts = latest_closed_ts
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="preflight_reject",
            killed_flag=is_killed(),
        )
        return

    ok_md, md_errors = validate_market_data(df_ltf, df_htf)
    if not ok_md:
        app.log.info("contracts: market data invalid: %s", md_errors)
        save_last_closed_candle_ts(latest_closed_ts)
        app._last_closed_candle_ts = latest_closed_ts
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="md_invalid",
            killed_flag=is_killed(),
        )
        return
    payload, errors = build_payload(
        symbol=app.symbol,
        df_ltf=df_ltf,
        df_htf=df_htf,
        account_snapshot=preflight.get("account") or {},
        position_snapshot=position_snapshot,
        price_snapshot=preflight.get("price") or {},
        filters_snapshot=preflight.get("filters") or {},
        timestamp_closed=latest_closed_ts,
        timeframe=interval,
        htf_timeframe=htf_interval,
    )
    if payload is None:
        app.log.info("contracts: payload invalid: %s", errors)
        save_last_closed_candle_ts(latest_closed_ts)
        app._last_closed_candle_ts = latest_closed_ts
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason="payload_invalid",
            killed_flag=is_killed(),
        )
        return

    payload_hash = hash_json(payload)

    daily_state = load_or_initialize_daily_state(payload.get("account_state", {}).get("equity", 0.0))

    decision = make_decision(payload, daily_state)
    decision_hash = hash_json(decision)
    explain_fields = _build_explain_fields(payload, decision)

    exchange_positions = []
    try:
        from core.exchange_private import fetch_futures_private
        data = fetch_futures_private()
        if isinstance(data, dict):
            exchange_positions = data.get("positions") or []
    except Exception:
        exchange_positions = []

    trade_plan = None
    rejections: List[str] = []
    cooldown_active = False
    explain_fields["cooldown_active"] = cooldown_active
    decision_rejects = list(decision.get("reject_reasons") or [])
    all_rejects = list(decision_rejects)
    decision_invalid = any("jsonschema" in str(r) or "schema" in str(r) for r in decision_rejects)

    append_event(
        event_type="decision_created",
        symbol=app.symbol,
        timeframe=interval,
        correlation_id=f"{app.symbol}:{latest_closed_ts}",
        payload_hash=payload_hash,
        decision_hash=decision_hash,
        details={"timestamp_closed": latest_closed_ts, **explain_fields},
    )

    intent = decision.get("intent")
    time_exit_signal = bool(decision.get("signal", {}).get("time_exit_signal"))
    explain_fields["time_exit_signal"] = time_exit_signal
    decision_log = _build_decision_log(
        latest_closed_ts=latest_closed_ts,
        interval=interval,
        payload=payload,
        explain_fields=explain_fields,
        trade_plan=trade_plan,
        all_rejects=all_rejects,
        cooldown_active=cooldown_active,
        time_exit_signal=time_exit_signal,
    )

    if intent not in ("LONG", "SHORT", "CLOSE"):
        app.log.info("contracts: no trade signal")
        _log_structured(app.log, "decision_candle", decision_log)
        _log_decision_clean(app.log, decision_log)
        skip_reason = "no_trade_signal"
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason=skip_reason,
            killed_flag=is_killed(),
        )
        save_last_closed_candle_ts(latest_closed_ts)
        app._last_closed_candle_ts = latest_closed_ts
        return

    trade_plan, rejections = create_trade_plan(payload, decision, daily_state, exchange_positions)
    cooldown_active = any(str(r).startswith("cooldown_active") for r in rejections)
    explain_fields["cooldown_active"] = cooldown_active
    all_rejects = [*decision_rejects, *[r for r in rejections if r not in decision_rejects]]
    decision_log["decision"] = "TRADE" if trade_plan is not None else "HOLD"
    decision_log["reject_reasons"] = all_rejects
    decision_log["cooldown_active"] = cooldown_active
    if trade_plan is not None and decision.get("signal", {}).get("selected_strategy") == "MEANREV_EXTREME":
        daily_state["extreme_snapback_ts"] = int(latest_closed_ts)
        if daily_state.get("date"):
            save_daily_state(daily_state["date"], daily_state)
    if trade_plan is not None:
        decision_log.update({
            "side": intent,
            "entry": decision.get("entry"),
            "sl": decision.get("sl"),
            "tp": decision.get("tp"),
            "rr": decision.get("rr"),
            "qty": trade_plan.get("quantity"),
            "action": trade_plan.get("action"),
        })
        if get_bool("LIVE_READONLY", False):
            decision_log["note"] = "NOT SUBMITTED: READONLY"
    _log_structured(app.log, "decision_candle", decision_log)
    _log_decision_clean(app.log, decision_log)

    if trade_plan is None:
        app.log.info("contracts: trade_plan blocked: %s", rejections)
        skip_reason = "risk_reject" if intent in ("LONG", "SHORT", "CLOSE") else ("decision_invalid" if decision_invalid else "no_trade_signal")
        _emit_tick_summary(
            app,
            now_ts=now_ts,
            interval=interval,
            latest_closed_ts=latest_closed_ts,
            last_processed_ts=last_processed_ts,
            will_process=False,
            skip_reason=skip_reason,
            killed_flag=is_killed(),
        )
        save_last_closed_candle_ts(latest_closed_ts)
        app._last_closed_candle_ts = latest_closed_ts
        return

    trade_plan_hash = hash_json(trade_plan)
    append_event(
        event_type="trade_plan_created",
        symbol=app.symbol,
        timeframe=interval,
        correlation_id=f"{app.symbol}:{latest_closed_ts}",
        payload_hash=payload_hash,
        decision_hash=decision_hash,
        trade_plan_hash=trade_plan_hash,
        client_order_id=trade_plan.get("client_order_id"),
        details={**explain_fields},
    )

    if intent in ("LONG", "SHORT"):
        record_trade_attempt(decision.get("intent"), latest_closed_ts)

    ok, tp_errors = validate_trade_plan(trade_plan)
    if not ok:
        app.log.error("contracts: trade_plan schema invalid: %s", tp_errors)
        raise RuntimeError(f"trade_plan_invalid: {tp_errors}")

    live_readonly = get_bool("LIVE_READONLY", False)
    if live_readonly:
        append_event(
            event_type="execution_attempted",
            symbol=app.symbol,
            timeframe=interval,
            correlation_id=f"{app.symbol}:{latest_closed_ts}",
            payload_hash=payload_hash,
            decision_hash=decision_hash,
            trade_plan_hash=trade_plan_hash,
            client_order_id=trade_plan.get("client_order_id"),
            details={"result": "blocked_readonly", **explain_fields},
        )
        if app.trade_enabled:
            exe = ExecutionService(logger=app.log)
            res = exe.execute_trade_plan(trade_plan)
            app.log.info("contracts: execution result: %s", res)
        _log_structured(app.log, "execution_blocked", {
            "timestamp_closed": latest_closed_ts,
            "interval": interval,
            "note": "NOT SUBMITTED: READONLY",
        })
    elif app.trade_enabled:
        exe = ExecutionService(logger=app.log)
        append_event(
            event_type="execution_attempted",
            symbol=app.symbol,
            timeframe=interval,
            correlation_id=f"{app.symbol}:{latest_closed_ts}",
            payload_hash=payload_hash,
            decision_hash=decision_hash,
            trade_plan_hash=trade_plan_hash,
            client_order_id=trade_plan.get("client_order_id"),
            details={"result": "attempted", **explain_fields},
        )
        res = exe.execute_trade_plan(trade_plan)
        app.log.info("contracts: execution result: %s", res)
    else:
        app.log.info("contracts: trade disabled — execution skipped")
        _log_structured(app.log, "execution_skipped", {
            "timestamp_closed": latest_closed_ts,
            "interval": interval,
            "note": "trade_disabled",
        })

    _emit_tick_summary(
        app,
        now_ts=now_ts,
        interval=interval,
        latest_closed_ts=latest_closed_ts,
        last_processed_ts=last_processed_ts,
        will_process=True,
        skip_reason=None,
        killed_flag=is_killed(),
    )
    save_last_closed_candle_ts(latest_closed_ts)
    app._last_closed_candle_ts = latest_closed_ts

def _ensure_utc_timestamps(df: Any) -> Any:
    if df is None or not hasattr(df, "columns"):
        return df
    for col in ("open_time", "close_time", "time"):
        if col in df.columns:
            series = pd.to_datetime(df[col], utc=True, errors="coerce")
            df[col] = series
    return df


def _filter_closed_candles(df: Any) -> Any:
    if df is None or not hasattr(df, "columns") or df.empty:
        return df
    now = datetime.now(timezone.utc)
    if "close_time" in df.columns:
        closed = df["close_time"] <= now
    elif "open_time" in df.columns:
        closed = df["open_time"] <= now
    else:
        return df
    filtered = df.loc[closed]
    return filtered if not filtered.empty else df.iloc[0:0]

def _latest_closed_candle_ts(df: Any) -> Optional[int]:
    if df is None or not hasattr(df, "columns") or df.empty:
        return None
    if "close_time" in df.columns:
        ts_val = df["close_time"].iloc[-1]
    elif "open_time" in df.columns:
        ts_val = df["open_time"].iloc[-1]
    elif "time" in df.columns:
        ts_val = df["time"].iloc[-1]
    else:
        return None
    try:
        return int(pd.to_datetime(ts_val, utc=True, errors="coerce").timestamp())
    except Exception:
        return None


def _log_structured(logger: logging.Logger, event: str, payload: Dict[str, Any]) -> None:
    try:
        data = {"event": event}
        data.update(payload)
        logger.info(json.dumps(data, sort_keys=True, separators=(",", ":")))
    except Exception:
        logger.info("event=%s payload=%s", event, payload)

def _prioritize_blockers(blockers: List[str]) -> List[str]:
    if not blockers:
        return []
    seen = set()

    def _uniq(items: List[str]) -> List[str]:
        ordered: List[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    funds_blockers = [b for b in blockers if b in ("funds_source_missing", "funds_nonpositive")]
    if funds_blockers:
        return _uniq(funds_blockers)

    sizing_prefixes = (
        "min_qty_not_met_after_rounding",
        "invalid_entry",
        "invalid_sl",
        "invalid_step_size",
        "invalid_min_qty",
        "invalid_sl_distance",
        "invalid_leverage",
    )
    sizing_blockers = [b for b in blockers if any(str(b).startswith(p) for p in sizing_prefixes)]
    if sizing_blockers:
        return _uniq(sizing_blockers)

    margin_blockers = [b for b in blockers if str(b).startswith("insufficient_margin")]
    strategy_blockers = [
        b
        for b in blockers
        if isinstance(b, str)
        and any(b.startswith(prefix) for prefix in ("T:", "B:", "M:", "P:", "C:", "R:", "X:"))
    ]
    other_blockers = [
        b
        for b in blockers
        if b not in margin_blockers and b not in strategy_blockers
    ]
    return _uniq([*margin_blockers, *strategy_blockers, *other_blockers])


def _log_decision_clean(logger: logging.Logger, decision_log: Dict[str, Any]) -> None:
    blockers = _prioritize_blockers(list(decision_log.get("reject_reasons") or []))
    categories = []
    for code in blockers:
        if not isinstance(code, str) or ":" not in code:
            continue
        prefix = code.split(":", 1)[0]
        if prefix and prefix not in categories:
            categories.append(prefix)
    payload = {
        "regime_detected": decision_log.get("regime_detected"),
        "regime_used_for_routing": decision_log.get("regime_used_for_routing"),
        "selected_strategy": decision_log.get("selected_strategy"),
        "eligible_strategies": decision_log.get("eligible_strategies"),
        "decision": decision_log.get("decision"),
        "equity": decision_log.get("equity"),
        "funds_base": decision_log.get("funds_base"),
        "funds_source": decision_log.get("funds_source"),
        "risk_usd": decision_log.get("risk_usd"),
        "qty_before_rounding": decision_log.get("qty_before_rounding"),
        "qty_after_rounding": decision_log.get("qty_after_rounding"),
        "required_margin": decision_log.get("required_margin"),
        "leverage_used": decision_log.get("leverage_used"),
        "blockers": blockers[:6],
        "blocker_categories": categories[:6],
    }
    _log_structured(logger, "decision_clean", payload)


def _log_tick_summary(
    logger: logging.Logger,
    *,
    now_ts: int,
    interval: str,
    latest_closed_ts: Optional[int],
    last_processed_ts: Optional[int],
    will_process: bool,
    skip_reason: Optional[str],
    killed_flag: bool,
) -> None:
    _log_structured(logger, "tick_summary", {
        "now": now_ts,
        "interval": interval,
        "latest_closed_ts": latest_closed_ts,
        "last_processed_ts": last_processed_ts,
        "will_process": bool(will_process),
        "skip_reason": skip_reason or "",
        "killed_flag": bool(killed_flag),
    })


def _emit_tick_summary(
    app: "TraderApp",
    *,
    now_ts: int,
    interval: str,
    latest_closed_ts: Optional[int],
    last_processed_ts: Optional[int],
    will_process: bool,
    skip_reason: Optional[str],
    killed_flag: bool,
) -> None:
    app._last_will_process = bool(will_process)
    throttle_sec = settings.get_int("LOG_SKIP_THROTTLE_SEC")
    agg_window_sec = settings.get_int("LOG_SKIP_AGG_SEC")
    if skip_reason:
        key = (skip_reason, latest_closed_ts, last_processed_ts)
        if app._skip_state_key == key:
            app._skip_agg_count += 1
            if app._skip_agg_start is None:
                app._skip_agg_start = now_ts
            if agg_window_sec > 0 and (now_ts - app._skip_agg_start) >= agg_window_sec:
                _log_structured(app.log, "tick_skip_agg", {
                    "skip_reason": skip_reason,
                    "latest_closed_ts": latest_closed_ts,
                    "last_processed_ts": last_processed_ts,
                    "count": app._skip_agg_count,
                    "window_sec": agg_window_sec,
                })
                app._skip_agg_count = 0
                app._skip_agg_start = now_ts
            if throttle_sec > 0 and (now_ts - app._skip_last_summary_ts) < throttle_sec:
                return
        else:
            app._skip_state_key = key
            app._skip_agg_count = 0
            app._skip_agg_start = now_ts
        app._skip_last_summary_ts = now_ts
    else:
        app._skip_state_key = None
        app._skip_agg_count = 0
        app._skip_agg_start = None
        app._skip_last_summary_ts = now_ts
    _log_tick_summary(
        app.log,
        now_ts=now_ts,
        interval=interval,
        latest_closed_ts=latest_closed_ts,
        last_processed_ts=last_processed_ts,
        will_process=will_process,
        skip_reason=skip_reason,
        killed_flag=killed_flag,
    )


def _build_explain_fields(payload: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    signal = decision.get("signal") or {}
    price_snapshot = payload.get("price_snapshot", {})
    account_state = payload.get("account_state", {})
    risk_policy = payload.get("risk_policy", {})
    exchange_limits = payload.get("exchange_limits", {})
    bid = price_snapshot.get("bid")
    ask = price_snapshot.get("ask")
    last = price_snapshot.get("last")
    close_ltf = signal.get("close_ltf")
    spread_pct = signal.get("spread_pct")
    if spread_pct is None and bid and ask and last:
        try:
            spread_pct = abs(float(ask) - float(bid)) / float(last) * 100.0
        except Exception:
            spread_pct = None
    funds_base = account_state.get("funds_base")
    funds_source = account_state.get("funds_source")
    leverage_used = account_state.get("leverage")
    risk_per_trade = risk_policy.get("risk_per_trade")
    entry = decision.get("entry")
    sl = decision.get("sl")
    step_size = exchange_limits.get("step_size")
    risk_usd = None
    qty_before_rounding = None
    qty_after_rounding = None
    required_margin = None
    try:
        if funds_base is not None and risk_per_trade is not None:
            risk_usd = float(funds_base) * float(risk_per_trade)
        if risk_usd is not None and entry is not None and sl is not None:
            sl_distance = abs(float(entry) - float(sl))
            if sl_distance > 0:
                qty_before_rounding = risk_usd / sl_distance
        if qty_before_rounding is not None and step_size is not None and float(step_size) > 0:
            qty_after_rounding = math.floor(qty_before_rounding / float(step_size)) * float(step_size)
        if qty_after_rounding is not None and entry is not None and leverage_used is not None and float(leverage_used) > 0:
            required_margin = (qty_after_rounding * float(entry)) / float(leverage_used)
    except Exception:
        pass

    return {
        "trend": signal.get("trend"),
        "direction": signal.get("direction"),
        "regime": signal.get("regime"),
        "regime_detected": signal.get("regime_detected"),
        "regime_used_for_routing": signal.get("regime_used_for_routing"),
        "trend_strength": signal.get("trend_strength"),
        "trend_stable_long": signal.get("trend_stable_long"),
        "trend_stable_short": signal.get("trend_stable_short"),
        "selected_strategy": signal.get("selected_strategy"),
        "eligible_strategies": signal.get("eligible_strategies"),
        "strategy_block_reason": signal.get("strategy_block_reason"),
        "atr_ratio": signal.get("atr_ratio"),
        "volatility_state": signal.get("volatility_state"),
        "cont_short_ok": signal.get("cont_short_ok"),
        "cont_long_ok": signal.get("cont_long_ok"),
        "slope_atr": signal.get("slope_atr"),
        "k_overextension": signal.get("k_overextension"),
        "break_level": signal.get("break_level"),
        "break_delta_atr": signal.get("break_delta_atr"),
        "cont_reject_codes": signal.get("cont_reject_codes") or [],
        "dist50": signal.get("dist50"),
        "dist50_prev": signal.get("dist50_prev"),
        "dc_width_atr": signal.get("dc_width_atr"),
        "high_ltf": signal.get("high_ltf"),
        "low_ltf": signal.get("low_ltf"),
        "consec_close_above_donchian_20": signal.get("consec_close_above_donchian_20"),
        "consec_close_below_donchian_20": signal.get("consec_close_below_donchian_20"),
        "consec_above_ema50": signal.get("consec_above_ema50"),
        "consec_below_ema50": signal.get("consec_below_ema50"),
        "consec_above_ema50_prev": signal.get("consec_above_ema50_prev"),
        "consec_below_ema50_prev": signal.get("consec_below_ema50_prev"),
        "time_exit_signal": signal.get("time_exit_signal"),
        "time_exit_bars": signal.get("time_exit_bars"),
        "time_exit_progress_atr": signal.get("time_exit_progress_atr"),
        "close_max_n": signal.get("close_max_n"),
        "close_min_n": signal.get("close_min_n"),
        "close_htf": signal.get("close_htf"),
        "ema200_htf": signal.get("ema200_htf"),
        "ema200_prev_n": signal.get("ema200_prev_n"),
        "ema200_slope_norm": signal.get("ema200_slope_norm"),
        "consec_above_ema200": signal.get("consec_above_ema200"),
        "consec_below_ema200": signal.get("consec_below_ema200"),
        "consec_higher_close": signal.get("consec_higher_close"),
        "consec_lower_close": signal.get("consec_lower_close"),
        "atr14_htf": signal.get("atr14_htf"),
        "ema200_prev_n": signal.get("ema200_prev_n"),
        "ema200_slope_norm": signal.get("ema200_slope_norm"),
        "consec_above_ema200": signal.get("consec_above_ema200"),
        "consec_below_ema200": signal.get("consec_below_ema200"),
        "consec_higher_close": signal.get("consec_higher_close"),
        "consec_lower_close": signal.get("consec_lower_close"),
        "pullback_atr_long": signal.get("pullback_atr_long"),
        "pullback_atr_short": signal.get("pullback_atr_short"),
        "reclaim_long": signal.get("reclaim_long"),
        "reclaim_short": signal.get("reclaim_short"),
        "prev_reclaim_long": signal.get("prev_reclaim_long"),
        "prev_reclaim_short": signal.get("prev_reclaim_short"),
        "prev_rsi_long": signal.get("prev_rsi_long"),
        "prev_rsi_short": signal.get("prev_rsi_short"),
        "reentry_long": signal.get("reentry_long"),
        "reentry_short": signal.get("reentry_short"),
        "breakout_long": signal.get("breakout_long"),
        "breakout_short": signal.get("breakout_short"),
        "consec_close_above_donchian_20": signal.get("consec_close_above_donchian_20"),
        "consec_close_below_donchian_20": signal.get("consec_close_below_donchian_20"),
        "volume_ratio": signal.get("volume_ratio"),
        "candle_body_ratio": signal.get("candle_body_ratio"),
        "high_ltf": signal.get("high_ltf"),
        "low_ltf": signal.get("low_ltf"),
        "consec_above_ema50": signal.get("consec_above_ema50"),
        "consec_below_ema50": signal.get("consec_below_ema50"),
        "consec_above_ema50_prev": signal.get("consec_above_ema50_prev"),
        "consec_below_ema50_prev": signal.get("consec_below_ema50_prev"),
        "time_exit_signal": signal.get("time_exit_signal"),
        "time_exit_bars": signal.get("time_exit_bars"),
        "time_exit_progress_atr": signal.get("time_exit_progress_atr"),
        "close_max_n": signal.get("close_max_n"),
        "close_min_n": signal.get("close_min_n"),
        "spread_pct": spread_pct,
        "atr": signal.get("atr"),
        "funds_base": funds_base,
        "funds_source": funds_source,
        "risk_usd": risk_usd,
        "qty_before_rounding": qty_before_rounding,
        "qty_after_rounding": qty_after_rounding,
        "required_margin": required_margin,
        "leverage_used": leverage_used,
        "close_ltf": close_ltf,
        "close_prev_ltf": signal.get("close_prev_ltf"),
        "ema50_ltf": signal.get("ema50_ltf"),
        "ema50_prev_12": signal.get("ema50_prev_12"),
        "ema120_ltf": signal.get("ema120_ltf"),
        "rsi14_ltf": signal.get("rsi14_ltf"),
        "rsi14_prev_ltf": signal.get("rsi14_prev_ltf"),
        "bb_upper": signal.get("bb_upper"),
        "bb_lower": signal.get("bb_lower"),
        "bb_mid": signal.get("bb_mid"),
        "donchian_high_20": signal.get("donchian_high_20"),
        "donchian_low_20": signal.get("donchian_low_20"),
        "thresholds": signal.get("thresholds") or {},
    }


def _build_decision_log(
    *,
    latest_closed_ts: Optional[int],
    interval: str,
    payload: Dict[str, Any],
    explain_fields: Dict[str, Any],
    trade_plan: Optional[Dict[str, Any]],
    all_rejects: List[str],
    cooldown_active: bool,
    time_exit_signal: bool,
) -> Dict[str, Any]:
    price_snapshot = payload.get("price_snapshot", {})
    return {
        "timestamp_closed": latest_closed_ts,
        "interval": interval,
        "close": explain_fields.get("close_ltf"),
        "bid": price_snapshot.get("bid"),
        "ask": price_snapshot.get("ask"),
        "spread_pct": explain_fields.get("spread_pct"),
        "atr": explain_fields.get("atr"),
        "equity": payload.get("account_state", {}).get("equity"),
        "funds_base": explain_fields.get("funds_base"),
        "funds_source": explain_fields.get("funds_source"),
        "risk_usd": explain_fields.get("risk_usd"),
        "qty_before_rounding": explain_fields.get("qty_before_rounding"),
        "qty_after_rounding": explain_fields.get("qty_after_rounding"),
        "required_margin": explain_fields.get("required_margin"),
        "leverage_used": explain_fields.get("leverage_used"),
        "atr14_5m": explain_fields.get("atr"),
        "regime": explain_fields.get("regime"),
        "regime_detected": explain_fields.get("regime_detected"),
        "regime_used_for_routing": explain_fields.get("regime_used_for_routing"),
        "direction": explain_fields.get("direction"),
        "trend_strength": explain_fields.get("trend_strength"),
        "trend_stable_long": explain_fields.get("trend_stable_long"),
        "trend_stable_short": explain_fields.get("trend_stable_short"),
        "atr_ratio": explain_fields.get("atr_ratio"),
        "volatility_state": explain_fields.get("volatility_state"),
        "trend": explain_fields.get("trend"),
        "cont_short_ok": explain_fields.get("cont_short_ok"),
        "cont_long_ok": explain_fields.get("cont_long_ok"),
        "slope_atr": explain_fields.get("slope_atr"),
        "k_overextension": explain_fields.get("k_overextension"),
        "break_level": explain_fields.get("break_level"),
        "break_delta_atr": explain_fields.get("break_delta_atr"),
        "cont_reject_codes": explain_fields.get("cont_reject_codes"),
        "close_htf": explain_fields.get("close_htf"),
        "ema200_htf": explain_fields.get("ema200_htf"),
        "ema200_prev_n": explain_fields.get("ema200_prev_n"),
        "ema200_slope_norm": explain_fields.get("ema200_slope_norm"),
        "consec_above_ema200": explain_fields.get("consec_above_ema200"),
        "consec_below_ema200": explain_fields.get("consec_below_ema200"),
        "consec_higher_close": explain_fields.get("consec_higher_close"),
        "consec_lower_close": explain_fields.get("consec_lower_close"),
        "atr14_htf": explain_fields.get("atr14_htf"),
        "ema50_5m": explain_fields.get("ema50_ltf"),
        "ema50_prev_12": explain_fields.get("ema50_prev_12"),
        "rsi14_5m": explain_fields.get("rsi14_ltf"),
        "bb_upper": explain_fields.get("bb_upper"),
        "bb_lower": explain_fields.get("bb_lower"),
        "bb_mid": explain_fields.get("bb_mid"),
        "donchian_high_20": explain_fields.get("donchian_high_20"),
        "donchian_low_20": explain_fields.get("donchian_low_20"),
        "consec_close_above_donchian_20": explain_fields.get("consec_close_above_donchian_20"),
        "consec_close_below_donchian_20": explain_fields.get("consec_close_below_donchian_20"),
        "volume_ratio_5m": explain_fields.get("volume_ratio"),
        "candle_body_ratio": explain_fields.get("candle_body_ratio"),
        "reentry_long": explain_fields.get("reentry_long"),
        "reentry_short": explain_fields.get("reentry_short"),
        "breakout_long": explain_fields.get("breakout_long"),
        "breakout_short": explain_fields.get("breakout_short"),
        "eligible_strategies": explain_fields.get("eligible_strategies"),
        "selected_strategy": explain_fields.get("selected_strategy"),
        "strategy_block_reason": explain_fields.get("strategy_block_reason"),
        "time_exit_signal": explain_fields.get("time_exit_signal"),
        "time_exit_bars": explain_fields.get("time_exit_bars"),
        "time_exit_progress_atr": explain_fields.get("time_exit_progress_atr"),
        "close_max_n": explain_fields.get("close_max_n"),
        "close_min_n": explain_fields.get("close_min_n"),
        "high_ltf": explain_fields.get("high_ltf"),
        "low_ltf": explain_fields.get("low_ltf"),
        "consec_above_ema50": explain_fields.get("consec_above_ema50"),
        "consec_below_ema50": explain_fields.get("consec_below_ema50"),
        "consec_above_ema50_prev": explain_fields.get("consec_above_ema50_prev"),
        "consec_below_ema50_prev": explain_fields.get("consec_below_ema50_prev"),
        "pullback_atr_long": explain_fields.get("pullback_atr_long"),
        "pullback_atr_short": explain_fields.get("pullback_atr_short"),
        "reclaim_long": explain_fields.get("reclaim_long"),
        "reclaim_short": explain_fields.get("reclaim_short"),
        "prev_reclaim_long": explain_fields.get("prev_reclaim_long"),
        "prev_reclaim_short": explain_fields.get("prev_reclaim_short"),
        "prev_rsi_long": explain_fields.get("prev_rsi_long"),
        "prev_rsi_short": explain_fields.get("prev_rsi_short"),
        "prev_close": explain_fields.get("close_prev_ltf"),
        "prev_rsi": explain_fields.get("rsi14_prev_ltf"),
        "decision": "TRADE" if trade_plan is not None else "HOLD",
        "reject_reasons": all_rejects,
        "cooldown_active": cooldown_active,
        "ema_ltf": explain_fields.get("ema50_ltf"),
        "thresholds": explain_fields.get("thresholds"),
        "time_exit_signal": time_exit_signal,
    }


def _get_wallet_usdt() -> Optional[float]:
    raw = settings.get_str("WALLET_USDT", None)
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except Exception:
        return None

def _allow_offline_fallback() -> bool:
    env = str(settings.get_str("ENV", "production") or "production").lower()
    if env != "production":
        return True
    runtime = get_runtime_settings()
    return "PYTEST_CURRENT_TEST" in os.environ or runtime.is_replay or runtime.is_offline

def _fetch_account_snapshot(wallet_usdt: Optional[float], ts: float) -> Dict[str, Any]:
    try:
        from core.exchange_private import fetch_futures_private
        data = fetch_futures_private()
    except Exception as e:
        data = {
            "mode": "PUBLIC_ONLY",
            "balances": None,
            "positions": None,
            "error": f"account_snapshot_failed: {e}",
            "error_category": "TRANSIENT_ERROR",
            "error_reason": "network_failure",
            "endpoint": "/fapi/v2/balance",
            "http_status": None,
            "binance_error_code": None,
            "binance_error_msg": str(e),
        }

    def _flt(val: Any) -> Optional[float]:
        try:
            return float(val)
        except Exception:
            return None

    balances = data.get("balances") if isinstance(data, dict) else None
    account_info = data.get("account") if isinstance(data, dict) else None
    equity_usd = None
    wallet_val = None
    available_usd = None
    total_margin = None
    total_wallet = None
    if isinstance(account_info, dict):
        available_usd = _flt(account_info.get("available_balance"))
        total_margin = _flt(account_info.get("total_margin_balance"))
        total_wallet = _flt(account_info.get("total_wallet_balance"))
        if total_margin is not None and total_margin > 0:
            equity_usd = total_margin
        elif total_wallet is not None and total_wallet > 0:
            equity_usd = total_wallet
        if total_wallet is not None and total_wallet > 0:
            wallet_val = total_wallet
    if isinstance(balances, dict):
        for key in ("USDT", "BUSD", "USDC"):
            if key in balances:
                try:
                    val = float(balances[key])
                except Exception:
                    continue
                if val > 0:
                    if equity_usd is None:
                        equity_usd = val
                    if wallet_val is None:
                        wallet_val = val
                    break

    source = "exchange" if equity_usd is not None else "missing"
    reason = data.get("error") if isinstance(data, dict) else None
    if reason and not str(reason).startswith("account_snapshot_failed"):
        reason = f"account_snapshot_failed: {reason}"
    error_category = data.get("error_category") if isinstance(data, dict) else None
    error_reason = data.get("error_reason") if isinstance(data, dict) else None
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    http_status = data.get("http_status") if isinstance(data, dict) else None
    binance_error_code = data.get("binance_error_code") if isinstance(data, dict) else None
    binance_error_msg = data.get("binance_error_msg") if isinstance(data, dict) else None
    response_text_trim = data.get("response_text_trim") if isinstance(data, dict) else None
    content_type = data.get("content_type") if isinstance(data, dict) else None
    request_method = data.get("request_method") if isinstance(data, dict) else None
    request_endpoint = data.get("request_endpoint") if isinstance(data, dict) else None
    signed_params_snapshot = data.get("signed_params_snapshot") if isinstance(data, dict) else None
    time_sync_snapshot = data.get("time_sync_snapshot") if isinstance(data, dict) else None

    if equity_usd is None and _allow_offline_fallback():
        fallback = wallet_usdt if wallet_usdt is not None else _get_wallet_usdt()
        if fallback is not None:
            equity_usd = float(fallback)
            wallet_val = float(fallback)
            source = "fallback"
            reason = reason or "fallback_wallet_usdt"
    if available_usd is None and _allow_offline_fallback():
        fallback = wallet_usdt if wallet_usdt is not None else _get_wallet_usdt()
        if fallback is not None:
            available_usd = float(fallback)

    return {
        "equity_usd": equity_usd,
        "available_usd": available_usd,
        "total_margin_usd": total_margin,
        "total_wallet_usd": total_wallet,
        "wallet_usdt": wallet_val,
        "source": source,
        "reason": reason,
        "error_category": error_category,
        "error_reason": error_reason,
        "endpoint": endpoint,
        "http_status": http_status,
        "binance_error_code": binance_error_code,
        "binance_error_msg": binance_error_msg,
        "response_text_trim": response_text_trim,
        "content_type": content_type,
        "request_method": request_method,
        "request_endpoint": request_endpoint,
        "signed_params_snapshot": signed_params_snapshot,
        "time_sync_snapshot": time_sync_snapshot,
        "ts": ts,
    }

def _fetch_price_snapshot(symbol: str, ts: float) -> Dict[str, Any]:
    try:
        from app.services.market_data import HttpMarketData
        from core.execution import binance_futures
        md = HttpMarketData()
        last_price = float(md.get_latest_price(symbol))
        book = binance_futures._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol}, private=False)
        bid = None
        ask = None
        if isinstance(book, dict):
            try:
                bid = float(book.get("bidPrice"))
            except Exception:
                bid = None
            try:
                ask = float(book.get("askPrice"))
            except Exception:
                ask = None
        return {
            "value": last_price,
            "bid": bid if bid is not None else last_price,
            "ask": ask if ask is not None else last_price,
            "mark": last_price,
            "source": "market_data",
            "ts": ts,
        }
    except Exception as e:
        return {"value": None, "bid": None, "ask": None, "mark": None, "source": "missing", "reason": str(e), "ts": ts}

def _fetch_filters_snapshot(symbol: str, ts: float) -> Dict[str, Any]:
    cached = _filters_cache_get(symbol, ts)
    if cached is not None:
        return cached
    try:
        from core.execution import binance_futures
        info = binance_futures.exchange_info(symbol)
    except Exception as e:
        stale = _filters_cache_get(symbol, ts, allow_stale=True)
        if stale is not None:
            stale["reason"] = f"refresh_failed:{e}"
            return stale
        return {"step_size": None, "min_qty": None, "tick_size": None, "source": "missing", "reason": str(e), "ts": ts}

    if not isinstance(info, dict):
        return {"step_size": None, "min_qty": None, "tick_size": None, "source": "missing", "reason": "invalid_response", "ts": ts}

    sym_info = next((s for s in info.get("symbols", []) if s.get("symbol") == symbol), None)
    if not sym_info:
        return {"step_size": None, "min_qty": None, "tick_size": None, "source": "missing", "reason": "symbol_not_found", "ts": ts}

    fmap = {f.get("filterType"): f for f in sym_info.get("filters", []) if f.get("filterType")}
    if "MIN_NOTIONAL" not in fmap and "NOTIONAL" in fmap:
        fmap["MIN_NOTIONAL"] = fmap["NOTIONAL"]

    def _flt_num(val: Any) -> Optional[float]:
        try:
            return float(val)
        except Exception:
            return None

    lot = fmap.get("LOT_SIZE", {})
    price_filter = fmap.get("PRICE_FILTER", {})
    snapshot = {
        "step_size": _flt_num(lot.get("stepSize")),
        "min_qty": _flt_num(lot.get("minQty")),
        "tick_size": _flt_num(price_filter.get("tickSize")),
        "source": "exchange_info",
        "ts": ts,
    }
    _filters_cache_set(symbol, ts, snapshot)
    return snapshot

def _filters_cache_get(symbol: str, ts: float, *, allow_stale: bool = False) -> Optional[Dict[str, Any]]:
    entry = _FILTERS_CACHE.get(symbol)
    if not entry:
        return None
    ttl = max(0, settings.get_int("FILTERS_CACHE_TTL_SEC"))
    if ttl <= 0:
        return None
    age = ts - entry.get("fetched_at", 0.0)
    if not allow_stale and ttl > 0 and age > ttl:
        return None
    snapshot = dict(entry.get("snapshot", {}))
    snapshot["source"] = "exchange_info_cache" if ttl <= 0 or age <= ttl else "exchange_info_cache_stale"
    snapshot["ts"] = ts
    return snapshot

def _filters_cache_set(symbol: str, ts: float, snapshot: Dict[str, Any]) -> None:
    _FILTERS_CACHE[symbol] = {"fetched_at": ts, "snapshot": dict(snapshot)}

def _validate_preflight(
    account: Dict[str, Any],
    price: Dict[str, Any],
    filters: Dict[str, Any],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    rejects: List[str] = []
    rejects_meta: List[Dict[str, Any]] = []
    equity_val = account.get("equity_usd")
    available_val = account.get("available_usd")
    wallet_val = account.get("wallet_usdt")
    acc_category = account.get("error_category")
    acc_reason = account.get("error_reason")
    acc_endpoint = account.get("endpoint")
    acc_http_status = account.get("http_status")
    acc_binance_code = account.get("binance_error_code")
    acc_binance_msg = account.get("binance_error_msg")
    acc_response_text = account.get("response_text_trim")
    acc_content_type = account.get("content_type")
    acc_request_method = account.get("request_method")
    acc_request_endpoint = account.get("request_endpoint")
    acc_signed_params = account.get("signed_params_snapshot")
    acc_time_sync = account.get("time_sync_snapshot")
    if acc_category == "CONFIG_ERROR" and acc_reason:
        reason = f"preflight_config_error:{acc_reason}"
        rejects.append(reason)
        rejects_meta.append({
            "reason": reason,
            "category": "CONFIG",
            "endpoint": acc_endpoint,
            "http_status": acc_http_status,
            "binance_error_code": acc_binance_code,
            "binance_error_msg": acc_binance_msg,
            "response_text_trim": acc_response_text,
            "content_type": acc_content_type,
            "request_method": acc_request_method,
            "request_endpoint": acc_request_endpoint,
            "signed_params_snapshot": acc_signed_params,
            "time_sync_snapshot": acc_time_sync,
        })
    elif acc_category == "TRANSIENT_ERROR" and acc_reason:
        reason = f"preflight_transient_error:{acc_reason}"
        rejects.append(reason)
        rejects_meta.append({
            "reason": reason,
            "category": "TRANSIENT",
            "endpoint": acc_endpoint,
            "http_status": acc_http_status,
            "binance_error_code": acc_binance_code,
            "binance_error_msg": acc_binance_msg,
            "response_text_trim": acc_response_text,
            "content_type": acc_content_type,
            "request_method": acc_request_method,
            "request_endpoint": acc_request_endpoint,
            "signed_params_snapshot": acc_signed_params,
            "time_sync_snapshot": acc_time_sync,
        })
    if acc_category not in ("CONFIG_ERROR", "TRANSIENT_ERROR"):
        if equity_val is None or float(equity_val or 0.0) <= 0:
            detail = account.get("reason") or account.get("source")
            rejects.append(f"missing_account_equity:{detail}" if detail else "missing_account_equity")
        if available_val is None or float(available_val or 0.0) <= 0:
            detail = account.get("reason") or account.get("source")
            rejects.append(f"missing_account_available:{detail}" if detail else "missing_account_available")
        if wallet_val is None or float(wallet_val or 0.0) <= 0:
            detail = account.get("reason") or account.get("source")
            rejects.append(f"missing_wallet_usdt:{detail}" if detail else "missing_wallet_usdt")
    price_val = price.get("value")
    if price_val is None or float(price_val or 0.0) <= 0:
        detail = price.get("reason") or price.get("source")
        rejects.append(f"missing_price:{detail}" if detail else "missing_price")
    step_size = filters.get("step_size")
    min_qty = filters.get("min_qty")
    tick_size = filters.get("tick_size")
    if step_size is None or float(step_size or 0.0) <= 0:
        detail = filters.get("reason") or filters.get("source")
        rejects.append(f"missing_filter_step_size:{detail}" if detail else "missing_filter_step_size")
    if min_qty is None or float(min_qty or 0.0) <= 0:
        detail = filters.get("reason") or filters.get("source")
        rejects.append(f"missing_filter_min_qty:{detail}" if detail else "missing_filter_min_qty")
    if tick_size is None or float(tick_size or 0.0) <= 0:
        detail = filters.get("reason") or filters.get("source")
        rejects.append(f"missing_filter_tick_size:{detail}" if detail else "missing_filter_tick_size")
    return rejects, rejects_meta

def _build_preflight_snapshot(symbol: str, wallet_usdt: Optional[float], ts: float) -> Dict[str, Any]:
    account = _fetch_account_snapshot(wallet_usdt, ts)
    price = _fetch_price_snapshot(symbol, ts)
    filters = _fetch_filters_snapshot(symbol, ts)
    rejects, rejects_meta = _validate_preflight(account, price, filters)
    return {
        "account": account,
        "price": price,
        "filters": filters,
        "rejects": rejects,
        "rejects_meta": rejects_meta,
    }

def _read_preflight(symbol: str, wallet_usdt: Optional[float]) -> Dict[str, Any]:
    ts = time.time()
    try:
        return _build_preflight_snapshot(symbol, wallet_usdt, ts)
    except Exception as e:
        reason = f"preflight_error:{e}"
        return {
            "account": {"equity_usd": None, "wallet_usdt": None, "source": "missing", "reason": reason, "ts": ts},
            "filters": {"step_size": None, "min_qty": None, "tick_size": None, "source": "missing", "reason": reason, "ts": ts},
            "price": {"value": None, "bid": None, "ask": None, "mark": None, "source": "missing", "reason": reason, "ts": ts},
            "rejects": [reason],
        }

def _run_binance_diagnostics(logger: logging.Logger) -> None:
    try:
        from core.execution import binance_futures
        from core.exchange_private import fetch_futures_private
    except Exception as e:
        logger.error("diagnose_binance_import_error: %s", e)
        return
    time_sync_snapshot = binance_futures.get_time_sync_snapshot(force_refresh=True)
    snapshot = fetch_futures_private()
    payload = {
        "event": "preflight_diagnostics",
        "time_sync_snapshot": time_sync_snapshot,
        "mode": snapshot.get("mode"),
        "error_category": snapshot.get("error_category"),
        "error_reason": snapshot.get("error_reason"),
        "http_status": snapshot.get("http_status"),
        "binance_error_code": snapshot.get("binance_error_code"),
        "binance_error_msg": snapshot.get("binance_error_msg"),
        "response_text_trim": snapshot.get("response_text_trim"),
        "content_type": snapshot.get("content_type"),
        "request_method": snapshot.get("request_method"),
        "request_endpoint": snapshot.get("request_endpoint"),
        "signed_params_snapshot": snapshot.get("signed_params_snapshot"),
        "time_sync_snapshot_request": snapshot.get("time_sync_snapshot"),
    }
    _log_structured(logger, "preflight_diagnostics", payload)


def main() -> None:
    load_dotenv_once()
    logger = _setup_logging()
    logger.info("=== Bot startup ===")
    _print_env(logger)

    if "--diagnose-binance" in sys.argv:
        _run_binance_diagnostics(logger)
        return

    fn = _try_get_main()
    oneshot_like = _is_testish_env()
    trade_enabled = get_bool("TRADE_ENABLED", False)
    api_key_present = bool(get_env("API_KEY", ""))

    if fn is not None:
        # Якщо НЕ бойовий режим (TRADE_ENABLED!=1) або немає API_KEY, або test/CI —
        # виконуємо одноразовий цикл через entrypoint і завершуємось.
        if (not trade_enabled) or (not api_key_present) or oneshot_like:
            logger.info("Delegating to entrypoint in oneshot (reason: trade_enabled=%s, api_key=%s, testish=%s).",
                        trade_enabled, "yes" if api_key_present else "no", oneshot_like)
            try:
                fn(["--once"])
            except TypeError:
                fn()
            return

        # Бойовий режим — звичайний старт
        try:
            fn()
        except KeyboardInterrupt:
            logger.info("Graceful shutdown.")
        except Exception as e:
            logger.exception("Unhandled exception from app entrypoint: %s", e)
            sys.exit(1)
    else:
        # Нема явного entrypoint — heartbeat; але в тест/CI зробимо oneshot
        _heartbeat(logger, oneshot_like)

if __name__ == "__main__":
    main()
