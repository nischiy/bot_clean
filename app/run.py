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
        
        # Initialize config fingerprint for governance
        from core.config.fingerprint import initialize_config_fingerprint, get_config_fingerprint
        config_fp = initialize_config_fingerprint()
        fp_info = get_config_fingerprint()
        _log_structured(self.log, "config_fingerprint_initialized", {
            "config_hash": config_fp,
            "config_keys_count": fp_info["config_keys_count"],
        })
        self.log.info("Config fingerprint: %s (%d keys)", config_fp[:16] + "...", fp_info["config_keys_count"])
        
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
            "config_hash": config_fp,
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
    positions_raw = exe.fetch_positions()
    # Filter to the configured symbol and only entries with an actual open position.
    # Binance /fapi/v2/positionRisk returns every symbol (all 600+ with positionAmt=0);
    # passing the unfiltered list causes false-positive kill-switch fires on startup.
    positions = [
        p for p in positions_raw
        if p.get("symbol") == symbol and abs(float(p.get("positionAmt", 0) or 0)) > 0
    ]
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
        load_position_state,
        load_decision_state,
        save_decision_state,
        record_trade_attempt,
    )
    from app.services.execution_service import ExecutionService
    from core.config.fingerprint import verify_config_unchanged
    from core.health_counters import get_health_counters
    from core.invariants import enforce_invariants
    
    # Verify config fingerprint unchanged (HARD STOP if changed)
    config_ok, config_error = verify_config_unchanged()
    if not config_ok:
        get_health_counters().increment("config_changes_detected")
        kill("config_changed", {"error": config_error})
        app.log.error("CONFIG CHANGED DURING RUNTIME: %s — trading HARD STOPPED", config_error)
        raise RuntimeError(f"config_changed_during_runtime: {config_error}")
    
    # Increment candles processed counter
    get_health_counters().increment("candles_processed")

    now_ts = int(time.time())
    interval = app.interval or settings.get_str("INTERVAL", "5m")
    htf_interval = settings.get_str("HTF_INTERVAL", "1h")
    if app._last_closed_candle_ts is None:
        app._last_closed_candle_ts = load_last_closed_candle_ts()
    last_processed_ts = app._last_closed_candle_ts
    latest_closed_ts: Optional[int] = None

    position_snapshot = _get_position_snapshot(app.symbol)
    # Preserve tracking fields (original_qty, tp1_qty) if they exist
    existing_state = load_position_state(app.symbol)
    if existing_state:
        if "original_qty" in existing_state:
            position_snapshot["original_qty"] = existing_state["original_qty"]
        if "tp1_qty" in existing_state:
            position_snapshot["tp1_qty"] = existing_state["tp1_qty"]
        # Clear tracking fields if position is closed
        if position_snapshot.get("qty", 0.0) == 0.0:
            position_snapshot.pop("original_qty", None)
            position_snapshot.pop("tp1_qty", None)
    save_position_state(app.symbol, position_snapshot)

    exe = ExecutionService(logger=app.log)
    try:
        exchange_positions_raw = exe.fetch_positions()
        # Filter to the configured symbol and only entries with an actual open position.
        # Binance /fapi/v2/positionRisk returns every symbol (all 600+ with positionAmt=0);
        # passing the unfiltered list causes false-positive kill-switch fires.
        exchange_positions = [
            p for p in exchange_positions_raw
            if p.get("symbol") == app.symbol and abs(float(p.get("positionAmt", 0) or 0)) > 0
        ]
        has_open_position = bool(exchange_positions)
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
        # HARD STOP: Any reconciliation failure must stop trading immediately
        skip_reason = "exits_missing" if any("position_without_" in err for err in errors) else "reconcile_failed"
        # Log structured kill-switch activation with reconciliation errors
        _log_structured(app.log, "kill_switch_triggered", {
            "timestamp": now_ts,
            "reason": "exit_reconcile_failed",
            "errors": errors,
            "position_state": position_snapshot,
            "exchange_positions_count": len(exchange_positions),
            "open_orders_count": len(open_orders) if open_orders else 0,
        })
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
        # Increment kill-switch counter
        get_health_counters().increment("kill_switch_activations")
        # Log structured kill-switch activation with state snapshot
        kill_reason = ""
        try:
            from core.risk_guard import _flag_file
            kill_flag_path = _flag_file()
            if kill_flag_path.exists():
                kill_reason = kill_flag_path.read_text(encoding="utf-8").strip()
        except Exception:
            kill_reason = "kill_switch_engaged"
        _log_structured(app.log, "kill_switch_active", {
            "timestamp_closed": latest_closed_ts,
            "reason": kill_reason,
            "position_state": position_snapshot,
            "daily_state": {},
        })
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
    
    # Note: TP1 fill detection and UPDATE_SLTP generation is handled in the decision engine
    # via the move_sl_to_be_after_tp1 flag. Full implementation requires:
    # 1. Tracking original position qty and TP1 qty when trade executes
    # 2. Comparing current position qty with expected qty after TP1 fill
    # 3. Generating UPDATE_SLTP decision if TP1 filled and TP2 still open
    # This is a placeholder - the decision engine will set move_sl_to_be_after_tp1
    # based on regime exit behavior, and the runtime will handle UPDATE_SLTP execution
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

    decision_state = load_decision_state(app.symbol)
    decision = make_decision(payload, daily_state, decision_state)
    decision_hash = hash_json(decision)
    
    # Enforce invariants: decision without valid payload → HOLD
    try:
        enforce_invariants(decision=decision, payload=payload)
    except Exception as e:
        get_health_counters().increment("invariant_violations")
        app.log.error("Invariant violation: %s", e)
        # Log to validation file if validation mode is enabled
        try:
            from core.validation_logger import log_invariant_violation
            error_code = getattr(e, "error_code", "UNKNOWN")
            details = getattr(e, "details", {})
            context_snapshot = {
                "timestamp_closed": latest_closed_ts,
                "symbol": app.symbol,
                "interval": interval,
                "payload_hash": payload_hash,
                "decision_hash": decision_hash,
                "payload": payload,
                "decision": decision,
                "daily_state": daily_state,
                "position_snapshot": position_snapshot,
            }
            log_invariant_violation(
                error_code=error_code,
                message=str(e),
                details=details,
                context_snapshot=context_snapshot,
            )
        except Exception:
            # Fail silently - validation logging should never affect trading
            pass
        # Fail-closed: set decision to HOLD
        decision["intent"] = "HOLD"
        decision["reject_reasons"] = decision.get("reject_reasons", []) + [f"invariant_violation: {str(e)}"]
    
    # Increment decision counter
    get_health_counters().increment("decisions_made")
    state_update = decision.get("state_update")
    if isinstance(state_update, dict):
        save_decision_state(app.symbol, state_update)
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
    
    # Build explainability fields for blockers
    signal = decision.get("signal") or {}
    explain_pullback = _build_explain_pullback(signal, decision_rejects)
    explain_range = _build_explain_range(signal, decision_rejects)
    explain_continuation = _build_explain_continuation(signal, decision_rejects)
    explain_breakout = _build_explain_breakout(signal, decision_rejects)
    # Build anti-reversal explain when evaluated (blocked or not blocked, but not when None/not evaluated)
    explain_anti_reversal = _build_explain_anti_reversal(signal)
    
    if explain_pullback:
        explain_fields["explain_pullback"] = explain_pullback
    if explain_range:
        explain_fields["explain_range"] = explain_range
    if explain_continuation:
        explain_fields["explain_continuation"] = explain_continuation
    if explain_breakout:
        explain_fields["explain_breakout"] = explain_breakout
    if explain_anti_reversal:
        explain_fields["explain_anti_reversal"] = explain_anti_reversal

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

    # Check if decision has UPDATE_SLTP intent (from move_sl_to_be_after_tp1)
    # Detect TP1 fill by comparing current position qty vs original qty
    if decision.get("move_sl_to_be_after_tp1") and has_open_position:
        original_qty = position_snapshot.get("original_qty")
        tp1_qty = position_snapshot.get("tp1_qty")
        current_qty = float(position_snapshot.get("qty", 0.0) or 0.0)
        exchange_limits = payload.get("exchange_limits", {})
        step_size = exchange_limits.get("step_size", 0.0)
        
        # Detect TP1 fill: current_qty should be approximately (original_qty - tp1_qty)
        # Add tolerance for step_size rounding (allow up to 2*step_size difference)
        tp1_filled = False
        if original_qty is not None and tp1_qty is not None and step_size > 0:
            expected_qty_after_tp1 = float(original_qty) - float(tp1_qty)
            tolerance = max(step_size * 2.0, step_size * 0.01)  # At least 2*step_size or 1% of step_size
            qty_diff = abs(current_qty - expected_qty_after_tp1)
            if qty_diff <= tolerance:
                tp1_filled = True
        elif original_qty is not None and tp1_qty is not None:
            # Fallback if step_size not available: use 1% tolerance
            expected_qty_after_tp1 = float(original_qty) - float(tp1_qty)
            tolerance = abs(expected_qty_after_tp1) * 0.01  # 1% tolerance
            qty_diff = abs(current_qty - expected_qty_after_tp1)
            if qty_diff <= tolerance:
                tp1_filled = True
        
        # Only trigger UPDATE_SLTP if TP1 is confirmed filled and TP2 still open
        if tp1_filled:
            tp_orders_open = [o for o in open_orders if str(o.get("type", "")).upper() == "TAKE_PROFIT_MARKET"]
            if len(tp_orders_open) > 0:  # TP2 still open
                # Generate UPDATE_SLTP decision
                pos_entry = float(position_snapshot.get("entry", 0.0) or 0.0)
                if pos_entry > 0:
                    # Set SL to break-even (entry price, accounting for fees)
                    fees = payload.get("fees", {})
                    taker_fee = fees.get("taker", 0.0004)
                    pos_side = position_snapshot.get("side")
                    if pos_side == "LONG":
                        be_sl = pos_entry * (1 + taker_fee)  # Slightly above entry for LONG
                    else:
                        be_sl = pos_entry * (1 - taker_fee)  # Slightly below entry for SHORT
                    
                    decision["intent"] = "UPDATE_SLTP"
                    decision["sl"] = be_sl
                    decision["entry"] = pos_entry
                    intent = "UPDATE_SLTP"  # Update intent variable
                    
                    app.log.info(
                        "contracts: TP1 fill detected: original_qty=%.6f tp1_qty=%.6f current_qty=%.6f expected=%.6f",
                        float(original_qty) if original_qty is not None else 0.0,
                        float(tp1_qty) if tp1_qty is not None else 0.0,
                        current_qty,
                        expected_qty_after_tp1 if original_qty is not None and tp1_qty is not None else 0.0
                    )
    
    if intent not in ("LONG", "SHORT", "CLOSE", "UPDATE_SLTP"):
        app.log.info("contracts: no trade signal")
        # Increment rejection counter
        get_health_counters().increment("decisions_rejected")
        # Blockers must never contain *:strategy_ineligible; use concrete strategy_block_reason in logs
        reject_blockers = _strip_strategy_ineligible_from_blockers(decision_rejects)
        main_blocker = reject_blockers[0] if reject_blockers else "no_signal"
        blocker_categories = list(set([r.split(":")[0] if ":" in r else "unknown" for r in reject_blockers]))
        reject_log = {
            "timestamp_closed": latest_closed_ts,
            "decision": intent,
            "main_blocker": main_blocker,
            "blocker_categories": blocker_categories,
            "blockers": reject_blockers,
            "reject_count": len(reject_blockers),
            "selected_strategy": decision.get("signal", {}).get("selected_strategy", "NONE"),
            "regime_detected": decision.get("signal", {}).get("regime_detected", "UNKNOWN"),
            "strategy_block_reason": decision.get("signal", {}).get("strategy_block_reason"),
        }
        # Add explain fields if present
        if explain_pullback:
            reject_log["explain_pullback"] = explain_pullback
        if explain_range:
            reject_log["explain_range"] = explain_range
        if explain_continuation:
            reject_log["explain_continuation"] = explain_continuation
        if explain_breakout:
            reject_log["explain_breakout"] = explain_breakout
        if explain_anti_reversal:
            reject_log["explain_anti_reversal"] = explain_anti_reversal
        _log_structured(app.log, "decision_reject", reject_log)
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
    
    # Enforce invariants: execution attempted without SL → HARD STOP
    if trade_plan is not None and intent in ("LONG", "SHORT"):
        try:
            enforce_invariants(trade_plan=trade_plan, intent=intent)
        except Exception as e:
            get_health_counters().increment("invariant_violations")
            app.log.error("CRITICAL: Invariant violation - execution without SL: %s", e)
            # Log to validation file if validation mode is enabled
            try:
                from core.validation_logger import log_invariant_violation
                from app.core.trade_ledger import hash_json
                error_code = getattr(e, "error_code", "UNKNOWN")
                details = getattr(e, "details", {})
                trade_plan_hash_val = hash_json(trade_plan) if trade_plan is not None else None
                context_snapshot = {
                    "timestamp_closed": latest_closed_ts,
                    "symbol": app.symbol,
                    "interval": interval,
                    "payload_hash": payload_hash,
                    "decision_hash": decision_hash,
                    "trade_plan_hash": trade_plan_hash_val,
                    "intent": intent,
                    "payload": payload,
                    "decision": decision,
                    "trade_plan": trade_plan,
                    "daily_state": daily_state,
                    "position_snapshot": position_snapshot,
                    "rejections": rejections,
                }
                log_invariant_violation(
                    error_code=error_code,
                    message=str(e),
                    details=details,
                    context_snapshot=context_snapshot,
                )
            except Exception:
                # Fail silently - validation logging should never affect trading
                pass
            kill("invariant_violation", {"error": str(e)})
            raise RuntimeError(f"invariant_violation_execution_no_sl: {e}") from e
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
        # Increment risk rejection counter
        get_health_counters().increment("risk_rejections")
        # Log structured risk rejection
        if intent in ("LONG", "SHORT", "CLOSE", "UPDATE_SLTP"):
            main_blocker = rejections[0] if rejections else "unknown"
            blocker_categories = list(set([r.split(":")[0] if ":" in r else "unknown" for r in rejections]))
            risk_reject_log = {
                "timestamp_closed": latest_closed_ts,
                "intent": intent,
                "main_blocker": main_blocker,
                "blocker_categories": blocker_categories,
                "blockers": rejections,
                "reject_count": len(rejections),
            }
            # Add explain fields if present (risk rejections may still have strategy explain fields)
            if explain_pullback:
                risk_reject_log["explain_pullback"] = explain_pullback
            if explain_range:
                risk_reject_log["explain_range"] = explain_range
            if explain_continuation:
                risk_reject_log["explain_continuation"] = explain_continuation
            if explain_breakout:
                risk_reject_log["explain_breakout"] = explain_breakout
            if explain_anti_reversal:
                risk_reject_log["explain_anti_reversal"] = explain_anti_reversal
            _log_structured(app.log, "risk_reject", risk_reject_log)
        
        # Enforce invariants: trade_plan without passed risk checks → REJECT
        try:
            enforce_invariants(trade_plan=trade_plan, rejections=rejections)
        except Exception as e:
            get_health_counters().increment("invariant_violations")
            app.log.error("Invariant violation: %s", e)
            # Log to validation file if validation mode is enabled
            try:
                from core.validation_logger import log_invariant_violation
                error_code = getattr(e, "error_code", "UNKNOWN")
                details = getattr(e, "details", {})
                context_snapshot = {
                    "timestamp_closed": latest_closed_ts,
                    "symbol": app.symbol,
                    "interval": interval,
                    "payload_hash": payload_hash,
                    "decision_hash": decision_hash,
                    "intent": intent,
                    "payload": payload,
                    "decision": decision,
                    "trade_plan": trade_plan,
                    "daily_state": daily_state,
                    "position_snapshot": position_snapshot,
                    "rejections": rejections,
                }
                log_invariant_violation(
                    error_code=error_code,
                    message=str(e),
                    details=details,
                    context_snapshot=context_snapshot,
                )
            except Exception:
                # Fail silently - validation logging should never affect trading
                pass
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
        
        # Save original position qty and TP1 qty for TP1 fill detection
        if res.get("executed") and intent in ("LONG", "SHORT"):
            original_qty = res.get("original_qty")
            tp1_qty = res.get("tp1_qty")
            if original_qty is not None:
                # Update position state with tracking fields
                current_pos = position_snapshot.copy()
                current_pos["original_qty"] = float(original_qty)
                if tp1_qty is not None:
                    current_pos["tp1_qty"] = float(tp1_qty)
                save_position_state(app.symbol, current_pos)
    else:
        app.log.info("contracts: trade disabled — execution skipped")
        _log_structured(app.log, "execution_skipped", {
            "timestamp_closed": latest_closed_ts,
            "interval": interval,
            "note": "trade_disabled",
        })

    # Emit periodic health summary
    from core.health_counters import emit_health_summary
    emit_health_summary(app.log, now_ts)
    
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


