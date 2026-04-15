#!/usr/bin/env python3
"""
tools/log_analyzer.py — Standalone diagnostic CLI for the BTCUSDT trading bot.

Reads structured JSON log lines from runtime.log / session logs and answers:
  WHY is the bot not trading?

Usage:
  python tools/log_analyzer.py --log logs/runtime.log
  python tools/log_analyzer.py --log logs/runtime.log --last 200
  python tools/log_analyzer.py --log logs/runtime.log --since 2026-03-19
  python tools/log_analyzer.py --log logs/sessions/ --last 500 --export reports/
  python tools/log_analyzer.py --log logs/runtime.log --only-holds
  python tools/log_analyzer.py --log logs/runtime.log --only-trades
  python tools/log_analyzer.py --log logs/runtime.log --verbose

Arguments:
  --log         path to a log file, directory of log files, or glob pattern
  --last N      analyze only the last N decision candles
  --since DATE  analyze only candles from this date onward (YYYY-MM-DD)
  --only-holds  filter to HOLD decisions only
  --only-trades filter to non-HOLD decisions only
  --export DIR  write CSV + JSON report files to this directory
  --verbose     print every candle row, not just summary

No dependencies beyond Python stdlib + what is already in requirements.txt.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BLOCKER_FAMILIES: Dict[str, str] = {
    "P": "Pullback",
    "C": "Continuation",
    "M": "Meta",
    "B": "Breakout",
    "T": "Trend",
    "R": "Range",
    "X": "Anti-Reversal",
}

# Metric name → blocker prefix that governs it → config key hint
THRESHOLD_HINTS: Dict[str, Tuple[str, str]] = {
    # blocker_prefix: (metric_field_in_decision_candle, config_key_hint)
    "P:dist50":      ("pullback_atr_long",       "PULLBACK_REENTRY_DIST50_MAX"),
    "P:reclaim":     ("reclaim_long",             "PULLBACK_REENTRY_RECLAIM_REQUIRED"),
    "P:pullback_atr":("pullback_atr_long",        "PULLBACK_REENTRY_ATR_MAX"),
    "C:slope":       ("slope_atr",                "CONT_SLOPE_ATR_MIN"),
    "C:rsi":         ("rsi14_5m",                 "CONT_RSI_MAX"),
    "B:break_delta": ("break_delta_atr",          "BREAKOUT_BREAK_DELTA_ATR_MIN"),
    "M:spread":      ("spread_pct",               "MAX_SPREAD_PCT"),
    "M:atr":         ("atr",                      "MIN_ATR"),
    "M:equity":      ("equity",                   "MIN_EQUITY"),
}


# ---------------------------------------------------------------------------
# Log line parsing
# ---------------------------------------------------------------------------
# Matches: 2026-03-19 10:00:00,123 INFO SomeName: {...}
#      or: [2026-03-19 10:00:00] INFO SomeName: {...}
_LINE_RE = re.compile(
    r'^[\["]?(20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d[^\]"]*?)[\]"]?\s+'
    r"(?:INFO|DEBUG|WARNING|ERROR|CRITICAL)\s+"
    r"[^:]+:\s+(\{.+)$"
)
_TS_RE = re.compile(r"(20\d\d-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)")


def _extract_json(line: str) -> Optional[Dict[str, Any]]:
    """Extract JSON object from a log line. Returns None on failure."""
    # Fast path: find the first '{' after the prefix
    idx = line.find("{")
    if idx == -1:
        return None
    try:
        return json.loads(line[idx:].rstrip())
    except json.JSONDecodeError:
        return None


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    # Handle ISO format with Z or +00:00
    ts_str = ts_str.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S,%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str[:26], fmt)
        except ValueError:
            pass
    m = _TS_RE.search(ts_str)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


def _ts_from_log_line(line: str) -> Optional[str]:
    """Extract timestamp string from the start of a log line."""
    m = _LINE_RE.match(line)
    if m:
        return m.group(1)
    # Fall back: grab first datetime-like token
    m2 = _TS_RE.search(line[:40])
    return m2.group(0) if m2 else None


def _ts_epoch_to_dt(ts_epoch: Any) -> Optional[datetime]:
    """Convert unix ms or seconds to datetime."""
    if ts_epoch is None:
        return None
    try:
        v = float(ts_epoch)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def _discover_files(log_path: str) -> List[Path]:
    p = Path(log_path)
    if p.is_file():
        return [p]
    if p.is_dir():
        files = sorted(p.glob("**/*.log")) + sorted(p.glob("**/*.jsonl"))
        return files
    # Try glob pattern
    parent = p.parent if p.parent != p else Path(".")
    pattern = p.name
    files = sorted(parent.glob(pattern))
    if files:
        return [f for f in files if f.is_file()]
    return []


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------
class LogRecord:
    """One parsed log event."""
    __slots__ = ("event", "line_ts", "data")

    def __init__(self, event: str, line_ts: Optional[str], data: Dict[str, Any]) -> None:
        self.event = event
        self.line_ts = line_ts
        self.data = data


def stream_records(files: List[Path]) -> Iterator[Tuple[LogRecord, int]]:
    """Yield (LogRecord, bad_line_count) per file, streaming line by line."""
    bad = 0
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.rstrip("\n\r")
                    if not raw or raw[0] not in ("{", "[", "2"):
                        # Quick pre-filter: skip obviously non-log lines
                        if "{" not in raw:
                            continue
                    obj = _extract_json(raw)
                    if obj is None:
                        bad += 1
                        continue
                    event = obj.get("event")
                    if not event:
                        continue
                    ts_str = _ts_from_log_line(raw)
                    yield LogRecord(event, ts_str, obj), bad
                    bad = 0  # report bad count per record gap; reset after yield
        except OSError as exc:
            print(f"  [WARN] Cannot read {fpath}: {exc}", file=sys.stderr)
    yield LogRecord("__eof__", None, {}), bad


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
class AnalysisData:
    """Accumulates all parsed events for report generation."""

    def __init__(self) -> None:
        # decision_candle rows (dicts)
        self.candles: List[Dict[str, Any]] = []
        # decision_clean rows matched by timestamp_closed
        self.clean_by_ts: Dict[Any, Dict[str, Any]] = {}
        # kill_switch_active events
        self.kill_events: List[Dict[str, Any]] = []
        # startup_reconcile_failed events (from kill_switch_triggered with that reason)
        self.startup_failures: List[Dict[str, Any]] = []
        # tick_summary events
        self.tick_events: List[Dict[str, Any]] = []
        # bad line count
        self.bad_lines: int = 0
        # files processed
        self.files_seen: int = 0


def collect(
    files: List[Path],
    *,
    since_dt: Optional[datetime] = None,
    only_holds: bool = False,
    only_trades: bool = False,
) -> AnalysisData:
    data = AnalysisData()
    data.files_seen = len(files)
    pending_bad = 0

    for record, bad in stream_records(files):
        pending_bad += bad
        ev = record.event

        if ev == "__eof__":
            data.bad_lines += pending_bad
            pending_bad = 0
            continue

        if ev == "decision_candle":
            row = record.data
            # Apply date filter
            if since_dt is not None:
                ts_closed = row.get("timestamp_closed")
                dt = _ts_epoch_to_dt(ts_closed)
                if dt is None:
                    dt = _parse_ts(record.line_ts)
                if dt is not None and dt < since_dt:
                    continue

            decision = str(row.get("decision") or "HOLD").upper()
            is_hold = (decision == "HOLD")
            if only_holds and not is_hold:
                continue
            if only_trades and is_hold:
                continue

            row["_decision_norm"] = decision
            data.candles.append(row)

        elif ev == "decision_clean":
            ts = record.data.get("timestamp_closed")
            # decision_clean may not have timestamp_closed; keep a small rolling buffer
            key = ts if ts is not None else f"_nots_{len(data.clean_by_ts)}"
            data.clean_by_ts[key] = record.data

        elif ev in ("kill_switch_active", "kill_switch_triggered"):
            data.kill_events.append({**record.data, "_line_ts": record.line_ts})

        elif ev == "startup_stage":
            details = record.data.get("details") or {}
            if "reconcile_failed" in str(details):
                data.startup_failures.append({**record.data, "_line_ts": record.line_ts})

        elif ev == "tick_summary":
            data.tick_events.append(record.data)

    data.bad_lines += pending_bad
    return data


def _apply_last_n(data: AnalysisData, last_n: int) -> None:
    """Trim candles list to the last N entries in-place."""
    if len(data.candles) > last_n:
        data.candles = data.candles[-last_n:]


def _merge_clean(data: AnalysisData) -> None:
    """
    Merge decision_clean fields into candle rows where timestamp_closed matches.
    Falls back to positional matching if timestamps are absent.
    """
    if not data.clean_by_ts:
        return
    ts_keyed = {k: v for k, v in data.clean_by_ts.items() if not str(k).startswith("_nots_")}
    no_ts = [v for k, v in data.clean_by_ts.items() if str(k).startswith("_nots_")]

    for row in data.candles:
        ts = row.get("timestamp_closed")
        if ts is not None and ts in ts_keyed:
            clean = ts_keyed[ts]
            # Prefer clean's blockers / regime data
            for field in ("regime_detected", "regime_used_for_routing", "selected_strategy",
                          "eligible_strategies", "blockers", "blocker_categories",
                          "gating_summary", "stability_mode_used", "main_blocker",
                          "equity", "funds_base", "risk_usd", "qty_after_rounding"):
                if field not in row and field in clean:
                    row[field] = clean[field]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def _fval(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_of_candle(row: Dict[str, Any]) -> str:
    ts = row.get("timestamp_closed")
    dt = _ts_epoch_to_dt(ts)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return "unknown"


def _all_blockers(row: Dict[str, Any]) -> List[str]:
    """Return the deduplicated blocker list for a candle row."""
    # Prefer 'blockers' (from decision_clean merge), fall back to 'reject_reasons'
    bl = row.get("blockers") or row.get("reject_reasons") or []
    if isinstance(bl, str):
        bl = [bl]
    return [str(b) for b in bl if b]


def _blocker_family(code: str) -> str:
    prefix = code.split(":")[0] if ":" in code else "other"
    return BLOCKER_FAMILIES.get(prefix, prefix.upper() or "other")


def _safe_median(vals: List[float]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return statistics.median(clean)


def _safe_mean(vals: List[float]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return statistics.mean(clean)


def _fmt(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


def _bold(s: str) -> str:
    """ANSI bold — graceful fallback on Windows without ANSI support."""
    return f"\033[1m{s}\033[0m"


def _hr(char: str = "─", width: int = 72) -> str:
    return char * width


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def section1_overview(data: AnalysisData) -> Dict[str, Any]:
    candles = data.candles
    total = len(candles)
    holds = sum(1 for c in candles if c.get("_decision_norm", "HOLD") == "HOLD")
    trades = total - holds
    long_c = sum(1 for c in candles if c.get("_decision_norm") in ("OPEN_LONG", "LONG", "TRADE"))
    short_c = sum(1 for c in candles if c.get("_decision_norm") in ("OPEN_SHORT", "SHORT"))
    close_c = sum(1 for c in candles if c.get("_decision_norm") == "CLOSE")
    # "TRADE" without side info counts as a generic trade
    generic_trade = sum(1 for c in candles if c.get("_decision_norm") == "TRADE")

    dates = []
    for c in candles:
        d = _date_of_candle(c)
        if d != "unknown":
            dates.append(d)
    date_first = min(dates) if dates else "n/a"
    date_last = max(dates) if dates else "n/a"

    kill_count = len(data.kill_events)
    startup_fail_count = len(data.startup_failures)

    return {
        "total": total,
        "holds": holds,
        "holds_pct": 100.0 * holds / total if total else 0.0,
        "trades": trades,
        "open_long": long_c,
        "open_short": short_c,
        "close": close_c,
        "generic_trade": generic_trade,
        "kill_events": kill_count,
        "startup_failures": startup_fail_count,
        "date_first": date_first,
        "date_last": date_last,
        "files": data.files_seen,
        "bad_lines": data.bad_lines,
    }


def section2_blockers(candles: List[Dict[str, Any]], total: int) -> Dict[str, Any]:
    counter: Counter = Counter()
    family_counter: Counter = Counter()
    pair_counter: Counter = Counter()

    for row in candles:
        bl = _all_blockers(row)
        seen_in_row: set = set()
        for code in bl:
            counter[code] += 1
            fam = _blocker_family(code)
            family_counter[fam] += 1
            seen_in_row.add(code)
        for a, b in combinations(sorted(seen_in_row), 2):
            pair_counter[(a, b)] += 1

    ranked = counter.most_common()
    top_pairs = pair_counter.most_common(5)
    dominant = ranked[0][0] if ranked else None

    return {
        "ranked": ranked,          # list of (code, count)
        "family_counts": dict(family_counter.most_common()),
        "top_pairs": top_pairs,     # list of ((a,b), count)
        "dominant": dominant,
        "total_candles": total,
    }


def section3_regime(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_regime: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candles:
        regime = str(row.get("regime_detected") or row.get("regime") or "UNKNOWN")
        by_regime[regime].append(row)

    result = {}
    for regime, rows in sorted(by_regime.items(), key=lambda x: -len(x[1])):
        total_r = len(rows)
        holds_r = sum(1 for r in rows if r.get("_decision_norm", "HOLD") == "HOLD")
        bl_counter: Counter = Counter()
        for r in rows:
            for code in _all_blockers(r):
                bl_counter[code] += 1
        top3 = bl_counter.most_common(3)
        result[regime] = {
            "count": total_r,
            "holds": holds_r,
            "hold_rate": 100.0 * holds_r / total_r if total_r else 0.0,
            "top_blockers": top3,
        }
    return result


def section4_strategies(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    eligible_counter: Counter = Counter()
    selected_counter: Counter = Counter()
    gating_counter: Counter = Counter()
    stability_hard = stability_soft = stability_block = 0

    for row in candles:
        eligible = row.get("eligible_strategies") or []
        if isinstance(eligible, str):
            eligible = [eligible]
        for s in eligible:
            eligible_counter[str(s)] += 1

        sel = str(row.get("selected_strategy") or "NONE")
        selected_counter[sel] += 1

        if sel == "NONE":
            gating = row.get("gating_summary") or row.get("main_blocker") or "unknown"
            gating_counter[str(gating)] += 1

        # Stability
        if row.get("stable_ok") is True:
            pass
        if row.get("stable_soft") is True:
            stability_soft += 1
        if row.get("stable_block") is True:
            stability_block += 1
        elif row.get("stable_ok") is True:
            stability_hard += 1

    return {
        "eligible_counts": dict(eligible_counter.most_common()),
        "selected_counts": dict(selected_counter.most_common()),
        "gating_when_none": dict(gating_counter.most_common(10)),
        "stability_hard": stability_hard,
        "stability_soft": stability_soft,
        "stability_block": stability_block,
    }


def section5_market_conditions(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    holds = [c for c in candles if c.get("_decision_norm", "HOLD") == "HOLD"]
    trades_c = [c for c in candles if c.get("_decision_norm") != "HOLD"]

    def _stats(rows: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
        vals = [_fval(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
        return {
            "n": len(vals),
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
            "min": min(vals),
            "max": max(vals),
        }

    fields = ["spread_pct", "atr", "rsi14_5m", "volume_ratio_5m", "equity"]
    hold_stats = {f: _stats(holds, f) for f in fields}
    trade_stats = {f: _stats(trades_c, f) for f in fields}

    # Detect if spread is near threshold
    spread_vals = [_fval(c.get("spread_pct")) for c in holds]
    spread_vals = [v for v in spread_vals if v is not None]
    spread_concern = None
    if spread_vals:
        med_spread = statistics.median(spread_vals)
        # Common default is 0.05% but we flag if median > 0.03
        if med_spread > 0.03:
            spread_concern = med_spread

    return {
        "hold_stats": hold_stats,
        "trade_stats": trade_stats,
        "spread_concern": spread_concern,
        "hold_count": len(holds),
        "trade_count": len(trades_c),
    }


def section6_kill_switch(data: AnalysisData) -> Dict[str, Any]:
    kill_reasons: Counter = Counter()
    kill_list = []
    for ev in data.kill_events:
        reason = ev.get("reason") or ev.get("kill_switch_reason") or "unknown"
        kill_reasons[str(reason)] += 1
        kill_list.append({
            "ts": ev.get("_line_ts") or ev.get("timestamp"),
            "reason": reason,
        })

    startup_list = []
    for ev in data.startup_failures:
        startup_list.append({
            "ts": ev.get("_line_ts"),
            "details": ev.get("details"),
        })

    return {
        "kill_count": len(data.kill_events),
        "kill_reasons": dict(kill_reasons.most_common()),
        "kill_list": kill_list,
        "startup_failure_count": len(data.startup_failures),
        "startup_list": startup_list,
        "kill_dominated": len(data.kill_events) > 0 and len(data.candles) == 0,
    }


def section7_timeline(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_day: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "candles": 0, "holds": 0, "long": 0, "short": 0, "close": 0,
        "blocker_counter": Counter(),
    })

    for row in candles:
        d = _date_of_candle(row)
        day = by_day[d]
        day["candles"] += 1
        dec = row.get("_decision_norm", "HOLD")
        if dec == "HOLD":
            day["holds"] += 1
        elif dec in ("LONG", "OPEN_LONG", "TRADE"):
            day["long"] += 1
        elif dec in ("SHORT", "OPEN_SHORT"):
            day["short"] += 1
        elif dec == "CLOSE":
            day["close"] += 1
        for code in _all_blockers(row):
            day["blocker_counter"][code] += 1

    rows_out = []
    for date_str in sorted(by_day.keys()):
        d = by_day[date_str]
        top_bl = d["blocker_counter"].most_common(1)
        rows_out.append({
            "date": date_str,
            "candles": d["candles"],
            "holds": d["holds"],
            "long": d["long"],
            "short": d["short"],
            "close": d["close"],
            "top_blocker": top_bl[0][0] if top_bl else "",
        })

    return {"days": rows_out}


def section8_diagnosis(
    s1: Dict, s2: Dict, s3: Dict, s4: Dict, s5: Dict, s6: Dict
) -> Dict[str, Any]:
    total = s1["total"]
    holds = s1["holds"]
    trades = s1["trades"]
    kill_count = s1["kill_events"]
    startup_fails = s1["startup_failures"]
    hold_pct = s1["holds_pct"]

    findings: List[str] = []
    recommendations: List[str] = []

    no_trades = trades == 0

    # Primary cause detection
    primary_cause = "unknown"
    if kill_count > 0 and total == 0:
        primary_cause = "kill_switch_always_active"
        findings.append(
            f"Kill switch is the primary issue: {kill_count} activation(s) observed, "
            f"bot could not process any candles."
        )
    elif startup_fails > 0 and total == 0:
        primary_cause = "startup_reconcile_failure"
        findings.append(
            f"Startup reconcile failure: bot crashed on startup {startup_fails} time(s) "
            f"before processing any candles."
        )
    elif total > 0 and hold_pct >= 95.0:
        ranked = s2["ranked"]
        dominant = s2["dominant"]
        if dominant:
            dom_count = dict(ranked)[dominant]
            dom_pct = 100.0 * dom_count / total
            primary_cause = f"blocker:{dominant}"
            fam = _blocker_family(dominant)
            findings.append(
                f"Blocker '{dominant}' ({fam}) fires on {dom_pct:.1f}% of candles "
                f"— this is the dominant HOLD reason."
            )
        else:
            primary_cause = "no_signal"
            findings.append("Bot is processing candles but no valid signals are generated.")
    elif total > 0 and no_trades:
        primary_cause = "all_holds"
        ranked = s2["ranked"]
        if ranked:
            top3 = ranked[:3]
            t3_str = ", ".join(f"'{c}' ({n}x)" for c, n in top3)
            findings.append(f"All {total} candles resulted in HOLD. Top blockers: {t3_str}")
        else:
            findings.append(f"All {total} candles resulted in HOLD. No blocker codes logged.")
    else:
        findings.append(f"Bot is trading: {trades}/{total} candles resulted in a trade action.")

    # Regime analysis
    if s3:
        worst_regime = max(s3.items(), key=lambda x: x[1]["hold_rate"], default=None)
        if worst_regime and worst_regime[1]["hold_rate"] >= 90.0:
            r, info = worst_regime
            findings.append(
                f"Regime '{r}' has {info['hold_rate']:.0f}% HOLD rate "
                f"({info['holds']}/{info['count']} candles)."
            )
            recs_top = [c for c, _ in info["top_blockers"][:2]]
            if recs_top:
                recommendations.append(
                    f"Investigate regime '{r}': top blockers are {recs_top}. "
                    f"Consider loosening thresholds or adding a regime-specific override."
                )

    # Strategy eligibility
    sel = s4["selected_counts"]
    none_sel = sel.get("NONE", 0)
    if total > 0 and none_sel / max(total, 1) > 0.7:
        gating = s4["gating_when_none"]
        top_gate = next(iter(gating), None)
        findings.append(
            f"No strategy selected (NONE) on {_pct(none_sel, total)} of candles. "
            f"Top gating reason: '{top_gate}'."
        )
        if top_gate:
            recommendations.append(
                f"Strategy router returns NONE too often (top gate: '{top_gate}'). "
                f"Review strategy eligibility conditions."
            )

    # Spread concern
    if s5["spread_concern"] is not None:
        med_sp = s5["spread_concern"]
        findings.append(
            f"Median spread at HOLD time is {med_sp:.4f}% — this is elevated. "
            f"If MAX_SPREAD_PCT is set too low, it may be blocking entries."
        )
        recommendations.append(
            f"Check MAX_SPREAD_PCT config: median spread at HOLD = {med_sp:.4f}%. "
            f"Consider raising the threshold slightly."
        )

    # Kill switch
    if kill_count > 0:
        reasons = s6["kill_reasons"]
        top_reason = next(iter(reasons), None)
        findings.append(
            f"Kill switch triggered {kill_count} time(s). "
            f"Top reason: '{top_reason}'."
        )
        recommendations.append(
            f"Kill switch: '{top_reason}' — investigate the root cause. "
            f"For 'startup_reconcile_failed', check if all open exchange positions "
            f"for BTCUSDT have SL/TP orders placed."
        )

    # Blocker threshold hints
    threshold_warnings: List[str] = []
    if total > 0:
        blocker_dict = dict(s2["ranked"])
        for blocker_code, (metric_field, config_key) in THRESHOLD_HINTS.items():
            count = blocker_dict.get(blocker_code, 0)
            bl_pct = 100.0 * count / total
            if bl_pct > 50.0:
                # Compute actual median of metric across candles where this blocker fired
                fired_rows = [
                    c for c in []  # placeholder: we don't have candles ref here
                ]
                threshold_warnings.append(
                    f"WARNING: '{blocker_code}' fires on {bl_pct:.0f}% of candles. "
                    f"Consider reviewing {config_key}."
                )

    # Build summary sentence
    if primary_cause.startswith("blocker:"):
        bl = primary_cause.split(":", 1)[1]
        summary = (
            f"The bot is not trading primarily because blocker '{bl}' "
            f"prevents entries on the vast majority of candles."
        )
    elif primary_cause == "kill_switch_always_active":
        summary = (
            "The bot is not trading because the kill switch is active — "
            "it fires before any candles are processed."
        )
    elif primary_cause == "startup_reconcile_failure":
        summary = (
            "The bot is not trading because it crashes during startup reconciliation "
            "before any candle is processed."
        )
    elif primary_cause == "all_holds" or primary_cause == "no_signal":
        top_bl = s2["ranked"][:1]
        bl_str = f" (top: '{top_bl[0][0]}')" if top_bl else ""
        summary = (
            f"The bot is not trading: {total} candles processed, "
            f"all resulted in HOLD{bl_str}."
        )
    else:
        summary = "The bot appears to be trading normally."

    return {
        "summary": summary,
        "primary_cause": primary_cause,
        "findings": findings,
        "recommendations": recommendations,
        "threshold_warnings": threshold_warnings,
        "no_trades": no_trades,
    }


def _threshold_calibration(
    candles: List[Dict[str, Any]], s2_ranked: List[Tuple[str, int]], total: int
) -> List[str]:
    """
    For blockers firing >50% of candles, compute actual median of the relevant metric.
    Returns a list of warning strings.
    """
    if total == 0:
        return []

    warnings: List[str] = []
    blocker_dict = dict(s2_ranked)

    for blocker_code, (metric_field, config_key) in THRESHOLD_HINTS.items():
        count = blocker_dict.get(blocker_code, 0)
        bl_pct = 100.0 * count / total
        if bl_pct <= 50.0:
            continue

        # Collect metric values on rows where this blocker fired
        fired_vals: List[float] = []
        all_vals: List[float] = []

        for c in candles:
            v = _fval(c.get(metric_field))
            if v is not None:
                all_vals.append(v)
                if blocker_code in _all_blockers(c):
                    fired_vals.append(v)

        if fired_vals:
            med = statistics.median(fired_vals)
            warn = (
                f"WARNING: '{blocker_code}' fires on {bl_pct:.0f}% of candles. "
                f"Median {metric_field}={med:.4f} when blocked. "
                f"Consider reviewing threshold {config_key}."
            )
            if all_vals:
                global_med = statistics.median(all_vals)
                warn += f" (global median {metric_field}={global_med:.4f})"
            warnings.append(warn)

    return warnings


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(
    s1: Dict, s2: Dict, s3: Dict, s4: Dict, s5: Dict, s6: Dict, s7: Dict, s8: Dict,
    *,
    verbose: bool = False,
    candles: Optional[List[Dict]] = None,
    threshold_warnings: Optional[List[str]] = None,
) -> None:
    W = 72

    def header(title: str) -> None:
        print()
        print(_hr("═", W))
        print(f"  {title}")
        print(_hr("═", W))

    def sub(title: str) -> None:
        print(f"\n  {_bold('─── ' + title)}")

    # ── Banner ──────────────────────────────────────────────────────────────
    if s8["no_trades"]:
        print()
        print("!" * W)
        print("!  NO TRADES DETECTED IN THIS LOG — bot has never opened a position  !")
        print("!" * W)

    # ── Section 1 ───────────────────────────────────────────────────────────
    header("SECTION 1 — OVERVIEW")
    total = s1["total"]
    print(f"  Files analyzed   : {s1['files']}")
    print(f"  Date range       : {s1['date_first']}  →  {s1['date_last']}")
    print(f"  Total candles    : {total}")
    print(f"  HOLD             : {s1['holds']}  ({_pct(s1['holds'], total)})")
    print(f"  OPEN_LONG        : {s1['open_long']}")
    print(f"  OPEN_SHORT       : {s1['open_short']}")
    print(f"  CLOSE            : {s1['close']}")
    if s1["generic_trade"]:
        print(f"  TRADE (generic)  : {s1['generic_trade']}")
    print(f"  Kill sw. events  : {s1['kill_events']}")
    print(f"  Startup failures : {s1['startup_failures']}")
    if s1["bad_lines"]:
        print(f"  Malformed lines  : {s1['bad_lines']} (skipped)")

    # ── Section 2 ───────────────────────────────────────────────────────────
    header("SECTION 2 — TOP BLOCKERS")
    ranked = s2["ranked"]
    dominant = s2["dominant"]
    if not ranked:
        print("  No blockers logged.")
    else:
        print(f"  {'Code':<35} {'Count':>7}  {'Of candles':>10}  Family")
        print(f"  {_hr('-', 68)}")
        for i, (code, cnt) in enumerate(ranked[:20]):
            fam = _blocker_family(code)
            pct_str = _pct(cnt, total)
            mark = " ◄ DOMINANT" if code == dominant and i == 0 else ""
            line = f"  {code:<35} {cnt:>7}  {pct_str:>10}  {fam}{mark}"
            if i == 0:
                print(_bold(line))
            else:
                print(line)
        if len(ranked) > 20:
            print(f"  … and {len(ranked) - 20} more blocker codes")

        sub("Blocker Families")
        for fam, cnt in sorted(s2["family_counts"].items(), key=lambda x: -x[1]):
            print(f"    {fam:<20} {cnt:>6}  ({_pct(cnt, total)})")

        sub("Top Co-Occurrence Pairs")
        if s2["top_pairs"]:
            for (a, b), cnt in s2["top_pairs"]:
                print(f"    {a}  +  {b}  →  {cnt}x")
        else:
            print("    (none)")

    # Threshold calibration warnings
    if threshold_warnings:
        print()
        for w in threshold_warnings:
            print(f"  {_bold(w)}")

    # ── Section 3 ───────────────────────────────────────────────────────────
    header("SECTION 3 — REGIME BREAKDOWN")
    if not s3:
        print("  No regime data logged.")
    else:
        print(f"  {'Regime':<28} {'Candles':>8}  {'Hold%':>7}  Top-3 blockers")
        print(f"  {_hr('-', 68)}")
        for regime, info in s3.items():
            top3_str = ", ".join(c for c, _ in info["top_blockers"]) or "—"
            print(
                f"  {regime:<28} {info['count']:>8}  {info['hold_rate']:>6.1f}%  {top3_str}"
            )

    # ── Section 4 ───────────────────────────────────────────────────────────
    header("SECTION 4 — STRATEGY ELIGIBILITY")
    sub("Eligible strategy appearances")
    if s4["eligible_counts"]:
        for strat, cnt in sorted(s4["eligible_counts"].items(), key=lambda x: -x[1]):
            print(f"    {strat:<30} {cnt:>6}  ({_pct(cnt, total)})")
    else:
        print("    (no eligible_strategies logged)")

    sub("Selected strategy distribution")
    for strat, cnt in sorted(s4["selected_counts"].items(), key=lambda x: -x[1]):
        print(f"    {strat:<30} {cnt:>6}  ({_pct(cnt, total)})")

    none_count = s4["selected_counts"].get("NONE", 0)
    if none_count > 0:
        sub("Gating reasons when selected=NONE")
        for gate, cnt in s4["gating_when_none"].items():
            print(f"    {gate:<35} {cnt:>6}  ({_pct(cnt, none_count)} of NONE)")

    sub("Stability gate")
    print(f"    stable_ok (hard gate pass) : {s4['stability_hard']}")
    print(f"    stable_soft (soft gate)    : {s4['stability_soft']}")
    print(f"    stable_block (gated out)   : {s4['stability_block']}")

    # ── Section 5 ───────────────────────────────────────────────────────────
    header("SECTION 5 — MARKET CONDITIONS AT HOLD TIME")
    hstats = s5["hold_stats"]
    tstats = s5["trade_stats"]

    def _row5(field: str, label: str) -> None:
        h = hstats[field]
        t = tstats[field]
        h_med = _fmt(h["median"])
        h_mean = _fmt(h["mean"])
        t_med = _fmt(t["median"]) if t["n"] else "—"
        print(
            f"    {label:<18} hold: median={h_med:>9}  mean={h_mean:>9} "
            f"| trade: median={t_med:>9}"
        )

    print(f"  (HOLD n={s5['hold_count']}, TRADE n={s5['trade_count']})")
    _row5("spread_pct",     "spread_pct")
    _row5("atr",            "atr")
    _row5("rsi14_5m",       "rsi14_5m")
    _row5("volume_ratio_5m","volume_ratio_5m")
    _row5("equity",         "equity")

    if s5["spread_concern"] is not None:
        print(f"\n  [!] Spread concern: median spread at HOLD = {s5['spread_concern']:.4f}%")

    # ── Section 6 ───────────────────────────────────────────────────────────
    header("SECTION 6 — KILL SWITCH & STARTUP")
    ks = s6
    print(f"  Kill switch activations : {ks['kill_count']}")
    if ks["kill_reasons"]:
        for reason, cnt in ks["kill_reasons"].items():
            print(f"    {reason:<45} {cnt}x")
    if ks["kill_list"]:
        print()
        print("  Recent kill events (last 5):")
        for ev in ks["kill_list"][-5:]:
            print(f"    [{ev.get('ts', '?')}]  {ev.get('reason', '?')}")
    print()
    print(f"  Startup reconcile failures: {ks['startup_failure_count']}")
    if ks["startup_list"]:
        for ev in ks["startup_list"][-3:]:
            print(f"    [{ev.get('ts', '?')}]  {ev.get('details', '')}")
    if ks["kill_dominated"]:
        print()
        print("  [!] Kill switch is the PRIMARY reason for zero trades.")

    # ── Section 7 ───────────────────────────────────────────────────────────
    header("SECTION 7 — TIMELINE VIEW")
    days = s7["days"]
    if not days:
        print("  No timeline data (no candles with parseable timestamps).")
    else:
        print(f"  {'Date':<12} {'Candles':>8} {'HOLD':>6} {'LONG':>6} {'SHORT':>6} {'CLOSE':>6}  Top blocker")
        print(f"  {_hr('-', 70)}")
        for d in days:
            bl = d["top_blocker"] or "—"
            print(
                f"  {d['date']:<12} {d['candles']:>8} {d['holds']:>6} "
                f"{d['long']:>6} {d['short']:>6} {d['close']:>6}  {bl}"
            )

    # ── Section 8 ───────────────────────────────────────────────────────────
    header("SECTION 8 — ACTIONABLE DIAGNOSIS")
    print()
    print(f"  {_bold('SUMMARY:')}")
    print(f"  {s8['summary']}")
    print()
    if s8["findings"]:
        print(f"  {_bold('Findings:')}")
        for i, f in enumerate(s8["findings"], 1):
            print(f"  {i}. {f}")
    print()
    if s8["recommendations"]:
        print(f"  {_bold('Top recommendations:')}")
        for i, r in enumerate(s8["recommendations"], 1):
            print(f"  {i}. {r}")
    if threshold_warnings:
        print()
        print(f"  {_bold('Threshold calibration warnings:')}")
        for w in threshold_warnings:
            print(f"  • {w}")
    print()
    print(_hr("═", W))

    # ── Verbose candle table ─────────────────────────────────────────────────
    if verbose and candles:
        print()
        print(_hr("─", W))
        print("  VERBOSE: All candle rows")
        print(_hr("─", W))
        for row in candles:
            ts = row.get("timestamp_closed")
            dt = _ts_epoch_to_dt(ts)
            dt_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "?"
            dec = row.get("_decision_norm", "?")
            regime = row.get("regime_detected") or row.get("regime") or "?"
            bl = _all_blockers(row)
            bl_str = ", ".join(bl[:3]) or "—"
            print(f"  {dt_str}  {dec:<8}  {regime:<20}  {bl_str}")


# ---------------------------------------------------------------------------
# CSV / JSON export
# ---------------------------------------------------------------------------

def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Recursively flatten a nested dict for CSV export."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, (dict, list)):
                out.update(_flatten(v, key))
            else:
                out[key] = v
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj)
    else:
        out[prefix] = obj
    return out


def export_reports(
    export_dir: str,
    candles: List[Dict[str, Any]],
    s1: Dict, s2: Dict, s3: Dict, s4: Dict, s5: Dict, s6: Dict, s7: Dict, s8: Dict,
    threshold_warnings: List[str],
) -> None:
    out = Path(export_dir)
    out.mkdir(parents=True, exist_ok=True)

    # candles.csv
    candles_path = out / "candles.csv"
    if candles:
        all_keys: List[str] = []
        flat_rows = []
        for row in candles:
            flat = _flatten(row)
            flat_rows.append(flat)
            for k in flat:
                if k not in all_keys:
                    all_keys.append(k)
        with open(candles_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            for flat in flat_rows:
                writer.writerow(flat)
        print(f"  Exported: {candles_path}  ({len(candles)} rows)")

    # blockers.csv
    blockers_path = out / "blockers.csv"
    total = s1["total"]
    if s2["ranked"]:
        with open(blockers_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["code", "count", "pct_of_candles", "family"])
            for code, cnt in s2["ranked"]:
                writer.writerow([
                    code, cnt,
                    f"{100.0 * cnt / total:.2f}" if total else "0.00",
                    _blocker_family(code),
                ])
        print(f"  Exported: {blockers_path}  ({len(s2['ranked'])} blockers)")

    # timeline.csv
    timeline_path = out / "timeline.csv"
    days = s7["days"]
    if days:
        with open(timeline_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "candles", "holds", "long", "short", "close", "top_blocker"])
            writer.writeheader()
            writer.writerows(days)
        print(f"  Exported: {timeline_path}  ({len(days)} days)")

    # summary.json
    summary_path = out / "summary.json"

    def _jsonable(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _jsonable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_jsonable(v) for v in obj]
        if isinstance(obj, tuple):
            return [_jsonable(v) for v in obj]
        if isinstance(obj, (int, float, str, bool)) or obj is None:
            return obj
        return str(obj)

    summary = {
        "overview": s1,
        "blockers": {
            "ranked": s2["ranked"],
            "family_counts": s2["family_counts"],
            "top_pairs": [{"pair": list(p), "count": c} for p, c in s2["top_pairs"]],
            "dominant": s2["dominant"],
        },
        "regime_breakdown": s3,
        "strategy_eligibility": s4,
        "market_conditions": {
            k: {
                fld: {stat: v for stat, v in stats.items()}
                for fld, stats in s5[k].items()
            }
            if isinstance(s5[k], dict) else s5[k]
            for k in s5
        },
        "kill_switch": s6,
        "timeline": s7,
        "diagnosis": {
            "summary": s8["summary"],
            "primary_cause": s8["primary_cause"],
            "findings": s8["findings"],
            "recommendations": s8["recommendations"],
            "threshold_warnings": threshold_warnings,
        },
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(_jsonable(summary), f, indent=2)
    print(f"  Exported: {summary_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze bot runtime logs and produce a diagnostic report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--log", required=True, help="Log file, directory, or glob pattern")
    parser.add_argument("--last", type=int, default=0, metavar="N",
                        help="Analyze only the last N decision candles")
    parser.add_argument("--since", default="", metavar="DATE",
                        help="Analyze candles from this date onward (YYYY-MM-DD)")
    parser.add_argument("--only-holds", action="store_true",
                        help="Filter to HOLD decisions only")
    parser.add_argument("--only-trades", action="store_true",
                        help="Filter to non-HOLD decisions only")
    parser.add_argument("--export", default="", metavar="DIR",
                        help="Write CSV + JSON reports to this directory")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every candle row in the timeline")
    args = parser.parse_args(argv)

    # Parse --since
    since_dt: Optional[datetime] = None
    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
            return 2

    # Discover files
    files = _discover_files(args.log)
    if not files:
        # Try relative to CWD for convenience
        alt = Path.cwd() / args.log
        files = _discover_files(str(alt))
    if not files:
        print(f"ERROR: No log files found at: {args.log}", file=sys.stderr)
        return 1

    print(f"\nLog Analyzer — reading {len(files)} file(s)…")
    for f in files[:5]:
        print(f"  {f}")
    if len(files) > 5:
        print(f"  … and {len(files) - 5} more")

    # Collect data
    data = collect(
        files,
        since_dt=since_dt,
        only_holds=args.only_holds,
        only_trades=args.only_trades,
    )

    if args.last > 0:
        _apply_last_n(data, args.last)

    _merge_clean(data)

    candles = data.candles

    # Build sections
    s1 = section1_overview(data)
    s2 = section2_blockers(candles, s1["total"])
    s3 = section3_regime(candles)
    s4 = section4_strategies(candles)
    s5 = section5_market_conditions(candles)
    s6 = section6_kill_switch(data)
    s7 = section7_timeline(candles)
    s8 = section8_diagnosis(s1, s2, s3, s4, s5, s6)

    # Threshold calibration (needs candle list)
    threshold_warnings = _threshold_calibration(candles, s2["ranked"], s1["total"])
    s8["threshold_warnings"] = threshold_warnings

    # Print report
    print_report(
        s1, s2, s3, s4, s5, s6, s7, s8,
        verbose=args.verbose,
        candles=candles,
        threshold_warnings=threshold_warnings,
    )

    # Export
    if args.export:
        print(f"\nExporting reports to: {args.export}")
        export_reports(
            args.export,
            candles, s1, s2, s3, s4, s5, s6, s7, s8,
            threshold_warnings,
        )

    if data.bad_lines:
        print(f"\n[WARN] {data.bad_lines} malformed log lines were skipped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
