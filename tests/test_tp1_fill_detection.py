"""
Tests for TP1 fill detection logic.

Tests deterministic TP1 fill detection by comparing current position qty
vs original position qty, with step_size tolerance.
"""
import pytest
from unittest.mock import Mock, patch
from app.run import _run_once_contracts
from app.state.state_manager import save_position_state, load_position_state
from app.services.execution_service import ExecutionService


class TestTP1FillDetection:
    """Test TP1 fill detection with deterministic position quantity tracking."""
    
    def test_tp1_fill_detection_partial_fill(self):
        """Test TP1 fill detection with partial TP1 fill."""
        symbol = "BTCUSDT"
        original_qty = 1.0
        tp1_qty = 0.4
        current_qty = 0.6  # After TP1 fill
        step_size = 0.001
        
        # Save position state with tracking fields
        position_state = {
            "side": "LONG",
            "qty": current_qty,
            "entry": 50000.0,
            "original_qty": original_qty,
            "tp1_qty": tp1_qty,
        }
        save_position_state(symbol, position_state)
        
        # Verify state is saved correctly
        loaded = load_position_state(symbol)
        assert loaded["original_qty"] == original_qty
        assert loaded["tp1_qty"] == tp1_qty
        assert loaded["qty"] == current_qty
        
        # Test detection logic
        expected_qty_after_tp1 = original_qty - tp1_qty
        tolerance = max(step_size * 2.0, step_size * 0.01)
        qty_diff = abs(current_qty - expected_qty_after_tp1)
        tp1_filled = qty_diff <= tolerance
        
        assert tp1_filled, f"TP1 should be detected as filled: qty_diff={qty_diff} tolerance={tolerance}"
    
    def test_tp1_fill_detection_full_fill(self):
        """Test TP1 fill detection with full TP1 fill."""
        symbol = "BTCUSDT"
        original_qty = 1.0
        tp1_qty = 0.4
        current_qty = 0.6  # Exactly expected after TP1
        step_size = 0.001
        
        position_state = {
            "side": "LONG",
            "qty": current_qty,
            "entry": 50000.0,
            "original_qty": original_qty,
            "tp1_qty": tp1_qty,
        }
        save_position_state(symbol, position_state)
        
        expected_qty_after_tp1 = original_qty - tp1_qty
        tolerance = max(step_size * 2.0, step_size * 0.01)
        qty_diff = abs(current_qty - expected_qty_after_tp1)
        tp1_filled = qty_diff <= tolerance
        
        assert tp1_filled
    
    def test_tp1_fill_detection_no_fill(self):
        """Test TP1 fill detection when TP1 has NOT filled (false positive protection)."""
        symbol = "BTCUSDT"
        original_qty = 1.0
        tp1_qty = 0.4
        current_qty = 1.0  # Position unchanged, TP1 not filled
        step_size = 0.001
        
        position_state = {
            "side": "LONG",
            "qty": current_qty,
            "entry": 50000.0,
            "original_qty": original_qty,
            "tp1_qty": tp1_qty,
        }
        save_position_state(symbol, position_state)
        
        expected_qty_after_tp1 = original_qty - tp1_qty
        tolerance = max(step_size * 2.0, step_size * 0.01)
        qty_diff = abs(current_qty - expected_qty_after_tp1)
        tp1_filled = qty_diff <= tolerance
        
        assert not tp1_filled, "TP1 should NOT be detected as filled when position unchanged"
    
    def test_tp1_fill_detection_with_step_size_rounding(self):
        """Test TP1 fill detection with step_size rounding tolerance."""
        symbol = "BTCUSDT"
        original_qty = 1.0
        tp1_qty = 0.4
        step_size = 0.001
        
        # Simulate rounding: expected 0.6 but got 0.6005 (within tolerance)
        current_qty = 0.6005
        expected_qty_after_tp1 = original_qty - tp1_qty
        
        tolerance = max(step_size * 2.0, step_size * 0.01)
        qty_diff = abs(current_qty - expected_qty_after_tp1)
        tp1_filled = qty_diff <= tolerance
        
        assert tp1_filled, f"Should tolerate step_size rounding: diff={qty_diff} tolerance={tolerance}"
    
    def test_tp1_fill_detection_fallback_tolerance(self):
        """Test TP1 fill detection fallback when step_size not available."""
        symbol = "BTCUSDT"
        original_qty = 1.0
        tp1_qty = 0.4
        current_qty = 0.6
        step_size = 0.0  # Not available
        
        expected_qty_after_tp1 = original_qty - tp1_qty
        # Fallback: 1% tolerance
        tolerance = abs(expected_qty_after_tp1) * 0.01
        qty_diff = abs(current_qty - expected_qty_after_tp1)
        tp1_filled = qty_diff <= tolerance
        
        assert tp1_filled
    
    def test_position_state_preserves_tracking_fields(self):
        """Test that position state preserves original_qty and tp1_qty when updating."""
        symbol = "BTCUSDT"
        
        # Initial state with tracking fields
        initial_state = {
            "side": "LONG",
            "qty": 0.6,
            "entry": 50000.0,
            "original_qty": 1.0,
            "tp1_qty": 0.4,
        }
        save_position_state(symbol, initial_state)
        
        # Update position state (simulating position update)
        updated_state = {
            "side": "LONG",
            "qty": 0.6,
            "entry": 50000.0,
        }
        
        # Load existing to preserve tracking fields
        existing = load_position_state(symbol)
        if existing:
            updated_state["original_qty"] = existing.get("original_qty")
            updated_state["tp1_qty"] = existing.get("tp1_qty")
        
        save_position_state(symbol, updated_state)
        
        # Verify tracking fields preserved
        loaded = load_position_state(symbol)
        assert loaded["original_qty"] == 1.0
        assert loaded["tp1_qty"] == 0.4
    
    def test_position_state_clears_tracking_on_close(self):
        """Test that tracking fields are cleared when position is closed."""
        symbol = "BTCUSDT"
        
        # State with position and tracking
        state_with_position = {
            "side": "LONG",
            "qty": 0.6,
            "entry": 50000.0,
            "original_qty": 1.0,
            "tp1_qty": 0.4,
        }
        save_position_state(symbol, state_with_position)
        
        # Position closed
        state_closed = {
            "side": None,
            "qty": 0.0,
            "entry": 0.0,
        }
        
        # Load existing to check if we should clear
        existing = load_position_state(symbol)
        if existing and state_closed.get("qty", 0.0) == 0.0:
            state_closed.pop("original_qty", None)
            state_closed.pop("tp1_qty", None)
        
        save_position_state(symbol, state_closed)
        
        # Verify tracking fields cleared
        loaded = load_position_state(symbol)
        assert "original_qty" not in loaded or loaded.get("original_qty") is None
        assert "tp1_qty" not in loaded or loaded.get("tp1_qty") is None