def _router_debug_compact(router_debug: Optional[Dict[str, Any]]) -> Optional[str]:
    """One-line summary of router state for decision_clean.
    Uses the evaluated candidate walk when available; falls back to legacy mapped-strategy summary.
    """
    if not router_debug:
        return None
    regime = router_debug.get("regime_detected") or "?"
    for_regime = router_debug.get("strategies_for_regime") or []
    evaluations = router_debug.get("strategy_evaluations") or []
    selected = router_debug.get("selected_strategy") or "NONE"
    hold_reason = router_debug.get("hold_reason")
    candidate_count = router_debug.get("candidate_count")
    if candidate_count is None:
        candidate_count = len(for_regime)
    evaluated_count = router_debug.get("evaluated_count")
    if evaluated_count is None:
        evaluated_count = len(evaluations)
    parts = [f"r={regime}", f"cand={candidate_count}", f"eval={evaluated_count}", f"sel={selected}"]
    if hold_reason:
        parts.append(f"hold={hold_reason}")
    rej_items: List[str] = []
    if evaluations:
        for item in evaluations:
            if item.get("pass"):
                continue
            name = item.get("strategy")
            code = item.get("rejection_reason")
            if name and code and str(code).strip() and str(code).strip().lower() != "unknown":
                rej_items.append(f"{name}:{str(code)[:20]}")
            if len(rej_items) >= 3:
                break
    else:
        rejected = router_debug.get("rejected_strategies") or {}
        for name in for_regime:
            code = rejected.get(name)
            if code is not None and str(code).strip() and str(code).strip().lower() != "unknown":
                rej_items.append(f"{name}:{str(code)[:20]}")
            if len(rej_items) >= 3:
                break
    parts.append(f"rej={'|'.join(rej_items) if rej_items else 'none'}")
    return " ".join(parts)


