from __future__ import annotations

import json

from app.analysis.log_diagnostics import build_diagnostics, filter_candles, parse_log_files


def _prefixed(payload: dict) -> str:
    return f"[2026-03-18 00:00:00] INFO TraderApp: {json.dumps(payload, sort_keys=True)}"


def test_parse_and_merge_mixed_log_lines(tmp_path):
    log_path = tmp_path / "runtime.log"
    lines = [
        "plain info line",
        "[2026-03-18 00:00:01] INFO TraderApp: contracts: no trade signal",
        _prefixed(
            {
                "event": "decision_reject",
                "timestamp_closed": 1000,
                "main_blocker": "P:dist50",
                "blockers": ["P:dist50", "C:trend"],
                "selected_strategy": "NONE",
            }
        ),
        _prefixed(
            {
                "event": "decision_candle",
                "timestamp_closed": 1000,
                "decision": "HOLD",
                "execution_decision": "HOLD",
                "entry_mode": "NONE",
                "predictive_bias": "SHORT",
                "predictive_state": "BREAKDOWN_RISK",
                "confidence_tier": "HIGH",
                "confirmation_quality": "NONE",
                "trigger_candidates": ["failed_reclaim_long", "acceptance_break_short"],
                "invalidation_reasons": ["late_overextended_short"],
                "market_state_prev": "BEARISH_TRANSITION",
                "market_state_next": "BREAKDOWN_CONFIRMED",
                "transition_name": "BEARISH_TRANSITION_TO_BREAKDOWN_CONFIRMED",
                "selected_strategy": "NONE",
                "supporting_strategies": [],
                "opposing_strategies": ["CONTINUATION"],
                "validator_reject_map": {
                    "CONTINUATION": ["C:trend", "C:stability"],
                    "PULLBACK_REENTRY": ["P:dist50", "P:reclaim"],
                },
                "close": 99.0,
                "trend": "up",
                "trend_strength": 0.2,
                "stability_score": 0.21,
                "volume_ratio_5m": 0.66,
                "distance_to_reclaim": 4.65,
            }
        ),
        _prefixed(
            {
                "event": "decision_clean",
                "decision": "HOLD",
                "main_blocker": "P:dist50",
                "blockers": ["P:dist50", "C:trend", "A:cont"],
                "hold_reason_summary": "PULLBACK:no_candidate_passed",
                "strategy_block_reason": "gated_by_pullback_conditions",
            }
        ),
        "{broken json",
        _prefixed(
            {
                "event": "decision_candle",
                "timestamp_closed": 1300,
                "decision": "HOLD",
                "execution_decision": "HOLD_LOW_QUALITY",
                "entry_mode": "NONE",
                "predictive_bias": "LONG",
                "predictive_state": "EARLY_LONG",
                "confidence_tier": "MEDIUM",
                "confirmation_quality": "WEAK",
                "trigger_candidates": ["breakout_risk_long"],
                "invalidation_reasons": [],
                "market_state_prev": "RANGE_BALANCED",
                "market_state_next": "BREAKOUT_CONFIRMED",
                "transition_name": "RANGE_BALANCED_TO_BREAKOUT_CONFIRMED",
                "selected_strategy": "NONE",
                "supporting_strategies": [],
                "opposing_strategies": [],
                "validator_reject_map": {},
                "close": 100.0,
                "trend": "up",
                "trend_strength": 0.9,
            }
        ),
        _prefixed(
            {
                "event": "decision_candle",
                "timestamp_closed": 1600,
                "decision": "HOLD",
                "execution_decision": "HOLD_LOW_QUALITY",
                "entry_mode": "NONE",
                "predictive_bias": "NEUTRAL",
                "predictive_state": "NEUTRAL",
                "finalized_labels": [
                    {
                        "timestamp_closed": 1000,
                        "realized_move_label": "DOWN_1ATR",
                        "early_signal_right": True,
                    }
                ],
            }
        ),
        _prefixed(
            {
                "event": "decision_candle",
                "timestamp_closed": 1900,
                "decision": "HOLD",
                "execution_decision": "HOLD",
                "entry_mode": "NONE",
                "predictive_bias": "NEUTRAL",
                "predictive_state": "CHOP",
                "finalized_labels": [
                    {
                        "timestamp_closed": 1300,
                        "realized_move_label": "DOWN_1ATR",
                        "early_signal_right": False,
                    }
                ],
            }
        ),
    ]
    log_path.write_text("\n".join(lines), encoding="utf-8")

    candles = parse_log_files([log_path])

    assert [row["timestamp_closed"] for row in candles] == [1000, 1300, 1600, 1900]
    assert candles[0]["main_blocker"] == "P:dist50"
    assert candles[0]["hold_reason_summary"] == "PULLBACK:no_candidate_passed"
    assert candles[0]["realized_move_label"] == "DOWN_1ATR"
    assert candles[0]["early_signal_right"] is True
    assert candles[2]["predictive_bias"] == "NEUTRAL"


