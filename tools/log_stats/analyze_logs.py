from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALLOWLIST_EVENTS = {
    "decision_candle",
    "preflight_reject",
    "kill_switch_triggered",
    "decision_created",
    "trade_plan_created",
    "execution_attempted",
    "execution_submitted",
    "sltp_submitted",
    "position_closed",
}

DECISION_COLUMNS = (
    "timestamp_closed",
    "decision",
    "selected_strategy",
    "trend",
    "regime",
    "close",
    "atr14_5m",
    "atr14_htf",
    "atr_ratio",
    "volume_ratio_5m",
    "rsi14_5m",
    "candle_body_ratio",
    "k_overextension",
    "cont_reject_codes",
    "reject_reasons",
)


def _parse_json_from_line(line: str) -> Optional[Dict[str, Any]]:
    start = line.find("{")
    if start == -1:
        return None
    end = line.rfind("}")
    if end == -1 or end <= start:
        return None
    try:
        return json.loads(line[start : end + 1])
    except Exception:
        return None


def _filter_by_ts(
    obj: Dict[str, Any],
    from_ts: Optional[int],
    to_ts: Optional[int],
) -> bool:
    if from_ts is None and to_ts is None:
        return True
    ts_val = obj.get("timestamp_closed")
    if ts_val is None:
        return False
    try:
        ts_int = int(ts_val)
    except Exception:
        return False
    if from_ts is not None and ts_int < from_ts:
        return False
    if to_ts is not None and ts_int > to_ts:
        return False
    return True


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _date_from_ts(ts_val: Any) -> Optional[str]:
    try:
        ts_int = int(ts_val)
    except Exception:
        return None
    return datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime("%Y-%m-%d")


def _date_from_filename(path: str) -> Optional[str]:
    basename = os.path.basename(path)
    match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", basename)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def _add_metrics(accum: Dict[str, float], row: Dict[str, Any]) -> None:
    for key in ("atr_ratio", "volume_ratio_5m", "candle_body_ratio", "k_overextension", "rsi14_5m"):
        try:
            val = row.get(key)
            if val is None:
                continue
            accum[key] += float(val)
        except Exception:
            continue


def _avg_metrics(total: Dict[str, float], count: int) -> Dict[str, Optional[float]]:
    if count <= 0:
        return {k: None for k in ("atr_ratio", "volume_ratio_5m", "candle_body_ratio", "k_overextension", "rsi14_5m")}
    return {k: round(total.get(k, 0.0) / count, 6) for k in total}


def _normalize_list(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, list):
        return "|".join(str(v) for v in val)
    return str(val)


