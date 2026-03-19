"""
Payload Builder: Constructs deterministic payload.json from market data, account state, and features.
Fail-closed: If any required field is missing, NaN, or stale → payload fails validation → HOLD.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
import pandas as pd
import numpy as np

from core.config import settings
from app.core.validation import validate_payload


def _is_valid_number(value: Any) -> bool:
    """Check if value is a valid number (not NaN, not None, not inf)."""
    if value is None:
        return False
    try:
        fval = float(value)
        return math.isfinite(fval) and not math.isnan(fval)
    except (ValueError, TypeError):
        return False


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Convert to float if valid, else return default."""
    if not _is_valid_number(value):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _add_missing_field(missing_fields: List[str], field_name: str, value: Any) -> None:
    """Track fields that are semantically missing in the payload."""
    if value is not None:
        return
    if field_name not in missing_fields:
        missing_fields.append(field_name)


def _compute_donchian(df: pd.DataFrame, period: int = 20) -> Tuple[Optional[float], Optional[float]]:
    """Compute Donchian channels."""
    if df is None or df.empty or len(df) < period:
        return None, None
    high_col = df.get("high") if "high" in df.columns else df.get("High")
    low_col = df.get("low") if "low" in df.columns else df.get("Low")
    if high_col is None or low_col is None:
        return None, None
    try:
        donchian_high = float(high_col.rolling(period).max().iloc[-1])
        donchian_low = float(low_col.rolling(period).min().iloc[-1])
        if _is_valid_number(donchian_high) and _is_valid_number(donchian_low):
            return donchian_high, donchian_low
    except Exception:
        pass
    return None, None


def _compute_ema(df: pd.DataFrame, period: int) -> Optional[float]:
    """Compute EMA."""
    if df is None or df.empty:
        return None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None:
        return None
    try:
        ema = close_col.ewm(span=period, adjust=False).mean()
        ema_val = float(ema.iloc[-1])
        if _is_valid_number(ema_val):
            return ema_val
    except Exception:
        pass
    return None


def _compute_ema_at(df: pd.DataFrame, period: int, offset: int) -> Optional[float]:
    """Compute EMA value at a prior offset from the end."""
    if df is None or df.empty or offset <= 0:
        return None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None or len(close_col) <= offset:
        return None
    if len(close_col) < period + offset:
        return None
    try:
        ema = close_col.ewm(span=period, adjust=False).mean()
        ema_val = float(ema.iloc[-1 - offset])
        if _is_valid_number(ema_val):
            return ema_val
    except Exception:
        pass
    return None


def _ema_series(df: pd.DataFrame, period: int) -> Optional[pd.Series]:
    """Compute EMA series."""
    if df is None or df.empty:
        return None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None:
        return None
    if len(close_col) < period:
        return None
    try:
        ema = close_col.ewm(span=period, adjust=False).mean()
        return ema if len(ema) == len(close_col) else None
    except Exception:
        return None


def _count_consecutive_bool(flags: List[bool]) -> int:
    count = 0
    for val in reversed(flags):
        if val:
            count += 1
        else:
            break
    return count


def _count_consecutive_cross(
    close_series: pd.Series,
    level_series: pd.Series,
    *,
    side: str,
    end_index: int,
) -> Optional[int]:
    if close_series is None or level_series is None:
        return None
    if len(close_series) == 0 or len(level_series) == 0:
        return None
    if len(close_series) != len(level_series):
        return None
    if abs(end_index) >= len(close_series):
        return None
    flags: List[bool] = []
    for idx in range(len(close_series)):
        try:
            close_val = float(close_series.iloc[idx])
            level_val = float(level_series.iloc[idx])
        except Exception:
            return None
        if not _is_valid_number(close_val) or not _is_valid_number(level_val):
            return None
        if side == "above":
            flags.append(close_val > level_val)
        else:
            flags.append(close_val < level_val)
    end_pos = end_index if end_index >= 0 else len(flags) + end_index
    if end_pos < 0:
        return None
    trimmed = flags[: end_pos + 1]
    return _count_consecutive_bool(trimmed)


def _count_consecutive_relative(
    close_series: pd.Series,
    *,
    side: str,
    end_index: int,
) -> Optional[int]:
    if close_series is None or len(close_series) < 2:
        return None
    if abs(end_index) >= len(close_series):
        return None
    flags: List[bool] = []
    for idx in range(1, len(close_series)):
        try:
            prev_val = float(close_series.iloc[idx - 1])
            curr_val = float(close_series.iloc[idx])
        except Exception:
            return None
        if not _is_valid_number(prev_val) or not _is_valid_number(curr_val):
            return None
        if side == "higher":
            flags.append(curr_val > prev_val)
        else:
            flags.append(curr_val < prev_val)
    end_pos = end_index if end_index >= 0 else len(flags) + end_index
    if end_pos < 0:
        return None
    trimmed = flags[: end_pos + 1]
    return _count_consecutive_bool(trimmed)


