"""
Tests for JSON schema validation.
"""
import builtins
import importlib
import logging

import pytest
from app.core import validation
from app.core.validation import validate_payload, validate_decision, validate_trade_plan


@pytest.mark.critical
def test_validate_payload_valid():
    """Test validation of valid payload."""
    payload = {
        "market_identity": {
            "exchange": "binance_futures",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "timestamp_closed": 1234567890
        },
        "price_snapshot": {
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "mark": 50000.0
        },
        "fees": {
            "maker": 0.0002,
            "taker": 0.0004
        },
        "account_state": {
            "equity": 10000.0,
            "available": 9500.0,
            "margin_type": "isolated",
            "leverage": 5
        },
        "position_state": {
            "side": None,
            "qty": 0.0,
            "entry": 0.0,
            "unrealized_pnl": 0.0,
            "liq_price": None
        },
        "features_ltf": {
            "close": 1000.0,
            "close_prev": 995.0,
            "high": 1005.0,
            "low": 990.0,
            "ema50": 990.0,
            "ema120": 990.0,
            "donchian_high_240": 1010.0,
            "donchian_low_240": 950.0,
            "atr14": 20.0,
            "atr14_sma20": 18.0,
            "donchian_high_20": 1005.0,
            "donchian_low_20": 960.0,
            "consec_close_above_donchian_20": 2.0,
            "consec_close_below_donchian_20": 0.0,
            "bb_upper": 1020.0,
            "bb_lower": 980.0,
            "bb_mid": 1000.0,
            "candle_body_ratio": 0.7,
            "volume_ratio": 1.5,
            "rsi14": 45.0,
            "rsi14_prev": 46.0,
            "consec_above_ema50": 2.0,
            "consec_below_ema50": 0.0,
            "consec_above_ema50_prev": 1.0,
            "consec_below_ema50_prev": 2.0,
            "close_max_n": 1010.0,
            "close_min_n": 990.0,
            "time_exit_bars": 12
        },
        "context_htf": {
            "ema200": 990.0,
            "ema200_prev_n": 950.0,
            "ema200_slope_norm": 0.05,
            "consec_above_ema200": 5.0,
            "consec_below_ema200": 0.0,
            "consec_higher_close": 4.0,
            "consec_lower_close": 0.0,
            "close": 1000.0,
            "trend": "up",
            "atr14": 80.0,
            "timeframe": "1h"
        },
        "risk_policy": {
            "risk_per_trade": 0.05,
            "max_daily_drawdown": 0.03,
            "max_consecutive_losses": 3,
            "min_rr": 1.8
        },
        "market_meta": {
            "funding_rate": 0.0001,
            "funding_next_ts": 1234567890
        },
        "exchange_limits": {
            "tick_size": 0.1,
            "step_size": 0.001,
            "min_qty": 0.001
        }
    }
    
    ok, errors = validate_payload(payload)
    assert ok is True
    assert errors == []


def test_jsonschema_validation_available():
    assert validation.jsonschema is not None


def test_jsonschema_missing_logs_and_rejects(monkeypatch, caplog):
    original_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("forced missing jsonschema")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    caplog.set_level(logging.ERROR)

    try:
        missing_validation = importlib.reload(validation)
        ok, errors = missing_validation.validate_payload({})
        assert ok is False
        assert errors == ["jsonschema_not_installed"]
        assert any(
            "Missing dependency: jsonschema" in record.message
            and ".\\.venv\\Scripts\\python.exe -m pip install jsonschema" in record.message
            for record in caplog.records
        )
    finally:
        importlib.reload(validation)


@pytest.mark.critical
def test_validate_decision_valid():
    """Test validation of valid decision."""
    decision = {
        "intent": "LONG",
        "reject_reasons": [],
        "entry": 50000.0,
        "sl": 49100.0,
        "tp": 51500.0,
        "rr": 1.67,
        "timestamp": 1234567890
    }
    
    ok, errors = validate_decision(decision)
    assert isinstance(ok, bool)
    assert isinstance(errors, list)


def test_validate_decision_hold():
    """Test validation of HOLD decision."""
    decision = {
        "intent": "HOLD",
        "reject_reasons": ["no_signal"],
        "timestamp": 1234567890
    }
    
    ok, errors = validate_decision(decision)
    assert isinstance(ok, bool)
    assert isinstance(errors, list)


@pytest.mark.critical
def test_validate_trade_plan_valid():
    """Test validation of valid trade plan."""
    trade_plan = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 0.1,
        "client_order_id": "test-order-123",
        "action": "OPEN",
        "stop_loss": {
            "price": 49100.0,
            "client_order_id": "test-order-123-sl"
        },
        "take_profit": {
            "price": 51500.0,
            "client_order_id": "test-order-123-tp"
        },
        "leverage": 5,
        "margin_type": "isolated",
        "timestamp": 1234567890
    }
    
    ok, errors = validate_trade_plan(trade_plan)
    assert isinstance(ok, bool)
    assert isinstance(errors, list)


def test_validate_trade_plan_close():
    trade_plan = {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "type": "MARKET",
        "quantity": 0.1,
        "client_order_id": "test-order-123-close",
        "action": "CLOSE",
        "timestamp": 1234567890
    }
    ok, errors = validate_trade_plan(trade_plan)
    assert isinstance(ok, bool)
    assert isinstance(errors, list)


def test_validate_payload_missing_fields():
    """Test validation fails for missing required fields."""
    payload = {
        "market_identity": {
            "exchange": "binance_futures",
            "symbol": "BTCUSDT"
            # Missing timeframe and timestamp_closed
        }
    }
    
    ok, errors = validate_payload(payload)
    # Should fail validation
    assert isinstance(ok, bool)
    assert isinstance(errors, list)
