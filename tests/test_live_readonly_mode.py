from __future__ import annotations

import logging

import pandas as pd


def test_live_readonly_ledger_marks_blocked(monkeypatch, tmp_path):
    t1 = pd.Timestamp("2024-01-01T01:00:00Z")

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    ledger_path = tmp_path / "ledger"
    monkeypatch.setenv("LEDGER_DIR", str(ledger_path))
    monkeypatch.setenv("LEDGER_ENABLED", "1")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    monkeypatch.setenv("PAPER_TRADING", "0")
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    monkeypatch.setenv("SAFE_RUN", "0")
    monkeypatch.setenv("LIVE_READONLY", "1")
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_AGE_SECONDS", "999999999")

    from app.core import trade_ledger
    captured = []
    monkeypatch.setattr(trade_ledger, "_ledger_dir", lambda: ledger_path)
    monkeypatch.setattr(trade_ledger, "append_event", lambda **kwargs: captured.append(kwargs))

    trade_plan = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 0.1,
        "client_order_id": "BTCUSDT-123-entry",
        "action": "OPEN",
        "stop_loss": {"price": 0.5, "client_order_id": "BTCUSDT-123-sl"},
        "take_profit": {"price": 2.0, "client_order_id": "BTCUSDT-123-tp"},
        "leverage": 1,
        "margin_type": "isolated",
        "timestamp": int(t1.timestamp()),
        "timeframe": "5m",
    }

    from app.services.execution_service import ExecutionService
    exe = ExecutionService(logger=logging.getLogger("test"))
    exe.execute_trade_plan(trade_plan)

    attempts = [r for r in captured if r.get("event_type") == "execution_attempted"]
    assert attempts, "execution_attempted event not captured"
    assert any(a.get("details", {}).get("result") == "blocked_readonly" for a in attempts)