def _count_consecutive_level(
    close_series: pd.Series,
    *,
    level: float,
    side: str,
    end_index: int,
) -> Optional[int]:
    if close_series is None or len(close_series) == 0:
        return None
    if not _is_valid_number(level):
        return None
    if abs(end_index) >= len(close_series):
        return None
    flags: List[bool] = []
    for idx in range(len(close_series)):
        try:
            close_val = float(close_series.iloc[idx])
        except Exception:
            return None
        if not _is_valid_number(close_val):
            return None
        if side == "above":
            flags.append(close_val > level)
        else:
            flags.append(close_val < level)
    end_pos = end_index if end_index >= 0 else len(flags) + end_index
    if end_pos < 0:
        return None
    trimmed = flags[: end_pos + 1]
    return _count_consecutive_bool(trimmed)


def _compute_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Compute RSI."""
    if df is None or df.empty or len(df) < period + 1:
        return None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None:
        return None
    try:
        delta = close_col.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_val = float(rsi.iloc[-1])
        if _is_valid_number(rsi_val):
            return rsi_val
    except Exception:
        pass
    return None


def _compute_rsi_series(df: pd.DataFrame, period: int = 14) -> Optional[pd.Series]:
    """Compute RSI series."""
    if df is None or df.empty or len(df) < period + 1:
        return None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None:
        return None
    try:
        delta = close_col.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi_series = 100.0 - (100.0 / (1.0 + rs))
        return rsi_series if len(rsi_series) == len(close_col) else None
    except Exception:
        return None


def _compute_bollinger(
    df: pd.DataFrame, period: int = 20, std_mult: float = 2.0
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute Bollinger Bands (upper, lower, mid)."""
    if df is None or df.empty or len(df) < period:
        return None, None, None
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if close_col is None:
        return None, None, None
    try:
        mid = close_col.rolling(period).mean().iloc[-1]
        std = close_col.rolling(period).std(ddof=0).iloc[-1]
        upper = float(mid + std_mult * std)
        lower = float(mid - std_mult * std)
        mid_val = float(mid)
        if _is_valid_number(upper) and _is_valid_number(lower) and _is_valid_number(mid_val):
            return upper, lower, mid_val
    except Exception:
        pass
    return None, None, None


