"""
Tests for payload builder.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from app.data.payload_builder import build_payload

try:
    import jsonschema  # noqa: F401
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False

@pytest.fixture
def sample_df_ltf():
    """Sample 5m DataFrame."""
    dates = pd.date_range(start="2024-01-01", periods=300, freq="5min", tz=timezone.utc)
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.randn(300) * 100)
    high = close + np.random.rand(300) * 200
    low = close - np.random.rand(300) * 200
    volume = np.random.rand(300) * 1000
    
    df = pd.DataFrame({
        "time": dates,
        "open_time": dates,
        "close_time": dates + pd.Timedelta(minutes=5),
        "open": close - np.random.rand(300) * 100,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume
    })
    return df


@pytest.fixture
def sample_df_htf():
    """Sample 1h DataFrame."""
    dates = pd.date_range(start="2024-01-01", periods=220, freq="1h", tz=timezone.utc)
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.randn(220) * 200)
    
    df = pd.DataFrame({
        "time": dates,
        "open_time": dates,
        "close_time": dates + pd.Timedelta(hours=1),
        "open": close - np.random.rand(220) * 200,
        "high": close + np.random.rand(220) * 300,
        "low": close - np.random.rand(220) * 300,
        "close": close,
        "volume": np.random.rand(220) * 2000
    })
    return df


def test_build_payload_valid(sample_df_ltf, sample_df_htf):
    """Test building valid payload."""
    account_snapshot = {
        "equity_usd": 10000.0,
        "available": 9500.0,
        "margin_type": "isolated"
    }
    position_snapshot = {
        "side": None,
        "qty": 0.0,
        "entry": 0.0,
        "unrealized_pnl": 0.0,
        "liq_price": None
    }
    price_snapshot = {
        "value": 50000.0,
        "bid": 49999.0,
        "ask": 50001.0,
        "mark": 50000.0
    }
    filters_snapshot = {
        "step_size": 0.001,
        "min_qty": 0.001,
        "tick_size": 0.1,
        "value": {
            "LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
            "PRICE_FILTER": {"tickSize": "0.1"}
        }
    }
    timestamp_closed = int(datetime.now(timezone.utc).timestamp())
    
    payload, errors = build_payload(
        symbol="BTCUSDT",
        df_ltf=sample_df_ltf,
        df_htf=sample_df_htf,
        account_snapshot=account_snapshot,
        position_snapshot=position_snapshot,
        price_snapshot=price_snapshot,
        filters_snapshot=filters_snapshot,
        timestamp_closed=timestamp_closed,
        timeframe="5m",
        htf_timeframe="1h",
    )

    if not _JSONSCHEMA_AVAILABLE:
        assert payload is None
        assert "jsonschema_not_installed" in errors
        return

    assert payload is not None
    assert len(errors) == 0
    assert payload["market_identity"]["symbol"] == "BTCUSDT"
    assert payload["account_state"]["equity"] == 10000.0


def test_build_payload_missing_data(sample_df_ltf):
    """Test payload building fails with missing data."""
    account_snapshot = {
        "equity_usd": None,  # Missing equity
        "available": None
    }
    position_snapshot = {}
    price_snapshot = {}  # Missing price
    filters_snapshot = {}  # Missing filters
    timestamp_closed = int(datetime.now(timezone.utc).timestamp())
    
    payload, errors = build_payload(
        symbol="BTCUSDT",
        df_ltf=sample_df_ltf,
        df_htf=None,
        account_snapshot=account_snapshot,
        position_snapshot=position_snapshot,
        price_snapshot=price_snapshot,
        filters_snapshot=filters_snapshot,
        timestamp_closed=timestamp_closed,
        timeframe="5m",
        htf_timeframe="1h",
    )
    
    assert payload is None
    assert len(errors) > 0
    assert any("missing" in err.lower() or "invalid" in err.lower() for err in errors)