def _reclaim_debug_compact(decision_log: Dict[str, Any]) -> Optional[str]:
    """One-line reclaim summary for decision_clean."""
    level = decision_log.get("reclaim_level_used")
    tol = decision_log.get("effective_tolerance")
    dist = decision_log.get("distance_to_reclaim")
    if level is None and tol is None and dist is None:
        return None
    parts = []
    if level is not None:
        parts.append(f"level={level:.2f}" if isinstance(level, (int, float)) else f"level={level}")
    if tol is not None and isinstance(tol, (int, float)):
        parts.append(f"tol={tol:.4f}")
    if dist is not None and isinstance(dist, (int, float)):
        parts.append(f"dist_atr={dist:.3f}")
    return " ".join(parts) if parts else None


def _strip_strategy_ineligible_from_blockers(reject_reasons: List[str]) -> List[str]:
    """Remove any *:strategy_ineligible from list so it never appears in blockers."""
    return [c for c in reject_reasons if not (isinstance(c, str) and ":strategy_ineligible" in c)]


def _log_decision_clean(logger: logging.Logger, decision_log: Dict[str, Any]) -> None:
    raw_rejects = list(decision_log.get("reject_reasons") or [])
    blockers = _prioritize_blockers(_strip_strategy_ineligible_from_blockers(raw_rejects))
    categories = []
    for code in blockers:
        if not isinstance(code, str) or ":" not in code:
            continue
        prefix = code.split(":", 1)[0]
        if prefix and prefix not in categories:
            categories.append(prefix)
    
    # Build explain_main from explain fields if available
    explain_main = _build_explain_main(
        blockers,
        decision_log.get("explain_pullback"),
        decision_log.get("explain_range"),
        decision_log.get("explain_anti_reversal"),
        decision_log.get("explain_continuation"),
    )
    
    gating_summary = blockers[0] if blockers else None
    router_compact = _router_debug_compact(decision_log.get("router_debug"))
    reclaim_compact = _reclaim_debug_compact(decision_log)
    stability_mode_used = decision_log.get("stability_mode_used")
    
    payload = {
        "regime_detected": decision_log.get("regime_detected"),
        "regime_used_for_routing": decision_log.get("regime_used_for_routing"),
        "selected_strategy": decision_log.get("selected_strategy"),
        "eligible_strategies": decision_log.get("eligible_strategies"),
        "strategy_block_reason": decision_log.get("strategy_block_reason"),
        "hold_reason_summary": decision_log.get("hold_reason_summary"),
        "decision": decision_log.get("decision"),
        "stability_score": decision_log.get("stability_score"),
        "stability_mode_used": stability_mode_used,
        "equity": decision_log.get("equity"),
        "funds_base": decision_log.get("funds_base"),
        "funds_source": decision_log.get("funds_source"),
        "risk_usd": decision_log.get("risk_usd"),
        "qty_before_rounding": decision_log.get("qty_before_rounding"),
        "qty_after_rounding": decision_log.get("qty_after_rounding"),
        "required_margin": decision_log.get("required_margin"),
        "leverage_used": decision_log.get("leverage_used"),
        "main_blocker": blockers[0] if blockers else None,
        "blockers": blockers[:6],
        "blocker_categories": categories[:6],
        "gating_summary": gating_summary,
    }
    if decision_log.get("regime_explain") is not None:
        payload["regime_explain"] = decision_log.get("regime_explain")
    if router_compact is not None:
        payload["router_debug"] = {"compact": router_compact}
    if reclaim_compact is not None:
        payload["reclaim_debug"] = {"compact": reclaim_compact}
    if explain_main:
        payload["explain_main"] = explain_main
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