def test_diagnostic_tables_cover_blockers_missed_late_validator_and_transitions(tmp_path):
    log_path = tmp_path / "runtime.log"
    log_path.write_text(
        "\n".join(
            [
                _prefixed(
                    {
                        "event": "decision_reject",
                        "timestamp_closed": 1000,
                        "main_blocker": "P:dist50",
                        "blockers": ["P:dist50", "C:trend"],
                    }
                ),
                _prefixed(
                    {
                        "event": "decision_candle",
                        "timestamp_closed": 1000,
                        "decision": "HOLD",
                        "execution_decision": "HOLD",
                        "entry_mode": "NONE",
                        "predictive_bias": "SHORT",
                        "predictive_state": "BREAKDOWN_RISK",
                        "confidence_tier": "HIGH",
                        "confirmation_quality": "NONE",
                        "invalidation_reasons": ["late_overextended_short"],
                        "transition_name": "BEARISH_TRANSITION_TO_BREAKDOWN_CONFIRMED",
                        "supporting_strategies": [],
                        "opposing_strategies": ["CONTINUATION"],
                        "validator_reject_map": {
                            "CONTINUATION": ["C:trend", "C:stability"],
                            "PULLBACK_REENTRY": ["P:dist50", "P:reclaim"],
                        },
                        "trend": "up",
                        "trend_strength": 0.2,
                        "stability_score": 0.21,
                        "distance_to_reclaim": 4.65,
                    }
                ),
                _prefixed(
                    {
                        "event": "decision_clean",
                        "decision": "HOLD",
                        "main_blocker": "P:dist50",
                        "blockers": ["P:dist50", "C:trend", "A:cont"],
                    }
                ),
                _prefixed(
                    {
                        "event": "decision_candle",
                        "timestamp_closed": 1300,
                        "decision": "HOLD",
                        "execution_decision": "HOLD_LOW_QUALITY",
                        "entry_mode": "NONE",
                        "predictive_bias": "LONG",
                        "predictive_state": "EARLY_LONG",
                        "confidence_tier": "MEDIUM",
                        "confirmation_quality": "WEAK",
                        "transition_name": "RANGE_BALANCED_TO_BREAKOUT_CONFIRMED",
                    }
                ),
                _prefixed(
                    {
                        "event": "decision_candle",
                        "timestamp_closed": 1600,
                        "decision": "HOLD",
                        "execution_decision": "HOLD_EVENT",
                        "entry_mode": "NONE",
                        "predictive_bias": "NEUTRAL",
                        "predictive_state": "EVENT_DIRECTIONAL",
                        "event_classification": "EVENT_DIRECTIONAL",
                        "event_detected": True,
                        "event_hard_block": True,
                        "finalized_labels": [
                            {
                                "timestamp_closed": 1000,
                                "realized_move_label": "DOWN_1ATR",
                                "early_signal_right": True,
                            },
                            {
                                "timestamp_closed": 1300,
                                "realized_move_label": "DOWN_1ATR",
                                "early_signal_right": False,
                            },
                        ],
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    candles = filter_candles(parse_log_files([log_path]), since_ts=None, last=None, only_holds=False, only_predictive=False)
    diagnostics = build_diagnostics(candles, recent=10)

    blocker_top = diagnostics.tables["blocker_frequency"][0]
    assert blocker_top["blocker"] == "P:dist50"
    assert blocker_top["count"] >= 1

    missed = diagnostics.tables["missed_opportunities"]
    assert len(missed) == 1
    assert missed[0]["timestamp_closed"] == 1000

    late = diagnostics.tables["late_detection"]
    assert len(late) == 1
    assert late[0]["timestamp_closed"] == 1000

    validator = diagnostics.tables["validator_conflicts"]
    assert len(validator) == 1
    assert validator[0]["timestamp_closed"] == 1000

    transition_rows = diagnostics.tables["transition_table"]
    transition_names = {row["transition_name"] for row in transition_rows}
    assert "BEARISH_TRANSITION_TO_BREAKDOWN_CONFIRMED" in transition_names

    false_positives = diagnostics.tables["false_positives"]
    assert len(false_positives) == 1
    assert false_positives[0]["timestamp_closed"] == 1300

    overview = {row["metric"]: row["value"] for row in diagnostics.tables["overview_summary"]}
    assert overview["predictive_correct_but_not_traded"] == 1
    assert overview["late_overextended_invalidations"] == 1

    assert "Top suspected logical problems" in diagnostics.report
    assert "BLOCKER FREQUENCY TABLE" in diagnostics.report
