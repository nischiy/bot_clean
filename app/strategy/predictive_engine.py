from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import settings


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_div(numer: Optional[float], denom: Optional[float], default: Optional[float] = None) -> Optional[float]:
    if numer is None or denom is None or denom == 0:
        return default
    return numer / denom


def _ordered_unique(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _derive_metrics(context: Dict[str, Any], prior_state: Dict[str, Any]) -> Dict[str, Any]:
    close_ltf = _safe_float(context.get("close_ltf"))
    open_ltf = _safe_float(context.get("open_ltf"))
    high_ltf = _safe_float(context.get("high_ltf"))
    low_ltf = _safe_float(context.get("low_ltf"))
    ema50_ltf = _safe_float(context.get("ema50_ltf"))
    atr14 = _safe_float(context.get("atr14"))
    volume_ratio = _safe_float(context.get("volume_ratio"), 0.0) or 0.0
    candle_body_ratio = _safe_float(context.get("candle_body_ratio"), 0.0) or 0.0
    slope_atr = _safe_float(context.get("slope_atr"))
    dist50 = _safe_float(context.get("dist50"))
    prev_distance = _safe_float(((prior_state or {}).get("predictive_memory") or {}).get("distance_to_reclaim"))
    swing_high_m = _safe_float(context.get("swing_high_m"))
    swing_low_m = _safe_float(context.get("swing_low_m"))
    donchian_high_20 = _safe_float(context.get("donchian_high_20"))
    donchian_low_20 = _safe_float(context.get("donchian_low_20"))
    consec_above_ema50 = _safe_float(context.get("consec_above_ema50"), 0.0) or 0.0
    consec_below_ema50 = _safe_float(context.get("consec_below_ema50"), 0.0) or 0.0
    htf_trend = str(context.get("htf_trend") or "range").lower()
    true_range_atr = _safe_float(context.get("true_range_atr"))
    wick_ratio = _safe_float(context.get("wick_ratio"))

    candle_range = None
    if high_ltf is not None and low_ltf is not None:
        candle_range = max(high_ltf - low_ltf, 0.0)
    close_position = _safe_div(
        (close_ltf - low_ltf) if close_ltf is not None and low_ltf is not None else None,
        candle_range,
        default=None,
    )
    close_near_high = close_position is not None and close_position >= settings.get_float(
        "PREDICTIVE_CLOSE_POS_STRONG_HIGH",
        0.75,
    )
    close_near_low = close_position is not None and close_position <= settings.get_float(
        "PREDICTIVE_CLOSE_POS_STRONG_LOW",
        0.25,
    )
    body_dominance_min = settings.get_float("PREDICTIVE_BODY_DOMINANCE_MIN", 0.55)
    volume_expansion_min = settings.get_float("PREDICTIVE_VOLUME_EXPANSION_MIN", 1.20)
    slope_trigger = settings.get_float("PREDICTIVE_SLOPE_TRIGGER_ATR", 0.10)
    dist_widen_min = settings.get_float("PREDICTIVE_DIST_RECLAIM_WIDEN_MIN", 0.20)
    overextended_dist50 = settings.get_float("PREDICTIVE_OVEREXTENSION_HOLD_ATR", 2.20)

    distance_widening = False
    if dist50 is not None and prev_distance is not None:
        distance_widening = (dist50 - prev_distance) >= dist_widen_min
    distance_narrowing = False
    if dist50 is not None and prev_distance is not None:
        distance_narrowing = (prev_distance - dist50) >= dist_widen_min

    repeated_non_recovery_long = (
        htf_trend == "up"
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf < ema50_ltf
        and consec_below_ema50 >= 2
    )
    repeated_non_recovery_short = (
        htf_trend == "down"
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf > ema50_ltf
        and consec_above_ema50 >= 2
    )
    failed_reclaim_long = bool(
        htf_trend == "up"
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf < ema50_ltf
        and (
            bool(context.get("prev_reclaim_long"))
            or repeated_non_recovery_long
            or (
                high_ltf is not None
                and high_ltf >= ema50_ltf
                and candle_body_ratio >= body_dominance_min
                and close_near_low
            )
        )
        and (distance_widening or close_near_low)
    )
    failed_reclaim_short = bool(
        htf_trend == "down"
        and close_ltf is not None
        and ema50_ltf is not None
        and close_ltf > ema50_ltf
        and (
            bool(context.get("prev_reclaim_short"))
            or repeated_non_recovery_short
            or (
                low_ltf is not None
                and low_ltf <= ema50_ltf
                and candle_body_ratio >= body_dominance_min
                and close_near_high
            )
        )
        and (distance_widening or close_near_high)
    )
    local_break_down = bool(
        close_ltf is not None
        and (
            (swing_low_m is not None and close_ltf < swing_low_m)
            or (donchian_low_20 is not None and close_ltf < donchian_low_20)
        )
    )
    local_break_up = bool(
        close_ltf is not None
        and (
            (swing_high_m is not None and close_ltf > swing_high_m)
            or (donchian_high_20 is not None and close_ltf > donchian_high_20)
        )
    )
    downside_acceptance = bool(
        candle_body_ratio >= body_dominance_min
        and volume_ratio >= volume_expansion_min
        and close_near_low
    )
    upside_acceptance = bool(
        candle_body_ratio >= body_dominance_min
        and volume_ratio >= volume_expansion_min
        and close_near_high
    )
    slope_degradation = slope_atr is not None and slope_atr <= -slope_trigger
    slope_acceleration = slope_atr is not None and slope_atr >= slope_trigger
    trend_failure_short = bool(
        htf_trend == "up"
        and (failed_reclaim_long or repeated_non_recovery_long)
        and (slope_degradation or local_break_down or downside_acceptance)
    )
    trend_failure_long = bool(
        htf_trend == "down"
        and (failed_reclaim_short or repeated_non_recovery_short)
        and (slope_acceleration or local_break_up or upside_acceptance)
    )
    event_body_min = settings.get_float("PREDICTIVE_EVENT_BODY_MIN", 0.65)
    event_volume_min = settings.get_float("PREDICTIVE_EVENT_VOLUME_MIN", 1.80)
    event_directional = bool(
        bool(context.get("event_detected"))
        and true_range_atr is not None
        and candle_body_ratio >= event_body_min
        and volume_ratio >= event_volume_min
        and (
            (close_near_low and (local_break_down or downside_acceptance))
            or (close_near_high and (local_break_up or upside_acceptance))
        )
    )
    event_direction = "SHORT" if close_near_low and not close_near_high else ("LONG" if close_near_high else "NEUTRAL")
    event_chaotic = bool(context.get("event_detected")) and not event_directional

    late_entry = dist50 is not None and dist50 >= overextended_dist50

    return {
        "close_position": close_position,
        "close_near_high": close_near_high,
        "close_near_low": close_near_low,
        "distance_to_reclaim": dist50,
        "distance_widening": distance_widening,
        "distance_narrowing": distance_narrowing,
        "repeated_non_recovery_long": repeated_non_recovery_long,
        "repeated_non_recovery_short": repeated_non_recovery_short,
        "failed_reclaim_long": failed_reclaim_long,
        "failed_reclaim_short": failed_reclaim_short,
        "local_break_down": local_break_down,
        "local_break_up": local_break_up,
        "downside_acceptance": downside_acceptance,
        "upside_acceptance": upside_acceptance,
        "slope_degradation": slope_degradation,
        "slope_acceleration": slope_acceleration,
        "trend_failure_short": trend_failure_short,
        "trend_failure_long": trend_failure_long,
        "event_directional": event_directional,
        "event_chaotic": event_chaotic,
        "event_direction": event_direction,
        "late_entry": late_entry,
        "wick_ratio": wick_ratio,
        "true_range_atr": true_range_atr,
    }


def _resolve_market_state(context: Dict[str, Any], metrics: Dict[str, Any], prev_market_state: str) -> Dict[str, str]:
    htf_trend = str(context.get("htf_trend") or "range").lower()
    dist50 = _safe_float(context.get("dist50"))
    trend_strength = _safe_float(context.get("trend_strength"), 0.0) or 0.0
    trend_strength_min = settings.get_float("TREND_STRENGTH_MIN", 0.6)
    transition_name = "STATE_UNCHANGED"

    if metrics["event_chaotic"]:
        next_state = "EVENT_CHAOTIC"
    elif metrics["event_directional"]:
        next_state = "EVENT_DIRECTIONAL"
    elif htf_trend == "up":
        if metrics["trend_failure_short"] and metrics["local_break_down"]:
            next_state = "BREAKDOWN_CONFIRMED"
        elif metrics["trend_failure_short"]:
            next_state = "BEARISH_TRANSITION"
        elif metrics["failed_reclaim_long"]:
            next_state = "RECLAIM_FAILED"
        elif dist50 is not None and dist50 > settings.get_float("REGIME_TREND_DIST50_MAX", 1.0):
            next_state = "PULLBACK_ACTIVE"
        elif bool(context.get("reclaim_long")):
            next_state = "RECLAIM_PENDING"
        else:
            next_state = "UPTREND_HEALTHY"
    elif htf_trend == "down":
        if metrics["trend_failure_long"] and metrics["local_break_up"]:
            next_state = "BREAKOUT_CONFIRMED"
        elif metrics["trend_failure_long"]:
            next_state = "BULLISH_TRANSITION"
        elif metrics["failed_reclaim_short"]:
            next_state = "SHORT_RECLAIM_FAILED"
        elif dist50 is not None and dist50 > settings.get_float("REGIME_TREND_DIST50_MAX", 1.0):
            next_state = "SHORT_PULLBACK_ACTIVE"
        elif bool(context.get("reclaim_short")):
            next_state = "SHORT_RECLAIM_PENDING"
        else:
            next_state = "DOWNTREND_HEALTHY"
    elif trend_strength < trend_strength_min:
        next_state = "RANGE_BALANCED"
    else:
        next_state = prev_market_state or "RANGE_BALANCED"

    if prev_market_state and prev_market_state != next_state:
        transition_name = f"{prev_market_state}_TO_{next_state}"
    return {
        "market_state_prev": prev_market_state or "RANGE_BALANCED",
        "market_state_next": next_state,
        "transition_name": transition_name,
    }


def infer_predictive_layer(context: Dict[str, Any], prior_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prior_state = dict(prior_state or {})
    predictive_enabled = settings.get_bool("PREDICTIVE_LAYER_ENABLED", True)
    prev_market_state = str(prior_state.get("market_state") or "RANGE_BALANCED")
    metrics = _derive_metrics(context, prior_state)
    state_info = _resolve_market_state(context, metrics, prev_market_state)

    predictive_bias = "NEUTRAL"
    predictive_state = "NEUTRAL"
    confidence_tier = "LOW"
    trigger_candidates: List[str] = []
    invalidation_reasons: List[str] = []
    notes: List[str] = []

    if not predictive_enabled:
        notes.append("predictive_layer_disabled")
    elif metrics["event_chaotic"]:
        predictive_state = "CHOP"
        invalidation_reasons.append("event_chaotic")
        notes.append("chaotic_event_blocks_prediction")
    elif metrics["event_directional"]:
        predictive_bias = metrics["event_direction"]
        predictive_state = "EVENT_DIRECTIONAL"
        confidence_tier = "HIGH"
        trigger_candidates.extend([
            f"event_directional_{predictive_bias.lower()}",
            "event_body_dominance",
            "event_volume_expansion",
        ])
    elif metrics["trend_failure_short"]:
        predictive_bias = "SHORT"
        predictive_state = "LONG_FAILURE"
        trigger_candidates.extend([
            "failed_reclaim_long",
            "trend_failure_short",
        ])
        if metrics["local_break_down"]:
            trigger_candidates.append("acceptance_break_short")
            predictive_state = "BREAKDOWN_RISK"
        if metrics["downside_acceptance"]:
            trigger_candidates.append("downside_acceptance")
        confidence_tier = "HIGH" if metrics["local_break_down"] or metrics["downside_acceptance"] else "MEDIUM"
    elif metrics["trend_failure_long"]:
        predictive_bias = "LONG"
        predictive_state = "SHORT_FAILURE"
        trigger_candidates.extend([
            "failed_reclaim_short",
            "trend_failure_long",
        ])
        if metrics["local_break_up"]:
            trigger_candidates.append("acceptance_break_long")
            predictive_state = "BREAKOUT_RISK"
        if metrics["upside_acceptance"]:
            trigger_candidates.append("upside_acceptance")
        confidence_tier = "HIGH" if metrics["local_break_up"] or metrics["upside_acceptance"] else "MEDIUM"
    elif metrics["local_break_down"] and metrics["downside_acceptance"]:
        predictive_bias = "SHORT"
        predictive_state = "EARLY_SHORT"
        trigger_candidates.extend(["local_structure_break_down", "downside_acceptance"])
        confidence_tier = "MEDIUM"
    elif metrics["local_break_up"] and metrics["upside_acceptance"]:
        predictive_bias = "LONG"
        predictive_state = "EARLY_LONG"
        trigger_candidates.extend(["local_structure_break_up", "upside_acceptance"])
        confidence_tier = "MEDIUM"
    else:
        predictive_state = "CHOP" if state_info["market_state_next"] == "RANGE_BALANCED" else "NEUTRAL"
        if metrics["distance_widening"]:
            invalidation_reasons.append("distance_to_reclaim_widening_without_acceptance")

    if predictive_bias == "LONG":
        if metrics["failed_reclaim_long"] or metrics["downside_acceptance"]:
            invalidation_reasons.append("opposing_bearish_failure")
        if metrics["late_entry"]:
            invalidation_reasons.append("late_overextended_long")
    elif predictive_bias == "SHORT":
        if metrics["failed_reclaim_short"] or metrics["upside_acceptance"]:
            invalidation_reasons.append("opposing_bullish_failure")
        if metrics["late_entry"]:
            invalidation_reasons.append("late_overextended_short")
    elif metrics["late_entry"]:
        invalidation_reasons.append("late_overextended")

    if confidence_tier == "LOW" and predictive_bias in ("LONG", "SHORT"):
        confidence_tier = "MEDIUM" if len(trigger_candidates) >= 2 and not invalidation_reasons else "LOW"

    notes.extend([
        f"close_position={metrics['close_position']:.2f}" if metrics["close_position"] is not None else "close_position=na",
        f"market_state={state_info['market_state_next']}",
    ])

    return {
        "predictive_bias": predictive_bias,
        "predictive_state": predictive_state,
        "confidence_tier": confidence_tier,
        "trigger_candidates": _ordered_unique(trigger_candidates),
        "invalidation_reasons": _ordered_unique(invalidation_reasons),
        "market_state_prev": state_info["market_state_prev"],
        "market_state_next": state_info["market_state_next"],
        "transition_name": state_info["transition_name"],
        "notes": _ordered_unique(notes),
        "event_classification": (
            "EVENT_CHAOTIC"
            if metrics["event_chaotic"]
            else ("EVENT_DIRECTIONAL" if metrics["event_directional"] else "NONE")
        ),
        "metrics": metrics,
        "state_update": {
            "market_state": state_info["market_state_next"],
            "predictive_memory": {
                "distance_to_reclaim": metrics["distance_to_reclaim"],
                "predictive_state": predictive_state,
                "confidence_tier": confidence_tier,
            },
            "last_predictive_bias": predictive_bias,
            "last_transition": state_info["transition_name"],
        },
    }