class DailyWriter:
    def __init__(
        self,
        base_out: str,
        date_key: str,
        *,
        per_file_label: Optional[str],
        only_decisions: bool,
        source_label: str,
    ) -> None:
        self.date_key = date_key
        self.only_decisions = only_decisions
        self.source_label = source_label
        self.base_dir = self._build_base_dir(base_out, date_key, per_file_label)
        self.decisions_dir = os.path.join(self.base_dir, "decisions")
        self.stats_dir = os.path.join(self.base_dir, "stats")
        self.human_dir = os.path.join(self.base_dir, "human_logs")
        self.raw_dir = os.path.join(self.base_dir, "raw_extract")
        _ensure_dir(self.decisions_dir)
        _ensure_dir(self.raw_dir)
        if not self.only_decisions:
            _ensure_dir(self.stats_dir)
            _ensure_dir(self.human_dir)

        self.decisions_path = os.path.join(self.decisions_dir, "decisions.jsonl")
        self.decisions_handle = open(self.decisions_path, "w", encoding="utf-8")
        self.decisions_csv_handle = None
        self.decisions_csv_writer = None
        self.human_handle = None

        self.total_decisions = 0
        self.hold_none = 0
        self.decision_counts = Counter()
        self.strategy_counts = Counter()
        self.reject_reasons = Counter()
        self.cont_reject_codes = Counter()
        self.near_miss_1 = Counter()
        self.near_miss_2 = Counter()
        self.near_miss_1_metrics = defaultdict(lambda: defaultdict(float))
        self.near_miss_2_metrics = defaultdict(lambda: defaultdict(float))

        if not self.only_decisions:
            self.decisions_csv_handle = open(
                os.path.join(self.decisions_dir, "decisions.csv"),
                "w",
                encoding="utf-8",
                newline="",
            )
            self.decisions_csv_writer = csv.writer(self.decisions_csv_handle)
            self.decisions_csv_writer.writerow(DECISION_COLUMNS)
            self.human_handle = open(os.path.join(self.human_dir, "human.log"), "w", encoding="utf-8")

    @staticmethod
    def _build_base_dir(base_out: str, date_key: str, per_file_label: Optional[str]) -> str:
        root = os.path.join(base_out, "daily", date_key)
        if per_file_label:
            return os.path.join(root, "per_file", per_file_label)
        return root

    def write_decision(self, obj: Dict[str, Any]) -> None:
        self.decisions_handle.write(json.dumps(obj, sort_keys=True, separators=(",", ":")))
        self.decisions_handle.write("\n")
        self.total_decisions += 1

        if self.only_decisions or self.decisions_csv_writer is None:
            return

        row = [
            obj.get("timestamp_closed"),
            obj.get("decision"),
            obj.get("selected_strategy"),
            obj.get("trend"),
            obj.get("regime"),
            obj.get("close"),
            obj.get("atr14_5m"),
            obj.get("atr14_htf"),
            obj.get("atr_ratio"),
            obj.get("volume_ratio_5m"),
            obj.get("rsi14_5m"),
            obj.get("candle_body_ratio"),
            obj.get("k_overextension"),
            _normalize_list(obj.get("cont_reject_codes")),
            _normalize_list(obj.get("reject_reasons")),
        ]
        self.decisions_csv_writer.writerow(row)

        decision = obj.get("decision")
        selected_strategy = obj.get("selected_strategy")
        if decision:
            self.decision_counts[str(decision)] += 1
        if selected_strategy:
            self.strategy_counts[str(selected_strategy)] += 1

        if decision != "HOLD" or selected_strategy != "NONE":
            return

        self.hold_none += 1
        for reason in obj.get("reject_reasons") or []:
            self.reject_reasons[reason] += 1

        cont_codes = obj.get("cont_reject_codes") or []
        for code in cont_codes:
            self.cont_reject_codes[code] += 1

        if len(cont_codes) == 1:
            code = cont_codes[0]
            self.near_miss_1[code] += 1
            _add_metrics(self.near_miss_1_metrics[code], obj)
        elif len(cont_codes) == 2:
            combo = "|".join(sorted(str(c) for c in cont_codes))
            self.near_miss_2[combo] += 1
            _add_metrics(self.near_miss_2_metrics[combo], obj)

    def write_human(self, line: str) -> None:
        if self.only_decisions or self.human_handle is None:
            return
        self.human_handle.write(line)
        if not line.endswith("\n"):
            self.human_handle.write("\n")

    def finalize(self) -> None:
        self.decisions_handle.close()
        if self.decisions_csv_handle:
            self.decisions_csv_handle.close()
        if self.human_handle:
            self.human_handle.close()

        if self.only_decisions:
            return

        reject_path = os.path.join(self.stats_dir, "reject_reasons_top.csv")
        cont_path = os.path.join(self.stats_dir, "cont_reject_codes_top.csv")
        near1_path = os.path.join(self.stats_dir, "near_miss_1_condition.csv")
        near2_path = os.path.join(self.stats_dir, "near_miss_2_conditions.csv")
        decision_counts_path = os.path.join(self.stats_dir, "decision_counts.csv")
        strategy_counts_path = os.path.join(self.stats_dir, "strategy_counts.csv")
        summary_path = os.path.join(self.stats_dir, "summary.md")

        _write_csv(
            decision_counts_path,
            ("decision", "count"),
            [(k, v) for k, v in self.decision_counts.most_common()],
        )
        _write_csv(
            strategy_counts_path,
            ("strategy", "count"),
            [(k, v) for k, v in self.strategy_counts.most_common()],
        )
        _write_csv(
            reject_path,
            ("reason", "count"),
            [(reason, count) for reason, count in self.reject_reasons.most_common()],
        )
        _write_csv(
            cont_path,
            ("code", "count"),
            [(code, count) for code, count in self.cont_reject_codes.most_common()],
        )

        near1_rows = []
        for code, count in self.near_miss_1.most_common():
            metrics = _avg_metrics(self.near_miss_1_metrics[code], count)
            near1_rows.append(
                (
                    code,
                    count,
                    metrics["atr_ratio"],
                    metrics["volume_ratio_5m"],
                    metrics["candle_body_ratio"],
                    metrics["k_overextension"],
                    metrics["rsi14_5m"],
                )
            )
        _write_csv(
            near1_path,
            (
                "code",
                "count",
                "avg_atr_ratio",
                "avg_volume_ratio_5m",
                "avg_candle_body_ratio",
                "avg_k_overextension",
                "avg_rsi14_5m",
            ),
            near1_rows,
        )

        near2_rows = []
        for combo, count in self.near_miss_2.most_common():
            metrics = _avg_metrics(self.near_miss_2_metrics[combo], count)
            near2_rows.append(
                (
                    combo,
                    count,
                    metrics["atr_ratio"],
                    metrics["volume_ratio_5m"],
                    metrics["candle_body_ratio"],
                    metrics["k_overextension"],
                    metrics["rsi14_5m"],
                )
            )
        _write_csv(
            near2_path,
            (
                "codes",
                "count",
                "avg_atr_ratio",
                "avg_volume_ratio_5m",
                "avg_candle_body_ratio",
                "avg_k_overextension",
                "avg_rsi14_5m",
            ),
            near2_rows,
        )

        with open(summary_path, "w", encoding="utf-8") as handle:
            handle.write("# Log Summary\n\n")
            handle.write(f"- Source: `{self.source_label}`\n")
            handle.write(f"- Date: `{self.date_key}`\n")
            handle.write(f"- Decisions scanned: {self.total_decisions}\n")
            handle.write(f"- HOLD + selected_strategy=NONE: {self.hold_none}\n")
            handle.write(f"- Decisions output: `{self.decisions_path}`\n")
            handle.write(f"- Decision counts: `{decision_counts_path}`\n")
            handle.write(f"- Strategy counts: `{strategy_counts_path}`\n")
            handle.write(f"- Top rejects CSV: `{reject_path}`\n")
            handle.write(f"- Cont rejects CSV: `{cont_path}`\n")
            handle.write(f"- Near miss (1) CSV: `{near1_path}`\n")
            handle.write(f"- Near miss (2) CSV: `{near2_path}`\n")


