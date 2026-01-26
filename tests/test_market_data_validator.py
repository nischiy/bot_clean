from __future__ import annotations

import pandas as pd
import pytest
from datetime import timezone

from app.data.market_data_validator import validate_market_data
from core.runtime_mode import reset_runtime_settings


def _df(times):
    return pd.DataFrame({
        "open_time": times - pd.Timedelta(minutes=5),
        "close_time": times,
        "open": [1.0] * len(times),
        "high": [1.0] * len(times),
        "low": [1.0] * len(times),
        "close": [1.0] * len(times),
        "volume": [1.0] * len(times),
    })


def test_missing_columns(monkeypatch):
    reset_runtime_settings()
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    df = pd.DataFrame({"close_time": []})
    ok, errors = validate_market_data(df, None)
    assert not ok
    assert any("missing_col_ltf" in e for e in errors)


def test_time_gap(monkeypatch):
    reset_runtime_settings()
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_GAP_SECONDS", "10")
    times = pd.date_range("2024-01-01", periods=3, freq="5min", tz=timezone.utc)
    df = _df(times)
    ok, errors = validate_market_data(df, None)
    assert not ok
    assert "time_gap_exceeded" in errors


def test_nan_values(monkeypatch):
    reset_runtime_settings()
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    times = pd.date_range("2024-01-01", periods=10, freq="5min", tz=timezone.utc)
    df = _df(times)
    df.loc[df.index[-1], "close"] = float("nan")
    ok, errors = validate_market_data(df, None)
    assert not ok
    assert "nan_in_close" in errors


def test_staleness(monkeypatch):
    reset_runtime_settings()
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_AGE_SECONDS", "1")
    times = pd.date_range("2024-01-01", periods=10, freq="5min", tz=timezone.utc)
    df = _df(times)
    ok, errors = validate_market_data(df, None, now_ts=int(times[-1].timestamp()) + 10)
    assert not ok
    assert "stale_market_data" in errors