def _build_explain_pullback(signal: Dict[str, Any], blockers: List[str]) -> Optional[Dict[str, Any]]:
    """
    Build explain_pullback fields for PULLBACK strategy blockers.
    Only returns data if regime is PULLBACK or blockers contain P: codes.
    """
    regime_detected = signal.get("regime_detected")
    has_p_blockers = any(b.startswith("P:") for b in blockers)
    
    if regime_detected != "PULLBACK" and not has_p_blockers:
        return None
    
    from core.config import settings
    
    dist50 = signal.get("dist50")
    dist50_prev = signal.get("dist50_prev")
    dist50_min = settings.get_float("PULLBACK_REENTRY_DIST50_MIN")
    dist50_max = settings.get_tunable_float("PULLBACK_REENTRY_DIST50_MAX", "PULLBACK_REENTRY_DIST50_MAX_REAL")
    dist50_max_used = dist50_max
    
    reclaim_long = signal.get("reclaim_long", False)
    reclaim_short = signal.get("reclaim_short", False)
    htf_trend = signal.get("trend")
    close_ltf = signal.get("close_ltf")
    ema50_ltf = signal.get("ema50_ltf")
    
    # Determine reclaim requirement
    reclaim_required = "none"
    if htf_trend == "up":
        reclaim_required = "long"
        reclaim_ok = reclaim_long if close_ltf is not None and ema50_ltf is not None and close_ltf > ema50_ltf else False
    elif htf_trend == "down":
        reclaim_required = "short"
        reclaim_ok = reclaim_short if close_ltf is not None and ema50_ltf is not None and close_ltf < ema50_ltf else False
    else:
        reclaim_ok = False
    
    volume_ratio = signal.get("volume_ratio")
    vol_min = settings.get_tunable_float("PULLBACK_REENTRY_VOL_MIN", "PULLBACK_REENTRY_VOL_MIN_REAL")
    vol_min_used = vol_min
    vol_ok = volume_ratio is not None and volume_ratio >= vol_min if volume_ratio is not None else None
    
    reclaim_tol_atr = 0.0
    reclaim_tol_abs = 0.0
    if settings.get_bool("REAL_MARKET_TUNING", False):
        reclaim_tol_atr = settings.get_float("PULLBACK_RECLAIM_TOL_ATR", 0.10)
        atr14 = signal.get("atr")
        if atr14 is not None and atr14 > 0:
            reclaim_tol_abs = reclaim_tol_atr * atr14
    
    stability_score = signal.get("stability_score")
    stability_soft = settings.get_tunable_float("STABILITY_SOFT", "STABILITY_SOFT_REAL")
    stability_hard = settings.get_tunable_float("STABILITY_HARD", "STABILITY_HARD_REAL")
    stable_ok = stability_score is not None and stability_score >= stability_hard if stability_score is not None else None
    stable_soft_ok = stability_score is not None and stability_soft <= stability_score < stability_hard if stability_score is not None else None
    
    # Build confirm explain fields if P:confirm blocker present or regime is PULLBACK
    has_confirm_blocker = "P:confirm" in blockers
    confirm_explain = None
    if regime_detected == "PULLBACK" or has_confirm_blocker:
        candle_body_ratio = signal.get("candle_body_ratio")
        body_min = settings.get_float("PULLBACK_REENTRY_CONFIRM_BODY_MIN")
        body_ok = candle_body_ratio is not None and candle_body_ratio >= body_min if candle_body_ratio is not None else None
        
        min_bars = settings.get_int("PULLBACK_REENTRY_MIN_BARS")
        consec_above_ema50_prev = signal.get("consec_above_ema50_prev")
        consec_below_ema50_prev = signal.get("consec_below_ema50_prev")
        
        # Determine bars_since_signal based on trend
        bars_since_signal = None
        if htf_trend == "up":
            bars_since_signal = consec_below_ema50_prev
        elif htf_trend == "down":
            bars_since_signal = consec_above_ema50_prev
        
        bars_ok = bars_since_signal is not None and bars_since_signal >= min_bars if bars_since_signal is not None else None
        early_confirm_considered = signal.get("pullback_early_confirm_considered")
        early_confirm_ok = signal.get("pullback_early_confirm_ok")
        early_confirm_reasons = signal.get("pullback_early_confirm_reasons") or []
        min_bars_bypassed = signal.get("pullback_min_bars_bypassed")
        effective_confirmation_mode = signal.get("pullback_confirmation_mode")
        
        # Determine confirmation type and overall confirm_ok
        close_prev = signal.get("close_prev_ltf")
        confirm_ok = False
        confirmation_type = "NONE"
        
        if close_prev is None or close_ltf is None:
            confirmation_type = "MISSING_DATA"
            confirm_ok = False
        else:
            # Check close direction confirmation
            if htf_trend == "up":
                close_dir_ok = close_ltf > close_prev
            elif htf_trend == "down":
                close_dir_ok = close_ltf < close_prev
            else:
                close_dir_ok = False
            
            if not close_dir_ok:
                confirmation_type = "CLOSE_DIRECTION"
                confirm_ok = False
            elif body_ok is False:
                confirmation_type = "BODY"
                confirm_ok = False
            elif min_bars_bypassed:
                confirmation_type = "EARLY"
                confirm_ok = True
            elif bars_ok is False:
                confirmation_type = "BARS"
                confirm_ok = False
            else:
                confirmation_type = "OK"
                confirm_ok = True
        
        confirm_explain = {
            "body_ratio": candle_body_ratio,
            "body_min": body_min,
            "body_ok": body_ok,
            "min_bars": min_bars,
            "bars_since_signal": bars_since_signal,
            "bars_ok": bars_ok,
            "confirmation_type": confirmation_type,
            "confirm_ok": confirm_ok,
            "early_confirm_considered": early_confirm_considered,
            "early_confirm_ok": early_confirm_ok,
            "early_confirm_reasons": early_confirm_reasons,
            "min_bars_bypassed": min_bars_bypassed,
            "effective_confirmation_mode": effective_confirmation_mode,
        }
    
    result = {
        "dist50_prev": dist50_prev,
        "dist50_curr": dist50,
        "dist50_min": dist50_min,
        "dist50_max": dist50_max,
        "dist50_max_used": dist50_max_used,
        "dist50_prev_ok": dist50_prev is not None and dist50_min <= dist50_prev <= dist50_max if dist50_prev is not None else None,
        "dist50_curr_ok": dist50 is not None and dist50 <= dist50_max if dist50 is not None else None,
        "reclaim_short": reclaim_short,
        "reclaim_long": reclaim_long,
        "reclaim_required": reclaim_required,
        "reclaim_ok": reclaim_ok,
        "reclaim_tol_atr": reclaim_tol_atr,
        "reclaim_tol_abs": reclaim_tol_abs,
        "volume_ratio": volume_ratio,
        "vol_min": vol_min,
        "vol_min_used": vol_min_used,
        "vol_ok": vol_ok,
        "stability_score": stability_score,
        "stability_soft": stability_soft,
        "stability_hard": stability_hard,
        "stable_ok": stable_ok,
        "stable_soft_ok": stable_soft_ok,
        "signal_side": signal.get("pullback_signal_side"),
        "bars_since_signal": signal.get("pullback_bars_since_signal"),
        "prev_window_ok": signal.get("pullback_prev_window_ok"),
        "curr_window_ok": signal.get("pullback_current_dist_ok"),
        "min_bars_ok": signal.get("pullback_min_bars_ok"),
        "direction_confirm_ok": signal.get("pullback_direction_confirm_ok"),
        "body_ok": signal.get("pullback_body_ok"),
        "persistence_ok": signal.get("pullback_persistence_ok"),
        "vol_ok": signal.get("pullback_vol_ok", vol_ok),
        "confirmation_ready": signal.get("pullback_confirmation_ready"),
        "early_confirm_considered": signal.get("pullback_early_confirm_considered"),
        "early_confirm_ok": signal.get("pullback_early_confirm_ok"),
        "early_confirm_reasons": signal.get("pullback_early_confirm_reasons"),
        "min_bars_bypassed": signal.get("pullback_min_bars_bypassed"),
        "effective_confirmation_mode": signal.get("pullback_confirmation_mode"),
        "context_strong": signal.get("pullback_context_strong"),
        "trend_aligned": signal.get("pullback_trend_aligned"),
        "trend_strength_ok": signal.get("pullback_trend_strength_ok"),
        "ema_side_aligned": signal.get("pullback_ema_side_aligned"),
        "anti_reversal_block": signal.get("pullback_anti_reversal_block"),
        "lifecycle_state": signal.get("pullback_lifecycle_state"),
        "invalidation_stage": signal.get("pullback_invalidation_stage"),
        "pending_entry_status": signal.get("pending_entry_status"),
        "pending_entry_strategy": signal.get("pending_entry_strategy"),
        "pending_entry_set_ts": signal.get("pending_entry_set_ts"),
        "pending_entry_remaining": signal.get("pending_entry_remaining"),
    }
    
    if confirm_explain:
        result["confirm"] = confirm_explain
    
    return result


def _build_explain_range(signal: Dict[str, Any], blockers: List[str]) -> Optional[Dict[str, Any]]:
    """
    Build explain_range fields for RANGE mean-reversion strategy blockers.
    Only returns data if regime is RANGE or blockers contain M: codes.
    """
    regime_detected = signal.get("regime_detected")
    has_m_blockers = any(b.startswith("M:") for b in blockers)
    
    if regime_detected != "RANGE" and not has_m_blockers:
        return None
    
    from core.config import settings
    
    htf_trend = signal.get("trend")
    trend_strength = signal.get("trend_strength")
    trend_strength_min = settings.get_float("TREND_STRENGTH_MIN")
    # Mean-rev is allowed when: trend == "range" OR trend_strength < threshold
    # Blocked when: trend != "range" AND trend_strength >= threshold
    trend_ok = htf_trend == "range" or (trend_strength is not None and trend_strength < trend_strength_min)
    trend_block_reason = None
    if not trend_ok:
        if htf_trend != "range":
            trend_block_reason = f"trend={htf_trend}"
        elif trend_strength is not None and trend_strength >= trend_strength_min:
            trend_block_reason = f"trend_strength={trend_strength:.3f}>={trend_strength_min:.3f}"
    
    volume_ratio = signal.get("volume_ratio")
    vol_max = settings.get_float("RANGE_MEANREV_VOL_MAX")
    vol_ok = volume_ratio is not None and volume_ratio < vol_max if volume_ratio is not None else None
    
    rsi = signal.get("rsi14_ltf")
    rsi_long_max = settings.get_float("RANGE_RSI_LONG_MAX")
    rsi_short_min = settings.get_float("RANGE_RSI_SHORT_MIN")
    rsi_long_ok = rsi is not None and rsi <= rsi_long_max if rsi is not None else None
    rsi_short_ok = rsi is not None and rsi >= rsi_short_min if rsi is not None else None
    
    close_ltf = signal.get("close_ltf")
    atr14 = signal.get("atr")
    donchian_high_20 = signal.get("donchian_high_20")
    donchian_low_20 = signal.get("donchian_low_20")
    edge_atr_min = settings.get_float("RANGE_MEANREV_EDGE_ATR")
    
    # Compute edge_atr (distance from range edge)
    edge_atr = None
    edge_ok = None
    if close_ltf is not None and atr14 is not None and atr14 > 0 and donchian_high_20 is not None and donchian_low_20 is not None:
        # Distance from closest edge
        dist_from_low = (close_ltf - donchian_low_20) / atr14 if donchian_low_20 > 0 else None
        dist_from_high = (donchian_high_20 - close_ltf) / atr14 if donchian_high_20 > 0 else None
        if dist_from_low is not None and dist_from_high is not None:
            edge_atr = min(dist_from_low, dist_from_high)
            edge_ok = edge_atr >= edge_atr_min
        elif dist_from_low is not None:
            edge_atr = dist_from_low
            edge_ok = edge_atr >= edge_atr_min
        elif dist_from_high is not None:
            edge_atr = dist_from_high
            edge_ok = edge_atr >= edge_atr_min
    
    wick_ratio = signal.get("wick_ratio")
    wick_th = settings.get_float("ANTI_REV_WICK_TH")  # Reusing for range entries if applicable
    wick_ok = wick_ratio is not None and wick_ratio < wick_th if wick_ratio is not None else None
    
    return {
        "trend": htf_trend,
        "trend_ok": trend_ok,
        "trend_block_reason": trend_block_reason,
        "volume_ratio": volume_ratio,
        "vol_max": vol_max,
        "vol_ok": vol_ok,
        "rsi": rsi,
        "rsi_long_max": rsi_long_max,
        "rsi_short_min": rsi_short_min,
        "rsi_long_ok": rsi_long_ok,
        "rsi_short_ok": rsi_short_ok,
        "edge_atr": edge_atr,
        "edge_atr_min": edge_atr_min,
        "edge_ok": edge_ok,
        "wick_ratio": wick_ratio,
        "wick_th": wick_th,
        "wick_ok": wick_ok,
    }


