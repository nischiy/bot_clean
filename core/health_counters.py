"""
Operator-Level Health Signals: Lightweight runtime counters.

Non-trading metrics for operational visibility.
"""
from __future__ import annotations

from typing import Dict, Any
from datetime import datetime, timezone
from core.config import settings


class HealthCounters:
    """Runtime health counters for operational visibility."""
    
    def __init__(self):
        self.counters: Dict[str, int] = {
            "candles_processed": 0,
            "decisions_made": 0,
            "decisions_rejected": 0,
            "risk_rejections": 0,
            "kill_switch_activations": 0,
            "invariant_violations": 0,
            "config_changes_detected": 0,
        }
        self._last_health_log_ts: int = 0
    
    def increment(self, counter: str, amount: int = 1) -> None:
        """Increment a counter."""
        if counter in self.counters:
            self.counters[counter] = self.counters.get(counter, 0) + amount
    
    def get_counters(self) -> Dict[str, int]:
        """Get current counter values."""
        return dict(self.counters)
    
    def should_emit_health_summary(self, now_ts: int) -> bool:
        """Check if health summary should be emitted (hourly)."""
        health_interval_sec = settings.get_int("HEALTH_SUMMARY_INTERVAL_SEC", 3600)
        if self._last_health_log_ts == 0:
            self._last_health_log_ts = now_ts
            return False
        
        elapsed = now_ts - self._last_health_log_ts
        if elapsed >= health_interval_sec:
            self._last_health_log_ts = now_ts
            return True
        
        return False
    
    def reset(self) -> None:
        """Reset all counters (for testing)."""
        for key in self.counters:
            self.counters[key] = 0
        self._last_health_log_ts = 0


# Global health counters instance
_health_counters: HealthCounters | None = None


def get_health_counters() -> HealthCounters:
    """Get global health counters instance."""
    global _health_counters
    if _health_counters is None:
        _health_counters = HealthCounters()
    return _health_counters


def reset_health_counters() -> None:
    """Reset health counters (for testing)."""
    global _health_counters
    if _health_counters is not None:
        _health_counters.reset()
    else:
        _health_counters = HealthCounters()


def emit_health_summary(logger, now_ts: int) -> None:
    """
    Emit periodic health summary log.
    
    Args:
        logger: Logger instance
        now_ts: Current timestamp
    """
    counters = get_health_counters()
    
    if not counters.should_emit_health_summary(now_ts):
        return
    
    counter_values = counters.get_counters()
    
    # Emit structured health summary
    summary = {
        "event": "health_summary",
        "timestamp": now_ts,
        "timestamp_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "counters": counter_values,
    }
    
    try:
        import json
        logger.info(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    except Exception:
        logger.info("health_summary: %s", summary)
    
    # Log to validation file if validation mode is enabled
    try:
        from core.validation_logger import log_health_summary
        log_health_summary(counter_values, now_ts)
    except Exception:
        # Fail silently - validation logging should never affect trading
        pass
