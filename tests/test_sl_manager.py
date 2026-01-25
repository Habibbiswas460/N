"""
Unit Tests for SL Manager v1.1

Tests the structure-based trailing stop loss logic:
- Initial SL at entry - 10 points
- Breakeven at +8 points profit
- Structure-based TSL with HL tracking
- Tight trail at +20 points
- SL Breath rule
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from execution.sl_manager import (
    StopLossManager, SLStatus, TSLPhase, SLState, SwingLow
)


class MockOrderManager:
    """Mock order manager for testing."""
    
    def __init__(self):
        self.orders = {}
        self.order_counter = 0
        
    def place_sl_order(self, symbol, token, exchange, quantity, trigger_price):
        self.order_counter += 1
        order_id = f"SL_{self.order_counter}"
        self.orders[order_id] = {
            "trigger_price": trigger_price,
            "status": "open"
        }
        return Mock(success=True, order_id=order_id)
    
    def modify_order(self, order_id, new_trigger_price, new_price):
        if order_id in self.orders:
            self.orders[order_id]["trigger_price"] = new_trigger_price
            return Mock(success=True)
        return Mock(success=False, message="Order not found")
    
    def cancel_order(self, order_id):
        if order_id in self.orders:
            self.orders[order_id]["status"] = "cancelled"
            return Mock(success=True)
        return Mock(success=False, message="Order not found")
    
    def get_order_status(self, order_id):
        return Mock(OPEN="open")  # Never complete in tests


class MockStateStore:
    """Mock state store for testing."""
    
    def update_trade_sl(self, **kwargs):
        pass


class MockCandle:
    """Mock candle for testing."""
    
    def __init__(self, open_=100, high=105, low=95, close=102, timestamp=None):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.timestamp = timestamp or datetime.now()


class TestSLManagerInitialization:
    """Test SL manager initialization."""
    
    def test_default_config(self):
        """Test default configuration values."""
        order_manager = MockOrderManager()
        state_store = MockStateStore()
        
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=state_store
        )
        
        assert sl_manager.initial_sl_points == 10.0
        assert sl_manager.breakeven_trigger_points == 8.0
        assert sl_manager.tsl_buffer == 2.5
        assert sl_manager.tight_trigger_points == 20.0
        assert sl_manager.tight_buffer == 1.5
        assert sl_manager.enable_breath_rule == True
    
    def test_custom_config(self):
        """Test custom configuration."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore(),
            initial_sl_points=15.0,
            breakeven_trigger_points=10.0,
            tsl_buffer=3.0
        )
        
        assert sl_manager.initial_sl_points == 15.0
        assert sl_manager.breakeven_trigger_points == 10.0
        assert sl_manager.tsl_buffer == 3.0