def _build_explain_continuation(signal: Dict[str, Any], blockers: List[str]) -> Optional[Dict[str, Any]]:
    """
    Build explain_continuation fields for CONTINUATION strategy blockers.
    Only returns data if regime is TREND_CONTINUATION or blockers contain C: codes.
    """
    regime_detected = signal.get("regime_detected")
    has_c_blockers = any(b.startswith("C:") for b in blockers)
    
    if regime_detected != "TREND_CONTINUATION" and not has_c_blockers:
        return None
    
    from core.config import settings
    
    cont_long_ok = signal.get("cont_long_ok", False)
    cont_short_ok = signal.get("cont_short_ok", False)
    cont_reject_codes = signal.get("cont_reject_codes", [])
    
    direction = signal.get("direction")
    trend_stable_long = signal.get("trend_stable_long", False)
    trend_stable_short = signal.get("trend_stable_short", False)
    trend_strength = signal.get("trend_strength")
    trend_strength_min = settings.get_float("TREND_STRENGTH_MIN")
    trend_context_ok = signal.get("cont_long_trend_context_ok") if direction == "UP" else signal.get("cont_short_trend_context_ok")
    ema_side_ok = signal.get("cont_long_ema_side_ok") if direction == "UP" else signal.get("cont_short_ema_side_ok")
    
    candle_body_ratio = signal.get("candle_body_ratio")
    cont_body_min = settings.get_float("CONT_BODY_MIN", 0.50)
    body_ok = candle_body_ratio is not None and candle_body_ratio >= cont_body_min if candle_body_ratio is not None else None
    
    volume_ratio = signal.get("volume_ratio")
    cont_vol_min = settings.get_float("CONT_VOL_MIN", 1.0)
    vol_ok = volume_ratio is not None and volume_ratio >= cont_vol_min if volume_ratio is not None else None
    
    atr_ratio = signal.get("atr_ratio")
    cont_atr_ratio_min = settings.get_float("CONT_ATR_RATIO_MIN", 0.95)
    atr_ratio_ok = atr_ratio is not None and atr_ratio >= cont_atr_ratio_min if atr_ratio is not None else None
    
    slope_atr = signal.get("slope_atr")
    cont_slope_atr_min = settings.get_float("CONT_SLOPE_ATR_MIN")
    cont_slope_atr_max = settings.get_float("CONT_SLOPE_ATR_MAX")
    slope_ok = None
    if slope_atr is not None:
        if direction == "UP":
            slope_ok = slope_atr >= cont_slope_atr_min
        elif direction == "DOWN":
            slope_ok = slope_atr <= cont_slope_atr_max
    
    k_overextension = signal.get("k_overextension")
    cont_k_max = settings.get_float("CONT_K_MAX")
    k_ok = k_overextension is not None and abs(k_overextension) <= cont_k_max if k_overextension is not None else None
    
    rsi14 = signal.get("rsi14_ltf")
    cont_rsi_max_long = settings.get_float("CONT_RSI_MAX_LONG")
    cont_rsi_min_short = settings.get_float("CONT_RSI_MIN_SHORT")
    rsi_ok = None
    if rsi14 is not None:
        if direction == "UP":
            rsi_ok = rsi14 <= cont_rsi_max_long
        elif direction == "DOWN":
            rsi_ok = rsi14 >= cont_rsi_min_short
    
    break_level = signal.get("break_level")
    break_delta_atr = signal.get("break_delta_atr")
    close_ltf = signal.get("close_ltf")
    break_ok = None
    if break_level is not None and close_ltf is not None:
        if direction == "UP":
            break_ok = close_ltf >= break_level
        elif direction == "DOWN":
            break_ok = close_ltf <= break_level
    
    stability_score = signal.get("stability_score")
    stability_soft = settings.get_float("STABILITY_SOFT")
    stability_hard = settings.get_float("STABILITY_HARD")
    stable_ok = stability_score is not None and stability_score >= stability_hard if stability_score is not None else None
    stable_soft_ok = stability_score is not None and stability_soft <= stability_score < stability_hard if stability_score is not None else None
    
    continuation_confirmation_type = signal.get("continuation_confirmation_type")
    confirmation_ok = continuation_confirmation_type != "NONE"
    
    return {
        "cont_long_ok": cont_long_ok,
        "cont_short_ok": cont_short_ok,
        "direction": direction,
        "trend_stable_long": trend_stable_long,
        "trend_stable_short": trend_stable_short,
        "trend_strength": trend_strength,
        "trend_strength_min": trend_strength_min,
        "trend_ok": trend_context_ok,
        "ema_side_ok": ema_side_ok,
        "candle_body_ratio": candle_body_ratio,
        "cont_body_min": cont_body_min,
        "body_ok": body_ok,
        "volume_ratio": volume_ratio,
        "cont_vol_min": cont_vol_min,
        "vol_ok": vol_ok,
        "atr_ratio": atr_ratio,
        "cont_atr_ratio_min": cont_atr_ratio_min,
        "atr_ratio_ok": atr_ratio_ok,
        "slope_atr": slope_atr,
        "slope_delta": signal.get("slope_delta"),
        "slope_lookback_bars": signal.get("slope_lookback_bars"),
        "cont_slope_atr_min": cont_slope_atr_min,
        "cont_slope_atr_max": cont_slope_atr_max,
        "cont_slope_threshold": signal.get("cont_slope_threshold"),
        "cont_slope_state": signal.get("cont_slope_state"),
        "slope_ok": slope_ok,
        "k_overextension": k_overextension,
        "cont_k_max": cont_k_max,
        "k_ok": k_ok,
        "rsi14": rsi14,
        "cont_rsi_max_long": cont_rsi_max_long,
        "cont_rsi_min_short": cont_rsi_min_short,
        "rsi_ok": rsi_ok,
        "break_level": break_level,
        "break_delta_atr": break_delta_atr,
        "close_ltf": close_ltf,
        "break_ok": break_ok,
        "stability_score": stability_score,
        "stability_soft": stability_soft,
        "stability_hard": stability_hard,
        "stable_ok": stable_ok,
        "stable_soft_ok": stable_soft_ok,
        "continuation_confirmation_type": continuation_confirmation_type,
        "confirmation_ok": confirmation_ok,
        "primary_reject": signal.get("cont_primary_reject"),
        "cont_reject_codes": cont_reject_codes,
    }


