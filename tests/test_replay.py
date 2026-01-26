from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import pytest

from app import replay

try:
    import jsonschema  # noqa: F401
    _JSONSCHEMA_AVAILABLE = True
except Exception:
    _JSONSCHEMA_AVAILABLE = False


def _df_ltf():
    ts = pd.date_range("2024-01-01", periods=5, freq="5min", tz=timezone.utc)
    return pd.DataFrame({
        "open_time": ts - pd.Timedelta(minutes=5),
        "close_time": ts,
        "open": [100, 101, 102, 103, 104],
        "high": [101, 102, 103, 104, 105],
        "low": [99, 100, 101, 102, 103],
        "close": [100, 101, 102, 103, 104],
        "volume": [1, 1, 1, 1, 1],
    })


def _df_htf():
    ts = pd.date_range("2024-01-01", periods=2, freq="1h", tz=timezone.utc)
    return pd.DataFrame({
        "open_time": ts - pd.Timedelta(hours=1),
        "close_time": ts,
        "open": [100, 104],
        "high": [102, 106],
        "low": [98, 102],
        "close": [101, 105],
        "volume": [1, 1],
    })


def test_replay_runs_deterministic(monkeypatch, tmp_path):
    if not _JSONSCHEMA_AVAILABLE:
        pytest.skip("jsonschema required for replay")

    df1 = _df_ltf()
    df4 = _df_htf()

    def _fake_get_klines(self, symbol, interval, **_kwargs):
        return df4 if interval == "1h" else df1

    monkeypatch.setattr("app.services.market_data.HttpMarketData.get_klines", _fake_get_klines)
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_AGE_SECONDS", "999999999")
    monkeypatch.setattr("app.run._read_preflight", lambda *_a, **_k: {
        "account": {"equity_usd": 1000.0, "wallet_usdt": 1000.0},
        "filters": {"step_size": 0.1, "min_qty": 0.1, "tick_size": 0.1},
        "price": {"value": 100.0, "bid": 100.0, "ask": 100.0, "mark": 100.0},
        "rejects": [],
    })

    summary = replay.run_replay(
        "BTCUSDT",
        "2024-01-01",
        "2024-01-01",
        state_dir=str(tmp_path),
    )

    assert "holds" in summary
    assert "trade_intents" in summary
    assert "blocked_trades" in summary
