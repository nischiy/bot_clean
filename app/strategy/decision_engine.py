"""
Decision Engine: Converts payload.json to decision.json (INTENT ONLY, no execution authority).
Enforces strategy rules and validates against decision.schema.json.
"""
from __future__ import annotations

import math
from typing import Dict, Any, List, Tuple, Optional

from app.core.validation import validate_decision
from core.config import settings
from core.runtime_mode import get_runtime_settings


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        fval = float(value)
        return fval if math.isfinite(fval) else default
    except (TypeError, ValueError):
        return default


def _safe_div(numer: Optional[float], denom: Optional[float], default: Optional[float] = None) -> Optional[float]:
    if numer is None or denom is None or denom <= 0:
        return default
    return numer / denom


def compute_regime_5m(context: Dict[str, Any]) -> str:
    close_ltf = context.get("close_ltf")
    ema50_ltf = context.get("ema50_ltf")
    atr14 = context.get("atr14")
    donchian_high_20 = context.get("donchian_high_20")
    donchian_low_20 = context.get("donchian_low_20")
    volume_ratio = context.get("volume_ratio")
    candle_body_ratio = context.get("candle_body_ratio")
    trend = context.get("trend")
    cont_body_min = context.get("cont_body_min")
    cont_vol_min = context.get("cont_vol_min")

    breakout_vol_min = context.get("breakout_vol_min")
    compression_width_atr_max = context.get("compression_width_atr_max")
    compression_vol_max = context.get("compression_vol_max")
    trend_dist50_max = context.get("trend_dist50_max")

    dist50 = (
        _safe_div(abs(close_ltf - ema50_ltf), atr14, default=None)
        if close_ltf is not None and close_ltf > 0 and ema50_ltf is not None and ema50_ltf > 0
        else None
    )
    dc_width_atr = (
        _safe_div(donchian_high_20 - donchian_low_20, atr14, default=None)
        if donchian_high_20 is not None
        and donchian_high_20 > 0
        and donchian_low_20 is not None
        and donchian_low_20 > 0
        else None
    )
    brk_up = bool(close_ltf and close_ltf > 0 and donchian_high_20 and donchian_high_20 > 0 and close_ltf > donchian_high_20)
    brk_dn = bool(close_ltf and close_ltf > 0 and donchian_low_20 and donchian_low_20 > 0 and close_ltf < donchian_low_20)
    impulse_ok = bool(
        candle_body_ratio is not None
        and volume_ratio is not None
        and cont_body_min is not None
        and cont_vol_min is not None
        and candle_body_ratio >= cont_body_min
        and volume_ratio >= cont_vol_min
    )
    trend_bias = trend in ("up", "down")

    if (brk_up or brk_dn) and volume_ratio is not None and volume_ratio >= breakout_vol_min:
        return "BREAKOUT_EXPANSION"
    if (
        dc_width_atr is not None
        and volume_ratio is not None
        and dc_width_atr <= compression_width_atr_max
        and volume_ratio <= compression_vol_max
    ):
        return "COMPRESSION"
    if (
        trend_bias
        and dist50 is not None
        and dist50 <= trend_dist50_max
        and impulse_ok
        and (brk_up or brk_dn)
    ):
        return "TREND_CONTINUATION"
    if trend_bias and dist50 is not None and dist50 > trend_dist50_max:
        return "PULLBACK"
    return "RANGE"


def select_strategy_by_regime(regime: str, context: Dict[str, Any]) -> str:
    if regime == "BREAKOUT_EXPANSION":
        return "BREAKOUT_EXPANSION" if (context.get("breakout_expansion_long_ok") or context.get("breakout_expansion_short_ok")) else "NONE"
    if regime == "TREND_CONTINUATION":
        return "CONTINUATION" if (context.get("cont_long_ok") or context.get("cont_short_ok")) else "NONE"
    if regime == "PULLBACK":
        return "PULLBACK_REENTRY" if (context.get("pullback_reentry_long_ok") or context.get("pullback_reentry_short_ok")) else "NONE"
    if regime == "RANGE":
        return "RANGE_MEANREV" if (context.get("range_meanrev_long_ok") or context.get("range_meanrev_short_ok")) else "NONE"
    return "NONE"


