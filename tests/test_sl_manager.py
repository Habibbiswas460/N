"""
Unit Tests for SL Manager v2.0 Sniper Mode

Tests the v2.0 Sniper Mode trailing stop loss logic:
- Initial SL at entry - 5 points (tight)
- Safe Mode at +7 points → SL = Entry + 1pt
- Trail Mode at +10 points → TSL = High - 5pt
- Structure-based TSL with HL tracking (legacy)
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
        """Test default configuration values (v2.0 Sniper Mode)."""
        order_manager = MockOrderManager()
        state_store = MockStateStore()
        
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=state_store
        )
        
        # v2.0 Sniper Mode defaults
        assert sl_manager.initial_sl_points == 5.0  # v2.0: 5pt (was 10)
        assert sl_manager.safe_mode_trigger == 7.0  # v2.0: Safe at +7pt
        assert sl_manager.safe_mode_buffer == 1.0   # v2.0: Entry + 1pt
        assert sl_manager.trail_mode_trigger == 10.0  # v2.0: Trail at +10pt
        assert sl_manager.trail_mode_buffer == 5.0    # v2.0: High - 5pt
        assert sl_manager.enable_sniper_mode == True
        assert sl_manager.tsl_buffer == 2.5
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
        """Test SL initialization creates correct state (v2.0: 5pt SL)."""
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
        # v2.0: SL = Entry - 5pt
        assert sl_manager.state.initial_sl == 95.0  # 100 - 5
        assert sl_manager.state.current_sl == 95.0
        assert sl_manager.state.entry_price == 100.0
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
        """Test Safe Mode not triggered below +7pt profit."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Price at +5 points (below 7pt Safe Mode threshold)
        result = sl_manager.check_breakeven(105.0)
        
        assert result == False
        assert sl_manager.state.breakeven_hit == False
        assert sl_manager.state.current_sl == 95.0  # Still at initial (5pt SL)
    
    def test_breakeven_triggered_at_threshold(self):
        """Test Safe Mode triggers at +7pt profit (SL = Entry + 1pt)."""
        order_manager = MockOrderManager()
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Price at +7 points (at Safe Mode threshold)
        result = sl_manager.check_breakeven(107.0)
        
        assert result == True
        assert sl_manager.state.breakeven_hit == True
        assert sl_manager.state.current_sl == 101.0  # Moved to Entry + 1pt (Safe Mode)
        assert sl_manager.state.status == SLStatus.SAFE_MODE
    
    def test_breakeven_only_triggers_once(self):
        """Test Safe Mode/Trail Mode only triggers once (v2.0 Sniper Mode)."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # At +10pt, Trail Mode activates (status changes to TRAIL_MODE)
        # Trail Mode sets SL = High - 5pt = 105.0
        sl_manager.check_breakeven(110.0)
        assert sl_manager.state.status == SLStatus.TRAIL_MODE
        assert sl_manager.state.current_sl == 105.0  # 110 - 5pt
        
        # Second call at higher price - SL should trail higher
        result = sl_manager.check_breakeven(115.0)
        assert sl_manager.state.current_sl == 110.0  # 115 - 5pt


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
        """Test TSL trails to HL[-2] minus buffer (uses second-last for room)."""
        order_manager = MockOrderManager()
        sl_manager = StopLossManager(
            order_manager=order_manager,
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Add 2 swing lows - uses HL[-1] when only 2 HLs
        sl_manager.add_swing_low(100.0, datetime.now())  # HL1
        sl_manager.add_swing_low(102.0, datetime.now())  # HL2 - activates structure TSL
        
        # At +6pt profit, structure TSL trails
        # With 2 HLs, uses HL[-1] = 102.0 - 2.5 = 99.5
        sl_manager.trail_structure_based(106.0)
        assert sl_manager.state.status == SLStatus.STRUCTURE_TSL
        assert sl_manager.state.current_sl == 99.5
        
        # Add 3rd HL - now uses HL[-2] for more breathing room
        sl_manager.add_swing_low(104.0, datetime.now())  # HL3
        
        # With 3 HLs, uses HL[-2] = 102.0 - 2.5 = 99.5 (same as before)
        # No trail because 99.5 <= current 99.5
        sl_manager.trail_structure_based(108.0)
        assert sl_manager.state.current_sl == 99.5
        
        # Add 4th higher HL
        sl_manager.add_swing_low(106.0, datetime.now())  # HL4
        
        # Now HL[-2] = 104.0 - 2.5 = 101.5 > 99.5, should trail
        sl_manager.trail_structure_based(109.0)
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
            open_=96.0,
            high=98.0,
            low=93.0,  # Below SL of 95
            close=96.0  # Closes above SL
        )
        
        # Check trigger with breath rule
        triggered, reason = sl_manager.check_sl_triggered(93.0, candle)
        
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
        candle1 = MockCandle(low=93.0, close=96.0)  # Below SL of 95, closes above
        sl_manager.check_sl_triggered(93.0, candle1)
        assert sl_manager.state.breath_used == True
        
        # Second breach - should trigger SL
        candle2 = MockCandle(low=92.0, close=94.0)  # Below SL again
        triggered, reason = sl_manager.check_sl_triggered(92.0, candle2)
        
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
        
        candle = MockCandle(low=93.0, close=96.0)  # Below SL of 95
        triggered, reason = sl_manager.check_sl_triggered(93.0, candle)
        
        assert triggered == True
        assert sl_manager.state.breath_used == False


class TestUpdateOnTick:
    """Test the update_on_tick method."""
    
    def test_full_lifecycle(self):
        """Test full SL lifecycle from initial to triggered (v2.0 Sniper Mode)."""
        sl_manager = StopLossManager(
            order_manager=MockOrderManager(),
            state_store=MockStateStore()
        )
        
        sl_manager.initialize_sl(
            symbol="TEST", token="123", exchange="NFO",
            quantity=260, entry_price=100.0
        )
        
        # Phase 1: Initial (below +7pt Safe Mode threshold)
        status, _ = sl_manager.update_on_tick(102.0)
        assert status == SLStatus.INITIAL
        
        # Add swing lows - this activates Structure TSL
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
        
        # With 2+ HLs, Structure TSL is active
        # At +7pt, structure trail kicks in (HL[-1] - buffer)
        status, _ = sl_manager.update_on_tick(107.0)
        assert status == SLStatus.STRUCTURE_TSL
        
        # At +10pt with no higher HLs, Trail Mode takes over
        status, _ = sl_manager.update_on_tick(110.0)
        # Structure TSL continues as we have HLs
        assert status in [SLStatus.STRUCTURE_TSL, SLStatus.TRAIL_MODE]
        
        # At +15pt, Trail Mode dominates (High - 5pt trail is more aggressive)
        status, _ = sl_manager.update_on_tick(115.0)
        assert status in [SLStatus.STRUCTURE_TSL, SLStatus.TRAIL_MODE]
        
        # At +20pt, Trail Mode continues (Sniper Mode doesn't use Tight Trail)
        status, _ = sl_manager.update_on_tick(120.0)
        assert status in [SLStatus.TRAIL_MODE, SLStatus.TIGHT_TRAIL]
        
        # Triggered - price drops below current SL
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
        assert summary["current_sl"] == 95.0  # 5pt SL in v2.0
        assert summary["status"] == "initial"
        assert summary["phase"] == "initial"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
