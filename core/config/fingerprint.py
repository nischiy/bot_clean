"""
Configuration Fingerprint: Hash all effective config/env values for governance.

Ensures config consistency across sessions and detects runtime changes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Any, Set
from core.config import settings
from core.config.env import get_env


# Config keys that affect trading behavior (strategy & risk)
# Excludes: logging, state dirs, API keys (secrets), non-trading flags
STRATEGY_RISK_CONFIG_KEYS: Set[str] = {
    # Strategy thresholds
    "DECISION_VOLUME_RATIO_MIN",
    "DECISION_EMA50_ATR_MAX",
    "DECISION_BREAKOUT_ATR_MIN",
    "DECISION_SL_ATR_MULT",
    "DECISION_TP_ATR_MULT",
    "DECISION_MIN_RR",
    "MIN_RR",
    "TRADE_COOLDOWN_MINUTES",
    # Regime detection
    "REGIME_BREAKOUT_VOL_MIN",
    "REGIME_COMPRESSION_WIDTH_ATR_MAX",
    "REGIME_COMPRESSION_VOL_MAX",
    "REGIME_TREND_DIST50_MAX",
    # Strategy routing
    "BREAKOUT_ACCEPT_BARS",
    "BREAKOUT_REJECT_WICK_ATR",
    "BREAKOUT_RETEST_ATR",
    "BREAKOUT_SL_ATR",
    "BREAKOUT_RR_TARGET",
    "CONTINUATION_SL_ATR",
    "CONTINUATION_RR_TARGET",
    "CONT_BREAK_D_ATR",
    "CONT_SLOPE_ATR_MAX",
    "CONT_SLOPE_ATR_MIN",
    "CONT_K_MAX",
    "CONT_RSI_MIN_SHORT",
    "CONT_RSI_MAX_LONG",
    "CONT_BODY_MIN",
    "CONT_VOL_MIN",
    "CONT_ATR_RATIO_MIN",
    "TP1_FRACTION",
    "TP1_RR_FRACTION",
    "VOLATILITY_PERCENTILE_MIN",
    "PULLBACK_REENTRY_DIST50_MIN",
    "PULLBACK_REENTRY_DIST50_MAX",
    "PULLBACK_REENTRY_MIN_BARS",
    "PULLBACK_REENTRY_CONFIRM_BODY_MIN",
    "PULLBACK_REENTRY_RECLAIM_VOL_MIN",
    "PULLBACK_REENTRY_SL_ATR",
    "PULLBACK_REENTRY_RR_TARGET",
    "PULLBACK_REENTRY_VOL_MIN",
    "RANGE_MEANREV_EDGE_ATR",
    "RANGE_MEANREV_VOL_MAX",
    "RANGE_MEANREV_SL_ATR",
    "RANGE_MEANREV_RR_TARGET",
    "HTF_TREND_SLOPE_N",
    "HTF_TREND_SLOPE_MIN",
    "HTF_TREND_PERSIST_MIN",
    "HTF_TREND_STRUCTURE_MIN",
    "TIME_EXIT_BARS",
    "TIME_EXIT_PROGRESS_ATR",
    "TREND_STRENGTH_MIN",
    "VOLATILITY_EXPANSION_THRESHOLD",
    "BB_WIDTH_EXPANSION_MULT",
    "STABILITY_WEIGHT_R",
    "STABILITY_WEIGHT_W",
    "STABILITY_WEIGHT_X",
    "PULLBACK_ATR_MAX",
    "TREND_RSI_LONG_MAX",
    "TREND_RSI_SHORT_MIN",
    "RANGE_RSI_LONG_MAX",
    "RANGE_RSI_SHORT_MIN",
    "EXTREME_RSI_LONG_MIN",
    "EXTREME_RSI_SHORT_MAX",
    # Stability scoring
    "STABILITY_N",
    "WICK_TH",
    "XMAX",
    "STABILITY_HARD",
    "STABILITY_SOFT",
    # Continuation confirmation
    "CONFIRM_MIN_BODY_RATIO",
    "CONFIRM_MIN_BODY_RATIO_RETEST",
    "CONFIRM_MAX_CLOSE_POS_SHORT",
    "CONFIRM_MAX_CLOSE_POS_LONG",
    "CONFIRM_RETEST_TOL_ATR",
    "CONFIRM_SWING_M",
    "CONFIRM_DONCHIAN_K",
    "CONFIRM_BREAK_DELTA_ATR",
    # Anti-reversal
    "HTF_EMA_PERIOD",
    "HTF_RSI_PERIOD",
    "HTF_RSI_SLOPE_MIN",
    "ANTI_REV_WICK_TH",
    # Pending entry
    "PENDING_CONFIRM_CANDLES",
    "PENDING_EXPIRE_CANDLES",
    # Regimes
    "TREND_ACCEL_VOL_MULT",
    "SQUEEZE_BB_WIDTH_TH",
    "EVENT_TR_ATR",
    "EVENT_COOLDOWN_CANDLES",
    # EV gate
    "EV_GATE_ENABLED",
    "CONFIRM_BONUS",
    "EV_TP_R",
    "EV_SL_R",
    # Risk policy
    "RISK_PER_TRADE_PCT",
    "RISK_MAX_DD_PCT_DAY",
    "RISK_MAX_CONSEC_LOSSES",
    "RISK_MAX_LOSS_USD_DAY",
    "RISK_MAX_OPEN_RISK_USD",
    "RISK_MAX_TRADES_PER_DAY",
    "RISK_MAX_POS_USD",
    "RISK_MIN_EQUITY_USD",
    # Position sizing / leverage
    "LEVERAGE",
    "MAX_LEVERAGE",
    "PREFERRED_MAX_LEVERAGE",
    "RISK_MARGIN_FRACTION",
    "MAX_MARGIN_UTIL_PCT",
    "MIN_SL_TICKS",
    # Execution safety
    "SPREAD_MAX_PCT",
    "ATR_SPIKE_MAX_PCT",
    "EXIT_REQUIRE_TP",
    # SL/TP policies
    "SL_MODE",
    "TP_R_MULT",
    "SL_ATR_MULT",
    "SL_PCT",
    "TP_PCT",
}


def compute_config_fingerprint() -> str:
    """
    Compute deterministic hash of all effective config values affecting strategy & risk.
    
    Returns:
        SHA256 hash (hex string) of sorted key-value pairs
    """
    config_dict: Dict[str, Any] = {}
    
    # Collect all effective config values (using get_* methods to get resolved values)
    for key in sorted(STRATEGY_RISK_CONFIG_KEYS):
        # Use get_env to get actual effective value (env override or default)
        raw_value = get_env(key)
        if raw_value is None:
            # Fallback to default from settings
            default = settings.env_default(key)
            if default is not None:
                config_dict[key] = default
        else:
            config_dict[key] = str(raw_value).strip()
    
    # Create deterministic JSON representation
    # Sort keys for determinism
    json_str = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))
    
    # Compute hash
    hash_obj = hashlib.sha256(json_str.encode("utf-8"))
    return hash_obj.hexdigest()


def get_config_fingerprint() -> Dict[str, Any]:
    """
    Get config fingerprint with metadata.
    
    Returns:
        Dict with fingerprint hash and key count
    """
    fingerprint = compute_config_fingerprint()
    return {
        "config_hash": fingerprint,
        "config_keys_count": len(STRATEGY_RISK_CONFIG_KEYS),
    }


# Runtime config fingerprint tracking
_RUNTIME_FINGERPRINT: str | None = None


def initialize_config_fingerprint() -> str:
    """
    Initialize and store runtime config fingerprint.
    Must be called at startup before any trading decisions.
    
    Returns:
        Config fingerprint hash
    """
    global _RUNTIME_FINGERPRINT
    _RUNTIME_FINGERPRINT = compute_config_fingerprint()
    return _RUNTIME_FINGERPRINT


def get_runtime_fingerprint() -> str | None:
    """Get stored runtime config fingerprint."""
    return _RUNTIME_FINGERPRINT


def verify_config_unchanged() -> tuple[bool, str]:
    """
    Verify config has not changed since initialization.
    
    Returns:
        (is_valid: bool, error_message: str)
        If invalid, trading should be HARD STOPPED
    """
    if _RUNTIME_FINGERPRINT is None:
        return False, "config_fingerprint_not_initialized"
    
    current = compute_config_fingerprint()
    if current != _RUNTIME_FINGERPRINT:
        return False, f"config_changed: expected={_RUNTIME_FINGERPRINT[:16]}... got={current[:16]}..."
    
    return True, ""