def make_decision(payload: Dict[str, Any], daily_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Make trading decision from payload.

    Strategy rules (5m LTF, 1h HTF):
    LONG:
      - HTF trend up (close_htf > ema200_htf)
      - RSI14 <= 40
      - pullback_atr = (ema50_ltf - close_ltf)/atr14 <= 0.8
      - reclaim: close_ltf > ema50_ltf
        AND (prev_close <= ema50_ltf OR prev_rsi <= 40)
      - spread_pct <= SPREAD_MAX_PCT

    SHORT:
      - HTF trend down (close_htf < ema200_htf)
      - RSI14 >= 60
      - pullback_atr = (close_ltf - ema50_ltf)/atr14 <= 0.8
      - reclaim: close_ltf < ema50_ltf
        AND (prev_close >= ema50_ltf OR prev_rsi >= 60)
      - spread_pct <= SPREAD_MAX_PCT

    SL = entry ± 1.6 * ATR
    TP = entry ± 2.4 * ATR
    RR >= max(min_rr, MIN_RR)
    """
    reject_reasons: List[str] = []
    trend_rejects: List[str] = []
    cont_rejects: List[str] = []
    breakout_rejects: List[str] = []
    meanrev_rejects: List[str] = []
    pullback_rejects: List[str] = []

    def _reject(code: str, detail: str) -> None:
        reject_reasons.append(f"{code}:{detail}")

    def _insufficient(field: str, strategies: str) -> None:
        code = f"insufficient_history:{field}"
        if "T" in strategies:
            tag = f"T:{code}"
            trend_rejects.append(tag)
            if tag not in reject_reasons:
                reject_reasons.append(tag)
        if "B" in strategies:
            tag = f"B:{code}"
            breakout_rejects.append(tag)
            if tag not in reject_reasons:
                reject_reasons.append(tag)
        if "M" in strategies:
            tag = f"M:{code}"
            meanrev_rejects.append(tag)
            if tag not in reject_reasons:
                reject_reasons.append(tag)

    # Extract payload fields
    price_snapshot = payload.get("price_snapshot", {})
    features_ltf = payload.get("features_ltf", {})
    context_htf = payload.get("context_htf", {})
    risk_policy = payload.get("risk_policy", {})
    position_state = payload.get("position_state", {})
    market_identity = payload.get("market_identity", {})

    close_ltf = _to_float(features_ltf.get("close")) or _to_float(price_snapshot.get("last")) or _to_float(price_snapshot.get("mark"))
    close_prev = _to_float(features_ltf.get("close_prev"))
    high_ltf = _to_float(features_ltf.get("high"))
    low_ltf = _to_float(features_ltf.get("low"))
    ema50_ltf = _to_float(features_ltf.get("ema50"))
    ema50_prev_12 = _to_float(features_ltf.get("ema50_prev_12"))
    ema120_ltf = _to_float(features_ltf.get("ema120"))
    donchian_high_240 = _to_float(features_ltf.get("donchian_high_240"))
    donchian_low_240 = _to_float(features_ltf.get("donchian_low_240"))
    donchian_high_20 = _to_float(features_ltf.get("donchian_high_20"))
    donchian_low_20 = _to_float(features_ltf.get("donchian_low_20"))
    consec_close_above_donchian_20 = _to_float(features_ltf.get("consec_close_above_donchian_20"))
    consec_close_below_donchian_20 = _to_float(features_ltf.get("consec_close_below_donchian_20"))
    atr14 = _to_float(features_ltf.get("atr14"))
    atr14_sma20 = _to_float(features_ltf.get("atr14_sma20"))
    bb_upper = _to_float(features_ltf.get("bb_upper"), 0.0) or 0.0
    bb_lower = _to_float(features_ltf.get("bb_lower"), 0.0) or 0.0
    bb_mid = _to_float(features_ltf.get("bb_mid"), 0.0) or 0.0
    volume_ratio = _to_float(features_ltf.get("volume_ratio"))
    candle_body_ratio = _to_float(features_ltf.get("candle_body_ratio"))
    rsi14 = _to_float(features_ltf.get("rsi14"))
    rsi14_prev = _to_float(features_ltf.get("rsi14_prev"))
    consec_above_ema50 = _to_float(features_ltf.get("consec_above_ema50"))
    consec_below_ema50 = _to_float(features_ltf.get("consec_below_ema50"))
    consec_above_ema50_prev = _to_float(features_ltf.get("consec_above_ema50_prev"))
    consec_below_ema50_prev = _to_float(features_ltf.get("consec_below_ema50_prev"))
    close_max_n = _to_float(features_ltf.get("close_max_n"))
    close_min_n = _to_float(features_ltf.get("close_min_n"))
    time_exit_bars = _to_float(features_ltf.get("time_exit_bars"))
    close_htf = _to_float(context_htf.get("close"))
    ema200_htf = _to_float(context_htf.get("ema200"))
    ema200_prev_n = _to_float(context_htf.get("ema200_prev_n"))
    ema200_slope_norm = _to_float(context_htf.get("ema200_slope_norm"))
    consec_above_ema200 = _to_float(context_htf.get("consec_above_ema200"))
    consec_below_ema200 = _to_float(context_htf.get("consec_below_ema200"))
    consec_higher_close = _to_float(context_htf.get("consec_higher_close"))
    consec_lower_close = _to_float(context_htf.get("consec_lower_close"))
    atr14_htf = _to_float(context_htf.get("atr14"))
    htf_trend = context_htf.get("trend")
    min_rr = _to_float(risk_policy.get("min_rr", settings.get_float("DECISION_MIN_RR")), settings.get_float("DECISION_MIN_RR"))
    min_rr = max(min_rr or 0.0, settings.get_float("MIN_RR"))

    bid = price_snapshot.get("bid")
    ask = price_snapshot.get("ask")
    spread_pct = None
    if bid and ask and close_ltf:
        try:
            spread_pct = abs(float(ask) - float(bid)) / float(close_ltf) * 100.0
        except Exception:
            spread_pct = None

    decision_ts = market_identity.get("timestamp_closed")

    # Check if already in position
    pos_side = position_state.get("side")
    pos_qty = _to_float(position_state.get("qty", 0.0)) or 0.0
    pos_entry = _to_float(position_state.get("entry", 0.0)) or 0.0
    has_position = pos_side is not None and pos_qty > 0
    time_exit_signal = False
    time_exit_progress_atr = settings.get_float("TIME_EXIT_PROGRESS_ATR")
    if (
        has_position
        and pos_entry > 0
        and atr14 is not None
        and atr14 > 0
        and close_max_n is not None
        and close_min_n is not None
        and close_max_n > 0
        and close_min_n > 0
    ):
        favorable = (close_max_n - pos_entry) if pos_side == "LONG" else (pos_entry - close_min_n)
        if favorable < time_exit_progress_atr * atr14:
            time_exit_signal = True

    if has_position and not time_exit_signal:
        reject_reasons.append(f"already_in_position: {pos_side} qty={pos_qty}")

    # Validate required fields (fail-closed in strategy selection)
    if close_ltf is None or close_ltf <= 0:
        _insufficient("close_5m", "TBM")
    if close_prev is None or close_prev <= 0:
        _insufficient("prev_close_5m", "TM")
    if high_ltf is None or high_ltf <= 0:
        _insufficient("high_5m", "B")
    if low_ltf is None or low_ltf <= 0:
        _insufficient("low_5m", "B")
    if ema50_ltf is None or ema50_ltf <= 0:
        _insufficient("ema50_5m", "T")
    if ema120_ltf is None or ema120_ltf <= 0:
        _insufficient("ema120_5m", "T")
    if donchian_high_20 is None or donchian_high_20 <= 0:
        _insufficient("donchian_20", "B")
    if donchian_low_20 is None or donchian_low_20 <= 0:
        _insufficient("donchian_20", "B")
    if consec_close_above_donchian_20 is None or consec_close_above_donchian_20 < 0:
        _insufficient("consec_close_above_donchian_20", "B")
    if consec_close_below_donchian_20 is None or consec_close_below_donchian_20 < 0:
        _insufficient("consec_close_below_donchian_20", "B")
    if atr14 is None or atr14 <= 0:
        _insufficient("atr14_5m", "TBM")
    if atr14_sma20 is None or atr14_sma20 <= 0:
        _insufficient("atr14_sma20", "B")
    if volume_ratio is None or volume_ratio <= 0:
        _insufficient("volume_ratio_5m", "B")
    if candle_body_ratio is None or candle_body_ratio <= 0:
        _insufficient("candle_body_ratio", "B")
    if rsi14 is None or rsi14 <= 0:
        _insufficient("rsi14_5m", "TM")
    if rsi14_prev is None or rsi14_prev <= 0:
        _insufficient("rsi14_prev_5m", "T")
    if consec_above_ema50 is None or consec_above_ema50 < 0:
        _insufficient("consec_above_ema50", "T")
    if consec_below_ema50 is None or consec_below_ema50 < 0:
        _insufficient("consec_below_ema50", "T")
    if consec_above_ema50_prev is None or consec_above_ema50_prev < 0:
        _insufficient("consec_above_ema50_prev", "T")
    if consec_below_ema50_prev is None or consec_below_ema50_prev < 0:
        _insufficient("consec_below_ema50_prev", "T")
    if bb_upper <= 0 or bb_lower <= 0 or bb_mid <= 0:
        _insufficient("bb_20", "M")
    if ema200_htf is None or ema200_htf <= 0:
        _insufficient("ema200_1h", "TBM")
    if ema200_prev_n is None or ema200_prev_n <= 0:
        _insufficient("ema200_prev_n", "TB")
    if ema200_slope_norm is None:
        _insufficient("ema200_slope_norm", "TB")
    if consec_above_ema200 is None or consec_above_ema200 < 0:
        _insufficient("consec_above_ema200", "TB")
    if consec_below_ema200 is None or consec_below_ema200 < 0:
        _insufficient("consec_below_ema200", "TB")
    if consec_higher_close is None or consec_higher_close < 0:
        _insufficient("consec_higher_close", "TB")
    if consec_lower_close is None or consec_lower_close < 0:
        _insufficient("consec_lower_close", "TB")
    if close_htf is None or close_htf <= 0:
        _insufficient("close_1h", "TBM")
    if atr14_htf is None or atr14_htf <= 0:
        _insufficient("atr14_1h", "TBM")

    spread_max_pct = settings.get_float("SPREAD_MAX_PCT")
    base_min_rr = max(min_rr or 0.0, settings.get_float("MIN_RR"))
    trend_slope_min = settings.get_float("HTF_TREND_SLOPE_MIN")
    trend_persist_min = settings.get_int("HTF_TREND_PERSIST_MIN")
    trend_structure_min = max(settings.get_int("HTF_TREND_STRUCTURE_MIN"), 0)

    direction = "UP" if (close_htf is not None and ema200_htf is not None and close_htf > ema200_htf) else "DOWN"
    trend_strength_min = 0.6
    trend_strength = 0.0
    if close_htf is not None and ema200_htf is not None and atr14_htf is not None and atr14_htf > 0:
        trend_strength = abs(close_htf - ema200_htf) / atr14_htf
    regime = "TREND" if trend_strength >= trend_strength_min else "RANGE"
    structure_long_ok = (
        trend_structure_min <= 0
        or (consec_higher_close is not None and consec_higher_close >= trend_structure_min)
    )
    structure_short_ok = (
        trend_structure_min <= 0
        or (consec_lower_close is not None and consec_lower_close >= trend_structure_min)
    )
    trend_stable_long = (
        htf_trend == "up"
        and ema200_slope_norm is not None
        and ema200_slope_norm >= trend_slope_min
        and consec_above_ema200 is not None
        and consec_above_ema200 >= trend_persist_min
        and structure_long_ok
    )
    trend_stable_short = (
        htf_trend == "down"
        and ema200_slope_norm is not None
        and ema200_slope_norm >= trend_slope_min
        and consec_below_ema200 is not None
        and consec_below_ema200 >= trend_persist_min
        and structure_short_ok
    )

    atr_ratio = 0.0
    if atr14 is not None and atr14_sma20 is not None and atr14_sma20 > 0:
        atr_ratio = atr14 / atr14_sma20
    volatility_state = "VOL_EXPANSION" if atr_ratio >= 1.3 else "NORMAL"

    pullback_atr_long = (
        (ema50_ltf - close_ltf) / atr14
        if (
            atr14 is not None
            and atr14 > 0
            and ema50_ltf is not None
            and ema50_ltf > 0
            and close_ltf is not None
            and close_ltf > 0
        )
        else float("inf")
    )
    pullback_atr_short = (
        (close_ltf - ema50_ltf) / atr14
        if (
            atr14 is not None
            and atr14 > 0
            and ema50_ltf is not None
            and ema50_ltf > 0
            and close_ltf is not None
            and close_ltf > 0
        )
        else float("inf")
    )
    reclaim_long = (
        close_ltf is not None
        and close_ltf > 0
        and ema50_ltf is not None
        and ema50_ltf > 0
        and close_prev is not None
        and close_prev > 0
        and close_ltf > ema50_ltf
        and close_prev <= ema50_ltf
    )
    reclaim_short = (
        close_ltf is not None
        and close_ltf > 0
        and ema50_ltf is not None
        and ema50_ltf > 0
        and close_prev is not None
        and close_prev > 0
        and close_ltf < ema50_ltf
        and close_prev >= ema50_ltf
    )
    prev_reclaim_long = close_prev <= ema50_ltf if (close_prev is not None and ema50_ltf is not None) else False
    prev_reclaim_short = close_prev >= ema50_ltf if (close_prev is not None and ema50_ltf is not None) else False
    prev_rsi_long = rsi14_prev <= 40 if rsi14_prev is not None else False
    prev_rsi_short = rsi14_prev >= 60 if rsi14_prev is not None else False

    breakout_long = (
        close_ltf is not None
        and close_ltf > 0
        and donchian_high_20 is not None
        and donchian_high_20 > 0
        and close_ltf > donchian_high_20
    )
    breakout_short = (
        close_ltf is not None
        and close_ltf > 0
        and donchian_low_20 is not None
        and donchian_low_20 > 0
        and close_ltf < donchian_low_20
    )
    dist50 = (
        _safe_div(abs(close_ltf - ema50_ltf), atr14, default=None)
        if close_ltf is not None and close_ltf > 0 and ema50_ltf is not None and ema50_ltf > 0
        else None
    )
    dist50_prev = (
        _safe_div(abs(close_prev - ema50_ltf), atr14, default=None)
        if close_prev is not None and close_prev > 0 and ema50_ltf is not None and ema50_ltf > 0
        else None
    )
    dc_width_atr = (
        _safe_div(donchian_high_20 - donchian_low_20, atr14, default=None)
        if donchian_high_20 is not None
        and donchian_high_20 > 0
        and donchian_low_20 is not None
        and donchian_low_20 > 0
        else None
    )
    reentry_long = (
        close_prev is not None
        and close_prev > 0
        and close_ltf is not None
        and close_ltf > 0
        and bb_lower > 0
        and close_prev <= bb_lower
        and close_ltf > bb_lower
    )
    reentry_short = (
        close_prev is not None
        and close_prev > 0
        and close_ltf is not None
        and close_ltf > 0
        and bb_upper > 0
        and close_prev >= bb_upper
        and close_ltf < bb_upper
    )

    extreme_short = (
        regime == "TREND"
        and direction == "DOWN"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 <= 18
        and reentry_long
    )
    extreme_long = (
        regime == "TREND"
        and direction == "UP"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 >= 82
        and reentry_short
    )

    trend_long_ok = (
        regime == "TREND"
        and direction == "UP"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 <= 45
        and pullback_atr_long <= 1.0
        and reclaim_long
    )
    trend_short_ok = (
        regime == "TREND"
        and direction == "DOWN"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 >= 55
        and pullback_atr_short <= 1.0
        and reclaim_short
    )
    if regime != "TREND":
        trend_rejects.append("T:regime")
    if direction == "UP":
        if rsi14 is not None and rsi14 > 45:
            trend_rejects.append("T:rsi")
        if pullback_atr_long > 1.0:
            trend_rejects.append("T:pullback")
        if not reclaim_long:
            trend_rejects.append("T:reclaim")
    else:
        if rsi14 is not None and rsi14 < 55:
            trend_rejects.append("T:rsi")
        if pullback_atr_short > 1.0:
            trend_rejects.append("T:pullback")
        if not reclaim_short:
            trend_rejects.append("T:reclaim")
    if spread_pct is not None and spread_pct > spread_max_pct:
        trend_rejects.append("T:spread")

    d_atr = 0.15
    cont_slope_atr_max = -0.15
    cont_slope_atr_min = 0.15
    cont_k_max = 2.2
    cont_rsi_min_short = 30.0
    cont_rsi_max_long = 70.0
    cont_body_min = 0.50
    cont_vol_min = 1.0
    cont_atr_ratio_min = 0.95
    regime_breakout_vol_min = settings.get_float("REGIME_BREAKOUT_VOL_MIN")
    regime_compression_width_atr_max = settings.get_float("REGIME_COMPRESSION_WIDTH_ATR_MAX")
    regime_compression_vol_max = settings.get_float("REGIME_COMPRESSION_VOL_MAX")
    regime_trend_dist50_max = settings.get_float("REGIME_TREND_DIST50_MAX")
    breakout_accept_bars = settings.get_int("BREAKOUT_ACCEPT_BARS")
    breakout_reject_wick_atr = settings.get_float("BREAKOUT_REJECT_WICK_ATR")
    breakout_retest_atr = settings.get_float("BREAKOUT_RETEST_ATR")
    breakout_sl_atr = settings.get_float("BREAKOUT_SL_ATR")
    breakout_rr_target = settings.get_float("BREAKOUT_RR_TARGET")
    continuation_sl_atr = settings.get_float("CONTINUATION_SL_ATR")
    continuation_rr_target = settings.get_float("CONTINUATION_RR_TARGET")
    pullback_reentry_dist50_min = settings.get_float("PULLBACK_REENTRY_DIST50_MIN")
    pullback_reentry_dist50_max = settings.get_float("PULLBACK_REENTRY_DIST50_MAX")
    pullback_reentry_min_bars = settings.get_int("PULLBACK_REENTRY_MIN_BARS")
    pullback_reentry_confirm_body_min = settings.get_float("PULLBACK_REENTRY_CONFIRM_BODY_MIN")
    pullback_reentry_reclaim_vol_min = settings.get_float("PULLBACK_REENTRY_RECLAIM_VOL_MIN")
    pullback_reentry_sl_atr = settings.get_float("PULLBACK_REENTRY_SL_ATR")
    pullback_reentry_rr_target = settings.get_float("PULLBACK_REENTRY_RR_TARGET")
    pullback_reentry_vol_min = settings.get_float("PULLBACK_REENTRY_VOL_MIN")
    range_meanrev_edge_atr = settings.get_float("RANGE_MEANREV_EDGE_ATR")
    range_meanrev_vol_max = settings.get_float("RANGE_MEANREV_VOL_MAX")
    range_meanrev_sl_atr = settings.get_float("RANGE_MEANREV_SL_ATR")
    range_meanrev_rr_target = settings.get_float("RANGE_MEANREV_RR_TARGET")

    slope_atr = None
    if (
        atr14 is not None
        and atr14 > 0
        and ema50_ltf is not None
        and ema50_ltf > 0
        and ema50_prev_12 is not None
        and ema50_prev_12 > 0
    ):
        slope_atr = (ema50_ltf - ema50_prev_12) / atr14

    k_short = None
    k_long = None
    if (
        atr14 is not None
        and atr14 > 0
        and ema50_ltf is not None
        and ema50_ltf > 0
        and close_ltf is not None
        and close_ltf > 0
    ):
        k_short = (ema50_ltf - close_ltf) / atr14
        k_long = (close_ltf - ema50_ltf) / atr14

    break_level_short = None
    break_level_long = None
    if atr14 is not None and atr14 > 0:
        if donchian_low_20 is not None and donchian_low_20 > 0:
            break_level_short = donchian_low_20 - d_atr * atr14
        if donchian_high_20 is not None and donchian_high_20 > 0:
            break_level_long = donchian_high_20 + d_atr * atr14

    def _cont_context_ok(expected_dir: str, reject_bucket: List[str]) -> bool:
        ok = True
        if regime != "TREND" or direction != expected_dir or trend_strength < trend_strength_min:
            reject_bucket.append("C:trend")
            ok = False
        if expected_dir == "UP" and not trend_stable_long:
            reject_bucket.append("C:stability")
            ok = False
        if expected_dir == "DOWN" and not trend_stable_short:
            reject_bucket.append("C:stability")
            ok = False
        return ok

    def _cont_common_filters(reject_bucket: List[str]) -> bool:
        ok = True
        if candle_body_ratio is None or candle_body_ratio < cont_body_min:
            reject_bucket.append("C:body")
            ok = False
        if volume_ratio is None or volume_ratio < cont_vol_min:
            reject_bucket.append("C:vol")
            ok = False
        if atr_ratio < cont_atr_ratio_min:
            reject_bucket.append("C:atr_ratio")
            ok = False
        return ok

    cont_short_rejects: List[str] = []
    cont_long_rejects: List[str] = []
    cont_short_ok = False
    cont_long_ok = False

    if _cont_context_ok("DOWN", cont_short_rejects):
        if (
            close_ltf is None
            or close_ltf <= 0
            or ema50_ltf is None
            or ema50_ltf <= 0
            or close_ltf >= ema50_ltf
        ):
            cont_short_rejects.append("C:trend")
        if slope_atr is None or slope_atr > cont_slope_atr_max:
            cont_short_rejects.append("C:slope")
        if k_short is None or k_short > cont_k_max:
            cont_short_rejects.append("C:k")
        if rsi14 is None or rsi14 < cont_rsi_min_short:
            cont_short_rejects.append("C:rsi")
        if break_level_short is None or close_ltf is None or close_ltf > break_level_short:
            cont_short_rejects.append("C:break")
        _cont_common_filters(cont_short_rejects)
        cont_short_ok = len(cont_short_rejects) == 0

    if _cont_context_ok("UP", cont_long_rejects):
        if (
            close_ltf is None
            or close_ltf <= 0
            or ema50_ltf is None
            or ema50_ltf <= 0
            or close_ltf <= ema50_ltf
        ):
            cont_long_rejects.append("C:trend")
        if slope_atr is None or slope_atr < cont_slope_atr_min:
            cont_long_rejects.append("C:slope")
        if k_long is None or k_long > cont_k_max:
            cont_long_rejects.append("C:k")
        if rsi14 is None or rsi14 > cont_rsi_max_long:
            cont_long_rejects.append("C:rsi")
        if break_level_long is None or close_ltf is None or close_ltf < break_level_long:
            cont_long_rejects.append("C:break")
        _cont_common_filters(cont_long_rejects)
        cont_long_ok = len(cont_long_rejects) == 0

    cont_rejects = cont_short_rejects if direction == "DOWN" else cont_long_rejects
    if not cont_long_ok and not cont_short_ok:
        cont_rejects.append("C:strategy_ineligible")

    regime_detected = compute_regime_5m({
        "close_ltf": close_ltf,
        "ema50_ltf": ema50_ltf,
        "atr14": atr14,
        "donchian_high_20": donchian_high_20,
        "donchian_low_20": donchian_low_20,
        "volume_ratio": volume_ratio,
        "candle_body_ratio": candle_body_ratio,
        "trend": htf_trend or "range",
        "cont_body_min": cont_body_min,
        "cont_vol_min": cont_vol_min,
        "breakout_vol_min": regime_breakout_vol_min,
        "compression_width_atr_max": regime_compression_width_atr_max,
        "compression_vol_max": regime_compression_vol_max,
        "trend_dist50_max": regime_trend_dist50_max,
    })

    breakout_accept_bars = max(breakout_accept_bars, 1)
    impulse_long = breakout_long and volume_ratio is not None and volume_ratio >= regime_breakout_vol_min
    impulse_short = breakout_short and volume_ratio is not None and volume_ratio >= regime_breakout_vol_min
    accept_long = False
    accept_short = False
    if breakout_accept_bars > 1:
        if consec_close_above_donchian_20 is not None and consec_close_above_donchian_20 >= breakout_accept_bars:
            accept_long = True
        if consec_close_below_donchian_20 is not None and consec_close_below_donchian_20 >= breakout_accept_bars:
            accept_short = True
    else:
        if low_ltf is not None and donchian_high_20 is not None and atr14 is not None and breakout_long:
            accept_long = low_ltf >= donchian_high_20 - breakout_reject_wick_atr * atr14
        if high_ltf is not None and donchian_low_20 is not None and atr14 is not None and breakout_short:
            accept_short = high_ltf <= donchian_low_20 + breakout_reject_wick_atr * atr14
    retest_long = False
    retest_short = False
    if (
        close_prev is not None
        and donchian_high_20 is not None
        and low_ltf is not None
        and atr14 is not None
        and close_prev > donchian_high_20
        and close_ltf is not None
        and close_ltf > donchian_high_20
        and low_ltf <= donchian_high_20 + breakout_retest_atr * atr14
        and volume_ratio is not None
        and volume_ratio >= regime_breakout_vol_min
    ):
        retest_long = True
    if (
        close_prev is not None
        and donchian_low_20 is not None
        and high_ltf is not None
        and atr14 is not None
        and close_prev < donchian_low_20
        and close_ltf is not None
        and close_ltf < donchian_low_20
        and high_ltf >= donchian_low_20 - breakout_retest_atr * atr14
        and volume_ratio is not None
        and volume_ratio >= regime_breakout_vol_min
    ):
        retest_short = True
    breakout_expansion_long_ok = impulse_long and (accept_long or retest_long) and trend_stable_long
    breakout_expansion_short_ok = impulse_short and (accept_short or retest_short) and trend_stable_short
    pullback_reentry_long_ok = (
        htf_trend == "up"
        and trend_stable_long
        and dist50 is not None
        and dist50 <= pullback_reentry_dist50_max
        and dist50_prev is not None
        and dist50_prev >= pullback_reentry_dist50_min
        and dist50_prev <= pullback_reentry_dist50_max
        and consec_below_ema50_prev is not None
        and consec_below_ema50_prev >= pullback_reentry_min_bars
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf > ema50_ltf
        and close_prev is not None
        and close_ltf > close_prev
        and candle_body_ratio is not None
        and candle_body_ratio >= pullback_reentry_confirm_body_min
        and (
            (consec_above_ema50 is not None and consec_above_ema50 >= 2)
            or (volume_ratio is not None and volume_ratio >= pullback_reentry_reclaim_vol_min)
        )
        and volume_ratio is not None
        and volume_ratio >= pullback_reentry_vol_min
    )
    pullback_reentry_short_ok = (
        htf_trend == "down"
        and trend_stable_short
        and dist50 is not None
        and dist50 <= pullback_reentry_dist50_max
        and dist50_prev is not None
        and dist50_prev >= pullback_reentry_dist50_min
        and dist50_prev <= pullback_reentry_dist50_max
        and consec_above_ema50_prev is not None
        and consec_above_ema50_prev >= pullback_reentry_min_bars
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf < ema50_ltf
        and close_prev is not None
        and close_ltf < close_prev
        and candle_body_ratio is not None
        and candle_body_ratio >= pullback_reentry_confirm_body_min
        and (
            (consec_below_ema50 is not None and consec_below_ema50 >= 2)
            or (volume_ratio is not None and volume_ratio >= pullback_reentry_reclaim_vol_min)
        )
        and volume_ratio is not None
        and volume_ratio >= pullback_reentry_vol_min
    )
    range_meanrev_long_ok = (
        regime_detected == "RANGE"
        and (htf_trend == "range" or trend_strength < trend_strength_min)
        and not (breakout_long or breakout_short)
        and close_ltf is not None
        and donchian_low_20 is not None
        and atr14 is not None
        and close_ltf <= donchian_low_20 + range_meanrev_edge_atr * atr14
        and volume_ratio is not None
        and volume_ratio < range_meanrev_vol_max
    )
    range_meanrev_short_ok = (
        regime_detected == "RANGE"
        and (htf_trend == "range" or trend_strength < trend_strength_min)
        and not (breakout_long or breakout_short)
        and close_ltf is not None
        and donchian_high_20 is not None
        and atr14 is not None
        and close_ltf >= donchian_high_20 - range_meanrev_edge_atr * atr14
        and volume_ratio is not None
        and volume_ratio < range_meanrev_vol_max
    )

    breakout_long_ok = breakout_expansion_long_ok
    breakout_short_ok = breakout_expansion_short_ok
    if not breakout_long and not breakout_short:
        breakout_rejects.append("B:breakout")
    if not impulse_long and not impulse_short:
        breakout_rejects.append("B:impulse")
    if not (accept_long or retest_long or accept_short or retest_short):
        breakout_rejects.append("B:accept")
    if (impulse_long and not trend_stable_long) or (impulse_short and not trend_stable_short):
        breakout_rejects.append("B:stability")
    if volume_ratio is None or volume_ratio < regime_breakout_vol_min:
        breakout_rejects.append("B:vol")
    if spread_pct is not None and spread_pct > spread_max_pct:
        breakout_rejects.append("B:spread")
    if not breakout_long_ok and not breakout_short_ok:
        breakout_rejects.append("B:strategy_ineligible")

    meanrev_long_ok = range_meanrev_long_ok
    meanrev_short_ok = range_meanrev_short_ok
    meanrev_ok = meanrev_long_ok or meanrev_short_ok
    if regime_detected != "RANGE":
        meanrev_rejects.append("M:regime")
    if breakout_long or breakout_short:
        meanrev_rejects.append("M:breakout")
    if htf_trend != "range" and trend_strength >= trend_strength_min:
        meanrev_rejects.append("M:trend")
    if volume_ratio is None or volume_ratio >= range_meanrev_vol_max:
        meanrev_rejects.append("M:vol")
    if close_ltf is None or atr14 is None or donchian_high_20 is None or donchian_low_20 is None:
        meanrev_rejects.append("M:range_edge")
    else:
        if close_ltf > donchian_low_20 + range_meanrev_edge_atr * atr14:
            meanrev_rejects.append("M:range_long")
        if close_ltf < donchian_high_20 - range_meanrev_edge_atr * atr14:
            meanrev_rejects.append("M:range_short")
    if spread_pct is not None and spread_pct > spread_max_pct:
        meanrev_rejects.append("M:spread")
    if not meanrev_ok:
        meanrev_rejects.append("M:strategy_ineligible")

    if regime_detected != "PULLBACK":
        pullback_rejects.append("P:regime")
    if htf_trend not in ("up", "down"):
        pullback_rejects.append("P:trend")
    if dist50 is None or dist50 > pullback_reentry_dist50_max:
        pullback_rejects.append("P:dist50")
    if dist50_prev is None or dist50_prev < pullback_reentry_dist50_min or dist50_prev > pullback_reentry_dist50_max:
        pullback_rejects.append("P:dist50_prev")
    if htf_trend == "up":
        if consec_below_ema50_prev is None or consec_below_ema50_prev < pullback_reentry_min_bars:
            pullback_rejects.append("P:pullback_bars")
    if htf_trend == "down":
        if consec_above_ema50_prev is None or consec_above_ema50_prev < pullback_reentry_min_bars:
            pullback_rejects.append("P:pullback_bars")
    if htf_trend == "up":
        if close_ltf is None or ema50_ltf is None or close_ltf <= ema50_ltf:
            pullback_rejects.append("P:reclaim")
    if htf_trend == "down":
        if close_ltf is None or ema50_ltf is None or close_ltf >= ema50_ltf:
            pullback_rejects.append("P:reclaim")
    if close_prev is None or close_ltf is None:
        pullback_rejects.append("P:confirm")
    else:
        if htf_trend == "up" and close_ltf <= close_prev:
            pullback_rejects.append("P:confirm")
        if htf_trend == "down" and close_ltf >= close_prev:
            pullback_rejects.append("P:confirm")
    if candle_body_ratio is None or candle_body_ratio < pullback_reentry_confirm_body_min:
        pullback_rejects.append("P:body")
    if not (
        (consec_above_ema50 is not None and consec_above_ema50 >= 2)
        or (volume_ratio is not None and volume_ratio >= pullback_reentry_reclaim_vol_min)
    ):
        pullback_rejects.append("P:fake_reclaim")
    if volume_ratio is None or volume_ratio < pullback_reentry_vol_min:
        pullback_rejects.append("P:vol")
    if (htf_trend == "up" and not trend_stable_long) or (htf_trend == "down" and not trend_stable_short):
        pullback_rejects.append("P:stability")
    if spread_pct is not None and spread_pct > spread_max_pct:
        pullback_rejects.append("P:spread")
    if not pullback_reentry_long_ok and not pullback_reentry_short_ok:
        pullback_rejects.append("P:strategy_ineligible")

    # Extreme snapback limiter
    extreme_rejects: List[str] = []
    if extreme_long or extreme_short:
        if decision_ts is None:
            extreme_rejects.append("X:extreme_ts")
        else:
            last_extreme_ts = None
            if daily_state:
                last_extreme_ts = daily_state.get("extreme_snapback_ts")
            if last_extreme_ts is not None:
                delta = int(decision_ts) - int(last_extreme_ts)
                if delta < 6 * 3600:
                    extreme_rejects.append("X:extreme_rate")
                if delta < 30 * 60:
                    extreme_rejects.append("X:extreme_cooldown")
    if extreme_rejects:
        meanrev_rejects.extend(extreme_rejects)

    intent = "HOLD"
    entry = None
    sl = None
    tp = None
    rr = None
    rule = None
    selected_strategy = "NONE"
    eligible_strategies: List[str] = []
    strategy_block_reason = None

    def _apply_risk(intent_side: str, sl_mult: float, rr_target: float) -> None:
        nonlocal intent, entry, sl, tp, rr
        if close_ltf is None or atr14 is None or atr14 <= 0:
            return
        entry = close_ltf
        if intent_side == "LONG":
            sl = entry - sl_mult * atr14
        else:
            sl = entry + sl_mult * atr14
        risk = abs(entry - sl)
        if risk > 0:
            rr = rr_target
            if intent_side == "LONG":
                tp = entry + rr_target * risk
            else:
                tp = entry - rr_target * risk
            if rr_target < base_min_rr:
                reject_reasons.append(f"{intent_side[0]}:rr")
                intent = "HOLD"
                entry = None
                sl = None
                tp = None
                rr = None
        else:
            reject_reasons.append(f"{intent_side[0]}:rr")
            intent = "HOLD"
            entry = None
            sl = None
            tp = None
            rr = None

    routing_regime = regime_detected
    runtime = get_runtime_settings()
    allow_override = runtime.is_test or runtime.is_replay or runtime.is_offline or runtime.env != "production"
    override_regime = str(settings.get_str("ROUTING_REGIME_OVERRIDE", "") or "").strip().upper()
    if override_regime and allow_override:
        override_map = {
            "BREAKOUT_EXPANSION": "BREAKOUT_EXPANSION",
            "TREND_CONTINUATION": "TREND_CONTINUATION",
            "PULLBACK": "PULLBACK",
            "RANGE": "RANGE",
            "COMPRESSION": "COMPRESSION",
        }
        if override_regime in override_map:
            routing_regime = override_map[override_regime]

    if time_exit_signal:
        eligible_strategies = ["TIME_EXIT"]
        selected_strategy = "TIME_EXIT"
        intent = "CLOSE"
        rule = selected_strategy
    else:
        strategy_by_regime = {
            "BREAKOUT_EXPANSION": "BREAKOUT_EXPANSION",
            "TREND_CONTINUATION": "CONTINUATION",
            "PULLBACK": "PULLBACK_REENTRY",
            "RANGE": "RANGE_MEANREV",
            "COMPRESSION": None,
        }
        strategy_ok = {
            "BREAKOUT_EXPANSION": breakout_expansion_long_ok or breakout_expansion_short_ok,
            "CONTINUATION": cont_long_ok or cont_short_ok,
            "PULLBACK_REENTRY": pullback_reentry_long_ok or pullback_reentry_short_ok,
            "RANGE_MEANREV": range_meanrev_long_ok or range_meanrev_short_ok,
        }
        routed_strategy = strategy_by_regime.get(routing_regime)
        if routing_regime == "COMPRESSION":
            strategy_block_reason = "regime:COMPRESSION"
        elif routed_strategy is None:
            strategy_block_reason = f"regime:{routing_regime or 'UNKNOWN'}"
        elif strategy_ok.get(routed_strategy):
            eligible_strategies = [routed_strategy]
            selected_strategy = routed_strategy
        else:
            strategy_block_reason = "strategy_ineligible"

        if selected_strategy == "BREAKOUT_EXPANSION":
            intent = "LONG" if breakout_expansion_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, breakout_sl_atr, breakout_rr_target)
        elif selected_strategy == "CONTINUATION":
            intent = "LONG" if cont_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, continuation_sl_atr, continuation_rr_target)
        elif selected_strategy == "PULLBACK_REENTRY":
            intent = "LONG" if pullback_reentry_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, pullback_reentry_sl_atr, pullback_reentry_rr_target)
        elif selected_strategy == "RANGE_MEANREV":
            intent = "LONG" if range_meanrev_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, range_meanrev_sl_atr, range_meanrev_rr_target)

    if reject_reasons and intent != "CLOSE":
        intent = "HOLD"
        entry = None
        sl = None
        tp = None
        rr = None

    if intent == "HOLD":
        active_rejects: List[str] = []
        if routing_regime == "BREAKOUT_EXPANSION":
            active_rejects.extend(breakout_rejects)
        elif routing_regime == "TREND_CONTINUATION":
            active_rejects.extend(cont_rejects)
        elif routing_regime == "PULLBACK":
            active_rejects.extend(pullback_rejects)
        elif routing_regime == "RANGE":
            active_rejects.extend(meanrev_rejects)
        elif routing_regime == "COMPRESSION":
            active_rejects.append("R:compression")
        if routing_regime != regime_detected:
            active_rejects.append("M:regime")

        for code in active_rejects:
            if code not in reject_reasons:
                reject_reasons.append(code)

    decision = {
        "intent": intent,
        "reject_reasons": reject_reasons if reject_reasons else [],
        "strategy": "REGIME_MULTI",
        "signal": {
            "trend": htf_trend or "range",
            "direction": direction,
            "regime": regime,
            "regime_detected": regime_detected,
            "regime_used_for_routing": routing_regime,
            "trend_strength": trend_strength,
            "trend_stable_long": trend_stable_long,
            "trend_stable_short": trend_stable_short,
            "trend_structure_long_ok": structure_long_ok,
            "trend_structure_short_ok": structure_short_ok,
            "selected_strategy": selected_strategy,
            "eligible_strategies": eligible_strategies,
            "strategy_block_reason": strategy_block_reason,
            "atr_ratio": atr_ratio,
            "volatility_state": volatility_state,
            "cont_short_ok": cont_short_ok,
            "cont_long_ok": cont_long_ok,
            "slope_atr": slope_atr,
            "k_overextension": k_short if direction == "DOWN" else k_long,
            "break_level": break_level_short if direction == "DOWN" else break_level_long,
            "break_delta_atr": d_atr,
            "cont_reject_codes": cont_rejects,
            "dist50": dist50,
            "dist50_prev": dist50_prev,
            "dc_width_atr": dc_width_atr,
            "high_ltf": high_ltf,
            "low_ltf": low_ltf,
            "consec_close_above_donchian_20": consec_close_above_donchian_20,
            "consec_close_below_donchian_20": consec_close_below_donchian_20,
            "consec_above_ema50": consec_above_ema50,
            "consec_below_ema50": consec_below_ema50,
            "consec_above_ema50_prev": consec_above_ema50_prev,
            "consec_below_ema50_prev": consec_below_ema50_prev,
            "time_exit_signal": time_exit_signal,
            "time_exit_bars": time_exit_bars,
            "time_exit_progress_atr": time_exit_progress_atr,
            "close_max_n": close_max_n,
            "close_min_n": close_min_n,
            "breakout_expansion_long_ok": breakout_expansion_long_ok,
            "breakout_expansion_short_ok": breakout_expansion_short_ok,
            "pullback_reentry_long_ok": pullback_reentry_long_ok,
            "pullback_reentry_short_ok": pullback_reentry_short_ok,
            "range_meanrev_long_ok": range_meanrev_long_ok,
            "range_meanrev_short_ok": range_meanrev_short_ok,
            "pullback_atr_long": pullback_atr_long,
            "pullback_atr_short": pullback_atr_short,
            "reclaim_long": reclaim_long,
            "reclaim_short": reclaim_short,
            "prev_reclaim_long": prev_reclaim_long,
            "prev_reclaim_short": prev_reclaim_short,
            "prev_rsi_long": prev_rsi_long,
            "prev_rsi_short": prev_rsi_short,
            "reentry_long": reentry_long,
            "reentry_short": reentry_short,
            "breakout_long": breakout_long,
            "breakout_short": breakout_short,
            "volume_ratio": volume_ratio or 0.0,
            "candle_body_ratio": candle_body_ratio or 0.0,
            "spread_pct": spread_pct or 0.0,
            "atr": atr14 or 0.0,
            "atr14_htf": atr14_htf or 0.0,
            "close_ltf": close_ltf or 0.0,
            "close_prev_ltf": close_prev or 0.0,
            "ema50_ltf": ema50_ltf or 0.0,
            "ema50_prev_12": ema50_prev_12 or 0.0,
            "ema120_ltf": ema120_ltf or 0.0,
            "rsi14_ltf": rsi14 or 0.0,
            "rsi14_prev_ltf": rsi14_prev or 0.0,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_mid": bb_mid,
            "donchian_high_20": donchian_high_20 or 0.0,
            "donchian_low_20": donchian_low_20 or 0.0,
            "donchian_high_240": donchian_high_240 or 0.0,
            "donchian_low_240": donchian_low_240 or 0.0,
            "close_htf": close_htf or 0.0,
            "ema200_htf": ema200_htf or 0.0,
            "ema200_prev_n": ema200_prev_n or 0.0,
            "ema200_slope_norm": ema200_slope_norm or 0.0,
            "consec_above_ema200": consec_above_ema200 or 0.0,
            "consec_below_ema200": consec_below_ema200 or 0.0,
            "consec_higher_close": consec_higher_close or 0.0,
            "consec_lower_close": consec_lower_close or 0.0,
            "thresholds": {
                "trend_strength_min": trend_strength_min,
                "regime_breakout_vol_min": regime_breakout_vol_min,
                "regime_compression_width_atr_max": regime_compression_width_atr_max,
                "regime_compression_vol_max": regime_compression_vol_max,
                "regime_trend_dist50_max": regime_trend_dist50_max,
                "breakout_accept_bars": breakout_accept_bars,
                "breakout_reject_wick_atr": breakout_reject_wick_atr,
                "breakout_retest_atr": breakout_retest_atr,
                "breakout_sl_atr": breakout_sl_atr,
                "breakout_rr_target": breakout_rr_target,
                "continuation_sl_atr": continuation_sl_atr,
                "continuation_rr_target": continuation_rr_target,
                "pullback_reentry_dist50_min": pullback_reentry_dist50_min,
                "pullback_reentry_dist50_max": pullback_reentry_dist50_max,
                "pullback_reentry_min_bars": pullback_reentry_min_bars,
                "pullback_reentry_confirm_body_min": pullback_reentry_confirm_body_min,
                "pullback_reentry_reclaim_vol_min": pullback_reentry_reclaim_vol_min,
                "pullback_reentry_sl_atr": pullback_reentry_sl_atr,
                "pullback_reentry_rr_target": pullback_reentry_rr_target,
                "pullback_reentry_vol_min": pullback_reentry_vol_min,
                "range_meanrev_edge_atr": range_meanrev_edge_atr,
                "range_meanrev_vol_max": range_meanrev_vol_max,
                "range_meanrev_sl_atr": range_meanrev_sl_atr,
                "range_meanrev_rr_target": range_meanrev_rr_target,
                "trend_slope_min": trend_slope_min,
                "trend_persist_min": trend_persist_min,
                "trend_structure_min": trend_structure_min,
                "time_exit_bars": time_exit_bars,
                "time_exit_progress_atr": time_exit_progress_atr,
                "cont_slope_atr_max": cont_slope_atr_max,
                "cont_slope_atr_min": cont_slope_atr_min,
                "cont_k_max": cont_k_max,
                "cont_rsi_min_short": cont_rsi_min_short,
                "cont_rsi_max_long": cont_rsi_max_long,
                "cont_body_min": cont_body_min,
                "cont_vol_min": cont_vol_min,
                "cont_atr_ratio_min": cont_atr_ratio_min,
                "cont_break_d_atr": d_atr,
                "sl_atr_cont": 1.2,
                "tp_atr_cont": 1.8,
                "trend_rsi_long_max": 45,
                "trend_rsi_short_min": 55,
                "range_rsi_long_max": 35,
                "range_rsi_short_min": 65,
                "extreme_rsi_long_min": 82,
                "extreme_rsi_short_max": 18,
                "pullback_atr_max": 1.0,
                "breakout_volume_min": 1.8,
                "breakout_body_min": 0.65,
                "breakout_atr_ratio_min": 1.2,
                "breakout_chop_min": 1.1,
                "sl_atr_trend": 1.6,
                "tp_atr_trend": 2.6,
                "sl_atr_breakout": 1.0,
                "tp_atr_breakout": 1.8,
                "sl_atr_range": 1.2,
                "tp_atr_range": 1.8,
                "bb_period": 20,
                "bb_std": 2.0,
                "ema_period": 50,
                "atr_period": 14,
                "donchian_n": 20,
            },
        },
    }
    if rule:
        decision["rule"] = rule
    if decision_ts is not None:
        decision["timestamp"] = int(decision_ts)
    if entry is not None:
        decision["entry"] = entry
    if sl is not None:
        decision["sl"] = sl
    if tp is not None:
        decision["tp"] = tp
    if rr is not None:
        decision["rr"] = rr

    # Validate decision
    is_valid, validation_errors = validate_decision(decision)
    if not is_valid:
        decision["intent"] = "HOLD"
        decision["reject_reasons"].extend(validation_errors)
        decision.pop("entry", None)
        decision.pop("sl", None)
        decision.pop("tp", None)
        decision.pop("rr", None)

    return decision
