from __future__ import annotations

import math
import time
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd

from core.config import settings
from core.runtime_mode import get_runtime_settings


def validate_market_data(
    df_ltf: pd.DataFrame,
    df_htf: Optional[pd.DataFrame],
    *,
    now_ts: Optional[int] = None,
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    runtime = get_runtime_settings()
    now_ts = int(now_ts or time.time())

    required_cols = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
    for col in required_cols:
        if col not in df_ltf.columns:
            errors.append(f"missing_col_ltf:{col}")

    if errors:
        return False, errors

    if df_ltf.empty:
        return False, ["empty_df_ltf"]

    min_bars_ltf = settings.get_int("MD_MIN_BARS_1H")
    min_bars_htf = settings.get_int("MD_MIN_BARS_4H")
    max_gap_seconds = settings.get_int("MD_MAX_GAP_SECONDS")
    max_age_seconds = settings.get_int("MD_MAX_AGE_SECONDS")

    if len(df_ltf) < min_bars_ltf:
        errors.append(f"insufficient_bars_ltf:{len(df_ltf)}<{min_bars_ltf}")

    if df_htf is not None and not df_htf.empty and len(df_htf) < min_bars_htf:
        errors.append(f"insufficient_bars_htf:{len(df_htf)}<{min_bars_htf}")

    close_time = pd.to_datetime(df_ltf["close_time"], utc=True, errors="coerce")
    if close_time.isna().any():
        errors.append("invalid_close_time")
    if not close_time.is_monotonic_increasing:
        errors.append("non_monotonic_close_time")

    if close_time.dt.tz is None:
        errors.append("close_time_not_tz_aware")

    if not runtime.is_replay:
        last_ts = int(close_time.iloc[-1].timestamp())
        if now_ts - last_ts > max_age_seconds:
            errors.append("stale_market_data")

    gaps = close_time.diff().dt.total_seconds().fillna(0.0)
    if (gaps > max_gap_seconds).any():
        errors.append("time_gap_exceeded")

    # NaN checks on required series within lookback window
    lookback = min(len(df_ltf), min_bars_ltf)
    for col in ["close", "high", "low", "volume"]:
        series = pd.to_numeric(df_ltf[col], errors="coerce").iloc[-lookback:]
        if series.isna().any():
            errors.append(f"nan_in_{col}")

    return len(errors) == 0, errors
