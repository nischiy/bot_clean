from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path


def _reset_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)
    return logger


def test_runtime_log_created_when_not_pytest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from app import run

    _reset_logger(run.LOG_NAME)
    run._setup_logging()

    assert (tmp_path / "runtime.log").exists()
    sessions = list((tmp_path / "sessions").glob("*.log"))
    assert sessions, "session log not created"


def test_runtime_log_skipped_in_pytest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from app import run

    _reset_logger(run.LOG_NAME)
    run._setup_logging()

    assert not (tmp_path / "runtime.log").exists()
    assert not (tmp_path / "sessions").exists()


def test_runtime_logs_not_created_on_import(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    import importlib

    importlib.import_module("app.run")

    assert not (tmp_path / "runtime.log").exists()
    assert not (tmp_path / "sessions").exists()


def test_decision_clean_prioritizes_funds_blockers() -> None:
    from app import run

    logger = logging.getLogger("decision_clean_test")
    logger.setLevel(logging.INFO)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger.handlers = [handler]
    logger.propagate = False

    decision_log = {
        "reject_reasons": [
            "insufficient_margin",
            "min_qty_not_met_after_rounding: qty=0.0 min_qty=0.001",
            "funds_source_missing",
            "funds_nonpositive",
            "M:regime",
        ]
    }

    run._log_decision_clean(logger, decision_log)
    handler.flush()
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])

    assert payload["blockers"] == ["funds_source_missing", "funds_nonpositive"]


def test_decision_clean_strips_strategy_ineligible_from_blockers() -> None:
    """decision_clean blockers must never contain *:strategy_ineligible."""
    from app import run

    logger = logging.getLogger("decision_clean_test")
    logger.setLevel(logging.INFO)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger.handlers = [handler]
    logger.propagate = False

    decision_log = {
        "reject_reasons": ["P:reclaim", "P:strategy_ineligible", "P:dist50"],
        "strategy_block_reason": "gated_by_reclaim",
    }
    run._log_decision_clean(logger, decision_log)
    handler.flush()
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])

    assert "P:strategy_ineligible" not in payload["blockers"]
    assert payload["blockers"] == ["P:reclaim", "P:dist50"]
    assert payload.get("strategy_block_reason") == "gated_by_reclaim"


def test_router_debug_compact_rej_from_strategies_for_regime_order() -> None:
    """router_debug.compact rej= must be first rejected strategy in strategies_for_regime order, not dict order."""
    from app.run import _router_debug_compact

    # Dict order may put BREAKOUT_EXPANSION first; strategies_for_regime says PULLBACK_REENTRY is the routed one
    router_debug = {
        "regime_detected": "PULLBACK",
        "strategies_for_regime": ["PULLBACK_REENTRY"],
        "enabled_strategies": [],
        "rejected_strategies": {
            "BREAKOUT_EXPANSION": "B:vol",
            "PULLBACK_REENTRY": "P:reclaim",
        },
    }
    compact = _router_debug_compact(router_debug)
    assert compact is not None
    assert "rej=PULLBACK_REENTRY:P:reclaim" in compact or "rej=PULLBACK_REENTRY:" in compact
    assert "rej=BREAKOUT_EXPANSION" not in compact


def test_router_debug_compact_rej_none_when_all_enabled() -> None:
    """When no strategy in strategies_for_regime is rejected, rej=none."""
    from app.run import _router_debug_compact

    router_debug = {
        "regime_detected": "PULLBACK",
        "strategies_for_regime": ["PULLBACK_REENTRY"],
        "enabled_strategies": ["PULLBACK_REENTRY"],
        "rejected_strategies": {},
    }
    compact = _router_debug_compact(router_debug)
    assert compact is not None
    assert "rej=none" in compact
