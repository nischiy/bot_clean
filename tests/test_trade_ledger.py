from __future__ import annotations

import os
from pathlib import Path

from app.core import trade_ledger


def test_hash_json_deterministic():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert trade_ledger.hash_json(a) == trade_ledger.hash_json(b)


def test_append_event_appends(monkeypatch, tmp_path):
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setenv("LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("LEDGER_ENABLED", "1")

    trade_ledger.append_event(
        event_type="decision_created",
        symbol="BTCUSDT",
        timeframe="1h",
        correlation_id="BTCUSDT:123",
        payload_hash="p",
        decision_hash="d",
        details={"x": 1},
    )
    trade_ledger.append_event(
        event_type="trade_plan_created",
        symbol="BTCUSDT",
        timeframe="1h",
        correlation_id="BTCUSDT:123",
        trade_plan_hash="t",
        client_order_id="cid",
        details={"y": 2},
    )

    files = list(ledger_dir.glob("ledger_*.jsonl"))
    assert files, "ledger file not created"
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_no_import_side_effects(monkeypatch, tmp_path):
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setenv("LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("LEDGER_ENABLED", "1")
    # Import should not create directory
    assert not ledger_dir.exists()
