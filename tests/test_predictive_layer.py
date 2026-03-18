import copy

from app.strategy.analytics_labels import update_analytics_labels
from app.strategy.decision_engine import stage3_execution_decision
from app.strategy.predictive_engine import infer_predictive_layer


def test_long_reclaim_failure_becomes_predictive_short():
    context = {
        "htf_trend": "up",
        "trend_strength": 0.9,
        "close_ltf": 98.0,
        "open_ltf": 101.0,
        "high_ltf": 101.0,
        "low_ltf": 97.0,
        "ema50_ltf": 100.0,
        "atr14": 2.0,
        "volume_ratio": 1.6,
        "candle_body_ratio": 0.75,
        "slope_atr": -0.30,
        "dist50": 1.1,
        "swing_low_m": 99.0,
        "swing_high_m": 103.0,
        "donchian_low_20": 99.0,
        "donchian_high_20": 104.0,
        "consec_above_ema50": 0,
        "consec_below_ema50": 3,
        "prev_reclaim_long": True,
        "event_detected": False,
        "true_range_atr": 2.0,
    }

    result = infer_predictive_layer(context, {"market_state": "RECLAIM_PENDING"})

    assert result["predictive_bias"] == "SHORT"
    assert result["predictive_state"] in ("LONG_FAILURE", "BREAKDOWN_RISK")
    assert result["confidence_tier"] in ("MEDIUM", "HIGH")
    assert "failed_reclaim_long" in result["trigger_candidates"]


def test_short_reclaim_failure_becomes_predictive_long():
    context = {
        "htf_trend": "down",
        "trend_strength": 0.9,
        "close_ltf": 102.0,
        "open_ltf": 99.0,
        "high_ltf": 103.0,
        "low_ltf": 99.0,
        "ema50_ltf": 100.0,
        "atr14": 2.0,
        "volume_ratio": 1.6,
        "candle_body_ratio": 0.75,
        "slope_atr": 0.30,
        "dist50": 1.1,
        "swing_low_m": 97.0,
        "swing_high_m": 101.0,
        "donchian_low_20": 96.0,
        "donchian_high_20": 101.0,
        "consec_above_ema50": 3,
        "consec_below_ema50": 0,
        "prev_reclaim_short": True,
        "event_detected": False,
        "true_range_atr": 2.0,
    }

    result = infer_predictive_layer(context, {"market_state": "SHORT_RECLAIM_PENDING"})

    assert result["predictive_bias"] == "LONG"
    assert result["predictive_state"] in ("SHORT_FAILURE", "BREAKOUT_RISK")
    assert result["confidence_tier"] in ("MEDIUM", "HIGH")
    assert "failed_reclaim_short" in result["trigger_candidates"]


def test_directional_event_and_chaotic_event_are_split():
    directional = infer_predictive_layer(
        {
            "htf_trend": "up",
            "trend_strength": 0.9,
            "close_ltf": 105.0,
            "open_ltf": 99.0,
            "high_ltf": 106.0,
            "low_ltf": 98.0,
            "ema50_ltf": 100.0,
            "atr14": 2.0,
            "volume_ratio": 2.2,
            "candle_body_ratio": 0.85,
            "slope_atr": 0.20,
            "dist50": 1.0,
            "swing_high_m": 104.0,
            "swing_low_m": 98.0,
            "donchian_high_20": 104.0,
            "donchian_low_20": 97.0,
            "consec_above_ema50": 3,
            "consec_below_ema50": 0,
            "event_detected": True,
            "true_range_atr": 4.0,
        }
    )
    chaotic = infer_predictive_layer(
        {
            "htf_trend": "range",
            "trend_strength": 0.2,
            "close_ltf": 100.0,
            "open_ltf": 99.5,
            "high_ltf": 106.0,
            "low_ltf": 94.0,
            "ema50_ltf": 100.0,
            "atr14": 2.0,
            "volume_ratio": 2.2,
            "candle_body_ratio": 0.20,
            "slope_atr": 0.0,
            "dist50": 0.1,
            "swing_high_m": 107.0,
            "swing_low_m": 93.0,
            "donchian_high_20": 107.0,
            "donchian_low_20": 93.0,
            "consec_above_ema50": 0,
            "consec_below_ema50": 0,
            "event_detected": True,
            "true_range_atr": 5.0,
        }
    )

    assert directional["event_classification"] == "EVENT_DIRECTIONAL"
    assert directional["predictive_bias"] == "LONG"
    assert chaotic["event_classification"] == "EVENT_CHAOTIC"
    assert chaotic["predictive_bias"] == "NEUTRAL"
    assert chaotic["predictive_state"] == "CHOP"


def test_stage3_allows_early_entry_when_prediction_survives_soft_legacy_rejection():
    predictive_result = {
        "predictive_bias": "SHORT",
        "predictive_state": "LONG_FAILURE",
        "confidence_tier": "MEDIUM",
        "event_classification": "NONE",
        "metrics": {"late_entry": False},
    }
    validation_summary = {
        "confirmation_quality": "NONE",
        "supporting_strategies": [],
        "opposing_strategies": [],
        "validator_reject_map": {
            "CONTINUATION": ["C:slope"],
            "BREAKOUT_EXPANSION": ["B:impulse"],
        },
    }

    result = stage3_execution_decision(
        predictive_result=predictive_result,
        validation_summary=validation_summary,
        legacy_intent="HOLD",
        legacy_selected_strategy="NONE",
        legacy_entry=None,
        legacy_sl=None,
        legacy_tp=None,
        legacy_rr=None,
        close_ltf=98.0,
        atr14=2.0,
        ema50_ltf=100.0,
        swing_high_m=101.0,
        swing_low_m=97.0,
        donchian_high_20=102.0,
        donchian_low_20=97.0,
        event_hard_block=False,
        reject_reasons=[],
        stable_block=False,
        hold_reason="all_strategies_failed",
    )

    assert result["execution_decision"] == "OPEN_SHORT_EARLY"
    assert result["entry_mode"] == "EARLY"
    assert result["intent"] == "SHORT"


def test_analytics_labels_finalize_after_six_candles():
    state = {}
    initial = {
        "timestamp_closed": 1000,
        "close_ltf": 100.0,
        "high_ltf": 101.0,
        "low_ltf": 99.0,
        "atr": 5.0,
        "predictive_bias": "LONG",
        "predictive_state": "EARLY_LONG",
        "market_state_prev": "RANGE_BALANCED",
        "market_state_next": "BREAKOUT_CONFIRMED",
        "event_classification": "NONE",
        "execution_decision": "OPEN_LONG_EARLY",
        "entry_mode": "EARLY",
        "confirmation_quality": "NONE",
        "supporting_strategies": [],
        "opposing_strategies": [],
        "blocked_by_confirmation": False,
        "blocked_by_event": False,
        "blocked_by_late": False,
        "failed_reclaim_unconverted": False,
        "prior_predictive_same_side_age": 0,
    }
    result = update_analytics_labels(state, initial)
    state = {"analytics_queue": result["analytics_queue"]}

    for idx in range(1, 7):
        snapshot = copy.deepcopy(initial)
        snapshot["timestamp_closed"] = 1000 + idx * 300
        snapshot["high_ltf"] = 106.0 if idx == 1 else 104.0
        snapshot["low_ltf"] = 99.0
        result = update_analytics_labels(state, snapshot)
        state = {"analytics_queue": result["analytics_queue"]}

    assert result["latest_finalized_label"] is not None
    assert result["latest_finalized_label"]["realized_move_label"] == "UP_1ATR"
    assert result["latest_finalized_label"]["early_signal_right"] is True
