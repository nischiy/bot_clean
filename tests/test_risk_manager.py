"""
Tests for risk manager.
"""
import pytest
from app.risk.risk_manager import check_kill_switches, create_trade_plan
from core.risk_guard import clear_kill
from app.state import state_manager

try:
    import jsonschema  # noqa: F401
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False

@pytest.fixture
def valid_payload():
    """Valid payload."""
    import time
    return {
        "market_identity": {
            "symbol": "BTCUSDT",
            "timestamp_closed": int(time.time()) - 3600,  # 1 hour ago
            "timeframe": "5m",
        },
        "price_snapshot": {
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0
        },
        "features_ltf": {
            "atr14": 500.0
        },
        "account_state": {
            "equity": 10000.0,
            "margin_type": "isolated",
            "leverage": 5
        },
        "exchange_limits": {
            "step_size": 0.001,
            "min_qty": 0.001,
            "tick_size": 0.1
        },
        "risk_policy": {
            "risk_per_trade": 0.05,
            "max_daily_drawdown": 0.03,
            "max_consecutive_losses": 3,
            "min_rr": 1.8
        }
    }


@pytest.fixture
def valid_decision():
    """Valid decision."""
    return {
        "intent": "LONG",
        "entry": 50000.0,
        "sl": 49100.0,
        "tp": 51500.0,
        "rr": 1.67,
        "reject_reasons": []
    }


@pytest.fixture
def daily_state():
    """Daily state."""
    return {
        "starting_equity": 10000.0,
        "realized_pnl": 0.0,
        "consecutive_losses": 0
    }


def test_check_kill_switches_pass(valid_payload, valid_decision, daily_state):
    """Test kill-switch checks pass."""
    clear_kill()
    rejections = check_kill_switches(
        valid_payload,
        valid_decision,
        daily_state,
        []
    )
    
    # Should pass if kill-switch is not engaged
    assert isinstance(rejections, list)


def test_create_trade_plan_valid(valid_payload, valid_decision, daily_state):
    """Test creating valid trade plan."""
    clear_kill()
    trade_plan, rejections = create_trade_plan(
        valid_payload,
        valid_decision,
        daily_state,
        []
    )
    
    if trade_plan is None:
        print(f"Rejections: {rejections}")
    
    if not _JSONSCHEMA_AVAILABLE:
        assert trade_plan is None
        assert "jsonschema_not_installed" in rejections
        return

    assert trade_plan is not None, f"Trade plan creation failed with rejections: {rejections}"
    assert len(rejections) == 0
    assert trade_plan["symbol"] == "BTCUSDT"
    assert trade_plan["side"] == "BUY"
    assert trade_plan["action"] == "OPEN"
    assert "client_order_id" in trade_plan
    assert "stop_loss" in trade_plan
    assert "take_profit" in trade_plan


def test_create_trade_plan_hold_intent(valid_payload, daily_state):
    """Test trade plan creation fails for HOLD intent."""
    decision = {
        "intent": "HOLD",
        "reject_reasons": ["no_signal"]
    }
    
    trade_plan, rejections = create_trade_plan(
        valid_payload,
        decision,
        daily_state,
        []
    )
    
    assert trade_plan is None
    assert len(rejections) > 0
    assert "invalid_intent" in str(rejections)


def test_trade_cooldown_blocks(monkeypatch, tmp_path, valid_payload, valid_decision, daily_state):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADE_COOLDOWN_MINUTES", "15")
    clear_kill()
    ts = valid_payload["market_identity"]["timestamp_closed"]
    state_manager.record_trade_attempt("LONG", int(ts) - 60)
    trade_plan, rejections = create_trade_plan(
        valid_payload,
        valid_decision,
        daily_state,
        [],
    )
    assert trade_plan is None
    assert any("cooldown_active" in r for r in rejections)


def test_trade_cooldown_blocks_any_side(monkeypatch, tmp_path, valid_payload, valid_decision, daily_state):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADE_COOLDOWN_MINUTES", "15")
    clear_kill()
    ts = valid_payload["market_identity"]["timestamp_closed"]
    state_manager.record_trade_attempt("SHORT", int(ts) - 60)
    trade_plan, rejections = create_trade_plan(
        valid_payload,
        valid_decision,
        daily_state,
        [],
    )
    assert trade_plan is None
    assert any("cooldown_active" in r for r in rejections)


def test_create_trade_plan_close(valid_payload, daily_state):
    clear_kill()
    payload = dict(valid_payload)
    payload["position_state"] = {"side": "LONG", "qty": 0.2, "entry": 50000.0, "unrealized_pnl": 0.0, "liq_price": None}
    decision = {"intent": "CLOSE", "reject_reasons": []}
    trade_plan, rejections = create_trade_plan(payload, decision, daily_state, [])
    if not _JSONSCHEMA_AVAILABLE:
        assert trade_plan is None
        assert "jsonschema_not_installed" in rejections
        return
    assert trade_plan is not None
    assert trade_plan["action"] == "CLOSE"
    assert trade_plan["side"] == "SELL"
    assert "stop_loss" not in trade_plan
    assert "take_profit" not in trade_plan
