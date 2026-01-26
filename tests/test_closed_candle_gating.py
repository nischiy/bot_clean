from __future__ import annotations

import logging
import pytest
from typing import Any, Dict

import pandas as pd

from app.run import TraderApp

class DummyMD:
    def __init__(self, frames, *, interval: str):
        self.frames = list(frames)
        self.last = None
        self.interval = interval

    def get_klines(self, symbol: str, interval: str, limit: int = 1000, **_kwargs):
        assert interval == self.interval
        if not self.frames:
            return self.last
        self.last = self.frames.pop(0)
        return self.last

def _df_for_close(ts: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({
        "open_time": [ts - pd.Timedelta(minutes=5)],
        "close_time": [ts],
        "open": [1.0],
        "high": [1.0],
        "low": [1.0],
        "close": [1.0],
        "volume": [1.0],
    })

@pytest.mark.critical
def test_closed_candle_gating(monkeypatch, tmp_path):
    t1 = pd.Timestamp("2024-01-01T01:00:00Z")
    t2 = pd.Timestamp("2024-01-01T01:05:00Z")
    df1 = _df_for_close(t1)
    df2 = _df_for_close(t2)

    app = TraderApp(symbol="BTCUSDT", interval="5m", logger=logging.getLogger("test"))
    app.md = DummyMD([df1, df1, df2], interval="5m")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADE_ENABLED", "0")
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_AGE_SECONDS", "999999999")

    monkeypatch.setattr("app.run._read_preflight", lambda *_a, **_k: {
        "rejects": [],
        "account": {"equity_usd": 1000.0, "wallet_usdt": 1000.0},
        "price": {"value": 1.0, "bid": 1.0, "ask": 1.0, "mark": 1.0},
        "filters": {"step_size": 0.1, "min_qty": 0.1, "tick_size": 0.1},
    })

    calls = {"payload": 0}

    def _build_payload(**_kwargs):
        calls["payload"] += 1
        return ({
            "market_identity": {"exchange": "x", "symbol": "BTCUSDT", "timeframe": "5m", "timestamp_closed": int(t1.timestamp())},
            "price_snapshot": {"last": 1.0, "bid": 1.0, "ask": 1.0, "mark": 1.0},
            "fees": {"maker": 0.0, "taker": 0.0},
            "account_state": {"equity": 1000.0, "available": 1000.0, "margin_type": "isolated", "leverage": 1},
            "position_state": {"side": None, "qty": 0.0, "entry": 0.0, "unrealized_pnl": 0.0, "liq_price": None},
            "features_ltf": {
                "close": 1.0,
                "close_prev": 1.0,
                "high": 1.0,
                "low": 1.0,
                "ema50": 1.0,
                "ema120": 1.0,
                "donchian_high_240": 1.0,
                "donchian_low_240": 1.0,
                "donchian_high_20": 1.0,
                "donchian_low_20": 1.0,
                "consec_close_above_donchian_20": 0.0,
                "consec_close_below_donchian_20": 0.0,
                "atr14": 1.0,
                "atr14_sma20": 1.0,
                "bb_upper": 1.0,
                "bb_lower": 1.0,
                "bb_mid": 1.0,
                "volume_ratio": 1.5,
                "candle_body_ratio": 0.5,
                "rsi14": 50.0,
                "rsi14_prev": 50.0,
                "consec_above_ema50": 0.0,
                "consec_below_ema50": 0.0,
                "consec_above_ema50_prev": 0.0,
                "consec_below_ema50_prev": 0.0,
                "close_max_n": 1.0,
                "close_min_n": 1.0,
                "time_exit_bars": 12,
            },
            "context_htf": {
                "ema200": 1.0,
                "ema200_prev_n": 1.0,
                "ema200_slope_norm": 0.05,
                "consec_above_ema200": 0.0,
                "consec_below_ema200": 0.0,
                "consec_higher_close": 0.0,
                "consec_lower_close": 0.0,
                "close": 1.0,
                "trend": "range",
                "atr14": 1.0,
                "timeframe": "1h"
            },
            "risk_policy": {"risk_per_trade": 0.01, "max_daily_drawdown": 0.1, "max_consecutive_losses": 3, "min_rr": 1.5},
            "market_meta": {"funding_rate": 0.0, "funding_next_ts": int(t1.timestamp())},
            "exchange_limits": {"tick_size": 0.1, "step_size": 0.1, "min_qty": 0.1},
        }, [])

    monkeypatch.setattr("app.data.payload_builder.build_payload", _build_payload)
    monkeypatch.setattr("app.strategy.decision_engine.make_decision", lambda _p, _d=None: {"intent": "HOLD", "reject_reasons": []})
    monkeypatch.setattr("app.risk.risk_manager.create_trade_plan", lambda *_a, **_k: (None, ["hold"]))

    app.run_once()
    assert calls["payload"] == 1

    app.run_once()
    assert calls["payload"] == 1

    app.run_once()
    assert calls["payload"] == 2

@pytest.mark.critical
def test_closed_candle_gating_restart(monkeypatch, tmp_path):
    from app.state import state_manager
    t1 = pd.Timestamp("2024-01-01T01:00:00Z")
    df1 = _df_for_close(t1)

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADE_ENABLED", "0")
    monkeypatch.setenv("MD_MIN_BARS_1H", "1")
    monkeypatch.setenv("MD_MIN_BARS_4H", "1")
    monkeypatch.setenv("MD_MAX_AGE_SECONDS", "999999999")

    state_manager.save_last_closed_candle_ts(int(t1.timestamp()))

    app = TraderApp(symbol="BTCUSDT", interval="5m", logger=logging.getLogger("test"))
    app.md = DummyMD([df1], interval="5m")

    monkeypatch.setattr("app.run._read_preflight", lambda *_a, **_k: {
        "rejects": [],
        "account": {"equity_usd": 1000.0, "wallet_usdt": 1000.0},
        "price": {"value": 1.0, "bid": 1.0, "ask": 1.0, "mark": 1.0},
        "filters": {"step_size": 0.1, "min_qty": 0.1, "tick_size": 0.1},
    })

    calls = {"payload": 0}
    def _build_payload(**_k):
        calls["payload"] += 1
        return None, ["nope"]
    monkeypatch.setattr("app.data.payload_builder.build_payload", _build_payload)

    app.run_once()
    assert calls["payload"] == 0

@pytest.mark.critical
def test_startup_reconcile_hard_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_ENABLED", "1")
    monkeypatch.setenv("PAPER_TRADING", "1")
    monkeypatch.setenv("DRY_RUN_ONLY", "1")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FORCE_RECONCILE", "1")

    from app.state.state_manager import save_position_state
    save_position_state("BTCUSDT", {"side": "LONG", "qty": 0.1})

    def _fake_fetch():
        return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "0.1"}]}

    def _fake_open_orders(_symbol: str):
        return []

    monkeypatch.setattr("core.exchange_private.fetch_futures_private", lambda: _fake_fetch())
    monkeypatch.setattr("app.services.notifications.get_open_orders", _fake_open_orders)

    app = TraderApp(symbol="BTCUSDT", interval="5m", logger=logging.getLogger("test"))
    with pytest.raises(RuntimeError):
        app.start(oneshot=True)
