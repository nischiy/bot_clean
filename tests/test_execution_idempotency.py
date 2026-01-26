from __future__ import annotations

import time

import pytest
from app.services.execution_service import ExecutionService
from core.runtime_mode import reset_runtime_settings

try:
    import jsonschema  # noqa: F401
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False


def _trade_plan(client_order_id: str) -> dict:
    return {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 0.1,
        "client_order_id": client_order_id,
        "action": "OPEN",
        "stop_loss": {"price": 49000.0, "client_order_id": f"{client_order_id}-sl"},
        "take_profit": {"price": 51000.0, "client_order_id": f"{client_order_id}-tp"},
        "leverage": 1,
        "margin_type": "isolated",
        "timestamp": int(time.time()),
    }


@pytest.mark.critical
def test_execution_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DRY_RUN_ONLY", "1")
    monkeypatch.setenv("SAFE_RUN", "0")
    reset_runtime_settings()
    client_order_id = f"test-{int(time.time()*1000)}"
    plan = _trade_plan(client_order_id)

    exe = ExecutionService()
    res1 = exe.execute_trade_plan(plan)
    if not _JSONSCHEMA_AVAILABLE:
        assert res1["reason"] == "invalid_trade_plan"
        return

    assert res1["reason"] in {"dry_run", "executed", "partial_failure"}

    exe2 = ExecutionService()
    res2 = exe2.execute_trade_plan(plan)
    assert res2["reason"] == "already_executed"


def test_execution_blocks_live(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    monkeypatch.setenv("PAPER_TRADING", "1")
    monkeypatch.setenv("SAFE_RUN", "0")
    reset_runtime_settings()
    exe = ExecutionService()
    res = exe.execute_trade_plan(_trade_plan("test-live"))
    if not _JSONSCHEMA_AVAILABLE:
        assert res["reason"] == "invalid_trade_plan"
        return
    assert res["reason"] == "dry_run"


def test_execution_safe_run_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    monkeypatch.setenv("SAFE_RUN", "1")
    reset_runtime_settings()
    exe = ExecutionService()
    res = exe.execute_trade_plan(_trade_plan("test-safe-run"))
    assert res["reason"] == "safe_run"


def test_execution_rejects_invalid_trade_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DRY_RUN_ONLY", "1")
    reset_runtime_settings()
    exe = ExecutionService()
    res = exe.execute_trade_plan({"symbol": "BTCUSDT"})
    assert res["reason"] == "invalid_trade_plan"
