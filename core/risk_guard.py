# -*- coding: utf-8 -*-
"""
core.risk_guard
Backwards-compatible risk guard + kill-switch.

Keeps your existing API:
- dataclasses AccountState, OrderPlan
- functions: daily_limits_blocked, position_cap_ok, compute_sl_tp, guard_and_enrich

Adds kill-switch + health logging (no new modules/files):
- is_killed() -> bool
- kill(reason, context=None) -> None
- clear_kill() -> None
- log_event(kind, payload) -> None

Adds a general evaluator for guard-loop compatibility:
- evaluate(metrics: dict) -> {"ok": bool, "violations": [...]}

Artifacts:
- run/TRADE_KILLED.flag
- logs/health/YYYY-MM-DD/health.jsonl
"""
from __future__ import annotations

import os
import json
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.config import settings

# ---------- Paths & logs ----------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _run_dir() -> Path:
    return PROJECT_ROOT / "run"

def _flag_file() -> Path:
    return _run_dir() / "TRADE_KILLED.flag"

def _health_log_path() -> Path:
    day = _dt.date.today().isoformat()
    log_dir = settings.get_str("LOG_DIR", "logs")
    return Path(log_dir) / "health" / day / "health.jsonl"

def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

# ---------- Your existing API (kept intact) ----------
@dataclass
class AccountState:
    equity_usd: float
    day_pnl_usd: float  # signed PnL for the current UTC day
    max_drawdown_pct_day: float  # 0..100

@dataclass
class OrderPlan:
    symbol: str
    side: str              # 'LONG' or 'SHORT'
    entry_price: float
    qty: float             # base asset qty (e.g., BTC)
    notional_usd: float    # entry_price * qty
    atr: Optional[float] = None  # in price units
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    reason: str = ""

def _get_float(name: str, default: float) -> float:
    try:
        v = settings.get_str(name, str(default))
        s = str(v).strip().strip('"').strip("'").replace("_","").replace(",","")
        if s.endswith("%"):
            s = s[:-1]
        return float(s)
    except Exception:
        return float(default)

def _get_int(name: str, default: int) -> int:
    try:
        v = settings.get_str(name, str(default))
        s = str(v).strip().strip('"').strip("'").replace("_","").replace(",","")
        return int(float(s))
    except Exception:
        return int(default)

# ---------- Kill-switch API ----------
def _now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()

def is_killed() -> bool:
    """True if the kill flag is present (block new orders)."""
    return _flag_file().exists()

def kill(reason: str, context: Optional[Dict[str, Any]] = None) -> None:
    """Create/overwrite kill flag and log an event (idempotent)."""
    flag = _flag_file()
    try:
        _ensure_parent(flag)
        flag.write_text(reason, encoding="utf-8")
    except Exception:
        _ensure_parent(flag)
        flag.touch(exist_ok=True)
    log_event("kill", {"reason": reason, "context": context or {}})

def clear_kill() -> None:
    flag = _flag_file()
    if flag.exists():
        flag.unlink()
        log_event("resume", {"reason": "manual clear"})

def log_event(kind: str, payload: Dict[str, Any]) -> None:
    evt = {"ts": _now_utc(), "kind": kind, **(payload or {})}
    out = _health_log_path()
    _ensure_parent(out)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")

# ---------- Existing checks (kept) ----------
def daily_limits_blocked(state: AccountState) -> bool:
    max_dd_pct = _get_float("RISK_MAX_DD_PCT_DAY", 3.0)
    max_loss_usd = _get_float("RISK_MAX_LOSS_USD_DAY", 10.0)
    if state.max_drawdown_pct_day >= max_dd_pct:
        return True
    if -state.day_pnl_usd >= max_loss_usd:
        return True
    return False

def position_cap_ok(notional_usd: float, equity_usd: float) -> bool:
    max_pos_usd = _get_float("RISK_MAX_POS_USD", 100.0)
    min_equity = _get_float("RISK_MIN_EQUITY_USD", 10.0)
    if equity_usd < min_equity:
        return False
    return notional_usd <= max_pos_usd

