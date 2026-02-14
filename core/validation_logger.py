"""
Production Validation Logger: Separate logging for validation mode.

When VALIDATION_MODE=1, logs validation events to a separate file:
- Daily health summaries
- Invariant violations with full context snapshots

This is purely observational - does not affect trading behavior.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import settings


def is_validation_mode() -> bool:
    """Check if validation mode is enabled."""
    return settings.get_bool("VALIDATION_MODE", False)


def _get_validation_log_dir() -> Path:
    """Get the validation log directory."""
    log_dir = settings.get_str("LOG_DIR", "logs")
    return Path(log_dir) / "validation"


def _get_daily_validation_file() -> Path:
    """Get the daily validation log file path."""
    log_dir = _get_validation_log_dir()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"validation_{day}.jsonl"


def log_validation_event(event_type: str, payload: Dict[str, Any]) -> None:
    """
    Log a validation event to the validation log file.
    
    Only logs when VALIDATION_MODE=1. This is passive observation only.
    
    Args:
        event_type: Type of validation event (e.g., "health_summary", "invariant_violation")
        payload: Event payload data
    """
    if not is_validation_mode():
        return
    
    try:
        event = {
            "event": event_type,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        }
        event.update(payload)
        
        log_file = _get_daily_validation_file()
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception:
        # Fail silently - validation logging should never affect trading
        pass


def log_health_summary(counters: Dict[str, int], now_ts: int) -> None:
    """
    Log daily health summary to validation file.
    
    Args:
        counters: Health counter values
        now_ts: Current timestamp
    """
    log_validation_event("health_summary", {
        "counters": counters,
    })


def log_invariant_violation(
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log invariant violation with full context snapshot.
    
    Args:
        error_code: Invariant violation error code
        message: Violation message
        details: Violation details from InvariantViolation exception
        context_snapshot: Full context snapshot (payload, decision, trade_plan, etc.)
    """
    payload = {
        "error_code": error_code,
        "message": message,
    }
    if details:
        payload["details"] = details
    if context_snapshot:
        payload["context_snapshot"] = context_snapshot
    
    log_validation_event("invariant_violation", payload)
