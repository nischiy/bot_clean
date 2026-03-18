from __future__ import annotations

import argparse
import csv
import glob
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DECISION_EVENTS = {"decision_reject", "decision_candle", "decision_clean"}
HOLD_EXECUTIONS = {"HOLD", "HOLD_LOW_QUALITY", "HOLD_EVENT", "HOLD_LATE"}
CONFIDENCE_SCORES = {"LOW": 1.0, "MEDIUM": 2.0, "HIGH": 3.0}
DEFAULT_RECENT_CASES = 50
DEFAULT_TABLE_ROWS = 20


@dataclass
class CandleAggregate:
    timestamp_closed: int
    event_payloads: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    source_paths: List[str] = field(default_factory=list)
    source_lines: List[int] = field(default_factory=list)
    finalized_label: Optional[Dict[str, Any]] = None
    diagnostics: List[str] = field(default_factory=list)
    explanation: str = ""

    def merge_event(self, event: str, payload: Dict[str, Any], path: str, line_no: int) -> None:
        existing = self.event_payloads.get(event, {})
        self.event_payloads[event] = _merge_dict(existing, payload)
        if path not in self.source_paths:
            self.source_paths.append(path)
        self.source_lines.append(line_no)

    def to_row(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for event in ("decision_reject", "decision_clean", "decision_candle"):
            row = _merge_dict(row, self.event_payloads.get(event, {}))
        row["timestamp_closed"] = self.timestamp_closed
        row["source_events"] = [event for event in ("decision_reject", "decision_clean", "decision_candle") if event in self.event_payloads]
        row["source_paths"] = list(self.source_paths)
        row["source_lines"] = list(self.source_lines)
        if self.finalized_label:
            row["finalized_label"] = dict(self.finalized_label)
            for key, value in self.finalized_label.items():
                if key == "timestamp_closed":
                    continue
                row[key] = value
        row["diagnostics"] = list(self.diagnostics)
        row["diagnostic_explanation"] = self.explanation
        return row


@dataclass
class DiagnosticArtifacts:
    candles: List[Dict[str, Any]]
    summary: Dict[str, Any]
    tables: Dict[str, List[Dict[str, Any]]]
    report: str


def _parse_json_from_line(line: str) -> Optional[Dict[str, Any]]:
    start = line.find("{")
    if start == -1:
        return None
    end = line.rfind("}")
    if end == -1 or end <= start:
        return None
    try:
        obj = json.loads(line[start : end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _ordered_unique(items: Iterable[Any]) -> List[Any]:
    seen = set()
    ordered: List[Any] = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, default=str) if isinstance(item, dict) else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(item)
    return ordered


def _merge_value(existing: Any, incoming: Any) -> Any:
    if _is_empty(existing):
        return incoming
    if _is_empty(incoming):
        return existing
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return _merge_dict(existing, incoming)
    if isinstance(existing, list) and isinstance(incoming, list):
        return _ordered_unique([*existing, *incoming])
    return incoming


def _merge_dict(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "event":
            continue
        merged[key] = _merge_value(merged.get(key), value)
    return merged


def _flatten_validator_reject_map(value: Any) -> List[str]:
    if isinstance(value, dict):
        codes: List[str] = []
        for reasons in value.values():
            if isinstance(reasons, list):
                codes.extend(str(reason) for reason in reasons if reason and ":strategy_ineligible" not in str(reason))
        return _ordered_unique(codes)
    return []


def _flatten_blockers(row: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    for key in ("main_blocker",):
        value = row.get(key)
        if value:
            blockers.append(str(value))
    for key in ("blockers", "reject_reasons"):
        value = row.get(key)
        if isinstance(value, list):
            blockers.extend(str(item) for item in value if item and ":strategy_ineligible" not in str(item))
    blockers.extend(_flatten_validator_reject_map(row.get("validator_reject_map")))
    return _ordered_unique(blockers)


def _row_main_blocker(row: Dict[str, Any]) -> str:
    main_blocker = row.get("main_blocker")
    if main_blocker:
        return str(main_blocker)
    blockers = _flatten_blockers(row)
    return str(blockers[0]) if blockers else ""


def _get_dist50(row: Dict[str, Any]) -> Optional[float]:
    for value in (
        row.get("dist50_curr"),
        row.get("distance_to_reclaim"),
        (row.get("regime_explain") or {}).get("dist50") if isinstance(row.get("regime_explain"), dict) else None,
        (row.get("explain_pullback") or {}).get("dist50_curr") if isinstance(row.get("explain_pullback"), dict) else None,
    ):
        numeric = _safe_float(value)
        if numeric is not None:
            return numeric
    return None


def _get_volume_ratio(row: Dict[str, Any]) -> Optional[float]:
    for value in (
        row.get("volume_ratio"),
        row.get("volume_ratio_5m"),
        (row.get("regime_explain") or {}).get("volume_ratio") if isinstance(row.get("regime_explain"), dict) else None,
    ):
        numeric = _safe_float(value)
        if numeric is not None:
            return numeric
    return None


def _has_blocker(row: Dict[str, Any], prefix: str) -> bool:
    return any(str(code).startswith(prefix) for code in _flatten_blockers(row))


def _has_invalidation(row: Dict[str, Any], needle: str) -> bool:
    reasons = row.get("invalidation_reasons") or []
    return any(needle in str(reason) for reason in reasons)


def _confidence_score(row: Dict[str, Any]) -> Optional[float]:
    confidence = _normalize_string(row.get("confidence_tier"), "UNKNOWN").upper()
    return CONFIDENCE_SCORES.get(confidence)


def _realized_label(row: Dict[str, Any]) -> str:
    return _normalize_string(row.get("realized_move_label"), "")


def _direction_supported(row: Dict[str, Any]) -> Optional[bool]:
    bias = _normalize_string(row.get("predictive_bias"), "NEUTRAL").upper()
    if bias not in {"LONG", "SHORT"}:
        return None

    if row.get("early_signal_right") is True:
        return True
    if row.get("early_signal_right") is False:
        return False

    realized = _realized_label(row)
    if not realized:
        return None
    if bias == "LONG":
        if realized == "UP_1ATR":
            return True
        if realized in {"DOWN_1ATR", "NO_1ATR_MOVE"}:
            return False
    if bias == "SHORT":
        if realized == "DOWN_1ATR":
            return True
        if realized in {"UP_1ATR", "NO_1ATR_MOVE"}:
            return False
    return None


def _is_directional(row: Dict[str, Any]) -> bool:
    return _normalize_string(row.get("predictive_bias"), "NEUTRAL").upper() in {"LONG", "SHORT"}


def _is_open(row: Dict[str, Any]) -> bool:
    return _normalize_string(row.get("execution_decision")).startswith("OPEN_")


def _is_hold(row: Dict[str, Any]) -> bool:
    execution = _normalize_string(row.get("execution_decision"), row.get("decision"))
    return execution in HOLD_EXECUTIONS or execution.startswith("HOLD")


def _is_early_candidate(row: Dict[str, Any]) -> bool:
    return _normalize_string(row.get("predictive_state")).upper().startswith("EARLY_")


def _is_confirmed_candidate(row: Dict[str, Any]) -> bool:
    return _normalize_string(row.get("confirmation_quality")).upper() == "STRONG" or _normalize_string(row.get("entry_mode")).upper() == "CONFIRMED"


def _event_blocked(row: Dict[str, Any]) -> bool:
    return bool(row.get("event_hard_block") or row.get("event_block") or _normalize_string(row.get("execution_decision")) == "HOLD_EVENT" or _has_blocker(row, "E:"))


def _supporting_strategies(row: Dict[str, Any]) -> List[str]:
    value = row.get("supporting_strategies") or []
    return [str(item) for item in value] if isinstance(value, list) else []


def _opposing_strategies(row: Dict[str, Any]) -> List[str]:
    value = row.get("opposing_strategies") or []
    return [str(item) for item in value] if isinstance(value, list) else []


def _validator_reject_summary(row: Dict[str, Any], limit: int = 3) -> str:
    mapping = row.get("validator_reject_map")
    if not isinstance(mapping, dict) or not mapping:
        return ""
    parts: List[str] = []
    for strategy, reasons in mapping.items():
        if not reasons:
            continue
        joined = ",".join(str(reason) for reason in list(reasons)[:3])
        parts.append(f"{strategy}:{joined}")
        if len(parts) >= limit:
            break
    return "; ".join(parts)


def _list_to_text(value: Any, limit: int = 4) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value[:limit])
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _label_distribution(rows: Sequence[Dict[str, Any]], limit: int = 3) -> str:
    counter = Counter(_realized_label(row) or "UNLABELED" for row in rows)
    return ", ".join(f"{label}:{count}" for label, count in counter.most_common(limit))


def _most_common_value(values: Iterable[str], default: str = "") -> str:
    filtered = [value for value in values if value]
    if not filtered:
        return default
    return Counter(filtered).most_common(1)[0][0]


def _diagnose_row(row: Dict[str, Any]) -> Tuple[List[str], str]:
    diagnostics: List[str] = []
    detail: List[str] = []
    directional = _is_directional(row)
    supported = _direction_supported(row)
    hold = _is_hold(row)
    late = _has_invalidation(row, "late_overextended_") or _normalize_string(row.get("execution_decision")) == "HOLD_LATE"
    support_weak = len(_supporting_strategies(row)) == 0 or _normalize_string(row.get("confirmation_quality")).upper() in {"NONE", "WEAK"}
    reject_codes = _flatten_validator_reject_map(row.get("validator_reject_map"))
    has_validator_opposition = bool(_opposing_strategies(row) or len(reject_codes) >= 2)

    if directional and supported is True and hold:
        diagnostics.append("predictive_correct_but_blocked")
        detail.append(f"{row.get('predictive_bias')} bias was right, but execution stayed {_normalize_string(row.get('execution_decision'), 'HOLD')}")
    if directional and late:
        diagnostics.append("predictive_late")
        detail.append("late_overextended gating indicates the predictive call arrived after extension")
    if directional and support_weak and has_validator_opposition:
        diagnostics.append("validator_conflict")
        detail.append("legacy validator support was weak while rejection pressure stayed high")
    if not directional:
        diagnostics.append("no_predictive_edge")
    if directional and _event_blocked(row) and supported is True:
        diagnostics.append("event_blocked_directional_move")
        detail.append("event gating blocked a move that later resolved in the predictive direction")
    if directional and supported is True and any(code.startswith("P:reclaim") for code in _flatten_blockers(row)):
        diagnostics.append("reclaim_wait_missed_move")
        detail.append("reclaim confirmation delayed a move that later paid in the predictive direction")
    if directional and supported is True and any(code.startswith("P:dist50") for code in _flatten_blockers(row)):
        diagnostics.append("dist50_overextension_after_delay")
        detail.append("dist50 overextension blocked a move that still resolved in the predictive direction")
    if directional and hold and _normalize_string(row.get("confirmation_quality")).upper() in {"NONE", "WEAK"}:
        diagnostics.append("legacy_confirmation_absent")
    if _normalize_string(row.get("transition_name")) and _normalize_string(row.get("transition_name")) != "STATE_UNCHANGED":
        diagnostics.append("state_machine_transition_detected")
    if directional and hold and supported is True and (late or _normalize_string(row.get("transition_name")).endswith("_CONFIRMED")):
        diagnostics.append("likely_post_factum_detection")
        detail.append("transition/exhaustion evidence suggests the signal may still be arriving post-factum")

    main_blocker = _row_main_blocker(row)
    if main_blocker:
        detail.append(f"main blocker={main_blocker}")

    return _ordered_unique(diagnostics), "; ".join(_ordered_unique(detail))


def _extract_finalized_labels(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    labels: List[Dict[str, Any]] = []
    finalized = payload.get("finalized_labels")
    if isinstance(finalized, list):
        labels.extend(label for label in finalized if isinstance(label, dict))
    latest = payload.get("latest_finalized_label")
    if isinstance(latest, dict):
        labels.append(latest)
    deduped: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for label in labels:
        ts = _safe_int(label.get("timestamp_closed"))
        if ts is None or ts in seen:
            continue
        seen.add(ts)
        deduped.append(dict(label))
    return deduped


def resolve_log_paths(inputs: Sequence[str]) -> List[Path]:
    discovered: List[Path] = []
    for item in inputs:
        path = Path(item)
        if path.exists() and path.is_dir():
            candidates: List[Path] = []
            runtime_path = path / "runtime.log"
            if runtime_path.exists():
                candidates.append(runtime_path)
            if (path / "sessions_clean").exists():
                candidates.extend(sorted((path / "sessions_clean").glob("*.log")))
            if (path / "sessions").exists():
                candidates.extend(sorted((path / "sessions").glob("*.log")))
            candidates.extend(sorted(p for p in path.glob("*.log") if p.name != "runtime.log"))
            discovered.extend(candidates)
            continue
        if path.exists():
            discovered.append(path)
            continue
        for match in glob.glob(item):
            matched = Path(match)
            if matched.is_file():
                discovered.append(matched)

    unique: List[Path] = []
    seen: set[str] = set()
    for path in discovered:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return sorted(unique, key=lambda current: (current.name != "runtime.log", str(current)))


def parse_log_files(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    records: Dict[int, CandleAggregate] = {}
    finalized_by_origin: Dict[int, Dict[str, Any]] = {}
    seen_fingerprints: set[str] = set()

    for path in paths:
        last_decision_ts: Optional[int] = None
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line_no, raw_line in enumerate(handle, start=1):
                obj = _parse_json_from_line(raw_line)
                if not obj:
                    continue
                event = _normalize_string(obj.get("event"))
                if event not in DECISION_EVENTS and "finalized_labels" not in obj and "latest_finalized_label" not in obj:
                    continue

                ts = _safe_int(obj.get("timestamp_closed"))
                if event in {"decision_reject", "decision_candle"} and ts is not None:
                    last_decision_ts = ts
                elif event == "decision_clean" and ts is None:
                    ts = last_decision_ts

                for finalized in _extract_finalized_labels(obj):
                    origin_ts = _safe_int(finalized.get("timestamp_closed"))
                    if origin_ts is None:
                        continue
                    finalized_by_origin[origin_ts] = _merge_dict(finalized_by_origin.get(origin_ts, {}), finalized)

                if event not in DECISION_EVENTS or ts is None:
                    continue

                fingerprint = json.dumps(
                    {"event": event, "timestamp_closed": ts, "payload": obj},
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                )
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)

                aggregate = records.setdefault(ts, CandleAggregate(timestamp_closed=ts))
                aggregate.merge_event(event, obj, str(path), line_no)

    rows: List[Dict[str, Any]] = []
    for ts in sorted(records):
        aggregate = records[ts]
        if ts in finalized_by_origin:
            aggregate.finalized_label = _merge_dict(aggregate.finalized_label or {}, finalized_by_origin[ts])
        row = aggregate.to_row()
        row["dist50"] = _get_dist50(row)
        row["volume_ratio"] = _get_volume_ratio(row)
        row["main_blocker"] = _row_main_blocker(row)
        diagnostics, explanation = _diagnose_row(row)
        aggregate.diagnostics = diagnostics
        aggregate.explanation = explanation
        row["diagnostics"] = diagnostics
        row["diagnostic_explanation"] = explanation
        rows.append(row)

    return rows


def filter_candles(
    candles: Sequence[Dict[str, Any]],
    *,
    since_ts: Optional[int],
    last: Optional[int],
    only_holds: bool,
    only_predictive: bool,
) -> List[Dict[str, Any]]:
    filtered = [dict(row) for row in candles]
    if since_ts is not None:
        filtered = [row for row in filtered if _safe_int(row.get("timestamp_closed"), 0) >= since_ts]
    if only_holds:
        filtered = [row for row in filtered if _is_hold(row)]
    if only_predictive:
        filtered = [row for row in filtered if _is_directional(row)]
    filtered.sort(key=lambda row: _safe_int(row.get("timestamp_closed"), 0))
    if last is not None and last > 0:
        filtered = filtered[-last:]
    return filtered


def _overview_summary(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    directional = [row for row in candles if _is_directional(row)]
    open_rows = [row for row in candles if _is_open(row)]
    hold_rows = [row for row in candles if _is_hold(row)]
    finalized = [row for row in candles if _realized_label(row)]
    confirmation_counts = Counter(_normalize_string(row.get("confirmation_quality"), "UNKNOWN").upper() for row in candles)

    summary_items = [
        ("total_analyzed_decision_candles", len(candles)),
        ("total_predictive_directional_signals", len(directional)),
        ("total_open_decisions", len(open_rows)),
        ("total_hold_decisions", len(hold_rows)),
        ("total_early_candidates", sum(1 for row in candles if _is_early_candidate(row))),
        ("total_confirmed_candidates", sum(1 for row in candles if _is_confirmed_candidate(row))),
        ("total_predictive_bias_not_neutral", sum(1 for row in candles if _normalize_string(row.get("predictive_bias"), "NEUTRAL").upper() != "NEUTRAL")),
        ("confirmation_quality_none", confirmation_counts.get("NONE", 0)),
        ("confirmation_quality_weak", confirmation_counts.get("WEAK", 0)),
        ("confirmation_quality_strong", confirmation_counts.get("STRONG", 0)),
        ("total_with_finalized_labels", len(finalized)),
        ("predictive_correct_but_not_traded", sum(1 for row in candles if _is_hold(row) and _direction_supported(row) is True)),
        ("late_overextended_invalidations", sum(1 for row in candles if _has_invalidation(row, "late_overextended_"))),
        ("event_blocked_cases", sum(1 for row in candles if _event_blocked(row))),
        ("reclaim_blocked_cases", sum(1 for row in candles if _has_blocker(row, "P:reclaim"))),
        ("dist50_blocked_cases", sum(1 for row in candles if _has_blocker(row, "P:dist50"))),
    ]
    return [{"metric": metric, "value": value} for metric, value in summary_items]


def _blocker_frequency(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = max(len(candles), 1)
    hold_rows = [row for row in candles if _is_hold(row)]
    directional_rows = [row for row in candles if _is_directional(row)]
    hold_total = max(len(hold_rows), 1)
    directional_total = max(len(directional_rows), 1)

    blocker_counts: Counter[str] = Counter()
    hold_counts: Counter[str] = Counter()
    directional_counts: Counter[str] = Counter()

    for row in candles:
        blockers = _ordered_unique(_flatten_blockers(row))
        blocker_counts.update(blockers)
        if _is_hold(row):
            hold_counts.update(blockers)
        if _is_directional(row):
            directional_counts.update(blockers)

    return [
        {
            "blocker": blocker,
            "count": count,
            "percentage": round(100.0 * count / total, 2),
            "percentage_among_hold_only": round(100.0 * hold_counts.get(blocker, 0) / hold_total, 2),
            "percentage_among_predictive_directional": round(100.0 * directional_counts.get(blocker, 0) / directional_total, 2),
        }
        for blocker, count in blocker_counts.most_common()
    ]


def _predictive_state_performance(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candles:
        grouped[_normalize_string(row.get("predictive_state"), "UNKNOWN")].append(row)

    output: List[Dict[str, Any]] = []
    for state, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        confidence_values = [value for value in (_confidence_score(row) for row in rows) if value is not None]
        directional_rows = [row for row in rows if _is_directional(row)]
        correct_flags = [flag for flag in (_direction_supported(row) for row in rows) if flag is not None]
        output.append(
            {
                "predictive_state": state,
                "count": len(rows),
                "avg_confidence": round(mean(confidence_values), 2) if confidence_values else None,
                "percent_not_traded": round(100.0 * sum(1 for row in rows if _is_hold(row)) / len(rows), 2),
                "percent_late_overextended": round(100.0 * sum(1 for row in rows if _has_invalidation(row, "late_overextended_")) / len(rows), 2),
                "percent_with_supporting_validators": round(100.0 * sum(1 for row in rows if _supporting_strategies(row)) / len(rows), 2),
                "percent_with_opposing_validators": round(100.0 * sum(1 for row in rows if _opposing_strategies(row) or _flatten_validator_reject_map(row.get("validator_reject_map"))) / len(rows), 2),
                "realized_move_label_distribution": _label_distribution(rows),
                "percent_correct_direction": round(100.0 * sum(1 for flag in correct_flags if flag) / len(correct_flags), 2) if correct_flags and directional_rows else None,
            }
        )
    return output


def _transition_summary(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candles:
        grouped[_normalize_string(row.get("transition_name"), "UNKNOWN")].append(row)

    output: List[Dict[str, Any]] = []
    for transition_name, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        invalidations = Counter(
            reason
            for row in rows
            for reason in (row.get("invalidation_reasons") or [])
            if reason
        )
        output.append(
            {
                "transition_name": transition_name,
                "count": len(rows),
                "most_common_execution_decision": _most_common_value((_normalize_string(row.get("execution_decision")) for row in rows), default=""),
                "most_common_blocker": _most_common_value((_row_main_blocker(row) for row in rows), default=""),
                "realized_outcome_summary": _label_distribution(rows),
                "top_invalidation_reasons": ", ".join(reason for reason, _ in invalidations.most_common(3)),
            }
        )
    return output


def _missed_opportunities(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in candles:
        if not _is_directional(row) or not _is_hold(row) or _direction_supported(row) is not True:
            continue
        rows.append(
            {
                "timestamp_closed": row.get("timestamp_closed"),
                "predictive_bias": row.get("predictive_bias"),
                "predictive_state": row.get("predictive_state"),
                "confidence_tier": row.get("confidence_tier"),
                "execution_decision": row.get("execution_decision"),
                "main_blocker": _row_main_blocker(row),
                "invalidation_reasons": _list_to_text(row.get("invalidation_reasons")),
                "supporting_strategies": _list_to_text(row.get("supporting_strategies")),
                "opposing_strategies": _list_to_text(row.get("opposing_strategies")),
                "realized_move_label": _realized_label(row),
                "close": row.get("close"),
                "trend": row.get("trend"),
                "trend_strength": row.get("trend_strength"),
                "stability_score": row.get("stability_score"),
                "volume_ratio": row.get("volume_ratio"),
                "dist50": row.get("dist50"),
                "short_explanation": row.get("diagnostic_explanation"),
            }
        )
    return rows


def _false_positives(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in candles:
        if not _is_directional(row) or _direction_supported(row) is not False:
            continue
        rows.append(
            {
                "timestamp_closed": row.get("timestamp_closed"),
                "predictive_bias": row.get("predictive_bias"),
                "predictive_state": row.get("predictive_state"),
                "confidence_tier": row.get("confidence_tier"),
                "transition_name": row.get("transition_name"),
                "execution_decision": row.get("execution_decision"),
                "invalidation_reasons": _list_to_text(row.get("invalidation_reasons")),
                "finalized_label": _realized_label(row),
                "blocker_summary": _list_to_text(_flatten_blockers(row), limit=5),
                "short_explanation": row.get("diagnostic_explanation"),
            }
        )
    return rows


def _late_detection_rows(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in candles:
        if not _has_invalidation(row, "late_overextended_"):
            continue
        rows.append(
            {
                "timestamp_closed": row.get("timestamp_closed"),
                "predictive_bias": row.get("predictive_bias"),
                "predictive_state": row.get("predictive_state"),
                "transition_name": row.get("transition_name"),
                "dist50": row.get("dist50"),
                "execution_decision": row.get("execution_decision"),
                "realized_move_label": _realized_label(row),
                "notes": row.get("diagnostic_explanation"),
            }
        )
    return rows


def _validator_conflicts(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in candles:
        if not _is_directional(row):
            continue
        support_weak = len(_supporting_strategies(row)) == 0 or _normalize_string(row.get("confirmation_quality")).upper() in {"NONE", "WEAK"}
        validator_rejects = _flatten_validator_reject_map(row.get("validator_reject_map"))
        has_opposition = bool(_opposing_strategies(row) or len(validator_rejects) >= 2)
        if not (support_weak and has_opposition):
            continue
        rows.append(
            {
                "timestamp_closed": row.get("timestamp_closed"),
                "predictive_bias": row.get("predictive_bias"),
                "predictive_state": row.get("predictive_state"),
                "confidence_tier": row.get("confidence_tier"),
                "supporting_strategies": _list_to_text(row.get("supporting_strategies")),
                "opposing_strategies": _list_to_text(row.get("opposing_strategies")),
                "selected_strategy": row.get("selected_strategy"),
                "validator_reject_summary": _validator_reject_summary(row),
                "execution_decision": row.get("execution_decision"),
                "realized_move_label": _realized_label(row),
            }
        )
    return rows


def _event_analysis(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in candles:
        key = (
            _normalize_string(row.get("event_classification"), "NONE"),
            str(bool(row.get("event_detected"))),
            str(bool(row.get("event_hard_block"))),
            _normalize_string(row.get("predictive_bias"), "NEUTRAL"),
            _normalize_string(row.get("execution_decision"), "UNKNOWN"),
        )
        grouped[key].append(row)

    output: List[Dict[str, Any]] = []
    for key, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        event_classification, event_detected, event_hard_block, predictive_bias, execution_decision = key
        output.append(
            {
                "event_classification": event_classification,
                "event_detected": event_detected,
                "event_hard_block": event_hard_block,
                "predictive_bias": predictive_bias,
                "execution_decision": execution_decision,
                "count": len(rows),
                "realized_move_label_summary": _label_distribution(rows),
            }
        )
    return output


def _recent_cases(candles: Sequence[Dict[str, Any]], recent: int) -> List[Dict[str, Any]]:
    selected = list(sorted(candles, key=lambda row: _safe_int(row.get("timestamp_closed"), 0)))[-recent:]
    return [
        {
            "timestamp_closed": row.get("timestamp_closed"),
            "predictive_bias": row.get("predictive_bias"),
            "predictive_state": row.get("predictive_state"),
            "execution_decision": row.get("execution_decision"),
            "main_blocker": _row_main_blocker(row),
            "realized_move_label": _realized_label(row),
            "confirmation_quality": row.get("confirmation_quality"),
            "transition_name": row.get("transition_name"),
            "trend": row.get("trend"),
            "trend_strength": row.get("trend_strength"),
            "stability_score": row.get("stability_score"),
            "dist50": row.get("dist50"),
            "diagnostics": ",".join(row.get("diagnostics") or []),
        }
        for row in selected
    ]


def _top_suspected_problems(
    candles: Sequence[Dict[str, Any]],
    missed_rows: Sequence[Dict[str, Any]],
    validator_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    problems: List[str] = []
    directional = [row for row in candles if _is_directional(row)]
    profitable_directional = [row for row in directional if _direction_supported(row) is True]

    late_short = sum(1 for row in candles if _normalize_string(row.get("predictive_bias")).upper() == "SHORT" and _has_invalidation(row, "late_overextended_short"))
    late_long = sum(1 for row in candles if _normalize_string(row.get("predictive_bias")).upper() == "LONG" and _has_invalidation(row, "late_overextended_long"))
    if late_short or late_long:
        side = "SHORT" if late_short >= late_long else "LONG"
        problems.append(f"Predictive {side} signals are frequently blocked by late_overextended_{side.lower()}, suggesting the layer is still detecting that side after extension.")

    transition_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candles:
        if _is_hold(row) and _direction_supported(row) is True:
            transition_rows[_normalize_string(row.get("transition_name"), "UNKNOWN")].append(row)
    if transition_rows:
        top_transition, top_rows = max(transition_rows.items(), key=lambda item: len(item[1]))
        problems.append(f"{top_transition} often resolves in the predictive direction while execution remains HOLD ({len(top_rows)} profitable blocked cases).")

    if profitable_directional:
        conflicted_profitable = sum(1 for row in profitable_directional if "validator_conflict" in (row.get("diagnostics") or []))
        if conflicted_profitable:
            pct = round(100.0 * conflicted_profitable / len(profitable_directional), 1)
            problems.append(f"Legacy validators oppose predictive bias in {pct}% of profitable predictive cases.")

    profitable_missed = [row for row in candles if _is_hold(row) and _direction_supported(row) is True]
    if profitable_missed:
        blocker_counter = Counter(_row_main_blocker(row) for row in profitable_missed if _row_main_blocker(row))
        if blocker_counter:
            top_blocker, top_count = blocker_counter.most_common(1)[0]
            problems.append(f"{top_blocker} is the dominant blocker in profitable directional opportunities ({top_count} cases).")

    if not problems and missed_rows:
        problems.append("Predictive missed opportunities exist, but no single blocker family dominates strongly enough to stand out in this sample.")
    if not problems and validator_rows:
        problems.append("Validator conflicts appear in the sample, but they are not concentrated enough to isolate a dominant failure mode.")
    if not problems:
        problems.append("No dominant logical weakness stood out in the analyzed slice; expand the sample or include archived logs for stronger patterns.")
    return problems[:5]


def build_diagnostics(
    candles: Sequence[Dict[str, Any]],
    *,
    recent: int = DEFAULT_RECENT_CASES,
) -> DiagnosticArtifacts:
    overview = _overview_summary(candles)
    blocker_frequency = _blocker_frequency(candles)
    predictive_performance = _predictive_state_performance(candles)
    transitions = _transition_summary(candles)
    missed = _missed_opportunities(candles)
    false_positives = _false_positives(candles)
    late_detection = _late_detection_rows(candles)
    validator_conflicts = _validator_conflicts(candles)
    event_analysis = _event_analysis(candles)
    recent_cases = _recent_cases(candles, recent=max(1, recent))
    suspected_problems = _top_suspected_problems(candles, missed, validator_conflicts)

    tables = {
        "overview_summary": overview,
        "blocker_frequency": blocker_frequency,
        "predictive_state_performance": predictive_performance,
        "transition_table": transitions,
        "missed_opportunities": missed,
        "false_positives": false_positives,
        "late_detection": late_detection,
        "validator_conflicts": validator_conflicts,
        "event_analysis": event_analysis,
        "recent_cases": recent_cases,
    }
    summary = {
        "candles_analyzed": len(candles),
        "suspected_problems": suspected_problems,
        "table_counts": {name: len(rows) for name, rows in tables.items()},
    }
    report = render_report(candles, tables, suspected_problems)
    return DiagnosticArtifacts(candles=list(candles), summary=summary, tables=tables, report=report)


def _format_cell(value: Any, width: int) -> str:
    text = _stringify_value(value)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_table(rows: Sequence[Dict[str, Any]], columns: Sequence[Tuple[str, str]], *, max_rows: Optional[int] = None) -> str:
    if not rows:
        headers = [header for _, header in columns]
        widths = [len(header) for header in headers]
        header_line = " | ".join(header.ljust(width) for header, width in zip(headers, widths))
        separator = "-+-".join("-" * width for width in widths)
        return "\n".join([header_line, separator, "(no rows)"])

    visible_rows = list(rows[:max_rows]) if max_rows is not None else list(rows)
    max_widths: List[int] = []
    for key, header in columns:
        content_lengths = [len(_stringify_value(row.get(key))) for row in visible_rows]
        max_widths.append(min(max([len(header), *content_lengths]), 36))

    header_line = " | ".join(header.ljust(width) for (_, header), width in zip(columns, max_widths))
    separator = "-+-".join("-" * width for width in max_widths)
    body = [
        " | ".join(_format_cell(row.get(key), width).ljust(width) for (key, _), width in zip(columns, max_widths))
        for row in visible_rows
    ]
    if max_rows is not None and len(rows) > max_rows:
        body.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join([header_line, separator, *body])


def render_report(
    candles: Sequence[Dict[str, Any]],
    tables: Dict[str, List[Dict[str, Any]]],
    suspected_problems: Sequence[str],
) -> str:
    sections: List[str] = []
    sections.append("Trading Decision Log Diagnostics")
    sections.append("")
    sections.append(f"Analyzed candles: {len(candles)}")
    sections.append("")

    sections.append("Top suspected logical problems")
    for problem in suspected_problems:
        sections.append(f"- {problem}")
    sections.append("")

    table_specs = [
        ("1. OVERVIEW SUMMARY TABLE", "overview_summary", [("metric", "metric"), ("value", "value")], None),
        ("2. BLOCKER FREQUENCY TABLE", "blocker_frequency", [("blocker", "blocker"), ("count", "count"), ("percentage", "%"), ("percentage_among_hold_only", "% hold"), ("percentage_among_predictive_directional", "% directional")], DEFAULT_TABLE_ROWS),
        ("3. PREDICTIVE STATE PERFORMANCE TABLE", "predictive_state_performance", [("predictive_state", "state"), ("count", "count"), ("avg_confidence", "avg conf"), ("percent_not_traded", "% not traded"), ("percent_late_overextended", "% late"), ("percent_with_supporting_validators", "% support"), ("percent_with_opposing_validators", "% oppose"), ("realized_move_label_distribution", "labels"), ("percent_correct_direction", "% correct")], DEFAULT_TABLE_ROWS),
        ("4. TRANSITION TABLE", "transition_table", [("transition_name", "transition"), ("count", "count"), ("most_common_execution_decision", "common exec"), ("most_common_blocker", "common blocker"), ("realized_outcome_summary", "outcomes"), ("top_invalidation_reasons", "top invalidations")], DEFAULT_TABLE_ROWS),
        ("5. MISSED-OPPORTUNITY TABLE", "missed_opportunities", [("timestamp_closed", "timestamp"), ("predictive_bias", "bias"), ("predictive_state", "state"), ("confidence_tier", "conf"), ("execution_decision", "exec"), ("main_blocker", "blocker"), ("realized_move_label", "label"), ("trend", "trend"), ("trend_strength", "trend str"), ("stability_score", "stability"), ("volume_ratio", "vol"), ("dist50", "dist50"), ("short_explanation", "explanation")], DEFAULT_TABLE_ROWS),
        ("6. FALSE-POSITIVE TABLE", "false_positives", [("timestamp_closed", "timestamp"), ("predictive_bias", "bias"), ("predictive_state", "state"), ("confidence_tier", "conf"), ("transition_name", "transition"), ("execution_decision", "exec"), ("finalized_label", "label"), ("blocker_summary", "blockers"), ("short_explanation", "explanation")], DEFAULT_TABLE_ROWS),
        ("7. LATE-DETECTION TABLE", "late_detection", [("timestamp_closed", "timestamp"), ("predictive_bias", "bias"), ("predictive_state", "state"), ("transition_name", "transition"), ("dist50", "dist50"), ("execution_decision", "exec"), ("realized_move_label", "label"), ("notes", "notes")], DEFAULT_TABLE_ROWS),
        ("8. VALIDATOR CONFLICT TABLE", "validator_conflicts", [("timestamp_closed", "timestamp"), ("predictive_bias", "bias"), ("predictive_state", "state"), ("confidence_tier", "conf"), ("supporting_strategies", "support"), ("opposing_strategies", "oppose"), ("selected_strategy", "selected"), ("validator_reject_summary", "validator rejects"), ("execution_decision", "exec"), ("realized_move_label", "label")], DEFAULT_TABLE_ROWS),
        ("9. EVENT ANALYSIS TABLE", "event_analysis", [("event_classification", "event"), ("event_detected", "detected"), ("event_hard_block", "hard block"), ("predictive_bias", "bias"), ("execution_decision", "exec"), ("count", "count"), ("realized_move_label_summary", "outcomes")], DEFAULT_TABLE_ROWS),
        ("10. RECENT CASES TABLE", "recent_cases", [("timestamp_closed", "timestamp"), ("predictive_bias", "bias"), ("predictive_state", "state"), ("execution_decision", "exec"), ("main_blocker", "blocker"), ("realized_move_label", "label"), ("confirmation_quality", "confirm"), ("transition_name", "transition"), ("trend", "trend"), ("trend_strength", "trend str"), ("stability_score", "stability"), ("dist50", "dist50"), ("diagnostics", "diagnostics")], None),
    ]

    for title, key, columns, max_rows in table_specs:
        sections.append(title)
        sections.append(render_table(tables[key], columns, max_rows=max_rows))
        sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def export_csv(candles: Sequence[Dict[str, Any]], tables: Dict[str, List[Dict[str, Any]]], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    candle_columns = [
        "timestamp_closed",
        "decision",
        "execution_decision",
        "entry_mode",
        "predictive_bias",
        "predictive_state",
        "confidence_tier",
        "confirmation_quality",
        "transition_name",
        "main_blocker",
        "reject_reasons",
        "invalidation_reasons",
        "supporting_strategies",
        "opposing_strategies",
        "validator_reject_map",
        "realized_move_label",
        "early_signal_right",
        "close",
        "trend",
        "trend_strength",
        "stability_score",
        "volume_ratio",
        "dist50",
        "diagnostics",
        "diagnostic_explanation",
    ]
    decision_csv = out_dir / "decision_candles.csv"
    with decision_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=candle_columns)
        writer.writeheader()
        for candle in candles:
            row = dict(candle)
            for key in ("reject_reasons", "invalidation_reasons", "supporting_strategies", "opposing_strategies", "diagnostics"):
                row[key] = _list_to_text(row.get(key), limit=20)
            row["validator_reject_map"] = json.dumps(row.get("validator_reject_map") or {}, sort_keys=True)
            writer.writerow({key: row.get(key) for key in candle_columns})
    written.append(decision_csv)

    missed_csv = out_dir / "missed_opportunities.csv"
    missed_rows = tables.get("missed_opportunities", [])
    if missed_rows:
        with missed_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(missed_rows[0].keys()))
            writer.writeheader()
            writer.writerows(missed_rows)
        written.append(missed_csv)

    return written


def export_json(summary: Dict[str, Any], tables: Dict[str, List[Dict[str, Any]]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "tables": tables}, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def analyze_logs(
    *,
    log_inputs: Sequence[str],
    last: Optional[int] = None,
    since_ts: Optional[int] = None,
    only_holds: bool = False,
    only_predictive: bool = False,
    recent: int = DEFAULT_RECENT_CASES,
    csv_out: Optional[str] = None,
    json_out: Optional[str] = None,
) -> DiagnosticArtifacts:
    paths = resolve_log_paths(log_inputs)
    if not paths:
        raise SystemExit("No log files found for the provided --log input.")

    candles = parse_log_files(paths)
    candles = filter_candles(candles, since_ts=since_ts, last=last, only_holds=only_holds, only_predictive=only_predictive)
    diagnostics = build_diagnostics(candles, recent=recent)

    if csv_out:
        export_csv(diagnostics.candles, diagnostics.tables, Path(csv_out))
    if json_out:
        export_json(diagnostics.summary, diagnostics.tables, Path(json_out))
    return diagnostics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze predictive-first trading decision logs.")
    parser.add_argument("--log", action="append", required=True, help="Log file, glob, or logs directory. Repeat to add more inputs.")
    parser.add_argument("--last", type=int, default=None, help="Analyze only the last N merged decision candles after filtering.")
    parser.add_argument("--since-ts", type=int, default=None, help="Analyze only candles with timestamp_closed >= this Unix timestamp.")
    parser.add_argument("--csv-out", default=None, help="Output directory for CSV exports.")
    parser.add_argument("--json-out", default=None, help="Output file for JSON summary.")
    parser.add_argument("--only-holds", action="store_true", help="Restrict analysis to HOLD / HOLD_* execution rows.")
    parser.add_argument("--only-predictive", action="store_true", help="Restrict analysis to predictive directional rows.")
    parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_CASES, help="Row count for the recent-cases table.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    diagnostics = analyze_logs(
        log_inputs=args.log,
        last=args.last,
        since_ts=args.since_ts,
        only_holds=args.only_holds,
        only_predictive=args.only_predictive,
        recent=args.recent,
        csv_out=args.csv_out,
        json_out=args.json_out,
    )
    print(diagnostics.report, end="")


if __name__ == "__main__":
    main()