def compute_sl_tp(entry_price: float, side: str, atr: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    mode = settings.get_str("SL_MODE", "atr")  # 'atr' or 'none' or 'percent'
    tp_r = _get_float("TP_R_MULT", 1.5)
    sl_atr = _get_float("SL_ATR_MULT", 1.5)
    sl_pct = _get_float("SL_PCT", 0.5)  # if SL_MODE=percent
    tp_pct = _get_float("TP_PCT", 1.0)

    if mode.lower() == "none":
        return None, None

    if mode.lower() == "percent":
        sl = entry_price * (1 - sl_pct/100.0) if side == "LONG" else entry_price * (1 + sl_pct/100.0)
        tp = entry_price * (1 + tp_pct/100.0) if side == "LONG" else entry_price * (1 - tp_pct/100.0)
        return sl, tp

    # ATR mode
    if atr is None or atr <= 0:
        return None, None
    sl = entry_price - sl_atr * atr if side == "LONG" else entry_price + sl_atr * atr
    tp = entry_price + tp_r * sl_atr * atr if side == "LONG" else entry_price - tp_r * sl_atr * atr
    return sl, tp

def guard_and_enrich(plan: OrderPlan, state: AccountState) -> tuple[bool, OrderPlan, str]:
    """Return (allowed, possibly_modified_plan, reason)."""
    if is_killed():
        return False, plan, "Blocked by kill-switch"

    if daily_limits_blocked(state):
        return False, plan, "Blocked by daily limits"
    if not position_cap_ok(plan.notional_usd, state.equity_usd):
        return False, plan, "Blocked by position cap / min equity"

    sl, tp = compute_sl_tp(plan.entry_price, plan.side, plan.atr)
    plan.sl_price = sl
    plan.tp_price = tp
    return True, plan, "OK"

# ---------- General evaluator for guard-loop & DoD ----------
def evaluate(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate risk constraints from generic metrics dict.
    Expected metrics keys (numbers):
      daily_pnl_usd, equity_usd, start_equity_usd,
      open_risk_usd, trades_today, consec_losses
    Uses ENV thresholds:
      RISK_MAX_DD_PCT_DAY, RISK_MAX_LOSS_USD_DAY, RISK_MIN_EQUITY_USD,
      RISK_MAX_OPEN_RISK_USD, RISK_MAX_TRADES_PER_DAY, RISK_MAX_CONSEC_LOSSES
    """
    # --- KILL-SWITCH: миттєвий No-Go, якщо виставлено прапор ---
    if is_killed():
        reason = ""
        try:
            reason = _flag_file().read_text(encoding="utf-8").strip()
        except Exception:
            reason = "kill-switch engaged"
        return {
            "ok": False,
            "violations": [
                {"limit": "kill_switch", "value": 1, "limit_value": 0, "reason": reason}
            ],
        }

    def f(x: Any, d: float = 0.0) -> float:
        try:
            return float(x)
        except Exception:
            return float(d)

    def i(x: Any, d: int = 0) -> int:
        try:
            return int(float(x))
        except Exception:
            return int(d)

    # thresholds from env
    lim_dd_pct = _get_float("RISK_MAX_DD_PCT_DAY", 3.0)
    lim_loss = _get_float("RISK_MAX_LOSS_USD_DAY", 10.0)
    lim_min_equity = _get_float("RISK_MIN_EQUITY_USD", 10.0)
    lim_open_risk = _get_float("RISK_MAX_OPEN_RISK_USD", 100.0)
    lim_trades = _get_int("RISK_MAX_TRADES_PER_DAY", 1000)
    lim_consec = _get_int("RISK_MAX_CONSEC_LOSSES", 999999)

    # inputs
    daily_pnl = f(metrics.get("daily_pnl_usd"), 0.0)
    equity = f(metrics.get("equity_usd"), 0.0)
    start_equity = f(metrics.get("start_equity_usd"), max(equity, 1.0))
    open_risk = f(metrics.get("open_risk_usd"), 0.0)
    trades_today = i(metrics.get("trades_today"), 0)
    consec_losses = i(metrics.get("consec_losses"), 0)

    violations: List[Dict[str, Any]] = []

    # daily dd %
    dd_pct = 0.0
    if start_equity > 0:
        dd_pct = max(0.0, -daily_pnl) / start_equity * 100.0
    if dd_pct > lim_dd_pct:
        violations.append({"limit": "max_dd_pct_day", "value": dd_pct, "limit_value": lim_dd_pct})

    # daily loss USD
    if -daily_pnl > lim_loss:
        violations.append({"limit": "max_loss_usd_day", "value": -daily_pnl, "limit_value": lim_loss})

    # min equity
    if equity < lim_min_equity:
        violations.append({"limit": "min_equity_usd", "value": equity, "limit_value": lim_min_equity})

    # open risk
    if open_risk > lim_open_risk:
        violations.append({"limit": "max_open_risk_usd", "value": open_risk, "limit_value": lim_open_risk})

    # trades per day
    if trades_today > lim_trades:
        violations.append({"limit": "max_trades_day", "value": trades_today, "limit_value": lim_trades})

    # consecutive losses
    if consec_losses > lim_consec:
        violations.append({"limit": "max_consec_losses", "value": consec_losses, "limit_value": lim_consec})

    ok = len(violations) == 0
    return {"ok": ok, "violations": violations}


# ---- explicit exports (safe) ----
try:
    __all__  # type: ignore
except NameError:
    __all__ = []  # created if missing

_exports = {
    "AccountState", "OrderPlan",
    "daily_limits_blocked", "position_cap_ok", "compute_sl_tp", "guard_and_enrich",
    "evaluate",
}
for _name in ("RiskManager", "_read_last_equity", "is_killed", "kill", "clear_kill", "log_event"):
    if _name not in _exports:
        _exports.add(_name)

__all__ = sorted(_exports)
