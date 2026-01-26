from __future__ import annotations

import pytest

from core.runtime_mode import reset_runtime_settings
from app.services import notifications


def _set_live_env(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_TRADING", "0")
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    monkeypatch.setenv("SAFE_RUN", "0")
    monkeypatch.setenv("LIVE_READONLY", "0")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    reset_runtime_settings()

def _set_guard_env(monkeypatch, *, safe_run: str, dry_run: str) -> None:
    monkeypatch.setenv("PAPER_TRADING", "0")
    monkeypatch.setenv("SAFE_RUN", safe_run)
    monkeypatch.setenv("DRY_RUN_ONLY", dry_run)
    monkeypatch.setenv("LIVE_READONLY", "0")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    reset_runtime_settings()

def _set_readonly_env(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_TRADING", "0")
    monkeypatch.setenv("DRY_RUN_ONLY", "0")
    monkeypatch.setenv("SAFE_RUN", "0")
    monkeypatch.setenv("LIVE_READONLY", "1")
    monkeypatch.setenv("TRADE_ENABLED", "1")
    reset_runtime_settings()


def test_notifications_direct_submission_blocked_in_live(monkeypatch):
    _set_live_env(monkeypatch)
    with pytest.raises(RuntimeError, match="direct_order_submission_blocked"):
        notifications.place_order_via_rest(symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.1)


@pytest.mark.parametrize("safe_run,dry_run", [("1", "0"), ("0", "1")])
def test_order_mutation_blocked_in_safe_or_dry(monkeypatch, safe_run, dry_run):
    _set_guard_env(monkeypatch, safe_run=safe_run, dry_run=dry_run)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.place_order_via_rest(symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.1)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.cancel_order_via_rest(symbol="BTCUSDT", orderId=1)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.set_leverage_via_rest("BTCUSDT", 2)


def test_order_mutation_blocked_in_live_readonly(monkeypatch):
    _set_readonly_env(monkeypatch)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.place_order_via_rest(symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.1)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.cancel_order_via_rest(symbol="BTCUSDT", orderId=1)
    with pytest.raises(RuntimeError, match="order_submission_blocked"):
        notifications.set_leverage_via_rest("BTCUSDT", 2)


def test_live_readonly_allows_non_mutating_calls(monkeypatch):
    _set_readonly_env(monkeypatch)
    from core.execution import binance_futures
    binance_futures._block_order_mutation("/fapi/v1/klines")


def test_run_once_contracts_is_only_execution_path(monkeypatch):
    from app import run

    calls = {"count": 0}

    def _fake_contracts(_app):
        calls["count"] += 1

    monkeypatch.setattr(run, "_run_once_contracts", _fake_contracts)
    app = run.TraderApp(symbol="BTCUSDT")
    app.run_once()
    assert calls["count"] == 1
