"""
Unit Tests for State Machine v1.2

Tests the FSM with re-entry support:
- All trading states
- State transitions
- Re-entry state handling
- Context persistence
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.state_machine import (
    TradingStateMachine, TradingState, StateContext
)


class MockStateStore:
    """Mock state store for testing."""
    
    def __init__(self):
        self.fsm_state = None
        self.n_structure = None
    
    def get_fsm_state(self):
        return self.fsm_state
    
    def save_fsm_state(self, state, data):
        self.fsm_state = {"state": state, "data": data}
    
    def clear_n_structure(self):
        self.n_structure = None


class TestStateContext:
    """Test StateContext dataclass."""
    
    def test_default_values(self):
        """Test default context values."""
        ctx = StateContext()
        
        assert ctx.index_price == 0.0
        assert ctx.option_price == 0.0
        assert ctx.entry_price == 0.0
        assert ctx.sl_exit_price == 0.0
        assert ctx.reentry_count == 0
        assert ctx.is_reentry_trade == False
    
    def test_to_dict(self):
        """Test context serialization."""
        ctx = StateContext(
            entry_price=100.0,
            current_sl=90.0,
            sl_exit_price=88.0,
            reentry_count=1
        )
        
        data = ctx.to_dict()
        
        assert data["entry_price"] == 100.0
        assert data["current_sl"] == 90.0
        assert data["sl_exit_price"] == 88.0
        assert data["reentry_count"] == 1
    
    def test_from_dict(self):
        """Test context deserialization."""
        data = {
            "entry_price": 100.0,
            "current_sl": 90.0,
            "sl_exit_price": 88.0,
            "reentry_count": 1,
            "is_reentry_trade": True
        }
        
        ctx = StateContext.from_dict(data)
        
        assert ctx.entry_price == 100.0
        assert ctx.current_sl == 90.0
        assert ctx.sl_exit_price == 88.0
        assert ctx.reentry_count == 1
        assert ctx.is_reentry_trade == True


class TestStateTransitions:
    """Test valid state transitions."""
    
    def test_idle_to_watching(self):
        """Test IDLE -> WATCHING_BREAKOUT."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        result = fsm.on_breakout_detected(100.0)
        
        assert result == True
        assert fsm.state == TradingState.WATCHING_BREAKOUT
    
    def test_armed_to_in_position(self):
        """Test ARMED -> IN_POSITION."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        # Navigate to ARMED state
        fsm.transition_to(TradingState.ARMED, force=True)
        
        result = fsm.on_entry_triggered(100.0, 90.0)
        
        assert result == True
        assert fsm.state == TradingState.IN_POSITION
        assert fsm.context.entry_price == 100.0
        assert fsm.context.current_sl == 90.0
    
    def test_in_position_to_pending_reentry(self):
        """Test IN_POSITION -> PENDING_REENTRY on SL hit."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        # Navigate to IN_POSITION
        fsm.transition_to(TradingState.IN_POSITION, force=True)
        fsm.context.entry_price = 100.0
        
        result = fsm.on_sl_hit(90.0, can_reenter=True, max_reentries=2)
        
        assert result == True
        assert fsm.state == TradingState.PENDING_REENTRY
        assert fsm.context.sl_exit_price == 90.0
    
    def test_in_position_to_cooldown_no_reentry(self):
        """Test IN_POSITION -> COOLDOWN when re-entry disabled."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.IN_POSITION, force=True)
        fsm.context.entry_price = 100.0
        
        result = fsm.on_sl_hit(90.0, can_reenter=False)
        
        assert result == True
        assert fsm.state == TradingState.COOLDOWN
    
    def test_pending_reentry_to_armed(self):
        """Test PENDING_REENTRY -> ARMED on HH breakout."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.PENDING_REENTRY, force=True)
        fsm.context.sl_exit_price = 90.0
        
        result = fsm.on_reentry_hh_detected(95.0, 96.5)
        
        assert result == True
        assert fsm.state == TradingState.ARMED
        assert fsm.context.is_reentry_trade == True
        assert fsm.context.entry_trigger_price == 96.5
    
    def test_pending_reentry_to_cooldown_timeout(self):
        """Test PENDING_REENTRY -> COOLDOWN on timeout."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.PENDING_REENTRY, force=True)
        
        result = fsm.on_reentry_timeout(30)
        
        assert result == True
        assert fsm.state == TradingState.COOLDOWN


class TestReentryLogic:
    """Test re-entry specific logic."""
    
    def test_reentry_count_increment(self):
        """Test re-entry count increments on execution."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.ARMED, force=True)
        fsm.context.is_reentry_trade = True
        
        assert fsm.context.reentry_count == 0
        
        fsm.on_reentry_executed(100.0, 90.0)
        
        assert fsm.context.reentry_count == 1
    
    def test_hh_not_high_enough(self):
        """Test HH rejected if not high enough above SL exit."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.PENDING_REENTRY, force=True)
        fsm.context.sl_exit_price = 90.0
        
        # HH only 1 point above SL exit (need 2+)
        result = fsm.on_reentry_hh_detected(91.0, 92.5)
        
        assert result == False
        assert fsm.state == TradingState.PENDING_REENTRY
    
    def test_update_high_after_sl(self):
        """Test high tracking after SL hit."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.PENDING_REENTRY, force=True)
        
        fsm.update_high_after_sl(95.0)
        assert fsm.context.last_high_after_sl == 95.0
        
        fsm.update_high_after_sl(98.0)
        assert fsm.context.last_high_after_sl == 98.0
        
        # Lower high doesn't update
        fsm.update_high_after_sl(96.0)
        assert fsm.context.last_high_after_sl == 98.0