def _compute_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Compute ATR."""
    if df is None or df.empty or len(df) < period:
        return None
    high_col = df.get("high") if "high" in df.columns else df.get("High")
    low_col = df.get("low") if "low" in df.columns else df.get("Low")
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if high_col is None or low_col is None or close_col is None:
        return None
    try:
        prev_close = close_col.shift(1).fillna(close_col)
        tr1 = high_col - low_col
        tr2 = (high_col - prev_close).abs()
        tr3 = (low_col - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        atr_val = float(atr.iloc[-1])
        if _is_valid_number(atr_val):
            return atr_val
    except Exception:
        pass
    return None


def _compute_atr_sma(
    df: pd.DataFrame, atr_period: int = 14, sma_period: int = 20
) -> Tuple[Optional[float], Optional[float]]:
    """Compute ATR and SMA of ATR."""
    if df is None or df.empty or len(df) < (atr_period + sma_period):
        return None, None
    high_col = df.get("high") if "high" in df.columns else df.get("High")
    low_col = df.get("low") if "low" in df.columns else df.get("Low")
    close_col = df.get("close") if "close" in df.columns else df.get("Close")
    if high_col is None or low_col is None or close_col is None:
        return None, None
    try:
        prev_close = close_col.shift(1).fillna(close_col)
        tr1 = high_col - low_col
        tr2 = (high_col - prev_close).abs()
        tr3 = (low_col - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = tr.rolling(atr_period).mean()
        atr_val = float(atr_series.iloc[-1])
        atr_sma_val = float(atr_series.rolling(sma_period).mean().iloc[-1])
        if _is_valid_number(atr_val) and _is_valid_number(atr_sma_val):
            return atr_val, atr_sma_val
    except Exception:
        pass
    return None, None


def _compute_volume_ratio(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    """Compute volume ratio (current volume / average volume)."""
    if df is None or df.empty or len(df) < period:
        return None
    volume_col = df.get("volume") if "volume" in df.columns else df.get("Volume")
    if volume_col is None:
        return None
    try:
        current_vol = float(volume_col.iloc[-1])
        avg_vol = float(volume_col.rolling(period).mean().iloc[-1])
        if _is_valid_number(current_vol) and _is_valid_number(avg_vol) and avg_vol > 0:
            return current_vol / avg_vol
    except Exception:
        pass
    return None


def _determine_trend(df_htf: pd.DataFrame, ema200_htf: Optional[float]) -> str:
    """Determine trend from HTF context (close vs EMA200)."""
    if df_htf is None or df_htf.empty or ema200_htf is None:
        return "range"
    close_col = df_htf.get("close") if "close" in df_htf.columns else df_htf.get("Close")
    if close_col is None:
        return "range"
    try:
        current_close = float(close_col.iloc[-1])
        if _is_valid_number(current_close) and _is_valid_number(ema200_htf):
            if current_close > ema200_htf:
                return "up"
            elif current_close < ema200_htf:
                return "down"
    except Exception:
        pass
    return "range"


def build_payload(
    symbol: str,
    df_ltf: pd.DataFrame,
    df_htf: Optional[pd.DataFrame],
    account_snapshot: Dict[str, Any],
    position_snapshot: Dict[str, Any],
    price_snapshot: Dict[str, Any],
    filters_snapshot: Dict[str, Any],
    timestamp_closed: int,
    *,
    exchange: str = "binance_futures",
    timeframe: str = "5m",
    htf_timeframe: str = "1h",
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Build payload.json from market data and account state.
    
    Returns:
        (payload: Optional[Dict], errors: List[str])
        - If payload is None, errors contains reasons for failure
        - If payload is not None, it should be validated before use
    """
    errors = []
    missing_fields: List[str] = []
    
    # Market identity
    market_identity = {
        "exchange": exchange,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "timestamp_closed": timestamp_closed
    }
    
    # Price snapshot
    price_last = _safe_float(price_snapshot.get("value") or price_snapshot.get("last"))
    price_bid = _safe_float(price_snapshot.get("bid"), price_last)
    price_ask = _safe_float(price_snapshot.get("ask"), price_last)
    price_mark = _safe_float(price_snapshot.get("mark"), price_last)
    
    if price_last is None:
        errors.append("missing_price_last")
    if price_bid is None:
        errors.append("missing_price_bid")
    if price_ask is None:
        errors.append("missing_price_ask")
    if price_mark is None:
        errors.append("missing_price_mark")
    
    price_snapshot_obj = {
        "last": price_last or 0.0,
        "bid": price_bid or 0.0,
        "ask": price_ask or 0.0,
        "mark": price_mark or 0.0
    }
    
    # Fees (defaults from Binance Futures)
    fee_maker_bps = settings.get_float("FEE_MAKER_BPS")
    fee_taker_bps = settings.get_float("FEE_TAKER_BPS")
    fees = {
        "maker": fee_maker_bps / 100.0,
        "taker": fee_taker_bps / 100.0
    }
    
    # Account state
    equity = _safe_float(account_snapshot.get("equity_usd") or account_snapshot.get("equity"))
    available = _safe_float(
        account_snapshot.get("available_usd")
        or account_snapshot.get("available")
        or account_snapshot.get("wallet_usdt")
    )
    total_margin = _safe_float(account_snapshot.get("total_margin_usd") or account_snapshot.get("total_margin_balance"))
    funds_base = None
    funds_source = None
    if available is not None and total_margin is not None:
        funds_base = min(available, total_margin)
        funds_source = "min(available_balance,total_margin_balance)"
    elif available is not None:
        funds_base = available
        funds_source = "available_balance"
    margin_type = account_snapshot.get("margin_type", "isolated")
    leverage = settings.get_int("LEVERAGE")
    
    if equity is None or equity <= 0:
        errors.append("missing_or_invalid_equity")
    if funds_base is None:
        errors.append("funds_source_missing")
    elif funds_base <= 0:
        errors.append("funds_nonpositive")
    
    account_state = {
        "equity": equity or 0.0,
        "available": available or 0.0,
        "funds_base": funds_base if funds_base is not None else 0.0,
        "funds_source": funds_source or "missing",
        "total_margin_balance": total_margin,
        "margin_type": margin_type if margin_type in ("isolated", "cross") else "isolated",
        "leverage": leverage
    }
    
    # Position state
    pos_side = position_snapshot.get("side")
    if pos_side not in ("LONG", "SHORT", None):
        pos_side = None
    pos_qty = _safe_float(position_snapshot.get("qty") or position_snapshot.get("positionAmt"), 0.0)
    pos_entry = _safe_float(position_snapshot.get("entry") or position_snapshot.get("entryPrice"), 0.0)
    pos_pnl = _safe_float(position_snapshot.get("unrealizedPnl") or position_snapshot.get("unrealized_pnl"), 0.0)
    pos_liq = _safe_float(position_snapshot.get("liquidationPrice") or position_snapshot.get("liq_price"))
    
    position_state = {
        "side": pos_side,
        "qty": pos_qty or 0.0,
        "entry": pos_entry or 0.0,
        "unrealized_pnl": pos_pnl or 0.0,
        "liq_price": pos_liq
    }
    
    # LTF Features (5m default)
    close_col = df_ltf.get("close") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    close_ltf = _safe_float(close_col.iloc[-1]) if close_col is not None and not close_col.empty else None
    close_prev = _safe_float(close_col.iloc[-2]) if close_col is not None and len(close_col) > 1 else None
    open_col = df_ltf.get("open") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    open_ltf = _safe_float(open_col.iloc[-1]) if open_col is not None and not open_col.empty else None
    open_prev = _safe_float(open_col.iloc[-2]) if open_col is not None and len(open_col) > 1 else None
    high_col = df_ltf.get("high") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    low_col = df_ltf.get("low") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    high_ltf = _safe_float(high_col.iloc[-1]) if high_col is not None and not high_col.empty else None
    low_ltf = _safe_float(low_col.iloc[-1]) if low_col is not None and not low_col.empty else None
    high_prev = _safe_float(high_col.iloc[-2]) if high_col is not None and len(high_col) > 1 else None
    low_prev = _safe_float(low_col.iloc[-2]) if low_col is not None and len(low_col) > 1 else None
    volume_col = df_ltf.get("volume") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    volume_ltf = _safe_float(volume_col.iloc[-1]) if volume_col is not None and not volume_col.empty else None
    volume_prev = _safe_float(volume_col.iloc[-2]) if volume_col is not None and len(volume_col) > 1 else None
    ema50_ltf = _compute_ema(df_ltf, 50)
    ema50_prev_12 = _compute_ema_at(df_ltf, 50, 12)
    ema120_ltf = _compute_ema(df_ltf, 120)
    donchian_high, donchian_low = _compute_donchian(df_ltf, 240)
    donchian_high_20, donchian_low_20 = _compute_donchian(df_ltf, 20)
    atr14, atr14_sma20 = _compute_atr_sma(df_ltf, 14, 20)
    bb_upper, bb_lower, bb_mid = _compute_bollinger(df_ltf, 20, 2.0)
    volume_ratio = _compute_volume_ratio(df_ltf, 20)
    rsi14 = _compute_rsi(df_ltf, 14)
    candle_body_ratio = None
    if open_ltf is not None and close_ltf is not None and high_ltf is not None and low_ltf is not None:
        try:
            candle_range = max(abs(high_ltf - low_ltf), 1e-9)
            candle_body_ratio = abs(close_ltf - open_ltf) / candle_range
        except Exception:
            candle_body_ratio = None
    rsi14_prev = None
    rsi_series_ltf = _compute_rsi_series(df_ltf, 14)
    if rsi_series_ltf is not None and len(rsi_series_ltf) > 1:
        rsi14_prev = _safe_float(rsi_series_ltf.iloc[-2])

    close_series_ltf = df_ltf.get("close") if df_ltf is not None and hasattr(df_ltf, "get") else None
    ema50_series = _ema_series(df_ltf, 50) if df_ltf is not None and not df_ltf.empty else None
    consec_above_ema50 = _count_consecutive_cross(close_series_ltf, ema50_series, side="above", end_index=-1)
    consec_below_ema50 = _count_consecutive_cross(close_series_ltf, ema50_series, side="below", end_index=-1)
    consec_above_ema50_prev = _count_consecutive_cross(close_series_ltf, ema50_series, side="above", end_index=-2)
    consec_below_ema50_prev = _count_consecutive_cross(close_series_ltf, ema50_series, side="below", end_index=-2)
    consec_close_above_donchian_20 = (
        _count_consecutive_level(close_series_ltf, level=donchian_high_20, side="above", end_index=-1)
        if close_series_ltf is not None
        else None
    )
    consec_close_below_donchian_20 = (
        _count_consecutive_level(close_series_ltf, level=donchian_low_20, side="below", end_index=-1)
        if close_series_ltf is not None
        else None
    )
    time_exit_bars = max(settings.get_int("TIME_EXIT_BARS"), 1)
    close_max_n = None
    close_min_n = None
    if close_series_ltf is not None and len(close_series_ltf) >= time_exit_bars:
        close_max_n = _safe_float(close_series_ltf.iloc[-time_exit_bars:].max())
        close_min_n = _safe_float(close_series_ltf.iloc[-time_exit_bars:].min())

    stability_n = max(settings.get_int("STABILITY_N"), 1)
    trend_candles_below_ema50 = None
    trend_candles_above_ema50 = None
    wick_ratio_count = None
    if (
        df_ltf is not None
        and not df_ltf.empty
        and len(df_ltf) >= stability_n
        and ema50_series is not None
        and close_series_ltf is not None
        and high_col is not None
        and low_col is not None
        and open_col is not None
    ):
        try:
            close_tail = close_series_ltf.iloc[-stability_n:]
            ema_tail = ema50_series.iloc[-stability_n:]
            trend_candles_below_ema50 = int((close_tail < ema_tail).sum())
            trend_candles_above_ema50 = int((close_tail > ema_tail).sum())
            open_tail = open_col.iloc[-stability_n:]
            high_tail = high_col.iloc[-stability_n:]
            low_tail = low_col.iloc[-stability_n:]
            body = (close_tail - open_tail).abs()
            upper = high_tail - pd.concat([open_tail, close_tail], axis=1).max(axis=1)
            lower = pd.concat([open_tail, close_tail], axis=1).min(axis=1) - low_tail
            wick_ratio = (upper + lower) / body.replace(0, 1e-9)
            wick_th = settings.get_float("WICK_TH")
            wick_ratio_count = int((wick_ratio > wick_th).sum())
        except Exception:
            trend_candles_below_ema50 = None
            trend_candles_above_ema50 = None
            wick_ratio_count = None

    bb_width = None
    bb_width_prev = None
    if df_ltf is not None and not df_ltf.empty and len(df_ltf) >= 21:
        close_series = df_ltf.get("close") if "close" in df_ltf.columns else df_ltf.get("Close")
        if close_series is not None:
            try:
                mid_series = close_series.rolling(20).mean()
                std_series = close_series.rolling(20).std(ddof=0)
                upper_series = mid_series + 2.0 * std_series
                lower_series = mid_series - 2.0 * std_series
                width_series = upper_series - lower_series
                bb_width = _safe_float(width_series.iloc[-1])
                bb_width_prev = _safe_float(width_series.iloc[-2])
            except Exception:
                bb_width = None
                bb_width_prev = None

    confirm_swing_m = max(settings.get_int("CONFIRM_SWING_M"), 2)
    swing_high_m = None
    swing_low_m = None
    if df_ltf is not None and not df_ltf.empty and len(df_ltf) >= confirm_swing_m + 1:
        if high_col is not None and low_col is not None:
            try:
                swing_high_m = _safe_float(high_col.iloc[-(confirm_swing_m + 1):-1].max())
                swing_low_m = _safe_float(low_col.iloc[-(confirm_swing_m + 1):-1].min())
            except Exception:
                swing_high_m = None
                swing_low_m = None

    confirm_donchian_k = max(settings.get_int("CONFIRM_DONCHIAN_K"), 2)
    donchian_high_k, donchian_low_k = _compute_donchian(df_ltf, confirm_donchian_k)

    if close_ltf is None:
        _add_missing_field(missing_fields, "features_ltf.close", close_ltf)
    if close_prev is None and close_ltf is not None:
        close_prev = close_ltf
    if rsi14_prev is None and rsi14 is not None:
        rsi14_prev = rsi14

    ltf_missing_map = {
        "features_ltf.open": open_ltf,
        "features_ltf.open_prev": open_prev,
        "features_ltf.close": close_ltf,
        "features_ltf.close_prev": close_prev,
        "features_ltf.high": high_ltf,
        "features_ltf.low": low_ltf,
        "features_ltf.high_prev": high_prev,
        "features_ltf.low_prev": low_prev,
        "features_ltf.ema50": ema50_ltf,
        "features_ltf.ema50_prev_12": ema50_prev_12,
        "features_ltf.ema120": ema120_ltf,
        "features_ltf.donchian_high_240": donchian_high,
        "features_ltf.donchian_low_240": donchian_low,
        "features_ltf.donchian_high_20": donchian_high_20,
        "features_ltf.donchian_low_20": donchian_low_20,
        "features_ltf.donchian_high_k": donchian_high_k,
        "features_ltf.donchian_low_k": donchian_low_k,
        "features_ltf.consec_close_above_donchian_20": consec_close_above_donchian_20,
        "features_ltf.consec_close_below_donchian_20": consec_close_below_donchian_20,
        "features_ltf.atr14": atr14,
        "features_ltf.atr14_sma20": atr14_sma20,
        "features_ltf.bb_upper": bb_upper,
        "features_ltf.bb_lower": bb_lower,
        "features_ltf.bb_mid": bb_mid,
        "features_ltf.bb_width": bb_width,
        "features_ltf.bb_width_prev": bb_width_prev,
        "features_ltf.volume_ratio": volume_ratio,
        "features_ltf.volume": volume_ltf,
        "features_ltf.volume_prev": volume_prev,
        "features_ltf.candle_body_ratio": candle_body_ratio,
        "features_ltf.rsi14": rsi14,
        "features_ltf.rsi14_prev": rsi14_prev,
        "features_ltf.consec_above_ema50": consec_above_ema50,
        "features_ltf.consec_below_ema50": consec_below_ema50,
        "features_ltf.consec_above_ema50_prev": consec_above_ema50_prev,
        "features_ltf.consec_below_ema50_prev": consec_below_ema50_prev,
        "features_ltf.close_max_n": close_max_n,
        "features_ltf.close_min_n": close_min_n,
        "features_ltf.trend_candles_below_ema50": trend_candles_below_ema50,
        "features_ltf.trend_candles_above_ema50": trend_candles_above_ema50,
        "features_ltf.wick_ratio_count": wick_ratio_count,
        "features_ltf.swing_high_m": swing_high_m,
        "features_ltf.swing_low_m": swing_low_m,
    }
    for field_name, value in ltf_missing_map.items():
        _add_missing_field(missing_fields, field_name, value)

    features_ltf = {
        "open": open_ltf,
        "open_prev": open_prev,
        "close": close_ltf,
        "close_prev": close_prev,
        "high": high_ltf,
        "low": low_ltf,
        "high_prev": high_prev,
        "low_prev": low_prev,
        "ema50": ema50_ltf,
        "ema50_prev_12": ema50_prev_12,
        "ema120": ema120_ltf,
        "donchian_high_240": donchian_high,
        "donchian_low_240": donchian_low,
        "donchian_high_20": donchian_high_20,
        "donchian_low_20": donchian_low_20,
        "donchian_high_k": donchian_high_k,
        "donchian_low_k": donchian_low_k,
        "consec_close_above_donchian_20": consec_close_above_donchian_20,
        "consec_close_below_donchian_20": consec_close_below_donchian_20,
        "atr14": atr14,
        "atr14_sma20": atr14_sma20,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "bb_width": bb_width,
        "bb_width_prev": bb_width_prev,
        "volume_ratio": volume_ratio,
        "volume": volume_ltf,
        "volume_prev": volume_prev,
        "candle_body_ratio": candle_body_ratio,
        "rsi14": rsi14,
        "rsi14_prev": rsi14_prev,
        "consec_above_ema50": consec_above_ema50,
        "consec_below_ema50": consec_below_ema50,
        "consec_above_ema50_prev": consec_above_ema50_prev,
        "consec_below_ema50_prev": consec_below_ema50_prev,
        "close_max_n": close_max_n,
        "close_min_n": close_min_n,
        "time_exit_bars": time_exit_bars,
        "stability_n": stability_n,
        "trend_candles_below_ema50": trend_candles_below_ema50,
        "trend_candles_above_ema50": trend_candles_above_ema50,
        "wick_ratio_count": wick_ratio_count,
        "swing_high_m": swing_high_m,
        "swing_low_m": swing_low_m,
    }

    # HTF Context (1h default)
    ema200_htf = _compute_ema(df_htf, 200) if df_htf is not None and not df_htf.empty else None
    htf_ema_period = max(settings.get_int("HTF_EMA_PERIOD"), 1)
    ema_fast_htf = _compute_ema(df_htf, htf_ema_period) if df_htf is not None and not df_htf.empty else None
    htf_slope_n = max(settings.get_int("HTF_TREND_SLOPE_N"), 1)
    ema200_prev_n = _compute_ema_at(df_htf, 200, htf_slope_n) if df_htf is not None and not df_htf.empty else None
    atr14_htf = _compute_atr(df_htf, 14) if df_htf is not None and not df_htf.empty else None
    close_col_htf = df_htf.get("close") if df_htf is not None and hasattr(df_htf, "columns") else None
    close_htf = _safe_float(close_col_htf.iloc[-1]) if close_col_htf is not None and not close_col_htf.empty else None
    rsi_period_htf = max(settings.get_int("HTF_RSI_PERIOD"), 1)
    rsi_series_htf = _compute_rsi_series(df_htf, rsi_period_htf) if df_htf is not None and not df_htf.empty else None
    rsi_htf = _safe_float(rsi_series_htf.iloc[-1]) if rsi_series_htf is not None and len(rsi_series_htf) else None
    rsi_htf_prev = _safe_float(rsi_series_htf.iloc[-2]) if rsi_series_htf is not None and len(rsi_series_htf) > 1 else None
    trend = _determine_trend(df_htf, ema200_htf) if df_htf is not None and not df_htf.empty else "range"
    ema200_series_htf = _ema_series(df_htf, 200) if df_htf is not None and not df_htf.empty else None
    consec_above_ema200 = _count_consecutive_cross(close_col_htf, ema200_series_htf, side="above", end_index=-1)
    consec_below_ema200 = _count_consecutive_cross(close_col_htf, ema200_series_htf, side="below", end_index=-1)
    consec_higher_close = _count_consecutive_relative(close_col_htf, side="higher", end_index=-1)
    consec_lower_close = _count_consecutive_relative(close_col_htf, side="lower", end_index=-1)
    ema200_slope_norm = None
    if ema200_htf is not None and ema200_prev_n is not None and atr14_htf is not None and atr14_htf > 0:
        ema200_slope_norm = abs(ema200_htf - ema200_prev_n) / (atr14_htf * htf_slope_n)
    
    # Compute HTF ATR14 percentile (over last N candles, e.g., 720 for 30d on 1h)
    htf_atr14_percentile = None
    if df_htf is not None and not df_htf.empty and atr14_htf is not None:
        try:
            # Compute ATR14 series for HTF
            htf_high_col = df_htf.get("high") if "high" in df_htf.columns else df_htf.get("High")
            htf_low_col = df_htf.get("low") if "low" in df_htf.columns else df_htf.get("Low")
            htf_close_col = df_htf.get("close") if "close" in df_htf.columns else df_htf.get("Close")
            if htf_high_col is not None and htf_low_col is not None and htf_close_col is not None:
                htf_periods = min(720, len(df_htf))  # 30 days on 1h = 720 candles
                if len(df_htf) >= 14:
                    prev_close_htf = htf_close_col.shift(1).fillna(htf_close_col)
                    tr1_htf = htf_high_col - htf_low_col
                    tr2_htf = (htf_high_col - prev_close_htf).abs()
                    tr3_htf = (htf_low_col - prev_close_htf).abs()
                    tr_htf = pd.concat([tr1_htf, tr2_htf, tr3_htf], axis=1).max(axis=1)
                    atr14_series_htf = tr_htf.rolling(14).mean()
                    atr14_tail = atr14_series_htf.iloc[-htf_periods:].dropna()
                    if len(atr14_tail) > 0:
                        current_atr = atr14_htf
                        percentile = (atr14_tail <= current_atr).sum() / len(atr14_tail) * 100.0
                        htf_atr14_percentile = _safe_float(percentile)
        except Exception:
            htf_atr14_percentile = None
    
    # Compute session bucket from timestamp_closed (UTC)
    session_bucket = "Unknown"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(timestamp_closed, tz=timezone.utc)
        hour_utc = dt.hour
        # Asia: 00:00-08:00 UTC
        # London: 08:00-16:00 UTC
        # NY: 13:00-21:00 UTC (overlap with London 13:00-16:00)
        # Overlap: 13:00-16:00 UTC (London + NY)
        if 0 <= hour_utc < 8:
            session_bucket = "Asia"
        elif 8 <= hour_utc < 13:
            session_bucket = "London"
        elif 13 <= hour_utc < 16:
            session_bucket = "Overlap"
        elif 16 <= hour_utc < 21:
            session_bucket = "NY"
        else:  # 21:00-24:00 UTC
            session_bucket = "Asia"
    except Exception:
        session_bucket = "Unknown"

    htf_missing_map = {
        "context_htf.ema200": ema200_htf,
        "context_htf.ema_fast": ema_fast_htf,
        "context_htf.ema200_prev_n": ema200_prev_n,
        "context_htf.ema200_slope_norm": ema200_slope_norm,
        "context_htf.consec_above_ema200": consec_above_ema200,
        "context_htf.consec_below_ema200": consec_below_ema200,
        "context_htf.consec_higher_close": consec_higher_close,
        "context_htf.consec_lower_close": consec_lower_close,
        "context_htf.close": close_htf,
        "context_htf.rsi14": rsi_htf,
        "context_htf.rsi14_prev": rsi_htf_prev,
        "context_htf.atr14": atr14_htf,
        "context_htf.atr14_percentile": htf_atr14_percentile,
    }
    for field_name, value in htf_missing_map.items():
        _add_missing_field(missing_fields, field_name, value)

    context_htf = {
        "ema200": ema200_htf,
        "ema_fast": ema_fast_htf,
        "ema200_prev_n": ema200_prev_n,
        "ema200_slope_norm": ema200_slope_norm,
        "consec_above_ema200": consec_above_ema200,
        "consec_below_ema200": consec_below_ema200,
        "consec_higher_close": consec_higher_close,
        "consec_lower_close": consec_lower_close,
        "close": close_htf,
        "rsi14": rsi_htf,
        "rsi14_prev": rsi_htf_prev,
        "trend": trend,
        "atr14": atr14_htf,
        "atr14_percentile": htf_atr14_percentile,
        "session_bucket": session_bucket,
        "timeframe": htf_timeframe,
    }
    
    # Risk policy
    risk_per_trade = settings.get_float("RISK_PER_TRADE_PCT") / 100.0
    max_daily_drawdown = settings.get_float("RISK_MAX_DD_PCT_DAY") / 100.0
    max_consecutive_losses = settings.get_int("RISK_MAX_CONSEC_LOSSES")
    min_rr = settings.get_float("MIN_RR")
    
    risk_policy = {
        "risk_per_trade": risk_per_trade,
        "max_daily_drawdown": max_daily_drawdown,
        "max_consecutive_losses": max_consecutive_losses,
        "min_rr": min_rr
    }
    
    # Market meta
    funding_rate = _safe_float(settings.get_str("FUNDING_RATE"), 0.0)
    funding_next_ts = settings.get_int("FUNDING_NEXT_TS")
    if funding_next_ts <= 0:
        funding_next_ts = int(time.time()) + settings.get_int("FUNDING_INTERVAL_SECONDS")
    
    market_meta = {
        "funding_rate": funding_rate or 0.0,
        "funding_next_ts": funding_next_ts
    }
    
    # Exchange limits
    filters_raw = filters_snapshot.get("value") or filters_snapshot.get("raw") or {}
    step_size = _safe_float(filters_snapshot.get("step_size"))
    if step_size is None:
        lot_size = filters_raw.get("LOT_SIZE") or {}
        step_size = _safe_float(lot_size.get("stepSize"))
    
    min_qty = _safe_float(filters_snapshot.get("min_qty"))
    if min_qty is None:
        lot_size = filters_raw.get("LOT_SIZE") or {}
        min_qty = _safe_float(lot_size.get("minQty"))
    
    tick_size = _safe_float(filters_snapshot.get("tick_size"))
    if tick_size is None:
        price_filter = filters_raw.get("PRICE_FILTER") or {}
        tick_size = _safe_float(price_filter.get("tickSize"))
    
    if step_size is None or step_size <= 0:
        errors.append("missing_step_size")
    if min_qty is None or min_qty <= 0:
        errors.append("missing_min_qty")
    if tick_size is None or tick_size <= 0:
        errors.append("missing_tick_size")
    
    exchange_limits = {
        "tick_size": tick_size or 0.0,
        "step_size": step_size or 0.0,
        "min_qty": min_qty or 0.0
    }
    
    # Build payload
    payload = {
        "market_identity": market_identity,
        "price_snapshot": price_snapshot_obj,
        "fees": fees,
        "account_state": account_state,
        "position_state": position_state,
        "features_ltf": features_ltf,
        "context_htf": context_htf,
        "risk_policy": risk_policy,
        "market_meta": market_meta,
        "exchange_limits": exchange_limits,
        "missing_fields": missing_fields,
    }
    
    # Validate payload
    is_valid, validation_errors = validate_payload(payload)
    if not is_valid:
        errors.extend(validation_errors)
    
    # Check for stale data (timestamp_closed should be recent)
    now_ts = int(time.time())
    max_age = settings.get_int("DATA_MAX_AGE_SECONDS")
    if timestamp_closed < now_ts - max_age:
        errors.append("stale_timestamp_closed")
    
    if errors:
        return None, errors
    
    return payload, []
