"""
Tests for state manager.
"""
import pytest
import os
import json
from pathlib import Path
from app.state.state_manager import (
    save_last_closed_candle_ts,
    load_last_closed_candle_ts,
    save_daily_state,
    load_daily_state,
    save_trade_identifier,
    has_trade_identifier,
    reconcile_positions,
    load_or_initialize_daily_state,
)


def test_save_load_candle_ts(monkeypatch, tmp_path):
    """Test saving and loading candle timestamp."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    timestamp = 1234567890
    save_last_closed_candle_ts(timestamp)
    loaded = load_last_closed_candle_ts()
    assert loaded == timestamp


def test_save_load_daily_state(monkeypatch, tmp_path):
    """Test saving and loading daily state."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    date_str = "2024-01-01"
    state = {
        "date": date_str,
        "starting_equity": 10000.0,
        "realized_pnl": 100.0,
        "consecutive_losses": 0
    }
    save_daily_state(date_str, state)
    loaded = load_daily_state(date_str)
    assert loaded["date"] == date_str
    assert loaded["starting_equity"] == 10000.0


def test_trade_identifier(monkeypatch, tmp_path):
    """Test trade identifier tracking."""
    import time
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    client_order_id = f"test-order-{int(time.time() * 1000)}"  # Unique ID
    trade_hash = "hash-123"
    
    # Should not exist initially (using unique ID)
    assert not has_trade_identifier(client_order_id)
    
    # Save identifier
    save_trade_identifier(client_order_id, trade_hash)
    
    # Should exist now
    assert has_trade_identifier(client_order_id)


def test_reconcile_positions_no_positions(monkeypatch, tmp_path):
    """Test reconciliation with no positions."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    ok, errors = reconcile_positions([])
    assert ok is True
    assert len(errors) == 0


def test_reconcile_positions_one_position(monkeypatch, tmp_path):
    """Test reconciliation with one position."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    positions = [{
        "symbol": "BTCUSDT",
        "positionAmt": "0.1",
        "side": "LONG"
    }]
    ok, errors = reconcile_positions(positions, open_orders=[])
    assert ok is False
    assert any("position_without_sl" in e for e in errors)

def test_reconcile_positions_with_sl(monkeypatch, tmp_path):
    """Test reconciliation passes when SL exists."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    positions = [{
        "symbol": "BTCUSDT",
        "positionAmt": "0.1",
        "side": "LONG"
    }]
    open_orders = [{
        "symbol": "BTCUSDT",
        "side": "SELL",
        "type": "STOP_MARKET",
        "stopPrice": "49000",
        "closePosition": True,
        "reduceOnly": True,
    }]
    ok, errors = reconcile_positions(positions, open_orders=open_orders, require_tp=False)
    assert ok is True
    assert len(errors) == 0

def test_reconcile_positions_local_state_mismatch(monkeypatch, tmp_path):
    """Mismatch between local and exchange state should hard stop."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    positions = [{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
    local_state = {"side": None, "qty": 0.0}
    ok, errors = reconcile_positions(positions, open_orders=[], local_state=local_state)
    assert ok is False
    assert any("local_state_mismatch" in e for e in errors)


def test_reconcile_positions_requires_tp(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    positions = [{"symbol": "BTCUSDT", "positionAmt": "0.1", "side": "LONG"}]
    open_orders = [{
        "symbol": "BTCUSDT",
        "side": "SELL",
        "type": "STOP_MARKET",
        "stopPrice": "49000",
        "closePosition": True,
        "reduceOnly": True,
    }]
    ok, errors = reconcile_positions(positions, open_orders=open_orders, require_tp=True)
    assert ok is False
    assert any("position_without_tp" in e for e in errors)


def test_reconcile_positions_multiple_positions(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.1"},
        {"symbol": "ETHUSDT", "positionAmt": "0.2"},
    ]
    ok, errors = reconcile_positions(positions, open_orders=[])
    assert ok is False
    assert any("multiple_positions" in e for e in errors)


def test_daily_state_same_day(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    state = load_or_initialize_daily_state(1000.0)
    same_state = load_or_initialize_daily_state(2000.0)
    assert same_state["date"] == state["date"]
    assert same_state["starting_equity"] == 1000.0


def test_daily_state_new_day(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    first = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
    next_day = first + timedelta(days=1)
    state = load_or_initialize_daily_state(1000.0, now=first)
    next_state = load_or_initialize_daily_state(2000.0, now=next_day)
    assert next_state["date"] != state["date"]
    assert next_state["starting_equity"] == 2000.0
    assert next_state["consecutive_losses"] == 0


def test_daily_state_uses_utc_boundary(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    local_tz = timezone(timedelta(hours=3))
    local_time = datetime(2024, 1, 2, 1, 0, tzinfo=local_tz)  # 2024-01-01 22:00 UTC
    state = load_or_initialize_daily_state(1000.0, now=local_time)
    assert state["date"] == "2024-01-01"


def test_daily_state_utc_rollover_resets_pnl(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    before_midnight = datetime(2024, 1, 1, 23, 50, tzinfo=timezone.utc)
    after_midnight = before_midnight + timedelta(minutes=20)

    state = load_or_initialize_daily_state(1000.0, now=before_midnight)
    state["realized_pnl"] = 50.0
    state["consecutive_losses"] = 2
    save_daily_state(state["date"], state)

    next_state = load_or_initialize_daily_state(1500.0, now=after_midnight)
    assert next_state["date"] != state["date"]
    assert next_state["starting_equity"] == 1500.0
    assert next_state["realized_pnl"] == 0.0
    assert next_state["consecutive_losses"] == 0