class TestSLInitialization:
    """Test SL order initialization."""
    
    def test_initialize_sl(self):
        """Test SL initialization creates correct state."""
        order_manager = MockOrderManager()
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=MockStateStore()
        )
        
        order_id = sl_manager.initialize_sl(
            symbol="NIFTY25CE",
            token="12345",
            exchange="NFO",
            quantity=260,
            entry_price=100.0
        )
        
        assert order_id is not None
        assert sl_manager.state is not None
        assert sl_manager.state.entry_price == 100.0
        assert sl_manager.state.initial_sl == 90.0  # 100 - 10
        assert sl_manager.state.current_sl == 90.0
        assert sl_manager.state.status == SLStatus.INITIAL
        assert sl_manager.state.tsl_phase == TSLPhase.PHASE_1_INITIAL
    
    def test_initialize_with_swing_lows(self):
        """Test SL initialization with pre-existing swing lows."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        initial_hls = [
            SwingLow(price=95.0, timestamp=datetime.now(), candle_idx=1),
            SwingLow(price=97.0, timestamp=datetime.now(), candle_idx=5)
        ]
        
        sl_manager.initialize_sl(
            symbol="NIFTY25CE",
            token="12345",
            exchange="NFO",
            quantity=260,
            entry_price=100.0,
            initial_swing_lows=initial_hls
        )
        
        assert len(sl_manager.state.swing_lows) == 2
        assert sl_manager.state.swing_lows[0].price == 95.0


class TestBreakeven:
    """Test breakeven logic."""
    
    def test_breakeven_not_triggered_below_threshold(self):
        """Test BE not triggered when profit < 8 points."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Price at +7 points (below 8pt threshold)
        result = sl_manager.check_breakeven(107.0)
        
        assert result == False
        assert sl_manager.state.breakeven_hit == False
        assert sl_manager.state.current_sl == 90.0  # Unchanged
    
    def test_breakeven_triggered_at_threshold(self):
        """Test BE triggered when profit >= 8 points."""
        order_manager = MockOrderManager()
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Price at +8 points (at threshold)
        result = sl_manager.check_breakeven(108.0)
        
        assert result == True
        assert sl_manager.state.breakeven_hit == True
        assert sl_manager.state.current_sl == 100.0  # Moved to entry
        assert sl_manager.state.status == SLStatus.BREAKEVEN
    
    def test_breakeven_only_triggers_once(self):
        """Test BE only triggers once."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # First trigger
        sl_manager.check_breakeven(110.0)
        assert sl_manager.state.breakeven_hit == True
        
        # Second call should return False
        result = sl_manager.check_breakeven(115.0)
        assert result == False


class TestStructureTSL:
    """Test structure-based TSL logic."""
    
    def test_structure_tsl_activation(self):
        """Test TSL activates after 2+ swing lows."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Add first swing low - not enough
        sl_manager.add_swing_low(97.0, datetime.now())
        assert sl_manager.state.tsl_phase == TSLPhase.PHASE_1_INITIAL
        
        # Add second swing low - should activate
        sl_manager.add_swing_low(98.0, datetime.now())
        assert sl_manager.state.tsl_phase == TSLPhase.PHASE_3_STRUCTURE
        assert sl_manager.state.status == SLStatus.STRUCTURE_TSL
    
    def test_structure_trail_uses_hl_minus_buffer(self):
        """Test TSL trails to HL[-2] minus buffer."""
        order_manager = MockOrderManager()
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Add swing lows - higher values so trail can work after breakeven
        sl_manager.add_swing_low(100.0, datetime.now())  # HL1
        sl_manager.add_swing_low(102.0, datetime.now())  # HL2 
        sl_manager.add_swing_low(104.0, datetime.now())  # HL3
        
        # Trigger breakeven first (SL moves to 100.0)
        sl_manager.check_breakeven(110.0)
        
        # Trail should use HL[-2] = 102.0 - 2.5 buffer = 99.5
        # But 99.5 < 100.0 (current breakeven SL), so no trail yet
        result = sl_manager.trail_structure_based(112.0)
        
        # Since 99.5 < 100.0, trail returns False (SL stays at breakeven)
        assert result == False
        assert sl_manager.state.current_sl == 100.0  # Breakeven level
        
        # Add another higher HL
        sl_manager.add_swing_low(106.0, datetime.now())  # HL4
        
        # Now HL[-2] = 104.0 - 2.5 = 101.5 > 100.0
        result = sl_manager.trail_structure_based(114.0)
        assert result == True
        assert sl_manager.state.current_sl == 101.5
    
    def test_trail_only_moves_up(self):
        """Test SL only trails up, never down."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        sl_manager.add_swing_low(97.0, datetime.now())
        sl_manager.add_swing_low(99.0, datetime.now())
        sl_manager.check_breakeven(110.0)
        
        # First trail
        sl_manager.trail_structure_based(112.0)
        first_sl = sl_manager.state.current_sl
        
        # Add lower swing low
        sl_manager.add_swing_low(96.0, datetime.now())
        
        # Trail again - should not move down
        sl_manager.trail_structure_based(115.0)
        
        assert sl_manager.state.current_sl >= first_sl


class TestTightTrail:
    """Test tight trail activation."""
    
    def test_tight_trail_activates_at_20_points(self):
        """Test tight trail activates at +20 points profit."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        sl_manager.add_swing_low(95.0, datetime.now())
        sl_manager.add_swing_low(97.0, datetime.now())
        
        # At +15 points - still structure phase
        sl_manager.trail_structure_based(115.0)
        assert sl_manager.state.tsl_phase == TSLPhase.PHASE_3_STRUCTURE
        
        # At +20 points - should switch to tight
        sl_manager.trail_structure_based(120.0)
        assert sl_manager.state.tsl_phase == TSLPhase.PHASE_4_TIGHT
        assert sl_manager.state.status == SLStatus.TIGHT_TRAIL
    
    def test_tight_trail_uses_smaller_buffer(self):
        """Test tight trail uses 1.5pt buffer instead of 2.5pt."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        sl_manager.add_swing_low(105.0, datetime.now())
        sl_manager.add_swing_low(110.0, datetime.now())  # HL to use
        sl_manager.add_swing_low(115.0, datetime.now())
        
        # Activate tight trail
        sl_manager.trail_structure_based(125.0)
        
        # Should use HL[-2]=110 - 1.5 = 108.5
        assert sl_manager.state.current_sl == 108.5


class TestSLBreathRule:
    """Test SL breath rule."""
    
    def test_breath_allows_one_breach(self):
        """Test breath rule allows one candle to breach SL."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore(),
            enable_breath_rule=True
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Create candle that breaches SL but closes above
        candle = MockCandle(
            open_=92.0,
            high=94.0,
            low=88.0,  # Below SL of 90
            close=91.0  # Closes above SL
        )
        
        # Check trigger with breath rule
        triggered, reason = sl_manager.check_sl_triggered(88.0, candle)
        
        assert triggered == False
        assert sl_manager.state.breath_used == True
    
    def test_breath_only_once(self):
        """Test breath can only be used once."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore(),
            enable_breath_rule=True
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # First breach with recovery
        candle1 = MockCandle(low=88.0, close=91.0)
        sl_manager.check_sl_triggered(88.0, candle1)
        assert sl_manager.state.breath_used == True
        
        # Second breach - should trigger SL
        candle2 = MockCandle(low=87.0, close=89.0)
        triggered, reason = sl_manager.check_sl_triggered(87.0, candle2)
        
        assert triggered == True
        assert reason == "sl_hit_after_breath"
    
    def test_breath_disabled(self):
        """Test SL triggers immediately when breath disabled."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore(),
            enable_breath_rule=False
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        candle = MockCandle(low=88.0, close=91.0)
        triggered, reason = sl_manager.check_sl_triggered(88.0, candle)
        
        assert triggered == True
        assert sl_manager.state.breath_used == False