def _build_explain_breakout(signal: Dict[str, Any], blockers: List[str]) -> Optional[Dict[str, Any]]:
    """
    Build explain_breakout fields for BREAKOUT_EXPANSION strategy blockers.
    Only returns data if regime is BREAKOUT_EXPANSION or blockers contain B: codes.
    """
    regime_detected = signal.get("regime_detected")
    has_b_blockers = any(b.startswith("B:") for b in blockers)
    
    if regime_detected != "BREAKOUT_EXPANSION" and not has_b_blockers:
        return None
    
    from core.config import settings
    
    breakout_expansion_long_ok = signal.get("breakout_expansion_long_ok", False)
    breakout_expansion_short_ok = signal.get("breakout_expansion_short_ok", False)
    
    breakout_long = signal.get("breakout_long", False)
    breakout_short = signal.get("breakout_short", False)
    
    volume_ratio = signal.get("volume_ratio")
    regime_breakout_vol_min = settings.get_float("REGIME_BREAKOUT_VOL_MIN")
    vol_ok = volume_ratio is not None and volume_ratio >= regime_breakout_vol_min if volume_ratio is not None else None
    
    consec_close_above_donchian_20 = signal.get("consec_close_above_donchian_20")
    consec_close_below_donchian_20 = signal.get("consec_close_below_donchian_20")
    breakout_accept_bars = settings.get_int("BREAKOUT_ACCEPT_BARS")
    accept_bars_ok = None
    if consec_close_above_donchian_20 is not None:
        accept_bars_ok = consec_close_above_donchian_20 >= breakout_accept_bars
    elif consec_close_below_donchian_20 is not None:
        accept_bars_ok = consec_close_below_donchian_20 >= breakout_accept_bars
    
    trend_stable_long = signal.get("trend_stable_long", False)
    trend_stable_short = signal.get("trend_stable_short", False)
    
    donchian_high_20 = signal.get("donchian_high_20")
    donchian_low_20 = signal.get("donchian_low_20")
    close_ltf = signal.get("close_ltf")
    high_ltf = signal.get("high_ltf")
    low_ltf = signal.get("low_ltf")
    atr14 = signal.get("atr")
    breakout_reject_wick_atr = settings.get_float("BREAKOUT_REJECT_WICK_ATR")
    
    wick_ok = None
    if breakout_long and high_ltf is not None and donchian_high_20 is not None and atr14 is not None and atr14 > 0:
        wick_ok = low_ltf is not None and low_ltf >= donchian_high_20 - breakout_reject_wick_atr * atr14
    elif breakout_short and low_ltf is not None and donchian_low_20 is not None and atr14 is not None and atr14 > 0:
        wick_ok = high_ltf is not None and high_ltf <= donchian_low_20 + breakout_reject_wick_atr * atr14
    
    return {
        "breakout_expansion_long_ok": breakout_expansion_long_ok,
        "breakout_expansion_short_ok": breakout_expansion_short_ok,
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "volume_ratio": volume_ratio,
        "regime_breakout_vol_min": regime_breakout_vol_min,
        "vol_ok": vol_ok,
        "consec_close_above_donchian_20": consec_close_above_donchian_20,
        "consec_close_below_donchian_20": consec_close_below_donchian_20,
        "breakout_accept_bars": breakout_accept_bars,
        "accept_bars_ok": accept_bars_ok,
        "trend_stable_long": trend_stable_long,
        "trend_stable_short": trend_stable_short,
        "wick_ok": wick_ok,
    }


