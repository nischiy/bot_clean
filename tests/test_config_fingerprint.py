"""
Tests for configuration fingerprint mechanism.

Ensures identical config produces identical hash.
"""
import pytest
import os
from unittest.mock import patch
from core.config.fingerprint import (
    compute_config_fingerprint,
    initialize_config_fingerprint,
    verify_config_unchanged,
    get_runtime_fingerprint,
)


class TestConfigFingerprint:
    """Test config fingerprint determinism and governance."""
    
    def test_identical_config_produces_identical_hash(self):
        """Test that identical config values produce identical hash."""
        # Compute fingerprint twice with same config
        fp1 = compute_config_fingerprint()
        fp2 = compute_config_fingerprint()
        
        assert fp1 == fp2, "Identical config must produce identical hash"
        assert len(fp1) == 64, "SHA256 hash must be 64 hex characters"
    
    def test_config_fingerprint_initialization(self):
        """Test config fingerprint initialization."""
        # Reset runtime fingerprint
        from core.config.fingerprint import _RUNTIME_FINGERPRINT
        import core.config.fingerprint as fp_module
        fp_module._RUNTIME_FINGERPRINT = None
        
        fp = initialize_config_fingerprint()
        assert fp is not None
        assert len(fp) == 64
        
        # Verify stored
        stored = get_runtime_fingerprint()
        assert stored == fp
    
    def test_config_fingerprint_verification_unchanged(self):
        """Test config fingerprint verification when unchanged."""
        # Reset and initialize
        import core.config.fingerprint as fp_module
        fp_module._RUNTIME_FINGERPRINT = None
        initialize_config_fingerprint()
        
        # Verify unchanged
        is_valid, error = verify_config_unchanged()
        assert is_valid, f"Config should be valid: {error}"
        assert error == ""
    
    def test_config_fingerprint_verification_changed(self):
        """Test config fingerprint detects config changes."""
        # Reset and initialize
        import core.config.fingerprint as fp_module
        fp_module._RUNTIME_FINGERPRINT = None
        initialize_config_fingerprint()
        
        # Change a config value
        original_value = os.environ.get("MIN_RR", "1.5")
        try:
            os.environ["MIN_RR"] = "2.0"
            
            # Verify detects change
            is_valid, error = verify_config_unchanged()
            assert not is_valid, "Config change should be detected"
            assert "config_changed" in error
        finally:
            # Restore original
            if original_value:
                os.environ["MIN_RR"] = original_value
            else:
                os.environ.pop("MIN_RR", None)
    
    def test_config_fingerprint_includes_all_strategy_keys(self):
        """Test that fingerprint includes all strategy/risk config keys."""
        from core.config.fingerprint import STRATEGY_RISK_CONFIG_KEYS
        
        # Verify critical keys are included
        critical_keys = {
            "MIN_RR",
            "RISK_PER_TRADE_PCT",
            "RISK_MAX_DD_PCT_DAY",
            "BREAKOUT_RR_TARGET",
            "CONTINUATION_RR_TARGET",
            "STABILITY_HARD",
            "STABILITY_SOFT",
        }
        
        for key in critical_keys:
            assert key in STRATEGY_RISK_CONFIG_KEYS, f"Critical key {key} must be in fingerprint"
    
    def test_config_fingerprint_excludes_non_trading_keys(self):
        """Test that fingerprint excludes non-trading config keys."""
        from core.config.fingerprint import STRATEGY_RISK_CONFIG_KEYS
        
        # Non-trading keys should be excluded
        excluded_keys = {
            "LOG_DIR",
            "LOG_LEVEL",
            "STATE_DIR",
            "LEDGER_DIR",
            "BINANCE_API_KEY",  # Secret
            "BINANCE_API_SECRET",  # Secret
        }
        
        for key in excluded_keys:
            assert key not in STRATEGY_RISK_CONFIG_KEYS, f"Non-trading key {key} should be excluded"
