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


def _ordered_unique(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _finalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    origin_close = _safe_float(entry.get("origin_close"), 0.0) or 0.0
    origin_atr = max(_safe_float(entry.get("origin_atr"), 0.0) or 0.0, 1e-9)
    max_high = _safe_float(entry.get("max_high"), origin_close) or origin_close
    min_low = _safe_float(entry.get("min_low"), origin_close) or origin_close
    up_level = origin_close + origin_atr
    down_level = origin_close - origin_atr

    up_hit = max_high >= up_level
    down_hit = min_low <= down_level
    if up_hit and down_hit:
        realized_move_label = "BOTH_SIDES"
    elif up_hit:
        realized_move_label = "UP_1ATR"
    elif down_hit:
        realized_move_label = "DOWN_1ATR"
    else:
        realized_move_label = "NO_1ATR_MOVE"

    first_up_ts = entry.get("first_up_ts")
    first_down_ts = entry.get("first_down_ts")
    predictive_bias = str(entry.get("predictive_bias") or "NEUTRAL")
    execution_decision = str(entry.get("execution_decision") or "HOLD")
    confirmation_quality = str(entry.get("confirmation_quality") or "NONE")

    early_signal_right = False
    if predictive_bias == "LONG":
        early_signal_right = up_hit and (not down_hit or (first_up_ts is not None and (first_down_ts is None or first_up_ts <= first_down_ts)))
    elif predictive_bias == "SHORT":
        early_signal_right = down_hit and (not up_hit or (first_down_ts is not None and (first_up_ts is None or first_down_ts <= first_up_ts)))

    prior_predictive_age = int(entry.get("prior_predictive_same_side_age") or 0)
    confirmed_signal_late = bool(
        execution_decision.endswith("_CONFIRMED")
        and early_signal_right
        and prior_predictive_age >= 1
    )
    blocked_by_confirmation = bool(entry.get("blocked_by_confirmation"))
    blocked_by_event = bool(entry.get("blocked_by_event"))
    blocked_by_late = bool(entry.get("blocked_by_late"))
    failed_reclaim_unconverted = bool(entry.get("failed_reclaim_unconverted"))

    return {
        "timestamp_closed": entry.get("timestamp_closed"),
        "predictive_bias": predictive_bias,
        "predictive_state": entry.get("predictive_state"),
        "execution_decision": execution_decision,
        "entry_mode": entry.get("entry_mode"),
        "confirmation_quality": confirmation_quality,
        "realized_move_label": realized_move_label,
        "early_signal_right": early_signal_right,
        "confirmed_signal_late": confirmed_signal_late,
        "event_block_prevented_profitable_move": bool(blocked_by_event and early_signal_right),
        "reclaim_logic_missed_move": bool(failed_reclaim_unconverted and early_signal_right),
        "dist50_correct_avoidance_or_missed_opportunity": "MISSED_OPPORTUNITY" if blocked_by_late and early_signal_right else ("CORRECT_AVOIDANCE" if blocked_by_late else "NOT_APPLICABLE"),
        "missed_opportunities": _ordered_unique([
            "predicted_move_not_traded" if execution_decision.startswith("HOLD") and early_signal_right else "",
            "move_classified_too_late" if blocked_by_late and early_signal_right else "",
            "confirmation_blocked_profitable_move" if blocked_by_confirmation and early_signal_right else "",
            "long_failure_not_converted" if entry.get("market_state_prev") == "RECLAIM_FAILED" and predictive_bias != "SHORT" and down_hit else "",
            "short_failure_not_converted" if entry.get("market_state_prev") == "SHORT_RECLAIM_FAILED" and predictive_bias != "LONG" and up_hit else "",
            "event_directional_move_incorrectly_held" if blocked_by_event and entry.get("event_classification") == "EVENT_DIRECTIONAL" and early_signal_right else "",
            "overextension_block_after_early_move_missed" if blocked_by_late and early_signal_right else "",
        ]),
    }


def update_analytics_labels(state: Optional[Dict[str, Any]], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(state or {})
    queue = list(state.get("analytics_queue") or [])
    finalized: List[Dict[str, Any]] = []
    horizon = max(settings.get_int("PREDICTIVE_LABEL_HORIZON_CANDLES", 6), 1)
    current_ts = snapshot.get("timestamp_closed")
    current_high = _safe_float(snapshot.get("high_ltf"))
    current_low = _safe_float(snapshot.get("low_ltf"))

    updated_queue: List[Dict[str, Any]] = []
    for item in queue:
        ts = item.get("timestamp_closed")
        if current_ts is None or ts == current_ts:
            updated_queue.append(item)
            continue
        next_item = dict(item)
        next_item["candles_elapsed"] = int(next_item.get("candles_elapsed") or 0) + 1
        if current_high is not None:
            next_item["max_high"] = max(_safe_float(next_item.get("max_high"), current_high) or current_high, current_high)
        if current_low is not None:
            next_item["min_low"] = min(_safe_float(next_item.get("min_low"), current_low) or current_low, current_low)
        origin_close = _safe_float(next_item.get("origin_close"), 0.0) or 0.0
        origin_atr = max(_safe_float(next_item.get("origin_atr"), 0.0) or 0.0, 1e-9)
        if next_item.get("first_up_ts") is None and current_high is not None and current_high >= origin_close + origin_atr:
            next_item["first_up_ts"] = current_ts
        if next_item.get("first_down_ts") is None and current_low is not None and current_low <= origin_close - origin_atr:
            next_item["first_down_ts"] = current_ts
        if int(next_item.get("candles_elapsed") or 0) >= horizon:
            finalized.append(_finalize_entry(next_item))
            continue
        updated_queue.append(next_item)

    current_entry = {
        "timestamp_closed": current_ts,
        "origin_close": _safe_float(snapshot.get("close_ltf"), 0.0) or 0.0,
        "origin_atr": max(_safe_float(snapshot.get("atr"), 0.0) or 0.0, 1e-9),
        "max_high": current_high if current_high is not None else _safe_float(snapshot.get("close_ltf"), 0.0) or 0.0,
        "min_low": current_low if current_low is not None else _safe_float(snapshot.get("close_ltf"), 0.0) or 0.0,
        "first_up_ts": None,
        "first_down_ts": None,
        "candles_elapsed": 0,
        "predictive_bias": snapshot.get("predictive_bias"),
        "predictive_state": snapshot.get("predictive_state"),
        "market_state_prev": snapshot.get("market_state_prev"),
        "market_state_next": snapshot.get("market_state_next"),
        "event_classification": snapshot.get("event_classification"),
        "execution_decision": snapshot.get("execution_decision"),
        "entry_mode": snapshot.get("entry_mode"),
        "confirmation_quality": snapshot.get("confirmation_quality"),
        "supporting_strategies": list(snapshot.get("supporting_strategies") or []),
        "opposing_strategies": list(snapshot.get("opposing_strategies") or []),
        "blocked_by_confirmation": bool(snapshot.get("blocked_by_confirmation")),
        "blocked_by_event": bool(snapshot.get("blocked_by_event")),
        "blocked_by_late": bool(snapshot.get("blocked_by_late")),
        "failed_reclaim_unconverted": bool(snapshot.get("failed_reclaim_unconverted")),
        "prior_predictive_same_side_age": int(snapshot.get("prior_predictive_same_side_age") or 0),
    }
    if current_ts is not None:
        updated_queue.append(current_entry)

    max_queue = max(horizon + 4, 12)
    if len(updated_queue) > max_queue:
        updated_queue = updated_queue[-max_queue:]

    latest_finalized = finalized[-1] if finalized else None
    return {
        "analytics_queue": updated_queue,
        "finalized_labels": finalized,
        "latest_finalized_label": latest_finalized,
        "pending_count": len(updated_queue),
        "label_horizon_candles": horizon,
    }