def _write_csv(path: str, header: Iterable[str], rows: Iterable[Tuple[Any, ...]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _should_include_human(
    line: str,
    obj: Optional[Dict[str, Any]],
    from_ts: Optional[int],
    to_ts: Optional[int],
) -> bool:
    if "Tick #" in line:
        return False
    if "contracts:" in line:
        return True
    if not obj or obj.get("event") is None:
        return False
    event = obj.get("event")
    if event == "tick_skip_agg":
        return False
    if event == "tick_summary" and obj.get("skip_reason") == "already_processed":
        return False
    if event not in ALLOWLIST_EVENTS and event != "tick_summary":
        return False
    if from_ts is None and to_ts is None:
        return True
    ts_val = obj.get("timestamp_closed")
    if ts_val is None:
        return False
    return _filter_by_ts(obj, from_ts, to_ts)


def _iter_log_lines(path: str) -> Iterable[str]:
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            yield line.rstrip("\n")


def _process_log_file(
    path: str,
    out_root: str,
    *,
    per_file_label: Optional[str],
    only_decisions: bool,
    from_ts: Optional[int],
    to_ts: Optional[int],
) -> None:
    file_date = _date_from_filename(path)
    writers: Dict[str, DailyWriter] = {}
    last_date: Optional[str] = None
    last_ts: Optional[int] = None

    def _get_writer(date_key: str) -> DailyWriter:
        if date_key not in writers:
            writers[date_key] = DailyWriter(
                out_root,
                date_key,
                per_file_label=per_file_label,
                only_decisions=only_decisions,
                source_label=path,
            )
        return writers[date_key]

    for line in _iter_log_lines(path):
        obj = _parse_json_from_line(line)
        if obj and isinstance(obj, dict):
            ts_val = obj.get("timestamp_closed")
            date_key = _date_from_ts(ts_val) if ts_val is not None else None
            if date_key:
                last_date = date_key
                try:
                    last_ts = int(ts_val)
                except Exception:
                    last_ts = last_ts

            if obj.get("event") == "decision_candle":
                if not _filter_by_ts(obj, from_ts, to_ts):
                    continue
                date_key = date_key or file_date or last_date
                if date_key is None:
                    continue
                _get_writer(date_key).write_decision(obj)
                if not only_decisions and _should_include_human(line, obj, from_ts, to_ts):
                    _get_writer(date_key).write_human(line)
                continue

            if not only_decisions and _should_include_human(line, obj, from_ts, to_ts):
                date_key = date_key or last_date or file_date
                if date_key is None:
                    continue
                _get_writer(date_key).write_human(line)
            continue

        if only_decisions:
            continue
        if not _should_include_human(line, None, from_ts, to_ts):
            continue
        if from_ts is not None or to_ts is not None:
            if last_ts is None:
                continue
            if from_ts is not None and last_ts < from_ts:
                continue
            if to_ts is not None and last_ts > to_ts:
                continue
        date_key = last_date or file_date
        if date_key is None:
            continue
        _get_writer(date_key).write_human(line)

    for writer in writers.values():
        writer.finalize()


def _copy_ledger_files(ledger_glob: str, out_root: str) -> None:
    for path in sorted(glob.glob(ledger_glob)):
        match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
        if not match:
            continue
        date_key = match.group(1)
        dest_dir = os.path.join(out_root, "daily", date_key, "ledger")
        _ensure_dir(dest_dir)
        dest_path = os.path.join(dest_dir, os.path.basename(path))
        with open(path, "r", encoding="utf-8") as src, open(dest_path, "w", encoding="utf-8") as dst:
            for line in src:
                dst.write(line)


def analyze_logs(
    runtime_log: Optional[str],
    out_dir: str,
    *,
    sessions_glob: Optional[str],
    ledger_glob: Optional[str],
    only_decisions: bool,
    from_ts: Optional[int],
    to_ts: Optional[int],
) -> None:
    inputs: List[Tuple[str, Optional[str]]] = []
    if runtime_log:
        inputs.append((runtime_log, None))
    if sessions_glob:
        for path in sorted(glob.glob(sessions_glob)):
            label = os.path.splitext(os.path.basename(path))[0]
            inputs.append((path, label))

    if not inputs:
        raise SystemExit("No input logs provided.")

    per_file = len(inputs) > 1
    for path, label in inputs:
        _process_log_file(
            path,
            out_dir,
            per_file_label=label if per_file else None,
            only_decisions=only_decisions,
            from_ts=from_ts,
            to_ts=to_ts,
        )

    if ledger_glob:
        _copy_ledger_files(ledger_glob, out_dir)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze runtime logs for decision-only output and near-miss stats.")
    parser.add_argument("--runtime-log", help="Path to logs/runtime.log")
    parser.add_argument("--sessions-glob", help='Glob for session logs (e.g., "logs/sessions/*.log")')
    parser.add_argument("--ledger-glob", help='Glob for ledger files (e.g., "run/ledger/*.jsonl")')
    parser.add_argument("--out", required=True, help="Output directory root (e.g., reports)")
    parser.add_argument("--only-decisions", action="store_true", help="Only generate decisions.jsonl")
    parser.add_argument("--from-ts", type=int, default=None, help="Filter from timestamp_closed (inclusive)")
    parser.add_argument("--to-ts", type=int, default=None, help="Filter to timestamp_closed (inclusive)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    analyze_logs(
        args.runtime_log,
        args.out,
        sessions_glob=args.sessions_glob,
        ledger_glob=args.ledger_glob,
        only_decisions=args.only_decisions,
        from_ts=args.from_ts,
        to_ts=args.to_ts,
    )


if __name__ == "__main__":
    main()
