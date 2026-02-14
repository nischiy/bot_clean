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


def normalize_strategy_block_reason(code: Optional[str]) -> str:
    """
    Map reject code to concrete strategy_block_reason (no generic strategy_ineligible).
    Returns gated_by_unknown when code is None or empty for diagnosis via router_debug.
    """
    if code is None or not str(code).strip() or ":" not in str(code):
        return "gated_by_unknown"
    _, detail = str(code).split(":", 1)
    detail_lower = detail.lower()
    if "regime" in detail_lower or "compression" in detail_lower or "event" in detail_lower:
        return "not_mapped_to_regime"
    if "stability" in detail_lower or "stability_block" in detail_lower or "confirm_soft" in detail_lower:
        return "gated_by_stability"
    if "reclaim" in detail_lower:
        return "gated_by_reclaim"
    if "confirm" in detail_lower:
        return "gated_by_confirm"
    if "vol" in detail_lower:
        return "gated_by_volume"
    if "trend" in detail_lower:
        return "gated_by_trend"
    if "cooldown" in detail_lower:
        return "gated_by_cooldown"
    if "pending" in detail_lower:
        return "gated_by_pending"
    if "dist50" in detail_lower or "pullback_bars" in detail_lower:
        return "gated_by_pullback_conditions"
    if "body" in detail_lower or "break" in detail_lower or "slope" in detail_lower or "k" in detail_lower:
        return "gated_by_conditions"
    if "spread" in detail_lower:
        return "gated_by_spread"
    return "gated_by_conditions"


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


def _clamp(value: Optional[float], low: float, high: float, default: float = 0.0) -> float:
    if value is None:
        return default
    return max(low, min(high, float(value)))


def _wick_ratio(open_val: Optional[float], close_val: Optional[float], high_val: Optional[float], low_val: Optional[float]) -> Optional[float]:
    if open_val is None or close_val is None or high_val is None or low_val is None:
        return None
    body = abs(close_val - open_val)
    upper = high_val - max(open_val, close_val)
    lower = min(open_val, close_val) - low_val
    denom = body if body > 0 else 1e-9
    return (upper + lower) / denom


def _body_ratio(open_val: Optional[float], close_val: Optional[float], high_val: Optional[float], low_val: Optional[float]) -> Optional[float]:
    if open_val is None or close_val is None or high_val is None or low_val is None:
        return None
    rng = abs(high_val - low_val)
    if rng <= 0:
        return None
    return abs(close_val - open_val) / rng


def _close_position(close_val: Optional[float], high_val: Optional[float], low_val: Optional[float]) -> Optional[float]:
    if close_val is None or high_val is None or low_val is None:
        return None
    rng = abs(high_val - low_val)
    if rng <= 0:
        return None
    return (close_val - low_val) / rng


def _compute_stability(
    *,
    direction: str,
    stability_n: Optional[float],
    trend_candles_below: Optional[float],
    trend_candles_above: Optional[float],
    wick_ratio_count: Optional[float],
    dist50: Optional[float],
) -> Tuple[float, bool, bool, bool, str, Dict[str, float]]:
    score = 0.0
    reason = ""
    metrics: Dict[str, float] = {}
    if stability_n is None or stability_n <= 0:
        return 0.0, False, False, True, "stability_n_missing", metrics
    if wick_ratio_count is None or wick_ratio_count < 0:
        return 0.0, False, False, True, "wick_ratio_missing", metrics
    if direction == "DOWN":
        trend_candles = trend_candles_below
    else:
        trend_candles = trend_candles_above
    if trend_candles is None or trend_candles < 0:
        return 0.0, False, False, True, "trend_candles_missing", metrics
    if dist50 is None:
        return 0.0, False, False, True, "dist50_missing", metrics
    r_val = _safe_div(trend_candles, stability_n, default=0.0) or 0.0
    w_val = _safe_div(wick_ratio_count, stability_n, default=0.0) or 0.0
    xmax = max(settings.get_float("XMAX"), 1e-9)
    x_val = _clamp(_safe_div(dist50, xmax, default=0.0), 0.0, 1.0, default=0.0)
    stability_weight_r = settings.get_float("STABILITY_WEIGHT_R", 0.55)
    stability_weight_w = settings.get_float("STABILITY_WEIGHT_W", 0.25)
    stability_weight_x = settings.get_float("STABILITY_WEIGHT_X", 0.20)
    score = stability_weight_r * r_val + stability_weight_w * (1 - w_val) + stability_weight_x * (1 - x_val)
    metrics = {"R": r_val, "W": w_val, "X": x_val}
    stable_hard = settings.get_tunable_float("STABILITY_HARD", "STABILITY_HARD_REAL")
    stable_soft = settings.get_tunable_float("STABILITY_SOFT", "STABILITY_SOFT_REAL")
    stable_ok = score >= stable_hard
    stable_soft_ok = stable_soft <= score < stable_hard
    stable_block = score < stable_soft
    return score, stable_ok, stable_soft_ok, stable_block, reason, metrics


def _continuation_confirmation(
    *,
    direction: str,
    close_ltf: Optional[float],
    open_ltf: Optional[float],
    high_ltf: Optional[float],
    low_ltf: Optional[float],
    close_prev: Optional[float],
    open_prev: Optional[float],
    high_prev: Optional[float],
    low_prev: Optional[float],
    atr14: Optional[float],
    ema50_ltf: Optional[float],
    bb_mid: Optional[float],
    volume_ratio: Optional[float],
    swing_high_m: Optional[float],
    swing_low_m: Optional[float],
    donchian_high_k: Optional[float],
    donchian_low_k: Optional[float],
) -> Tuple[str, Dict[str, float], bool]:
    metrics: Dict[str, float] = {}
    min_body = settings.get_float("CONFIRM_MIN_BODY_RATIO")
    min_body_retest = settings.get_float("CONFIRM_MIN_BODY_RATIO_RETEST")
    max_close_pos_short = settings.get_float("CONFIRM_MAX_CLOSE_POS_SHORT")
    max_close_pos_long = settings.get_float("CONFIRM_MAX_CLOSE_POS_LONG")
    retest_tol_atr = settings.get_float("CONFIRM_RETEST_TOL_ATR")
    break_delta_atr = settings.get_float("CONFIRM_BREAK_DELTA_ATR")

    def _volume_ok() -> bool:
        return volume_ratio is None or volume_ratio <= 0 or volume_ratio >= 1.0

    body_ratio_1 = _body_ratio(open_ltf, close_ltf, high_ltf, low_ltf)
    body_ratio_2 = _body_ratio(open_prev, close_prev, high_prev, low_prev)
    close_pos_1 = _close_position(close_ltf, high_ltf, low_ltf)
    close_pos_2 = _close_position(close_prev, high_prev, low_prev)
    if body_ratio_1 is not None and body_ratio_2 is not None and close_pos_1 is not None and close_pos_2 is not None:
        if direction == "DOWN":
            two_bar_ok = (
                body_ratio_1 >= min_body
                and body_ratio_2 >= min_body
                and close_pos_1 <= max_close_pos_short
                and close_pos_2 <= max_close_pos_short
                and _volume_ok()
            )
        else:
            two_bar_ok = (
                body_ratio_1 >= min_body
                and body_ratio_2 >= min_body
                and close_pos_1 >= (1 - max_close_pos_long)
                and close_pos_2 >= (1 - max_close_pos_long)
                and _volume_ok()
            )
        if two_bar_ok:
            metrics.update({
                "body_ratio_last": body_ratio_1,
                "body_ratio_prev": body_ratio_2,
                "close_pos_last": close_pos_1,
                "close_pos_prev": close_pos_2,
            })
            return "TWO_BAR_CONTINUATION", metrics, True

    level = ema50_ltf if ema50_ltf is not None and ema50_ltf > 0 else bb_mid
    body_ratio = body_ratio_1
    if level is not None and atr14 is not None and atr14 > 0 and body_ratio is not None:
        if direction == "DOWN":
            retest_ok = (
                high_ltf is not None
                and close_ltf is not None
                and high_ltf >= level
                and high_ltf <= level + retest_tol_atr * atr14
                and close_ltf < level
                and body_ratio >= min_body_retest
            )
        else:
            retest_ok = (
                low_ltf is not None
                and close_ltf is not None
                and low_ltf <= level
                and low_ltf >= level - retest_tol_atr * atr14
                and close_ltf > level
                and body_ratio >= min_body_retest
            )
        if retest_ok:
            metrics.update({
                "retest_level": level,
                "body_ratio_last": body_ratio,
            })
            return "RETEST_REJECT", metrics, True

    if atr14 is not None and atr14 > 0:
        if direction == "DOWN":
            lower_high_ok = (
                swing_high_m is not None
                and high_ltf is not None
                and high_ltf < swing_high_m
                and donchian_low_k is not None
                and close_ltf is not None
                and close_ltf < (donchian_low_k - break_delta_atr * atr14)
            )
        else:
            lower_high_ok = (
                swing_low_m is not None
                and low_ltf is not None
                and low_ltf > swing_low_m
                and donchian_high_k is not None
                and close_ltf is not None
                and close_ltf > (donchian_high_k + break_delta_atr * atr14)
            )
        if lower_high_ok:
            metrics.update({
                "swing_level": swing_high_m if direction == "DOWN" else swing_low_m,
                "donchian_level": donchian_low_k if direction == "DOWN" else donchian_high_k,
                "break_delta_atr": break_delta_atr,
            })
            return "LOWER_HIGH_BREAK", metrics, True

    return "NONE", metrics, False


