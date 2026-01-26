from __future__ import annotations

import json

from tools.log_stats.analyze_logs import analyze_logs, _parse_json_from_line


def test_parse_json_from_prefixed_line():
    payload = {"event": "decision_candle", "value": 123}
    line = f"[2026-01-01 00:00:00] INFO TraderApp: {json.dumps(payload)}"
    parsed = _parse_json_from_line(line)
    assert parsed == payload


def test_daily_outputs_and_human_filter(tmp_path):
    runtime_log = tmp_path / "runtime.log"
    out_dir = tmp_path / "reports"

    lines = [
        '[ts] INFO TraderApp: {"event":"decision_candle","timestamp_closed":1704067200,"decision":"HOLD","selected_strategy":"NONE","reject_reasons":["C:k","B:body"],"cont_reject_codes":["C:k"],"atr_ratio":1.1,"volume_ratio_5m":1.2,"candle_body_ratio":0.55,"k_overextension":2.4,"rsi14_5m":45,"close":100,"atr14_5m":10,"atr14_htf":20,"trend":"up","regime":"TREND","rsi14_5m":45}',
        '[ts] INFO TraderApp: {"event":"decision_candle","timestamp_closed":1704153600,"decision":"HOLD","selected_strategy":"NONE","reject_reasons":["C:slope","C:break"],"cont_reject_codes":["C:slope","C:break"],"atr_ratio":0.9,"volume_ratio_5m":0.8,"candle_body_ratio":0.4,"k_overextension":2.1,"rsi14_5m":55,"close":101,"atr14_5m":11,"atr14_htf":21,"trend":"down","regime":"TREND"}',
        '[ts] INFO TraderApp: {"event":"tick_summary","timestamp_closed":1704067200,"skip_reason":"already_processed"}',
        '[ts] INFO TraderApp: {"event":"tick_skip_agg","timestamp_closed":1704067200}',
        "Tick #123",
        "contracts: preflight rejects: ['x']",
    ]
    runtime_log.write_text("\n".join(lines), encoding="utf-8")

    analyze_logs(str(runtime_log), str(out_dir), sessions_glob=None, ledger_glob=None, only_decisions=False, from_ts=None, to_ts=None)

    day1 = out_dir / "daily" / "2024-01-01"
    day2 = out_dir / "daily" / "2024-01-02"
    decisions = (day1 / "decisions" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(decisions) == 1
    decisions_day2 = (day2 / "decisions" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(decisions_day2) == 1

    near1 = (day1 / "stats" / "near_miss_1_condition.csv").read_text(encoding="utf-8")
    assert "C:k" in near1

    near2 = (day2 / "stats" / "near_miss_2_conditions.csv").read_text(encoding="utf-8")
    assert "C:break|C:slope" in near2 or "C:slope|C:break" in near2

    human_log = (day1 / "human_logs" / "human.log").read_text(encoding="utf-8")
    assert "contracts: preflight rejects" in human_log
    assert "tick_skip_agg" not in human_log
    assert "already_processed" not in human_log
    assert "Tick #123" not in human_log