def _build_explain_anti_reversal(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build explain_anti_reversal fields when anti-reversal logic is evaluated.
    
    Semantics:
    - evaluated: true when anti-reversal logic was evaluated (always true if this function returns data)
    - blocked: true when anti-reversal actually blocks entry/selection
    - condition_ok: true when condition passes (condition_ok == not blocked)
    
    Returns data when anti_reversal_block is explicitly set (True or False) or when
    anti_reversal_reason is present, indicating evaluation occurred.
    """
    anti_reversal_block = signal.get("anti_reversal_block")
    anti_reversal_reason = signal.get("anti_reversal_reason")
    anti_reversal_mode = signal.get("anti_reversal_mode")
    anti_reversal_active_side = signal.get("anti_reversal_active_side")
    
    # Return explain data if anti-reversal was evaluated:
    # - anti_reversal_block is explicitly set (True or False), OR
    # - anti_reversal_reason is present (even if empty string means "not blocked")
    # Empty string reason with False block means "evaluated, not blocked"
    if anti_reversal_block is None and anti_reversal_reason is None:
        return None
    
    close_htf = signal.get("close_htf")
    ema200_htf = signal.get("ema200_htf")
    ema_fast_htf = signal.get("ema_fast_htf")
    direction = signal.get("direction")
    rsi_htf = signal.get("rsi14_htf")
    rsi_htf_prev = signal.get("rsi14_htf_prev")
    wick_ratio_ltf = signal.get("wick_ratio")
    
    # Determine if blocked based on anti_reversal_block flag
    # This matches the actual decision logic
    # If anti_reversal_block is explicitly set, use it
    # Otherwise, infer from reason: "HTF_EMA_RECLAIM" or "HTF_RSI_SLOPE" means blocked
    if anti_reversal_block is not None:
        blocked = bool(anti_reversal_block)
    else:
        # Infer from reason if block flag not available
        blocked = anti_reversal_reason in ("HTF_EMA_RECLAIM", "HTF_RSI_SLOPE")
    
    # Determine condition_ok: condition passes when NOT blocked
    # condition_ok == (not blocked)
    condition_ok = not blocked
    
    htf_reclaim_level = None
    ema_reclaim_buffer_atr = 0.0
    ema_reclaim_buffer_abs = 0.0
    ema_threshold_long = None
    ema_threshold_short = None
    atr14_htf = signal.get("atr14_htf")
    if settings.get_bool("REAL_MARKET_TUNING", False) and atr14_htf is not None and atr14_htf > 0 and ema_fast_htf is not None:
        ema_reclaim_buffer_atr = settings.get_float("HTF_EMA_RECLAIM_ATR_BUFFER", 0.10)
        ema_reclaim_buffer_abs = ema_reclaim_buffer_atr * atr14_htf
        ema_threshold_long = ema_fast_htf - ema_reclaim_buffer_abs
        ema_threshold_short = ema_fast_htf + ema_reclaim_buffer_abs
    
    # Compute condition details based on reason
    if anti_reversal_reason == "HTF_EMA_RECLAIM":
        if direction == "DOWN" and close_htf is not None and ema_fast_htf is not None:
            htf_reclaim_level = ema_fast_htf
        elif direction == "UP" and close_htf is not None and ema_fast_htf is not None:
            htf_reclaim_level = ema_fast_htf
    elif anti_reversal_reason == "HTF_RSI_SLOPE":
        # RSI slope condition - already reflected in blocked status
        pass
    
    result = {
        "reason": anti_reversal_reason or "",
        "evaluated": True,
        "blocked": blocked,
        "condition_ok": condition_ok,  # condition_ok == (not blocked)
        "mode": anti_reversal_mode or "legacy",
        "active_side": anti_reversal_active_side or "NONE",
        "long_blocked": bool(signal.get("anti_reversal_long_block")),
        "long_reason": signal.get("anti_reversal_long_reason") or "",
        "short_blocked": bool(signal.get("anti_reversal_short_block")),
        "short_reason": signal.get("anti_reversal_short_reason") or "",
        "close_htf": close_htf,
        "ema200_htf": ema200_htf,
        "ema_fast_htf": ema_fast_htf,
        "htf_reclaim_level": htf_reclaim_level,
        "ema_reclaim_buffer_atr": ema_reclaim_buffer_atr,
        "ema_reclaim_buffer_abs": ema_reclaim_buffer_abs,
        "ema_threshold_long": ema_threshold_long,
        "ema_threshold_short": ema_threshold_short,
    }
    return result


def _build_explain_main(
    blockers: List[str],
    explain_pullback: Optional[Dict[str, Any]],
    explain_range: Optional[Dict[str, Any]],
    explain_anti_reversal: Optional[Dict[str, Any]],
    explain_continuation: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build explain_main summary for decision_clean log.
    Extracts the main blocker with its value, threshold, and pass/fail status.
    """
    if not blockers:
        return None
    
    main_blocker = blockers[0]
    
    # Try to extract from explain fields
    if main_blocker.startswith("P:") and explain_pullback:
        if main_blocker == "P:dist50":
            return {
                "blocker": main_blocker,
                "x": explain_pullback.get("dist50_curr"),
                "th": explain_pullback.get("dist50_max"),
                "ok": explain_pullback.get("dist50_curr_ok"),
            }
        elif main_blocker == "P:dist50_prev":
            return {
                "blocker": main_blocker,
                "x": explain_pullback.get("dist50_prev"),
                "th": f"{explain_pullback.get('dist50_min')}-{explain_pullback.get('dist50_max')}",
                "ok": explain_pullback.get("dist50_prev_ok"),
            }
        elif main_blocker == "P:reclaim":
            return {
                "blocker": main_blocker,
                "x": explain_pullback.get("reclaim_required"),
                "th": "required",
                "ok": explain_pullback.get("reclaim_ok"),
            }
        elif main_blocker == "P:vol":
            return {
                "blocker": main_blocker,
                "x": explain_pullback.get("volume_ratio"),
                "th": explain_pullback.get("vol_min"),
                "ok": explain_pullback.get("vol_ok"),
            }
        elif main_blocker == "P:stability":
            return {
                "blocker": main_blocker,
                "x": explain_pullback.get("stability_score"),
                "th": explain_pullback.get("stability_hard"),
                "ok": explain_pullback.get("stable_ok"),
            }
        elif main_blocker == "P:confirm":
            confirm = explain_pullback.get("confirm")
            if confirm:
                return {
                    "blocker": main_blocker,
                    "x": confirm.get("confirmation_type"),
                    "th": f"body>={confirm.get('body_min')},bars>={confirm.get('min_bars')}",
                    "ok": confirm.get("confirm_ok"),
                }
            else:
                return {
                    "blocker": main_blocker,
                    "x": None,
                    "th": None,
                    "ok": None,
                }
        elif main_blocker == "P:body":
            confirm = explain_pullback.get("confirm")
            if confirm:
                return {
                    "blocker": main_blocker,
                    "x": confirm.get("body_ratio"),
                    "th": confirm.get("body_min"),
                    "ok": confirm.get("body_ok"),
                }
        elif main_blocker == "P:pullback_bars":
            confirm = explain_pullback.get("confirm")
            if confirm:
                return {
                    "blocker": main_blocker,
                    "x": confirm.get("bars_since_signal"),
                    "th": f"bars>={confirm.get('min_bars')} or early_confirm",
                    "ok": confirm.get("bars_ok") or confirm.get("early_confirm_ok"),
                }
    
    if main_blocker.startswith("M:") and explain_range:
        if main_blocker == "M:trend":
            return {
                "blocker": main_blocker,
                "x": explain_range.get("trend"),
                "th": "range",
                "ok": explain_range.get("trend_ok"),
            }
        elif main_blocker == "M:vol":
            return {
                "blocker": main_blocker,
                "x": explain_range.get("volume_ratio"),
                "th": explain_range.get("vol_max"),
                "ok": explain_range.get("vol_ok"),
            }
        elif main_blocker in ("M:range_long", "M:range_short"):
            return {
                "blocker": main_blocker,
                "x": explain_range.get("edge_atr"),
                "th": explain_range.get("edge_atr_min"),
                "ok": explain_range.get("edge_ok"),
            }

    if main_blocker.startswith("C:") and explain_continuation:
        if main_blocker == "C:slope":
            threshold = (
                explain_continuation.get("cont_slope_atr_max")
                if explain_continuation.get("direction") == "DOWN"
                else explain_continuation.get("cont_slope_atr_min")
            )
            return {
                "blocker": main_blocker,
                "x": explain_continuation.get("slope_atr"),
                "th": threshold,
                "ok": explain_continuation.get("slope_ok"),
            }
        if main_blocker == "C:break":
            return {
                "blocker": main_blocker,
                "x": explain_continuation.get("close_ltf"),
                "th": explain_continuation.get("break_level"),
                "ok": explain_continuation.get("break_ok"),
            }
    
    if explain_anti_reversal and "anti_reversal" in main_blocker.lower():
        return {
            "blocker": main_blocker,
            "x": explain_anti_reversal.get("reason"),
            "th": "blocked",
            "ok": explain_anti_reversal.get("condition_ok"),
        }
    
    # Fallback: just the blocker code
    return {
        "blocker": main_blocker,
        "x": None,
        "th": None,
        "ok": None,
    }


def _build_explain_fields(payload: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    signal = decision.get("signal") or {}
    predictive = signal.get("predictive") or {}
    validation = signal.get("validation") or {}
    execution_profile = signal.get("execution_profile") or {}
    analytics = signal.get("analytics") or {}
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
        "execution_decision": decision.get("execution_decision"),
        "entry_mode": decision.get("entry_mode"),
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
        "hold_reason_summary": signal.get("hold_reason_summary"),
        "router_debug": signal.get("router_debug"),
        "regime_explain": signal.get("regime_explain"),
        "stability_score": signal.get("stability_score"),
        "stable_ok": signal.get("stable_ok"),
        "stable_soft": signal.get("stable_soft"),
        "stable_block": signal.get("stable_block"),
        "stable_block_reason": signal.get("stable_block_reason"),
        "stability_metrics": signal.get("stability_metrics"),
        "stability_mode_used": signal.get("stability_mode_used"),
        "adaptive_soft_stability": signal.get("adaptive_soft_stability"),
        "continuation_confirmation_type": signal.get("continuation_confirmation_type"),
        "confirmation_metrics": signal.get("confirmation_metrics"),
        "anti_reversal_block": signal.get("anti_reversal_block"),
        "anti_reversal_reason": signal.get("anti_reversal_reason"),
        "anti_reversal_mode": signal.get("anti_reversal_mode"),
        "anti_reversal_active_side": signal.get("anti_reversal_active_side"),
        "anti_reversal_long_block": signal.get("anti_reversal_long_block"),
        "anti_reversal_long_reason": signal.get("anti_reversal_long_reason"),
        "anti_reversal_short_block": signal.get("anti_reversal_short_block"),
        "anti_reversal_short_reason": signal.get("anti_reversal_short_reason"),
        "pending_entry_status": signal.get("pending_entry_status"),
        "pending_entry_strategy": signal.get("pending_entry_strategy"),
        "pending_entry_set_ts": signal.get("pending_entry_set_ts"),
        "pending_entry_remaining": signal.get("pending_entry_remaining"),
        "event_detected": signal.get("event_detected"),
        "event_block": signal.get("event_block"),
        "event_cooldown_remaining": signal.get("event_cooldown_remaining"),
        "trend_accel_long_ok": signal.get("trend_accel_long_ok"),
        "trend_accel_short_ok": signal.get("trend_accel_short_ok"),
        "squeeze_break_long_ok": signal.get("squeeze_break_long_ok"),
        "squeeze_break_short_ok": signal.get("squeeze_break_short_ok"),
        "ev_gate_enabled": signal.get("ev_gate_enabled"),
        "ev_value": signal.get("ev_value"),
        "ev_p": signal.get("ev_p"),
        "atr_ratio": signal.get("atr_ratio"),
        "volatility_state": signal.get("volatility_state"),
        "cont_short_ok": signal.get("cont_short_ok"),
        "cont_long_ok": signal.get("cont_long_ok"),
        "cont_short_trend_context_ok": signal.get("cont_short_trend_context_ok"),
        "cont_long_trend_context_ok": signal.get("cont_long_trend_context_ok"),
        "cont_short_ema_side_ok": signal.get("cont_short_ema_side_ok"),
        "cont_long_ema_side_ok": signal.get("cont_long_ema_side_ok"),
        "cont_primary_reject": signal.get("cont_primary_reject"),
        "slope_atr": signal.get("slope_atr"),
        "slope_delta": signal.get("slope_delta"),
        "slope_lookback_bars": signal.get("slope_lookback_bars"),
        "cont_slope_threshold": signal.get("cont_slope_threshold"),
        "cont_slope_state": signal.get("cont_slope_state"),
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
        "ema_fast_htf": signal.get("ema_fast_htf"),
        "ema200_prev_n": signal.get("ema200_prev_n"),
        "ema200_slope_norm": signal.get("ema200_slope_norm"),
        "consec_above_ema200": signal.get("consec_above_ema200"),
        "consec_below_ema200": signal.get("consec_below_ema200"),
        "consec_higher_close": signal.get("consec_higher_close"),
        "consec_lower_close": signal.get("consec_lower_close"),
        "atr14_htf": signal.get("atr14_htf"),
        "rsi14_htf": signal.get("rsi14_htf"),
        "rsi14_htf_prev": signal.get("rsi14_htf_prev"),
        "ema200_prev_n": signal.get("ema200_prev_n"),
        "ema200_slope_norm": signal.get("ema200_slope_norm"),
        "consec_above_ema200": signal.get("consec_above_ema200"),
        "consec_below_ema200": signal.get("consec_below_ema200"),
        "consec_higher_close": signal.get("consec_higher_close"),
        "consec_lower_close": signal.get("consec_lower_close"),
        "pullback_atr_long": signal.get("pullback_atr_long"),
        "pullback_atr_short": signal.get("pullback_atr_short"),
        "pullback_signal_side": signal.get("pullback_signal_side"),
        "pullback_bars_since_signal": signal.get("pullback_bars_since_signal"),
        "pullback_prev_window_ok": signal.get("pullback_prev_window_ok"),
        "pullback_current_dist_ok": signal.get("pullback_current_dist_ok"),
        "pullback_min_bars_ok": signal.get("pullback_min_bars_ok"),
        "pullback_reclaim_ok": signal.get("pullback_reclaim_ok"),
        "pullback_direction_confirm_ok": signal.get("pullback_direction_confirm_ok"),
        "pullback_body_ok": signal.get("pullback_body_ok"),
        "pullback_persistence_ok": signal.get("pullback_persistence_ok"),
        "pullback_confirmation_ready": signal.get("pullback_confirmation_ready"),
        "pullback_lifecycle_state": signal.get("pullback_lifecycle_state"),
        "pullback_invalidation_stage": signal.get("pullback_invalidation_stage"),
        "reclaim_long": signal.get("reclaim_long"),
        "reclaim_short": signal.get("reclaim_short"),
        "prev_reclaim_long": signal.get("prev_reclaim_long"),
        "prev_reclaim_short": signal.get("prev_reclaim_short"),
        "reclaim_level_used": signal.get("reclaim_level_used"),
        "effective_tolerance": signal.get("effective_tolerance"),
        "distance_to_reclaim": signal.get("distance_to_reclaim"),
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
        "wick_ratio": signal.get("wick_ratio"),
        "bb_width_atr": signal.get("bb_width_atr"),
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
        "predictive_bias": signal.get("predictive_bias") or predictive.get("predictive_bias"),
        "predictive_state": signal.get("predictive_state") or predictive.get("predictive_state"),
        "confidence_tier": signal.get("confidence_tier") or predictive.get("confidence_tier"),
        "trigger_candidates": signal.get("trigger_candidates") or predictive.get("trigger_candidates") or [],
        "invalidation_reasons": signal.get("invalidation_reasons") or predictive.get("invalidation_reasons") or [],
        "market_state_prev": signal.get("market_state_prev") or predictive.get("market_state_prev"),
        "market_state_next": signal.get("market_state_next") or predictive.get("market_state_next"),
        "transition_name": signal.get("transition_name") or predictive.get("transition_name"),
        "event_classification": signal.get("event_classification") or predictive.get("event_classification"),
        "confirmation_quality": signal.get("confirmation_quality") or validation.get("confirmation_quality"),
        "supporting_strategies": signal.get("supporting_strategies") or validation.get("supporting_strategies") or [],
        "opposing_strategies": signal.get("opposing_strategies") or validation.get("opposing_strategies") or [],
        "validator_reject_map": signal.get("validator_reject_map") or validation.get("validator_reject_map") or {},
        "execution_size_multiplier": signal.get("execution_size_multiplier") or execution_profile.get("size_multiplier"),
        "event_hard_block": signal.get("event_hard_block") or execution_profile.get("event_hard_block"),
        "analytics_pending_count": analytics.get("pending_count"),
        "analytics_label_horizon": analytics.get("label_horizon_candles"),
        "latest_finalized_label": analytics.get("latest_finalized_label"),
        "finalized_labels": analytics.get("finalized_labels") or [],
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
    log_dict = {
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
        "execution_decision": explain_fields.get("execution_decision"),
        "entry_mode": explain_fields.get("entry_mode"),
        "trend_strength": explain_fields.get("trend_strength"),
        "trend_stable_long": explain_fields.get("trend_stable_long"),
        "trend_stable_short": explain_fields.get("trend_stable_short"),
        "stability_score": explain_fields.get("stability_score"),
        "stable_ok": explain_fields.get("stable_ok"),
        "stable_soft": explain_fields.get("stable_soft"),
        "stable_block": explain_fields.get("stable_block"),
        "stable_block_reason": explain_fields.get("stable_block_reason"),
        "stability_mode_used": explain_fields.get("stability_mode_used"),
        "adaptive_soft_stability": explain_fields.get("adaptive_soft_stability"),
        "continuation_confirmation_type": explain_fields.get("continuation_confirmation_type"),
        "anti_reversal_block": explain_fields.get("anti_reversal_block"),
        "anti_reversal_reason": explain_fields.get("anti_reversal_reason"),
        "pending_entry_status": explain_fields.get("pending_entry_status"),
        "event_detected": explain_fields.get("event_detected"),
        "event_block": explain_fields.get("event_block"),
        "event_cooldown_remaining": explain_fields.get("event_cooldown_remaining"),
        "trend_accel_long_ok": explain_fields.get("trend_accel_long_ok"),
        "trend_accel_short_ok": explain_fields.get("trend_accel_short_ok"),
        "squeeze_break_long_ok": explain_fields.get("squeeze_break_long_ok"),
        "squeeze_break_short_ok": explain_fields.get("squeeze_break_short_ok"),
        "ev_gate_enabled": explain_fields.get("ev_gate_enabled"),
        "ev_value": explain_fields.get("ev_value"),
        "ev_p": explain_fields.get("ev_p"),
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
        "ema_fast_htf": explain_fields.get("ema_fast_htf"),
        "ema200_prev_n": explain_fields.get("ema200_prev_n"),
        "ema200_slope_norm": explain_fields.get("ema200_slope_norm"),
        "consec_above_ema200": explain_fields.get("consec_above_ema200"),
        "consec_below_ema200": explain_fields.get("consec_below_ema200"),
        "consec_higher_close": explain_fields.get("consec_higher_close"),
        "consec_lower_close": explain_fields.get("consec_lower_close"),
        "atr14_htf": explain_fields.get("atr14_htf"),
        "rsi14_htf": explain_fields.get("rsi14_htf"),
        "rsi14_htf_prev": explain_fields.get("rsi14_htf_prev"),
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
        "wick_ratio": explain_fields.get("wick_ratio"),
        "bb_width_atr": explain_fields.get("bb_width_atr"),
        "reentry_long": explain_fields.get("reentry_long"),
        "reentry_short": explain_fields.get("reentry_short"),
        "breakout_long": explain_fields.get("breakout_long"),
        "breakout_short": explain_fields.get("breakout_short"),
        "eligible_strategies": explain_fields.get("eligible_strategies"),
        "selected_strategy": explain_fields.get("selected_strategy"),
        "strategy_block_reason": explain_fields.get("strategy_block_reason"),
        "router_debug": explain_fields.get("router_debug"),
        "hold_reason_summary": explain_fields.get("hold_reason_summary"),
        "regime_explain": explain_fields.get("regime_explain"),
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
        "reclaim_level_used": explain_fields.get("reclaim_level_used"),
        "effective_tolerance": explain_fields.get("effective_tolerance"),
        "distance_to_reclaim": explain_fields.get("distance_to_reclaim"),
        "predictive_bias": explain_fields.get("predictive_bias"),
        "predictive_state": explain_fields.get("predictive_state"),
        "confidence_tier": explain_fields.get("confidence_tier"),
        "trigger_candidates": explain_fields.get("trigger_candidates"),
        "invalidation_reasons": explain_fields.get("invalidation_reasons"),
        "market_state_prev": explain_fields.get("market_state_prev"),
        "market_state_next": explain_fields.get("market_state_next"),
        "transition_name": explain_fields.get("transition_name"),
        "event_classification": explain_fields.get("event_classification"),
        "confirmation_quality": explain_fields.get("confirmation_quality"),
        "supporting_strategies": explain_fields.get("supporting_strategies"),
        "opposing_strategies": explain_fields.get("opposing_strategies"),
        "validator_reject_map": explain_fields.get("validator_reject_map"),
        "execution_size_multiplier": explain_fields.get("execution_size_multiplier"),
        "event_hard_block": explain_fields.get("event_hard_block"),
        "analytics_pending_count": explain_fields.get("analytics_pending_count"),
        "analytics_label_horizon": explain_fields.get("analytics_label_horizon"),
        "latest_finalized_label": explain_fields.get("latest_finalized_label"),
        "finalized_labels": explain_fields.get("finalized_labels"),
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
    # Add explain fields if present
    if explain_fields.get("explain_pullback"):
        log_dict["explain_pullback"] = explain_fields.get("explain_pullback")
    if explain_fields.get("explain_range"):
        log_dict["explain_range"] = explain_fields.get("explain_range")
    if explain_fields.get("explain_anti_reversal"):
        log_dict["explain_anti_reversal"] = explain_fields.get("explain_anti_reversal")
    if explain_fields.get("explain_continuation"):
        log_dict["explain_continuation"] = explain_fields.get("explain_continuation")
    return log_dict


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