def _anti_reversal_filter(
    *,
    entry_side: str,  # "LONG" or "SHORT" - the side we want to enter
    close_htf: Optional[float],
    ema_fast_htf: Optional[float],
    rsi_htf: Optional[float],
    rsi_htf_prev: Optional[float],
    wick_ratio_ltf: Optional[float],
    atr14_htf: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Check if entry should be blocked due to HTF reversal signals.
    
    Only blocks entries going AGAINST the HTF trend:
    - LONG entries blocked if HTF is reclaiming downward (close_htf < ema_fast_htf - buffer)
    - SHORT entries blocked if HTF is reclaiming upward (close_htf > ema_fast_htf + buffer)
    
    Does NOT block entries going WITH the HTF trend.
    When REAL_MARKET_TUNING=1, HTF_EMA_RECLAIM_ATR_BUFFER adds hysteresis.
    """
    buffer_abs = 0.0
    if settings.get_bool("REAL_MARKET_TUNING", False) and atr14_htf is not None and atr14_htf > 0:
        buffer_abs = settings.get_float("HTF_EMA_RECLAIM_ATR_BUFFER", 0.10) * atr14_htf
    if close_htf is None or close_htf <= 0 or ema_fast_htf is None or ema_fast_htf <= 0:
        ema_block = None
    else:
        # Block LONG if HTF is reclaiming downward (close below fast EMA - buffer)
        # Block SHORT if HTF is reclaiming upward (close above fast EMA + buffer)
        if entry_side == "LONG":
            ema_block = close_htf < ema_fast_htf - buffer_abs
        else:  # entry_side == "SHORT"
            ema_block = close_htf > ema_fast_htf + buffer_abs

    rsi_slope_min = settings.get_float("HTF_RSI_SLOPE_MIN")
    wick_th = settings.get_float("ANTI_REV_WICK_TH")
    rsi_block = None
    if rsi_htf is not None and rsi_htf_prev is not None and wick_ratio_ltf is not None:
        if entry_side == "LONG":
            # Block LONG if RSI is falling (reversal downward) with high wick
            rsi_block = (rsi_htf_prev - rsi_htf) >= rsi_slope_min and wick_ratio_ltf >= wick_th
        else:  # entry_side == "SHORT"
            # Block SHORT if RSI is rising (reversal upward) with high wick
            rsi_block = (rsi_htf - rsi_htf_prev) >= rsi_slope_min and wick_ratio_ltf >= wick_th

    if ema_block is True:
        return True, "HTF_EMA_RECLAIM"
    if rsi_block is True:
        return True, "HTF_RSI_SLOPE"
    if ema_block is None and rsi_block is None:
        return False, "UNAVAILABLE"
    return False, ""


def _update_event_cooldown(
    *,
    decision_ts: Optional[int],
    event_detected: bool,
    event_cooldown: Optional[Dict[str, Any]],
    cooldown_candles: int,
) -> Tuple[Dict[str, Any], int]:
    state = dict(event_cooldown or {})
    remaining = int(state.get("remaining", 0) or 0)
    last_ts = state.get("last_ts")
    if decision_ts is None:
        return {"remaining": remaining, "last_ts": last_ts}, remaining
    if last_ts == decision_ts:
        if event_detected and remaining < cooldown_candles:
            remaining = cooldown_candles
        return {"remaining": remaining, "last_ts": last_ts}, remaining
    if event_detected:
        remaining = cooldown_candles
    elif remaining > 0:
        remaining = max(0, remaining - 1)
    state["remaining"] = remaining
    state["last_ts"] = int(decision_ts)
    return state, remaining


def _update_pending_state(
    *,
    decision_ts: Optional[int],
    pending_state: Optional[Dict[str, Any]],
    confirm_candles: int,
    expire_candles: int,
) -> Tuple[Optional[Dict[str, Any]], str]:
    if pending_state is None:
        return None, "NONE"
    state = dict(pending_state)
    default_remaining = max(expire_candles, 1) + 1
    remaining = int(state.get("remaining", default_remaining) or default_remaining)
    last_ts = state.get("last_ts")
    if decision_ts is None:
        return state, "SET"
    if last_ts == decision_ts:
        return state, "SET"
    remaining = max(0, remaining - 1)
    if remaining <= 0:
        return None, "EXPIRED"
    state["remaining"] = remaining
    state["last_ts"] = int(decision_ts)
    return state, "SET"


def compute_regime_5m(context: Dict[str, Any]) -> str:
    close_ltf = context.get("close_ltf")
    ema50_ltf = context.get("ema50_ltf")
    atr14 = context.get("atr14")
    atr_ratio = context.get("atr_ratio")
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
    bb_width_atr = context.get("bb_width_atr")
    bb_width_prev = context.get("bb_width_prev")
    trend_accel_vol_mult = context.get("trend_accel_vol_mult")
    squeeze_break_long = context.get("squeeze_break_long")
    squeeze_break_short = context.get("squeeze_break_short")
    event_detected = bool(context.get("event_detected"))

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

    if event_detected:
        return "EVENT"
    if (squeeze_break_long or squeeze_break_short):
        return "SQUEEZE_BREAK"
    if (brk_up or brk_dn) and volume_ratio is not None and volume_ratio >= breakout_vol_min:
        return "BREAKOUT_EXPANSION"
    if (
        dc_width_atr is not None
        and volume_ratio is not None
        and dc_width_atr <= compression_width_atr_max
        and volume_ratio <= compression_vol_max
    ):
        return "COMPRESSION"
    vol_expansion = False
    if atr_ratio is not None and trend_accel_vol_mult is not None:
        vol_expansion = atr_ratio >= trend_accel_vol_mult
    if bb_width_atr is not None and bb_width_prev is not None and bb_width_prev > 0:
        bb_width_expansion_mult = settings.get_float("BB_WIDTH_EXPANSION_MULT", 1.2)
        vol_expansion = vol_expansion or (bb_width_atr >= bb_width_prev * bb_width_expansion_mult)
    if trend_bias and vol_expansion and dist50 is not None and dist50 <= trend_dist50_max:
        return "TREND_ACCEL"
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
    if regime == "SQUEEZE_BREAK":
        return "SQUEEZE_BREAK" if (context.get("squeeze_break_long_ok") or context.get("squeeze_break_short_ok")) else "NONE"
    if regime == "TREND_ACCEL":
        return "TREND_ACCEL" if (context.get("trend_accel_long_ok") or context.get("trend_accel_short_ok")) else "NONE"
    if regime == "TREND_CONTINUATION":
        return "CONTINUATION" if (context.get("cont_long_ok") or context.get("cont_short_ok")) else "NONE"
    if regime == "PULLBACK":
        return "PULLBACK_REENTRY" if (context.get("pullback_reentry_long_ok") or context.get("pullback_reentry_short_ok")) else "NONE"
    if regime == "RANGE":
        return "RANGE_MEANREV" if (context.get("range_meanrev_long_ok") or context.get("range_meanrev_short_ok")) else "NONE"
    if regime == "EVENT":
        return "NONE"
    return "NONE"


def make_decision(
    payload: Dict[str, Any],
    daily_state: Optional[Dict[str, Any]] = None,
    decision_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
    accel_rejects: List[str] = []
    squeeze_rejects: List[str] = []

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
    open_ltf = _to_float(features_ltf.get("open"))
    open_prev = _to_float(features_ltf.get("open_prev"))
    high_ltf = _to_float(features_ltf.get("high"))
    low_ltf = _to_float(features_ltf.get("low"))
    high_prev = _to_float(features_ltf.get("high_prev"))
    low_prev = _to_float(features_ltf.get("low_prev"))
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
    bb_width = _to_float(features_ltf.get("bb_width"))
    bb_width_prev = _to_float(features_ltf.get("bb_width_prev"))
    volume_ratio = _to_float(features_ltf.get("volume_ratio"))
    volume_ltf = _to_float(features_ltf.get("volume"))
    volume_prev = _to_float(features_ltf.get("volume_prev"))
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
    stability_n = _to_float(features_ltf.get("stability_n"))
    trend_candles_below_ema50 = _to_float(features_ltf.get("trend_candles_below_ema50"))
    trend_candles_above_ema50 = _to_float(features_ltf.get("trend_candles_above_ema50"))
    wick_ratio_count = _to_float(features_ltf.get("wick_ratio_count"))
    swing_high_m = _to_float(features_ltf.get("swing_high_m"))
    swing_low_m = _to_float(features_ltf.get("swing_low_m"))
    donchian_high_k = _to_float(features_ltf.get("donchian_high_k"))
    donchian_low_k = _to_float(features_ltf.get("donchian_low_k"))
    close_htf = _to_float(context_htf.get("close"))
    ema200_htf = _to_float(context_htf.get("ema200"))
    ema200_prev_n = _to_float(context_htf.get("ema200_prev_n"))
    ema200_slope_norm = _to_float(context_htf.get("ema200_slope_norm"))
    consec_above_ema200 = _to_float(context_htf.get("consec_above_ema200"))
    consec_below_ema200 = _to_float(context_htf.get("consec_below_ema200"))
    consec_higher_close = _to_float(context_htf.get("consec_higher_close"))
    consec_lower_close = _to_float(context_htf.get("consec_lower_close"))
    atr14_htf = _to_float(context_htf.get("atr14"))
    ema_fast_htf = _to_float(context_htf.get("ema_fast"))
    rsi14_htf = _to_float(context_htf.get("rsi14"))
    rsi14_htf_prev = _to_float(context_htf.get("rsi14_prev"))
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

    decision_state = dict(decision_state or {})
    pending_state = decision_state.get("pending_entry")
    event_state = decision_state.get("event_cooldown") or {}

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
    trend_strength_min = settings.get_float("TREND_STRENGTH_MIN", 0.6)
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

    atr_ratio_valid = atr14 is not None and atr14_sma20 is not None and atr14_sma20 > 0
    atr_ratio = 0.0
    if atr_ratio_valid:
        atr_ratio = atr14 / atr14_sma20
    volatility_expansion_threshold = settings.get_float("VOLATILITY_EXPANSION_THRESHOLD", 1.3)
    volatility_state = "VOL_EXPANSION" if atr_ratio >= volatility_expansion_threshold else "NORMAL"

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
    bb_width_atr = (
        _safe_div(bb_width, atr14, default=None)
        if bb_width is not None and atr14 is not None and atr14 > 0
        else None
    )
    wick_ratio_ltf = _wick_ratio(open_ltf, close_ltf, high_ltf, low_ltf)
    stability_score, stable_ok, stable_soft, stable_block, stable_block_reason, stability_metrics = _compute_stability(
        direction=direction,
        stability_n=stability_n,
        trend_candles_below=trend_candles_below_ema50,
        trend_candles_above=trend_candles_above_ema50,
        wick_ratio_count=wick_ratio_count,
        dist50=dist50,
    )
    confirmation_type, confirmation_metrics, confirmation_ok = _continuation_confirmation(
        direction=direction,
        close_ltf=close_ltf,
        open_ltf=open_ltf,
        high_ltf=high_ltf,
        low_ltf=low_ltf,
        close_prev=close_prev,
        open_prev=open_prev,
        high_prev=high_prev,
        low_prev=low_prev,
        atr14=atr14,
        ema50_ltf=ema50_ltf,
        bb_mid=bb_mid,
        volume_ratio=volume_ratio,
        swing_high_m=swing_high_m,
        swing_low_m=swing_low_m,
        donchian_high_k=donchian_high_k,
        donchian_low_k=donchian_low_k,
    )
    # Anti-reversal is now strategy-specific (checked per entry side)
    # We'll check it separately for LONG and SHORT when evaluating each strategy
    # Store HTF context for per-strategy checks
    anti_reversal_htf_context = {
        "close_htf": close_htf,
        "ema_fast_htf": ema_fast_htf,
        "rsi_htf": rsi14_htf,
        "rsi_htf_prev": rsi14_htf_prev,
        "wick_ratio_ltf": wick_ratio_ltf,
        "atr14_htf": atr14_htf,
    }
    # Legacy global check for backward compatibility (used in explain fields)
    # This checks if we're in a reversal state (regardless of entry direction)
    anti_reversal_block_global = False
    anti_reversal_reason_global = ""
    if close_htf is not None and close_htf > 0 and ema_fast_htf is not None and ema_fast_htf > 0:
        # Check if HTF is in reversal state (for explainability)
        if direction == "DOWN" and close_htf > ema_fast_htf:
            anti_reversal_block_global = True
            anti_reversal_reason_global = "HTF_EMA_RECLAIM"
        elif direction == "UP" and close_htf < ema_fast_htf:
            anti_reversal_block_global = True
            anti_reversal_reason_global = "HTF_EMA_RECLAIM"
    anti_reversal_block = anti_reversal_block_global
    anti_reversal_reason = anti_reversal_reason_global
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

    true_range = None
    if high_ltf is not None and low_ltf is not None:
        if close_prev is not None:
            true_range = max(
                abs(high_ltf - low_ltf),
                abs(high_ltf - close_prev),
                abs(low_ltf - close_prev),
            )
        else:
            true_range = abs(high_ltf - low_ltf)
    event_tr_atr = settings.get_float("EVENT_TR_ATR")
    event_detected = bool(
        true_range is not None
        and atr14 is not None
        and atr14 > 0
        and (true_range / atr14) >= event_tr_atr
    )
    event_cooldown_candles = max(settings.get_int("EVENT_COOLDOWN_CANDLES"), 0)
    event_state, event_cooldown_remaining = _update_event_cooldown(
        decision_ts=decision_ts,
        event_detected=event_detected,
        event_cooldown=event_state,
        cooldown_candles=event_cooldown_candles,
    )
    event_block = event_detected or event_cooldown_remaining > 0

    pending_state, pending_entry_status = _update_pending_state(
        decision_ts=decision_ts,
        pending_state=pending_state,
        confirm_candles=max(settings.get_int("PENDING_CONFIRM_CANDLES"), 1),
        expire_candles=max(settings.get_int("PENDING_EXPIRE_CANDLES"), 1),
    )
    if event_block and pending_state is not None:
        pending_state = None
        pending_entry_status = "EXPIRED"

    extreme_rsi_short_max = settings.get_float("EXTREME_RSI_SHORT_MAX", 18.0)
    extreme_rsi_long_min = settings.get_float("EXTREME_RSI_LONG_MIN", 82.0)
    extreme_short = (
        regime == "TREND"
        and direction == "DOWN"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 <= extreme_rsi_short_max
        and reentry_long
    )
    extreme_long = (
        regime == "TREND"
        and direction == "UP"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 >= extreme_rsi_long_min
        and reentry_short
    )

    pullback_atr_max = settings.get_float("PULLBACK_ATR_MAX", 1.0)
    trend_rsi_long_max = settings.get_float("TREND_RSI_LONG_MAX", 45.0)
    trend_rsi_short_min = settings.get_float("TREND_RSI_SHORT_MIN", 55.0)
    trend_long_ok = (
        regime == "TREND"
        and direction == "UP"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 <= trend_rsi_long_max
        and pullback_atr_long <= pullback_atr_max
        and reclaim_long
    )
    trend_short_ok = (
        regime == "TREND"
        and direction == "DOWN"
        and rsi14 is not None
        and rsi14 > 0
        and rsi14 >= trend_rsi_short_min
        and pullback_atr_short <= pullback_atr_max
        and reclaim_short
    )
    if regime != "TREND":
        trend_rejects.append("T:regime")
    if direction == "UP":
        if rsi14 is not None and rsi14 > trend_rsi_long_max:
            trend_rejects.append("T:rsi")
        if pullback_atr_long > pullback_atr_max:
            trend_rejects.append("T:pullback")
        if not reclaim_long:
            trend_rejects.append("T:reclaim")
    else:
        if rsi14 is not None and rsi14 < trend_rsi_short_min:
            trend_rejects.append("T:rsi")
        if pullback_atr_short > pullback_atr_max:
            trend_rejects.append("T:pullback")
        if not reclaim_short:
            trend_rejects.append("T:reclaim")
    if spread_pct is not None and spread_pct > spread_max_pct:
        trend_rejects.append("T:spread")

    d_atr = settings.get_float("CONT_BREAK_D_ATR", 0.15)
    cont_slope_atr_max = settings.get_float("CONT_SLOPE_ATR_MAX", -0.15)
    cont_slope_atr_min = settings.get_float("CONT_SLOPE_ATR_MIN", 0.15)
    cont_k_max = settings.get_float("CONT_K_MAX", 2.2)
    cont_rsi_min_short = settings.get_float("CONT_RSI_MIN_SHORT", 30.0)
    cont_rsi_max_long = settings.get_float("CONT_RSI_MAX_LONG", 70.0)
    cont_body_min = settings.get_float("CONT_BODY_MIN", 0.50)
    cont_vol_min = settings.get_tunable_float("CONT_VOL_MIN", "CONT_VOL_MIN_REAL", 1.0)
    cont_atr_ratio_min = settings.get_float("CONT_ATR_RATIO_MIN", 0.95)
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
    pullback_reentry_dist50_max = settings.get_tunable_float("PULLBACK_REENTRY_DIST50_MAX", "PULLBACK_REENTRY_DIST50_MAX_REAL")
    pullback_reentry_min_bars = settings.get_int("PULLBACK_REENTRY_MIN_BARS")
    pullback_reentry_confirm_body_min = settings.get_float("PULLBACK_REENTRY_CONFIRM_BODY_MIN")
    pullback_reentry_reclaim_vol_min = settings.get_float("PULLBACK_REENTRY_RECLAIM_VOL_MIN")
    pullback_reentry_sl_atr = settings.get_float("PULLBACK_REENTRY_SL_ATR")
    pullback_reentry_rr_target = settings.get_float("PULLBACK_REENTRY_RR_TARGET")
    pullback_reentry_vol_min = settings.get_tunable_float("PULLBACK_REENTRY_VOL_MIN", "PULLBACK_REENTRY_VOL_MIN_REAL")
    reclaim_tol_atr = settings.get_float("PULLBACK_RECLAIM_TOL_ATR", 0.10) if settings.get_bool("REAL_MARKET_TUNING", False) else 0.0
    reclaim_tol_abs = settings.get_float("PULLBACK_RECLAIM_TOL_ABS", 0.0)
    effective_tolerance = (
        max(reclaim_tol_abs, reclaim_tol_atr * atr14) if (atr14 is not None and atr14 > 0) else reclaim_tol_abs
    )
    pullback_reclaim_tol_abs = effective_tolerance  # used in pullback long/short conditions
    reclaim_level_used = ema50_ltf
    distance_to_reclaim = (
        _safe_div(abs(close_ltf - ema50_ltf), atr14, default=None)
        if close_ltf is not None and ema50_ltf is not None and atr14 is not None and atr14 > 0
        else None
    )
    range_meanrev_edge_atr = settings.get_float("RANGE_MEANREV_EDGE_ATR")
    range_meanrev_vol_max = settings.get_float("RANGE_MEANREV_VOL_MAX")
    range_meanrev_sl_atr = settings.get_float("RANGE_MEANREV_SL_ATR")
    range_meanrev_rr_target = settings.get_float("RANGE_MEANREV_RR_TARGET")
    trend_accel_vol_mult = settings.get_float("TREND_ACCEL_VOL_MULT")
    squeeze_bb_width_th = settings.get_float("SQUEEZE_BB_WIDTH_TH")

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

    def _apply_trend_stability_gate(base_ok: bool, reject_bucket: List[str], prefix: str, entry_side: Optional[str] = None) -> bool:
        """
        Apply trend stability gate with strategy-specific anti-reversal check.
        
        Args:
            base_ok: Base eligibility (before stability/anti-reversal checks)
            reject_bucket: List to append rejection codes
            prefix: Strategy prefix (C, P, etc.)
            entry_side: "LONG" or "SHORT" - used for strategy-specific anti-reversal check
        """
        if not base_ok:
            return False
        if stable_block:
            reject_bucket.append(f"{prefix}:stability_block")
            return False
        if stable_soft:
            if not confirmation_ok:
                reject_bucket.append(f"{prefix}:confirm_soft")
                return False
            # Check anti-reversal only for the specific entry side
            if entry_side:
                anti_rev_block, anti_rev_reason = _anti_reversal_filter(
                    entry_side=entry_side,
                    **anti_reversal_htf_context,
                )
                if anti_rev_block:
                    reject_bucket.append(f"{prefix}:anti_reversal")
                    return False
            elif anti_reversal_block:
                # Fallback to global check if entry_side not provided
                reject_bucket.append(f"{prefix}:anti_reversal")
                return False
        return True

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

    cont_short_ok = _apply_trend_stability_gate(cont_short_ok, cont_short_rejects, "C", entry_side="SHORT")
    cont_long_ok = _apply_trend_stability_gate(cont_long_ok, cont_long_rejects, "C", entry_side="LONG")

    cont_rejects = cont_short_rejects if direction == "DOWN" else cont_long_rejects

    squeeze_bias = None
    if htf_trend in ("up", "down"):
        squeeze_bias = htf_trend
    elif close_ltf is not None and ema120_ltf is not None and ema120_ltf > 0:
        if close_ltf > ema120_ltf:
            squeeze_bias = "up"
        elif close_ltf < ema120_ltf:
            squeeze_bias = "down"
        else:
            squeeze_bias = "range"
    squeeze_compression = (
        bb_width_atr is not None
        and squeeze_bb_width_th is not None
        and bb_width_atr <= squeeze_bb_width_th
        and atr_ratio_valid
        and atr_ratio < 1.0
    )
    squeeze_level_long = donchian_high_20 if donchian_high_20 is not None and donchian_high_20 > 0 else bb_upper
    squeeze_level_short = donchian_low_20 if donchian_low_20 is not None and donchian_low_20 > 0 else bb_lower
    squeeze_break_long = bool(
        squeeze_compression
        and squeeze_level_long
        and close_prev is not None
        and close_ltf is not None
        and close_prev > squeeze_level_long
        and close_ltf > squeeze_level_long
        and candle_body_ratio is not None
        and candle_body_ratio >= settings.get_float("CONFIRM_MIN_BODY_RATIO")
        and (volume_ratio is None or volume_ratio <= 0 or volume_ratio >= regime_breakout_vol_min)
        and squeeze_bias == "up"
    )
    squeeze_break_short = bool(
        squeeze_compression
        and squeeze_level_short
        and close_prev is not None
        and close_ltf is not None
        and close_prev < squeeze_level_short
        and close_ltf < squeeze_level_short
        and candle_body_ratio is not None
        and candle_body_ratio >= settings.get_float("CONFIRM_MIN_BODY_RATIO")
        and (volume_ratio is None or volume_ratio <= 0 or volume_ratio >= regime_breakout_vol_min)
        and squeeze_bias == "down"
    )

    regime_detected = compute_regime_5m({
        "close_ltf": close_ltf,
        "ema50_ltf": ema50_ltf,
        "atr14": atr14,
        "atr_ratio": atr_ratio if atr_ratio_valid else None,
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
        "bb_width_atr": bb_width_atr,
        "bb_width_prev": bb_width_prev,
        "squeeze_bb_width_th": squeeze_bb_width_th,
        "trend_accel_vol_mult": trend_accel_vol_mult,
        "squeeze_break_long": squeeze_break_long,
        "squeeze_break_short": squeeze_break_short,
        "event_detected": event_detected,
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
    # Check anti-reversal for breakout strategies
    breakout_long_anti_rev_block, breakout_long_anti_rev_reason = _anti_reversal_filter(
        entry_side="LONG",
        **anti_reversal_htf_context,
    ) if impulse_long and (accept_long or retest_long) else (False, "")
    breakout_short_anti_rev_block, breakout_short_anti_rev_reason = _anti_reversal_filter(
        entry_side="SHORT",
        **anti_reversal_htf_context,
    ) if impulse_short and (accept_short or retest_short) else (False, "")
    
    breakout_expansion_long_ok = (
        impulse_long 
        and (accept_long or retest_long) 
        and trend_stable_long
        and not breakout_long_anti_rev_block
    )
    breakout_expansion_short_ok = (
        impulse_short 
        and (accept_short or retest_short) 
        and trend_stable_short
        and not breakout_short_anti_rev_block
    )

    trend_accel_long_ok = bool(cont_long_ok and atr_ratio_valid and atr_ratio >= trend_accel_vol_mult)
    trend_accel_short_ok = bool(cont_short_ok and atr_ratio_valid and atr_ratio >= trend_accel_vol_mult)
    if not cont_long_ok and not cont_short_ok:
        accel_rejects.append("A:cont")
    if not atr_ratio_valid or atr_ratio < trend_accel_vol_mult:
        accel_rejects.append("A:vol")
    squeeze_break_long_ok = squeeze_break_long
    squeeze_break_short_ok = squeeze_break_short
    if not squeeze_compression:
        squeeze_rejects.append("S:compression")
    if not (squeeze_break_long or squeeze_break_short):
        squeeze_rejects.append("S:breakout")
    if squeeze_bias not in ("up", "down"):
        squeeze_rejects.append("S:bias")
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
        and close_ltf >= ema50_ltf - pullback_reclaim_tol_abs
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
        and close_ltf <= ema50_ltf + pullback_reclaim_tol_abs
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
    pullback_reentry_long_ok = _apply_trend_stability_gate(pullback_reentry_long_ok, pullback_rejects, "P", entry_side="LONG")
    pullback_reentry_short_ok = _apply_trend_stability_gate(pullback_reentry_short_ok, pullback_rejects, "P", entry_side="SHORT")

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
    range_rsi_long_max = settings.get_float("RANGE_RSI_LONG_MAX", 35.0)
    range_in_trend_enabled = settings.get_bool("RANGE_IN_TREND_ENABLED", False)
    volatility_contraction = atr_ratio_valid and atr_ratio is not None and atr_ratio < 1.0
    range_in_trend_long_ok = (
        range_in_trend_enabled
        and htf_trend == "up"
        and rsi14 is not None
        and rsi14 <= range_rsi_long_max
        and not (breakout_long or breakout_short)
        and close_ltf is not None
        and donchian_low_20 is not None
        and atr14 is not None
        and atr14 > 0
        and close_ltf <= donchian_low_20 + range_meanrev_edge_atr * atr14
        and volume_ratio is not None
        and volume_ratio < range_meanrev_vol_max
        and volatility_contraction
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
    range_in_trend_rejects: List[str] = []
    if not range_in_trend_long_ok and range_in_trend_enabled:
        if htf_trend != "up":
            range_in_trend_rejects.append("R:trend")
        elif rsi14 is None or rsi14 > range_rsi_long_max:
            range_in_trend_rejects.append("R:rsi")
        elif not volatility_contraction:
            range_in_trend_rejects.append("R:volatility")
        else:
            range_in_trend_rejects.append("R:conditions")
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
        if close_ltf is None or ema50_ltf is None or close_ltf < ema50_ltf - pullback_reclaim_tol_abs:
            pullback_rejects.append("P:reclaim")
    if htf_trend == "down":
        if close_ltf is None or ema50_ltf is None or close_ltf > ema50_ltf + pullback_reclaim_tol_abs:
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
    tp_targets = []
    move_sl_to_be_after_tp1 = False
    rule = None
    selected_strategy = "NONE"
    eligible_strategies: List[str] = []
    strategy_block_reason = None
    pending_entry_status = "NONE"
    
    # Extract HTF context for gating
    htf_atr14_percentile = _to_float(context_htf.get("atr14_percentile"))
    session_bucket = context_htf.get("session_bucket", "Unknown")

    def _apply_risk(intent_side: str, sl_mult: float, rr_target: float, regime_exit_behavior: str = "fixed") -> None:
        nonlocal intent, entry, sl, tp, rr, tp_targets, move_sl_to_be_after_tp1
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
            
            # Create tp_targets for multiple TPs based on regime exit behavior
            tp1_fraction = settings.get_float("TP1_FRACTION", 0.4)
            if regime_exit_behavior in ("trailing", "partial_fixed") and rr_target >= 2.0:
                # Split into TP1 and TP2
                tp1_rr_fraction = settings.get_float("TP1_RR_FRACTION", 0.6)
                tp1_rr = rr_target * tp1_rr_fraction  # TP1 at configurable fraction of target RR
                tp2_rr = rr_target  # TP2 at full target RR
                if intent_side == "LONG":
                    tp1_price = entry + tp1_rr * risk
                    tp2_price = entry + tp2_rr * risk
                else:
                    tp1_price = entry - tp1_rr * risk
                    tp2_price = entry - tp2_rr * risk
                
                tp_targets = [
                    {"price": tp1_price, "rr": tp1_rr, "qty_frac": tp1_fraction},
                    {"price": tp2_price, "rr": tp2_rr, "qty_frac": 1.0 - tp1_fraction}
                ]
                move_sl_to_be_after_tp1 = (regime_exit_behavior == "trailing")
            else:
                # Single TP (backward compatible)
                tp_targets = []
            
            if rr_target < base_min_rr:
                reject_reasons.append(f"{intent_side[0]}:rr")
                intent = "HOLD"
                entry = None
                sl = None
                tp = None
                rr = None
                tp_targets = []
                move_sl_to_be_after_tp1 = False
        else:
            reject_reasons.append(f"{intent_side[0]}:rr")
            intent = "HOLD"
            entry = None
            sl = None
            tp = None
            rr = None
            tp_targets = []
            move_sl_to_be_after_tp1 = False

    routing_regime = regime_detected
    runtime = get_runtime_settings()
    allow_override = runtime.is_test or runtime.is_replay or runtime.is_offline or runtime.env != "production"
    override_regime = str(settings.get_str("ROUTING_REGIME_OVERRIDE", "") or "").strip().upper()
    if override_regime and allow_override:
        override_map = {
            "BREAKOUT_EXPANSION": "BREAKOUT_EXPANSION",
            "SQUEEZE_BREAK": "SQUEEZE_BREAK",
            "TREND_CONTINUATION": "TREND_CONTINUATION",
            "TREND_ACCEL": "TREND_ACCEL",
            "PULLBACK": "PULLBACK",
            "RANGE": "RANGE",
            "COMPRESSION": "COMPRESSION",
            "EVENT": "EVENT",
        }
        if override_regime in override_map:
            routing_regime = override_map[override_regime]

    if time_exit_signal:
        eligible_strategies = ["TIME_EXIT"]
        selected_strategy = "TIME_EXIT"
        intent = "CLOSE"
        rule = selected_strategy
        router_debug = {
            "regime_detected": routing_regime,
            "strategies_for_regime": ["TIME_EXIT"],
            "enabled_strategies": ["TIME_EXIT"],
            "rejected_strategies": {},
        }
    else:
        strategy_by_regime = {
            "BREAKOUT_EXPANSION": "BREAKOUT_EXPANSION",
            "SQUEEZE_BREAK": "SQUEEZE_BREAK",
            "TREND_CONTINUATION": "CONTINUATION",
            "TREND_ACCEL": "TREND_ACCEL",
            "PULLBACK": "PULLBACK_REENTRY",
            "RANGE": "RANGE_MEANREV",
            "COMPRESSION": None,
            "EVENT": None,
        }
        # Check ALL strategies for eligibility, not just the one mapped to regime
        strategy_ok = {
            "BREAKOUT_EXPANSION": breakout_expansion_long_ok or breakout_expansion_short_ok,
            "SQUEEZE_BREAK": squeeze_break_long_ok or squeeze_break_short_ok,
            "CONTINUATION": cont_long_ok or cont_short_ok,
            "TREND_ACCEL": trend_accel_long_ok or trend_accel_short_ok,
            "PULLBACK_REENTRY": pullback_reentry_long_ok or pullback_reentry_short_ok,
            "RANGE_MEANREV": range_meanrev_long_ok or range_meanrev_short_ok,
        }
        if range_in_trend_enabled:
            strategy_ok["RANGE_IN_TREND_LONG"] = range_in_trend_long_ok
        
        # Populate eligible_strategies with ALL strategies that pass
        eligible_strategies = [name for name, ok in strategy_ok.items() if ok]
        
        # Strategy priority order (higher priority first)
        # When multiple strategies are eligible, prefer the one mapped to current regime
        # If regime strategy not eligible, select highest priority eligible strategy
        strategy_priority = [
            "BREAKOUT_EXPANSION",  # Highest priority: strong momentum
            "SQUEEZE_BREAK",       # High priority: compression breakouts
            "TREND_ACCEL",         # High priority: trend acceleration
            "CONTINUATION",        # Medium-high: trend continuation
            "PULLBACK_REENTRY",    # Medium: pullback entries
            "RANGE_MEANREV",       # Lower priority: mean reversion
            "RANGE_IN_TREND_LONG", # Optional: oversold long in trend (when enabled)
        ]
        if not range_in_trend_enabled:
            strategy_priority = [s for s in strategy_priority if s != "RANGE_IN_TREND_LONG"]
        
        routed_strategy = strategy_by_regime.get(routing_regime)
        # Map each strategy to its reject bucket for router_debug and concrete block reasons
        strategy_rejects_map = {
            "BREAKOUT_EXPANSION": breakout_rejects,
            "SQUEEZE_BREAK": squeeze_rejects,
            "CONTINUATION": cont_rejects,
            "TREND_ACCEL": accel_rejects,
            "PULLBACK_REENTRY": pullback_rejects,
            "RANGE_MEANREV": meanrev_rejects,
        }
        if range_in_trend_enabled:
            strategy_rejects_map["RANGE_IN_TREND_LONG"] = range_in_trend_rejects

        # Determine selected_strategy and block_reason (concrete reasons only, no generic strategy_ineligible)
        if routing_regime == "COMPRESSION":
            strategy_block_reason = "not_mapped_to_regime"
            selected_strategy = "NONE"
        elif routing_regime == "EVENT":
            strategy_block_reason = "not_mapped_to_regime"
            selected_strategy = "NONE"
        elif routed_strategy is None:
            strategy_block_reason = "not_mapped_to_regime"
            selected_strategy = "NONE"
        elif len(eligible_strategies) == 0:
            # No strategies eligible: use first reject of routed strategy as concrete reason
            routed_rejects = strategy_rejects_map.get(routed_strategy) or []
            first_code = routed_rejects[0] if routed_rejects else None
            strategy_block_reason = normalize_strategy_block_reason(first_code)
            selected_strategy = "NONE"
        elif routed_strategy in eligible_strategies:
            # Prefer the strategy mapped to current regime if it's eligible
            selected_strategy = routed_strategy
            strategy_block_reason = None
        else:
            # Regime strategy not eligible, but other strategies might be
            # Select highest priority eligible strategy
            for candidate in strategy_priority:
                if candidate in eligible_strategies:
                    selected_strategy = candidate
                    strategy_block_reason = None
                    break
            else:
                # Fallback: should not happen if eligible_strategies is non-empty
                selected_strategy = eligible_strategies[0] if eligible_strategies else "NONE"
                if not eligible_strategies:
                    routed_rejects = strategy_rejects_map.get(routed_strategy) or []
                    first_code = routed_rejects[0] if routed_rejects else None
                    strategy_block_reason = normalize_strategy_block_reason(first_code)
                else:
                    strategy_block_reason = None

        # Router visibility: expose regime, mapped strategies, enabled, and per-strategy reject reasons
        strategies_for_regime = [routed_strategy] if routed_strategy else []
        rejected_strategies = {}
        for name in strategy_rejects_map:
            if name in eligible_strategies:
                continue
            rejects = strategy_rejects_map.get(name) or []
            first_rej = rejects[0] if rejects else None
            rejected_strategies[name] = (
                str(first_rej) if first_rej is not None and str(first_rej).strip() else "unknown"
            )
        router_debug = {
            "regime_detected": routing_regime,
            "strategies_for_regime": strategies_for_regime,
            "enabled_strategies": list(eligible_strategies),
            "rejected_strategies": rejected_strategies,
        }

        # Map regimes to exit behavior: trailing vs fixed
        # BREAKOUT_EXPANSION / TREND_ACCEL: trailing SL for remainder after TP1
        # TREND_CONTINUATION: fixed TP1/TP2
        # RANGE: fixed TP only (no trailing)
        regime_exit_map = {
            "BREAKOUT_EXPANSION": "trailing",
            "TREND_ACCEL": "trailing",
            "CONTINUATION": "partial_fixed",
            "PULLBACK_REENTRY": "partial_fixed",
            "RANGE_MEANREV": "fixed",
            "SQUEEZE_BREAK": "trailing",
            "RANGE_IN_TREND_LONG": "fixed",
        }
        exit_behavior = regime_exit_map.get(selected_strategy, "fixed")
        
        # Apply gating rules: volatility percentile and session bucket
        volatility_percentile_min = settings.get_float("VOLATILITY_PERCENTILE_MIN", 20.0)
        if htf_atr14_percentile is not None and htf_atr14_percentile < volatility_percentile_min:
            # Block continuation/accel if volatility too low
            if selected_strategy in ("CONTINUATION", "TREND_ACCEL"):
                reject_reasons.append(f"volatility_too_low: {htf_atr14_percentile:.1f}% < {volatility_percentile_min:.1f}%")
                selected_strategy = "NONE"
                strategy_block_reason = "volatility_gate"
        
        # Block breakout expansion in Asia session
        if session_bucket == "Asia" and selected_strategy == "BREAKOUT_EXPANSION":
            reject_reasons.append("session_block:Asia_blocks_breakout")
            selected_strategy = "NONE"
            strategy_block_reason = "session_gate"
        
        if selected_strategy == "BREAKOUT_EXPANSION":
            intent = "LONG" if breakout_expansion_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, breakout_sl_atr, breakout_rr_target, exit_behavior)
        elif selected_strategy == "CONTINUATION":
            intent = "LONG" if cont_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, continuation_sl_atr, continuation_rr_target, exit_behavior)
        elif selected_strategy == "TREND_ACCEL":
            intent = "LONG" if trend_accel_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, continuation_sl_atr, continuation_rr_target, exit_behavior)
        elif selected_strategy == "PULLBACK_REENTRY":
            intent = "LONG" if pullback_reentry_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, pullback_reentry_sl_atr, pullback_reentry_rr_target, exit_behavior)
        elif selected_strategy == "RANGE_MEANREV":
            intent = "LONG" if range_meanrev_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, range_meanrev_sl_atr, range_meanrev_rr_target, exit_behavior)
        elif selected_strategy == "SQUEEZE_BREAK":
            intent = "LONG" if squeeze_break_long_ok else "SHORT"
            rule = selected_strategy
            _apply_risk(intent, breakout_sl_atr, breakout_rr_target, exit_behavior)
        elif selected_strategy == "RANGE_IN_TREND_LONG":
            intent = "LONG"
            rule = selected_strategy
            _apply_risk(intent, range_meanrev_sl_atr, range_meanrev_rr_target, exit_behavior)

    if intent in ("LONG", "SHORT") and event_block:
        reject_reasons.append("E:event_cooldown" if event_cooldown_remaining > 0 else "E:event")
        intent = "HOLD"
        entry = None
        sl = None
        tp = None
        rr = None

    trend_following_strategy = selected_strategy in ("CONTINUATION", "PULLBACK_REENTRY", "TREND_ACCEL")
    if intent in ("LONG", "SHORT") and trend_following_strategy and stable_soft:
        pending_match = (
            pending_state is not None
            and pending_state.get("side") == intent
            and pending_state.get("strategy") == selected_strategy
            and pending_state.get("set_ts") != decision_ts
        )
        if pending_match and confirmation_ok and not anti_reversal_block and not stable_block:
            pending_entry_status = "CONFIRMED"
            pending_state = None
        else:
            if pending_state is None and confirmation_ok and not anti_reversal_block and not stable_block:
                pending_state = {
                    "side": intent,
                    "strategy": selected_strategy,
                    "set_ts": int(decision_ts) if decision_ts is not None else None,
                    "last_ts": int(decision_ts) if decision_ts is not None else None,
                    "remaining": max(settings.get_int("PENDING_EXPIRE_CANDLES"), 1) + 1,
                }
                pending_entry_status = "SET"
            elif pending_state is not None:
                pending_entry_status = "SET"
                reject_reasons.append("P:pending_exists")
            else:
                pending_entry_status = "NONE"
            intent = "HOLD"
            entry = None
            sl = None
            tp = None
            rr = None
            if strategy_block_reason is None:
                strategy_block_reason = "pending_entry"

    ev_gate_enabled = settings.get_bool("EV_GATE_ENABLED", False)
    ev_value = None
    ev_p = None
    if intent in ("LONG", "SHORT") and ev_gate_enabled:
        base_p = 0.5 + 0.4 * (stability_score - 0.5)
        if confirmation_type != "NONE":
            base_p += settings.get_float("CONFIRM_BONUS")
        ev_p = _clamp(base_p, 0.05, 0.95, default=0.5)
        tp_r = settings.get_float("EV_TP_R")
        sl_r = settings.get_float("EV_SL_R")
        ev_value = ev_p * tp_r - (1 - ev_p) * sl_r
        if ev_value <= 0:
            reject_reasons.append("EV:low")
            intent = "HOLD"
            entry = None
            sl = None
            tp = None
            rr = None

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
        elif routing_regime == "SQUEEZE_BREAK":
            active_rejects.extend(squeeze_rejects)
        elif routing_regime == "TREND_CONTINUATION":
            active_rejects.extend(cont_rejects)
        elif routing_regime == "TREND_ACCEL":
            active_rejects.extend(accel_rejects)
        elif routing_regime == "PULLBACK":
            active_rejects.extend(pullback_rejects)
        elif routing_regime == "RANGE":
            active_rejects.extend(meanrev_rejects)
        elif routing_regime == "COMPRESSION":
            active_rejects.append("R:compression")
        elif routing_regime == "EVENT":
            active_rejects.append("E:event")
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
            "router_debug": router_debug,
            "stability_score": stability_score,
            "stable_ok": stable_ok,
            "stable_soft": stable_soft,
            "stable_block": stable_block,
            "stable_block_reason": stable_block_reason,
            "stability_metrics": stability_metrics,
            "stability_mode_used": "hard" if stable_ok else ("soft" if stable_soft else "block"),
            "adaptive_soft_stability": (
                settings.get_bool("ADAPTIVE_SOFT_STABILITY_ENABLED", False)
                and stable_soft
                and not stable_ok
                and confirmation_ok
                and (volume_ratio is not None and volume_ratio >= 0.5)
            ),
            "continuation_confirmation_type": confirmation_type,
            "confirmation_metrics": confirmation_metrics,
            "anti_reversal_block": anti_reversal_block,
            "anti_reversal_reason": anti_reversal_reason,
            "pending_entry_status": pending_entry_status,
            "event_detected": event_detected,
            "event_block": event_block,
            "event_cooldown_remaining": event_cooldown_remaining,
            "trend_accel_long_ok": trend_accel_long_ok,
            "trend_accel_short_ok": trend_accel_short_ok,
            "squeeze_break_long_ok": squeeze_break_long_ok,
            "squeeze_break_short_ok": squeeze_break_short_ok,
            "ev_gate_enabled": ev_gate_enabled,
            "ev_value": ev_value,
            "ev_p": ev_p,
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
            "range_in_trend_long_ok": range_in_trend_long_ok if range_in_trend_enabled else None,
            "pullback_atr_long": pullback_atr_long,
            "pullback_atr_short": pullback_atr_short,
            "reclaim_long": reclaim_long,
            "reclaim_short": reclaim_short,
            "prev_reclaim_long": prev_reclaim_long,
            "prev_reclaim_short": prev_reclaim_short,
            "reclaim_level_used": reclaim_level_used,
            "effective_tolerance": effective_tolerance,
            "distance_to_reclaim": distance_to_reclaim,
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
            "wick_ratio": wick_ratio_ltf or 0.0,
            "bb_width_atr": bb_width_atr or 0.0,
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
            "ema_fast_htf": ema_fast_htf or 0.0,
            "ema200_prev_n": ema200_prev_n or 0.0,
            "ema200_slope_norm": ema200_slope_norm or 0.0,
            "consec_above_ema200": consec_above_ema200 or 0.0,
            "consec_below_ema200": consec_below_ema200 or 0.0,
            "consec_higher_close": consec_higher_close or 0.0,
            "consec_lower_close": consec_lower_close or 0.0,
            "rsi14_htf": rsi14_htf or 0.0,
            "rsi14_htf_prev": rsi14_htf_prev or 0.0,
            "thresholds": {
                "trend_strength_min": trend_strength_min,
                "regime_breakout_vol_min": regime_breakout_vol_min,
                "regime_compression_width_atr_max": regime_compression_width_atr_max,
                "regime_compression_vol_max": regime_compression_vol_max,
                "regime_trend_dist50_max": regime_trend_dist50_max,
                "trend_accel_vol_mult": trend_accel_vol_mult,
                "squeeze_bb_width_th": squeeze_bb_width_th,
                "event_tr_atr": event_tr_atr,
                "event_cooldown_candles": event_cooldown_candles,
                "stability_hard": settings.get_float("STABILITY_HARD"),
                "stability_soft": settings.get_float("STABILITY_SOFT"),
                "stability_n": stability_n,
                "wick_th": settings.get_float("WICK_TH"),
                "xmax": settings.get_float("XMAX"),
                "confirm_min_body_ratio": settings.get_float("CONFIRM_MIN_BODY_RATIO"),
                "confirm_min_body_ratio_retest": settings.get_float("CONFIRM_MIN_BODY_RATIO_RETEST"),
                "confirm_max_close_pos_short": settings.get_float("CONFIRM_MAX_CLOSE_POS_SHORT"),
                "confirm_max_close_pos_long": settings.get_float("CONFIRM_MAX_CLOSE_POS_LONG"),
                "confirm_retest_tol_atr": settings.get_float("CONFIRM_RETEST_TOL_ATR"),
                "confirm_swing_m": settings.get_int("CONFIRM_SWING_M"),
                "confirm_donchian_k": settings.get_int("CONFIRM_DONCHIAN_K"),
                "confirm_break_delta_atr": settings.get_float("CONFIRM_BREAK_DELTA_ATR"),
                "htf_ema_period": settings.get_int("HTF_EMA_PERIOD"),
                "htf_rsi_period": settings.get_int("HTF_RSI_PERIOD"),
                "htf_rsi_slope_min": settings.get_float("HTF_RSI_SLOPE_MIN"),
                "anti_rev_wick_th": settings.get_float("ANTI_REV_WICK_TH"),
                "pending_confirm_candles": settings.get_int("PENDING_CONFIRM_CANDLES"),
                "pending_expire_candles": settings.get_int("PENDING_EXPIRE_CANDLES"),
                "ev_gate_enabled": ev_gate_enabled,
                "confirm_bonus": settings.get_float("CONFIRM_BONUS"),
                "ev_tp_r": settings.get_float("EV_TP_R"),
                "ev_sl_r": settings.get_float("EV_SL_R"),
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
                "trend_rsi_long_max": trend_rsi_long_max,
                "trend_rsi_short_min": trend_rsi_short_min,
                "range_rsi_long_max": settings.get_float("RANGE_RSI_LONG_MAX", 35.0),
                "range_rsi_short_min": settings.get_float("RANGE_RSI_SHORT_MIN", 65.0),
                "extreme_rsi_long_min": extreme_rsi_long_min,
                "extreme_rsi_short_max": extreme_rsi_short_max,
                "pullback_atr_max": pullback_atr_max,
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
    if tp_targets:
        decision["tp_targets"] = tp_targets
    if move_sl_to_be_after_tp1:
        decision["move_sl_to_be_after_tp1"] = True

    decision["state_update"] = {
        "pending_entry": pending_state,
        "event_cooldown": event_state,
        "last_ts": int(decision_ts) if decision_ts is not None else None,
    }

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
