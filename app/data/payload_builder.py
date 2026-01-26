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
    available = _safe_float(account_snapshot.get("available") or account_snapshot.get("wallet_usdt"))
    margin_type = account_snapshot.get("margin_type", "isolated")
    leverage = settings.get_int("LEVERAGE")
    
    if equity is None or equity <= 0:
        errors.append("missing_or_invalid_equity")
    if available is None:
        errors.append("missing_available")
    
    account_state = {
        "equity": equity or 0.0,
        "available": available or 0.0,
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
    high_col = df_ltf.get("high") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    low_col = df_ltf.get("low") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    high_ltf = _safe_float(high_col.iloc[-1]) if high_col is not None and not high_col.empty else None
    low_ltf = _safe_float(low_col.iloc[-1]) if low_col is not None and not low_col.empty else None
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
    if df_ltf is not None and not df_ltf.empty and len(df_ltf) > 1:
        try:
            close_series = df_ltf.get("close") if "close" in df_ltf.columns else df_ltf.get("Close")
            if close_series is not None and len(close_series) > 1:
                delta = close_series.diff()
                gain = delta.clip(lower=0.0)
                loss = (-delta).clip(lower=0.0)
                avg_gain = gain.rolling(14).mean()
                avg_loss = loss.rolling(14).mean()
                rs = avg_gain / avg_loss.replace(0, pd.NA)
                rsi_series = 100.0 - (100.0 / (1.0 + rs))
                rsi14_prev = _safe_float(rsi_series.iloc[-2])
        except Exception:
            rsi14_prev = None

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

    if close_ltf is None:
        errors.append("missing_close_ltf")
    if close_prev is None and close_ltf is not None:
        close_prev = close_ltf
    if rsi14_prev is None and rsi14 is not None:
        rsi14_prev = rsi14

    features_ltf = {
        "close": close_ltf if close_ltf is not None else -1.0,
        "close_prev": close_prev if close_prev is not None else -1.0,
        "high": high_ltf if high_ltf is not None else -1.0,
        "low": low_ltf if low_ltf is not None else -1.0,
        "ema50": ema50_ltf if ema50_ltf is not None else -1.0,
        "ema50_prev_12": ema50_prev_12 if ema50_prev_12 is not None else -1.0,
        "ema120": ema120_ltf if ema120_ltf is not None else -1.0,
        "donchian_high_240": donchian_high if donchian_high is not None else -1.0,
        "donchian_low_240": donchian_low if donchian_low is not None else -1.0,
        "donchian_high_20": donchian_high_20 if donchian_high_20 is not None else -1.0,
        "donchian_low_20": donchian_low_20 if donchian_low_20 is not None else -1.0,
        "consec_close_above_donchian_20": consec_close_above_donchian_20 if consec_close_above_donchian_20 is not None else -1.0,
        "consec_close_below_donchian_20": consec_close_below_donchian_20 if consec_close_below_donchian_20 is not None else -1.0,
        "atr14": atr14 if atr14 is not None else -1.0,
        "atr14_sma20": atr14_sma20 if atr14_sma20 is not None else -1.0,
        "bb_upper": bb_upper if bb_upper is not None else -1.0,
        "bb_lower": bb_lower if bb_lower is not None else -1.0,
        "bb_mid": bb_mid if bb_mid is not None else -1.0,
        "volume_ratio": volume_ratio if volume_ratio is not None else -1.0,
        "candle_body_ratio": candle_body_ratio if candle_body_ratio is not None else -1.0,
        "rsi14": rsi14 if rsi14 is not None else -1.0,
        "rsi14_prev": rsi14_prev if rsi14_prev is not None else -1.0,
        "consec_above_ema50": consec_above_ema50 if consec_above_ema50 is not None else -1.0,
        "consec_below_ema50": consec_below_ema50 if consec_below_ema50 is not None else -1.0,
        "consec_above_ema50_prev": consec_above_ema50_prev if consec_above_ema50_prev is not None else -1.0,
        "consec_below_ema50_prev": consec_below_ema50_prev if consec_below_ema50_prev is not None else -1.0,
        "close_max_n": close_max_n if close_max_n is not None else -1.0,
        "close_min_n": close_min_n if close_min_n is not None else -1.0,
        "time_exit_bars": time_exit_bars,
    }

    # HTF Context (1h default)
    ema200_htf = _compute_ema(df_htf, 200) if df_htf is not None and not df_htf.empty else None
    htf_slope_n = max(settings.get_int("HTF_TREND_SLOPE_N"), 1)
    ema200_prev_n = _compute_ema_at(df_htf, 200, htf_slope_n) if df_htf is not None and not df_htf.empty else None
    atr14_htf = _compute_atr(df_htf, 14) if df_htf is not None and not df_htf.empty else None
    close_col_htf = df_htf.get("close") if df_htf is not None and hasattr(df_htf, "columns") else None
    close_htf = _safe_float(close_col_htf.iloc[-1]) if close_col_htf is not None and not close_col_htf.empty else None
    trend = _determine_trend(df_htf, ema200_htf) if df_htf is not None and not df_htf.empty else "range"
    ema200_series_htf = _ema_series(df_htf, 200) if df_htf is not None and not df_htf.empty else None
    consec_above_ema200 = _count_consecutive_cross(close_col_htf, ema200_series_htf, side="above", end_index=-1)
    consec_below_ema200 = _count_consecutive_cross(close_col_htf, ema200_series_htf, side="below", end_index=-1)
    consec_higher_close = _count_consecutive_relative(close_col_htf, side="higher", end_index=-1)
    consec_lower_close = _count_consecutive_relative(close_col_htf, side="lower", end_index=-1)
    ema200_slope_norm = None
    if ema200_htf is not None and ema200_prev_n is not None and atr14_htf is not None and atr14_htf > 0:
        ema200_slope_norm = abs(ema200_htf - ema200_prev_n) / (atr14_htf * htf_slope_n)

    context_htf = {
        "ema200": ema200_htf if ema200_htf is not None else -1.0,
        "ema200_prev_n": ema200_prev_n if ema200_prev_n is not None else -1.0,
        "ema200_slope_norm": ema200_slope_norm if ema200_slope_norm is not None else -1.0,
        "consec_above_ema200": consec_above_ema200 if consec_above_ema200 is not None else -1.0,
        "consec_below_ema200": consec_below_ema200 if consec_below_ema200 is not None else -1.0,
        "consec_higher_close": consec_higher_close if consec_higher_close is not None else -1.0,
        "consec_lower_close": consec_lower_close if consec_lower_close is not None else -1.0,
        "close": close_htf if close_htf is not None else -1.0,
        "trend": trend,
        "atr14": atr14_htf if atr14_htf is not None else -1.0,
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
        "exchange_limits": exchange_limits
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
