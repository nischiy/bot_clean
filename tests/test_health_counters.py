"""
Tests for health counters.

Ensures runtime counters work correctly.
"""
import pytest
import time
from core.health_counters import (
    HealthCounters,
    get_health_counters,
    reset_health_counters,
    emit_health_summary,
)


class TestHealthCounters:
    """Test health counter functionality."""
    
    def test_counter_increment(self):
        """Test counter increment."""
        counters = HealthCounters()
        counters.increment("candles_processed")
        counters.increment("decisions_made", 2)
        
        assert counters.get_counters()["candles_processed"] == 1
        assert counters.get_counters()["decisions_made"] == 2
    
    def test_counter_get_counters(self):
        """Test get_counters returns all counters."""
        counters = HealthCounters()
        all_counters = counters.get_counters()
        
        assert "candles_processed" in all_counters
        assert "decisions_made" in all_counters
        assert "decisions_rejected" in all_counters
        assert "risk_rejections" in all_counters
        assert "kill_switch_activations" in all_counters
    
    def test_should_emit_health_summary_first_call(self):
        """Test should_emit_health_summary on first call."""
        counters = HealthCounters()
        now_ts = int(time.time())
        
        # First call should return False (initializes timestamp)
        assert not counters.should_emit_health_summary(now_ts)
    
    def test_should_emit_health_summary_interval(self):
        """Test should_emit_health_summary respects interval."""
        counters = HealthCounters()
        now_ts = int(time.time())
        
        # Initialize
        counters.should_emit_health_summary(now_ts)
        
        # Before interval
        assert not counters.should_emit_health_summary(now_ts + 1800)  # 30 min
        
        # After interval (1 hour)
        assert counters.should_emit_health_summary(now_ts + 3600)
    
    def test_get_health_counters_singleton(self):
        """Test get_health_counters returns singleton."""
        reset_health_counters()
        counters1 = get_health_counters()
        counters2 = get_health_counters()
        
        assert counters1 is counters2
    
    def test_reset_health_counters(self):
        """Test reset_health_counters."""
        counters = get_health_counters()
        counters.increment("candles_processed", 5)
        
        reset_health_counters()
        
        assert counters.get_counters()["candles_processed"] == 0
