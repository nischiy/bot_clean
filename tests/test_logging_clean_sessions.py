from __future__ import annotations

import logging

from app.core.logging import CleanSessionFilter


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("BotRun", logging.INFO, __file__, 1, msg, args=(), exc_info=None)


def test_clean_session_filter_blocks_skip_noise() -> None:
    flt = CleanSessionFilter()

    assert not flt.filter(_record("Tick #123"))
    assert not flt.filter(_record("tick_skip_agg"))
    assert not flt.filter(_record('{"event":"tick_skip_agg"}'))
    assert not flt.filter(_record('{"event":"tick_summary","skip_reason":"already_processed"}'))


def test_clean_session_filter_allows_important_lines() -> None:
    flt = CleanSessionFilter()

    assert flt.filter(_record("contracts: preflight rejects: ['x']"))
    assert not flt.filter(_record('{"event":"decision_candle","decision":"HOLD"}'))
    assert flt.filter(_record('{"event":"decision_clean","decision":"HOLD"}'))
    assert flt.filter(_record('{"event":"tick_summary","skip_reason":"preflight_reject"}'))