class TestUpdateOnTick:
    """Test the update_on_tick method."""
    
    def test_full_lifecycle(self):
        """Test full SL lifecycle from initial to triggered."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Phase 1: Initial
        status, _ = sl_manager.update_on_tick(102.0)
        assert status == SLStatus.INITIAL
        
        # Add swing lows
        sl_manager.update_on_tick(
            105.0, 
            new_swing_low=97.0, 
            swing_low_time=datetime.now()
        )
        sl_manager.update_on_tick(
            106.0,
            new_swing_low=99.0,
            swing_low_time=datetime.now()
        )
        
        # Phase 2: Breakeven
        status, _ = sl_manager.update_on_tick(108.0)
        assert sl_manager.state.breakeven_hit == True
        
        # Phase 3: Structure TSL
        status, _ = sl_manager.update_on_tick(115.0)
        assert status == SLStatus.STRUCTURE_TSL
        
        # Phase 4: Tight trail
        status, _ = sl_manager.update_on_tick(125.0)
        assert status == SLStatus.TIGHT_TRAIL
        
        # Triggered
        status, reason = sl_manager.update_on_tick(90.0)
        assert status == SLStatus.TRIGGERED


class TestProperties:
    """Test SL manager properties."""
    
    def test_is_active(self):
        """Test is_active property."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        assert sl_manager.is_active == False
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        assert sl_manager.is_active == True
    
    def test_is_trailing(self):
        """Test is_trailing property."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        assert sl_manager.is_trailing == False
        
        # Activate structure TSL
        sl_manager.add_swing_low(97.0, datetime.now())
        sl_manager.add_swing_low(99.0, datetime.now())
        
        assert sl_manager.is_trailing == True
    
    def test_get_status_summary(self):
        """Test status summary."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        summary = sl_manager.get_status_summary()
        
        assert summary["active"] == True
        assert summary["entry_price"] == 100.0
        assert summary["current_sl"] == 90.0
        assert summary["status"] == "initial"
        assert summary["phase"] == "initial"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
