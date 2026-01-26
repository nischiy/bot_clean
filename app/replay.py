from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple

import pandas as pd

from app.data.payload_builder import build_payload
from app.data.market_data_validator import validate_market_data
from app.strategy.decision_engine import make_decision
from app.risk.risk_manager import create_trade_plan
from app.services.execution_service import ExecutionService
from app.state import state_manager
from core.config import settings
from app.services.market_data import HttpMarketData
from app.run import _ensure_utc_timestamps, _filter_closed_candles, _read_preflight
from app.core.trade_ledger import append_event, hash_json


def _parse_date(value: str, end_of_day: bool = False) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=0)
    return dt.astimezone(timezone.utc)


def _fetch_klines(md: HttpMarketData, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    df = md.get_klines(symbol, interval, limit=1500, start_time=start_ms, end_time=end_ms, max_bars=None)
    df = _ensure_utc_timestamps(df)
    df = _filter_closed_candles(df)
    return df


def _simulate_outcome(
    side: str,
    entry: float,
    sl: float,
    tp: float,
    qty: float,
    future: pd.DataFrame,
) -> Tuple[float, str]:
    if future is None or future.empty:
        return 0.0, "open"
    for _, row in future.iterrows():
        high = float(row.get("high", row.get("High", 0.0)))
        low = float(row.get("low", row.get("Low", 0.0)))
        if side == "BUY":
            sl_hit = low <= sl
            tp_hit = high >= tp
            if sl_hit and tp_hit:
                return (sl - entry) * qty, "sl"
            if sl_hit:
                return (sl - entry) * qty, "sl"
            if tp_hit:
                return (tp - entry) * qty, "tp"
        else:
            sl_hit = high >= sl
            tp_hit = low <= tp
            if sl_hit and tp_hit:
                return (entry - sl) * qty, "sl"
            if sl_hit:
                return (entry - sl) * qty, "sl"
            if tp_hit:
                return (entry - tp) * qty, "tp"
    return 0.0, "open"


def run_replay(
    symbol: str,
    date_from: str,
    date_to: str,
    *,
    state_dir: str = "run/replay_state",
    logger: logging.Logger | None = None,
) -> Dict[str, Any]:
    log = logger or logging.getLogger("Replay")
    log.setLevel(logging.INFO)

    os.environ["SAFE_RUN"] = "1"
    os.environ["REPLAY_MODE"] = "1"
    os.environ["STATE_DIR"] = state_dir

    start = _parse_date(date_from, end_of_day=False)
    end = _parse_date(date_to, end_of_day=True)

    md = HttpMarketData()
    interval = settings.get_str("INTERVAL", "5m")
    htf_interval = settings.get_str("HTF_INTERVAL", "1h")
    df_ltf = _fetch_klines(md, symbol, interval, start, end)
    df_htf = _fetch_klines(md, symbol, htf_interval, start, end)

    if df_ltf is None or df_ltf.empty:
        raise RuntimeError(f"replay: no {interval} candles loaded")

    preflight = _read_preflight(symbol, None)
    account = preflight.get("account") or {}
    filters = preflight.get("filters") or {}
    equity = float(account.get("equity_usd") or 0.0)
    if equity <= 0:
        raise RuntimeError("replay: missing equity in account snapshot")

    exe = ExecutionService(logger=log)
    stats = {
        "holds": 0,
        "trade_intents": 0,
        "blocked_trades": {},
        "max_drawdown": 0.0,
        "max_consecutive_losses": 0,
        "pnl": 0.0,
    }

    peak_equity = equity
    current_equity = equity
    current_date = None

    for i in range(len(df_ltf)):
        row = df_ltf.iloc[i]
        close_time = pd.to_datetime(row.get("close_time"), utc=True)
        ts = int(close_time.timestamp())

        last_ts = state_manager.load_last_closed_candle_ts()
        if last_ts is not None and ts <= last_ts:
            continue

        date_str = state_manager.current_kyiv_date_str(close_time)
        if current_date != date_str:
            current_date = date_str
        daily_state = state_manager.load_or_initialize_daily_state(current_equity, now=close_time)

        df_ltf_window = df_ltf[df_ltf["close_time"] <= close_time]
        df_htf_window = df_htf[df_htf["close_time"] <= close_time] if df_htf is not None else None

        price = float(row.get("close"))
        price_snapshot = {"value": price, "bid": price, "ask": price, "mark": price}

        ok_md, md_errors = validate_market_data(df_ltf_window, df_htf_window, now_ts=ts)
        if not ok_md:
            stats["holds"] += 1
            state_manager.save_last_closed_candle_ts(ts)
            continue

        payload, errors = build_payload(
            symbol=symbol,
            df_ltf=df_ltf_window,
            df_htf=df_htf_window,
            account_snapshot=account,
            position_snapshot={"side": None, "qty": 0.0, "entry": 0.0, "unrealized_pnl": 0.0, "liq_price": None},
            price_snapshot=price_snapshot,
            filters_snapshot=filters,
            timestamp_closed=ts,
            timeframe=interval,
            htf_timeframe=htf_interval,
        )
        if payload is None:
            stats["holds"] += 1
            state_manager.save_last_closed_candle_ts(ts)
            continue

        payload_hash = hash_json(payload)
        decision = make_decision(payload, daily_state)
        decision_hash = hash_json(decision)
        append_event(
            event_type="decision_created",
            symbol=symbol,
            timeframe=interval,
            correlation_id=f"{symbol}:{ts}",
            payload_hash=payload_hash,
            decision_hash=decision_hash,
            details={"timestamp_closed": ts},
        )
        log.info("replay decision ts=%s intent=%s reasons=%s", ts, decision.get("intent"), decision.get("reject_reasons"))

        if decision.get("intent") == "HOLD":
            stats["holds"] += 1
            state_manager.save_last_closed_candle_ts(ts)
            continue

        stats["trade_intents"] += 1
        trade_plan, rejections = create_trade_plan(payload, decision, daily_state, [])
        if trade_plan is None:
            for r in rejections:
                stats["blocked_trades"][r] = stats["blocked_trades"].get(r, 0) + 1
            state_manager.save_last_closed_candle_ts(ts)
            continue

        trade_plan_hash = hash_json(trade_plan)
        append_event(
            event_type="trade_plan_created",
            symbol=symbol,
            timeframe=interval,
            correlation_id=f"{symbol}:{ts}",
            payload_hash=payload_hash,
            decision_hash=decision_hash,
            trade_plan_hash=trade_plan_hash,
            client_order_id=trade_plan.get("client_order_id"),
            details={},
        )
        log.info("replay trade_plan ts=%s side=%s qty=%s", ts, trade_plan.get("side"), trade_plan.get("quantity"))
        exe.execute_trade_plan(trade_plan)

        future = df_ltf.iloc[i + 1 :]
        pnl, outcome = _simulate_outcome(
            trade_plan.get("side"),
            decision.get("entry"),
            decision.get("sl"),
            decision.get("tp"),
            trade_plan.get("quantity"),
            future,
        )
        if outcome in {"sl", "tp"}:
            append_event(
                event_type="position_closed",
                symbol=symbol,
                timeframe=interval,
                correlation_id=f"{symbol}:{ts}",
                trade_plan_hash=trade_plan_hash,
                client_order_id=trade_plan.get("client_order_id"),
                details={"pnl": pnl, "outcome": outcome},
            )
            current_equity += pnl
            daily_state["realized_pnl"] = float(daily_state.get("realized_pnl", 0.0)) + pnl
            if pnl < 0:
                daily_state["consecutive_losses"] = int(daily_state.get("consecutive_losses", 0)) + 1
            else:
                daily_state["consecutive_losses"] = 0
            stats["max_consecutive_losses"] = max(stats["max_consecutive_losses"], daily_state["consecutive_losses"])

        peak_equity = max(peak_equity, current_equity)
        if peak_equity > 0:
            dd = (peak_equity - current_equity) / peak_equity
            stats["max_drawdown"] = max(stats["max_drawdown"], dd)

        state_manager.save_daily_state(date_str, daily_state)
        state_manager.save_last_closed_candle_ts(ts)

    stats["pnl"] = current_equity - equity
    log.info("replay summary: %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic replay runner")
    parser.add_argument("--symbol", default=settings.get_str("SYMBOL", "BTCUSDT"))
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--state-dir", default="run/replay_state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    run_replay(args.symbol, args.date_from, args.date_to, state_dir=args.state_dir)


if __name__ == "__main__":
    main()