class TestProperties:
    """Test FSM properties."""
    
    def test_is_idle(self):
        """Test is_idle property."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        assert fsm.is_idle == True
        
        fsm.transition_to(TradingState.ARMED, force=True)
        assert fsm.is_idle == False
    
    def test_is_in_position(self):
        """Test is_in_position property."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        assert fsm.is_in_position == False
        
        fsm.transition_to(TradingState.IN_POSITION, force=True)
        assert fsm.is_in_position == True
    
    def test_is_pending_reentry(self):
        """Test is_pending_reentry property."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        assert fsm.is_pending_reentry == False
        
        fsm.transition_to(TradingState.PENDING_REENTRY, force=True)
        assert fsm.is_pending_reentry == True
    
    def test_is_reentry_trade(self):
        """Test is_reentry_trade property."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        assert fsm.is_reentry_trade == False
        
        fsm.context.is_reentry_trade = True
        assert fsm.is_reentry_trade == True
    
    def test_reentry_count(self):
        """Test reentry_count property."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        assert fsm.reentry_count == 0
        
        fsm.context.reentry_count = 2
        assert fsm.reentry_count == 2


class TestInvalidTransitions:
    """Test invalid state transitions."""
    
    def test_can_arm_from_idle_v2(self):
        """Test v2.0: CAN directly go to ARMED from IDLE (changed behavior)."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        # v2.0: IDLE→ARMED is now ALLOWED for quick setup
        result = fsm.transition_to(TradingState.ARMED)
        
        assert result == True  # v2.0: This is now allowed
        assert fsm.state == TradingState.ARMED
    
    def test_cannot_enter_from_cooldown(self):
        """Test cannot enter position from COOLDOWN."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.COOLDOWN, force=True)
        result = fsm.on_entry_triggered(100.0, 90.0)
        
        assert result == False


class TestPersistence:
    """Test state persistence."""
    
    def test_state_saved(self):
        """Test state is saved on transition."""
        store = MockStateStore()
        fsm = TradingStateMachine(state_store=store)
        
        fsm.on_breakout_detected(100.0)
        
        assert store.fsm_state is not None
        assert store.fsm_state["state"] == "WATCHING_BREAKOUT"
    
    def test_state_restored(self):
        """Test state is restored from store."""
        store = MockStateStore()
        store.fsm_state = {
            "state": "IN_POSITION",
            "data": {
                "entry_price": 100.0,
                "current_sl": 90.0
            }
        }
        
        fsm = TradingStateMachine(state_store=store)
        
        assert fsm.state == TradingState.IN_POSITION
        assert fsm.context.entry_price == 100.0


class TestCallbacks:
    """Test state transition callbacks."""
    
    def test_callback_called_on_transition(self):
        """Test callback is called on state transition."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        transitions = []
        fsm.add_callback(lambda old, new, ctx: transitions.append((old, new)))
        
        fsm.on_breakout_detected(100.0)
        
        assert len(transitions) == 1
        assert transitions[0] == (TradingState.IDLE, TradingState.WATCHING_BREAKOUT)
    
    def test_callback_removed(self):
        """Test callback can be removed."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        transitions = []
        callback = lambda old, new, ctx: transitions.append((old, new))
        
        fsm.add_callback(callback)
        fsm.on_breakout_detected(100.0)
        
        fsm.remove_callback(callback)
        fsm.on_pullback_started()
        
        assert len(transitions) == 1


class TestReset:
    """Test FSM reset."""
    
    def test_reset_to_idle(self):
        """Test full reset to IDLE."""
        fsm = TradingStateMachine(state_store=MockStateStore())
        
        fsm.transition_to(TradingState.IN_POSITION, force=True)
        fsm.context.entry_price = 100.0
        fsm.context.reentry_count = 2
        
        fsm.reset()
        
        assert fsm.state == TradingState.IDLE
        assert fsm.context.entry_price == 0.0
        assert fsm.context.reentry_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
