"""
Unit Tests for Risk Manager v1.2

Tests the simplified risk management with max SL only:
- Fixed 260 qty position sizing
- Max 3 SL hits per day (only limiter)
- Cooldown after trades
- Re-entry tracking
"""

import pytest
from datetime import datetime, date
from unittest.mock import Mock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from risk.risk_manager import (
    RiskManager, RiskLimits, RiskStatus, RiskEvent, TradeRecord,
    initialize_risk_manager
)


class MockStateStore:
    """Mock state store for testing."""
    
    def __init__(self):
        self.stats = {
            'total_pnl': 0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'sl_hits': 0,
            'reentries': 0
        }
        self._rm = None  # Reference to risk manager for syncing
    
    def set_risk_manager(self, rm):
        """Set reference to risk manager for syncing stats."""
        self._rm = rm
    
    def get_daily_stats(self):
        # Sync stats from risk manager if available
        if self._rm:
            self.stats['sl_hits'] = self._rm._status.sl_hits_today
            self.stats['reentries'] = self._rm._status.reentries_today
            self.stats['total_trades'] = self._rm._status.trades_today
            self.stats['total_pnl'] = self._rm._status.daily_pnl
        return self.stats
    
    def update_daily_stats(self, trade_pnl):
        self.stats['total_pnl'] += trade_pnl
        self.stats['total_trades'] += 1


def create_test_risk_manager(**kwargs):
    """Create a RiskManager with time filter disabled for testing."""
    limits_kwargs = {'enable_time_filter': False}
    limits_kwargs.update(kwargs)
    limits = RiskLimits(**limits_kwargs)
    store = MockStateStore()
    rm = RiskManager(limits=limits, state_store=store)
    store.set_risk_manager(rm)  # Link back for syncing
    return rm


class TestRiskLimits:
    """Test risk limits configuration."""
    
    def test_default_limits(self):
        """Test default risk limits."""
        limits = RiskLimits()
        
        assert limits.lot_size == 65
        assert limits.num_lots == 4
        assert limits.fixed_quantity == 260
        assert limits.sl_points == 10.0
        assert limits.risk_per_trade == 2600.0
        assert limits.max_sl_per_day == 3
        assert limits.max_reentries_per_day == 2
    
    def test_custom_limits(self):
        """Test custom risk limits."""
        limits = RiskLimits(
            lot_size=50,
            num_lots=2,
            fixed_quantity=100,
            max_sl_per_day=5
        )
        
        assert limits.lot_size == 50
        assert limits.num_lots == 2
        assert limits.fixed_quantity == 100
        assert limits.max_sl_per_day == 5


class TestRiskManagerInitialization:
    """Test risk manager initialization."""
    
    def test_default_initialization(self):
        """Test default initialization."""
        rm = create_test_risk_manager()
        
        assert rm.limits.fixed_quantity == 260
        assert rm.limits.max_sl_per_day == 3
        assert rm.can_trade == True
    
    def test_initialize_function(self):
        """Test initialize_risk_manager function."""
        rm = initialize_risk_manager(
            lot_size=50,
            num_lots=3,
            sl_points=15.0,
            max_sl_per_day=4
        )
        # Disable time filter for testing
        rm.limits.enable_time_filter = False
        rm._evaluate_can_trade()
        
        assert rm.limits.lot_size == 50
        assert rm.limits.num_lots == 3
        assert rm.limits.fixed_quantity == 150
        assert rm.limits.sl_points == 15.0
        assert rm.limits.risk_per_trade == 15.0 * 150  # 2250


class TestCanTrade:
    """Test trading permission logic."""
    
    def test_can_trade_initially(self):
        """Test can trade at start of day."""
        rm = create_test_risk_manager()
        
        can_trade, reason = rm.can_enter_trade()
        
        assert can_trade == True
        assert reason == "OK"
    
    def test_blocked_after_max_sl(self):
        """Test trading blocked after max SL hits."""
        rm = create_test_risk_manager()
        
        # Hit 3 SL
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        can_trade, reason = rm.can_enter_trade()
        
        assert can_trade == False
        assert "Max SL hits reached" in reason
    
    def test_unlimited_profitable_trades(self):
        """Test unlimited trades when profitable."""
        rm = create_test_risk_manager()
        
        # 10 profitable trades
        for i in range(10):
            rm.record_trade(5000, 20.0, "tsl_exit")
            rm.tick_cooldown()  # Clear cooldown
            for _ in range(20):  # Wait cooldown
                rm.tick_cooldown()
        
        can_trade, reason = rm.can_enter_trade()
        
        assert can_trade == True
        assert rm.status.trades_today == 10


class TestRecordTrade:
    """Test trade recording."""
    
    def test_record_winning_trade(self):
        """Test recording a winning trade."""
        rm = create_test_risk_manager()
        
        status = rm.record_trade(
            pnl=5000.0,
            pnl_points=20.0,
            exit_reason="tsl_exit",
            entry_price=100.0,
            exit_price=120.0
        )
        
        assert status.trades_today == 1
        assert status.wins_today == 1
        assert status.losses_today == 0
        assert status.daily_pnl == 5000.0
        assert status.sl_hits_today == 0
    
    def test_record_sl_hit(self):
        """Test recording an SL hit."""
        rm = create_test_risk_manager()
        
        status = rm.record_trade(
            pnl=-2600.0,
            pnl_points=-10.0,
            exit_reason="sl_hit"
        )
        
        assert status.losses_today == 1
        assert status.sl_hits_today == 1
        assert status.daily_pnl == -2600.0
    
    def test_record_reentry(self):
        """Test recording a re-entry trade."""
        rm = create_test_risk_manager()
        
        # First trade SL hit
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        # Re-entry trade
        status = rm.record_trade(
            pnl=3000.0,
            pnl_points=12.0,
            exit_reason="tsl_exit",
            is_reentry=True
        )
        
        assert status.reentries_today == 1
        assert status.sl_hits_today == 1


class TestCooldown:
    """Test cooldown logic."""
    
    def test_cooldown_after_trade(self):
        """Test cooldown starts after trade."""
        rm = create_test_risk_manager()
        
        rm.record_trade(5000, 20.0, "tsl_exit")
        
        assert rm.in_cooldown == True
        assert rm.status.cooldown_candles_remaining == 15  # Normal cooldown
    
    def test_longer_cooldown_after_sl(self):
        """Test longer cooldown after SL hit."""
        rm = create_test_risk_manager()
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        assert rm.in_cooldown == True
        assert rm.status.cooldown_candles_remaining == 30  # SL cooldown
    
    def test_cooldown_tick(self):
        """Test cooldown countdown."""
        rm = create_test_risk_manager()
        
        rm.record_trade(5000, 20.0, "tsl_exit")
        initial = rm.status.cooldown_candles_remaining
        
        rm.tick_cooldown()
        
        assert rm.status.cooldown_candles_remaining == initial - 1
    
    def test_cooldown_ends(self):
        """Test cooldown ends after countdown."""
        rm = create_test_risk_manager()
        
        rm.record_trade(5000, 20.0, "tsl_exit")
        
        # Tick through cooldown
        for _ in range(15):
            ended = rm.tick_cooldown()
        
        assert ended == True
        assert rm.in_cooldown == False


class TestReentry:
    """Test re-entry logic."""
    
    def test_can_reenter_after_sl(self):
        """Test can re-enter after SL hit."""
        rm = create_test_risk_manager()
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        # Clear cooldown
        for _ in range(30):
            rm.tick_cooldown()
        
        can_reenter, reason = rm.can_reenter()
        
        assert can_reenter == True
    
    def test_cannot_reenter_without_sl(self):
        """Test cannot re-enter without prior SL hit."""
        rm = create_test_risk_manager()
        
        can_reenter, reason = rm.can_reenter()
        
        assert can_reenter == False
        assert "No SL hit" in reason
    
    def test_max_reentries_limit(self):
        """Test max re-entries limit."""
        rm = create_test_risk_manager()
        
        # First SL hit and 2 re-entries
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(3000, 12.0, "tsl_exit", is_reentry=True)
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(3000, 12.0, "tsl_exit", is_reentry=True)
        
        # Third re-entry should be blocked
        can_reenter, reason = rm.can_reenter()
        
        assert can_reenter == False
        assert "Max re-entries" in reason


class TestPositionSize:
    """Test position sizing."""
    
    def test_fixed_position_size(self):
        """Test fixed position size returned."""
        rm = create_test_risk_manager()
        
        qty = rm.get_position_size()
        
        assert qty == 260
    
    def test_validate_correct_size(self):
        """Test validation accepts correct size."""
        rm = create_test_risk_manager()
        
        is_valid, reason = rm.validate_position_size(260)
        
        assert is_valid == True
    
    def test_validate_wrong_size(self):
        """Test validation rejects wrong size."""
        rm = create_test_risk_manager()
        
        is_valid, reason = rm.validate_position_size(100)
        
        assert is_valid == False
        assert "fixed quantity" in reason


class TestRiskBudget:
    """Test risk budget calculations."""
    
    def test_remaining_sl_budget(self):
        """Test remaining SL budget calculation."""
        rm = create_test_risk_manager()
        
        assert rm.get_remaining_sl_budget() == 3
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        assert rm.get_remaining_sl_budget() == 2
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        assert rm.get_remaining_sl_budget() == 1
    
    def test_max_loss_today(self):
        """Test max loss calculation."""
        rm = create_test_risk_manager()
        
        max_loss = rm.get_max_loss_today()
        
        # 3 SL × ₹2,600 = ₹7,800
        assert max_loss == 7800.0


class TestSummary:
    """Test summary and status."""
    
    def test_get_summary(self):
        """Test summary generation."""
        rm = create_test_risk_manager()
        
        rm.record_trade(5000, 20.0, "tsl_exit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        # Clear cooldown
        for _ in range(30):
            rm.tick_cooldown()
        
        summary = rm.get_summary()
        
        assert summary["trades_today"] == 2
        assert summary["sl_hits_today"] == 1
        assert summary["sl_remaining"] == 2
        assert summary["daily_pnl"] == 2400.0
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["win_rate"] == 50.0
        assert summary["position_size"] == 260
    
    def test_daily_reset(self):
        """Test daily reset."""
        rm = create_test_risk_manager()
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        rm.reset_daily()
        
        assert rm.status.trades_today == 0
        assert rm.status.sl_hits_today == 0
        assert rm.status.daily_pnl == 0.0
        assert rm.can_trade == True


class TestEventCallbacks:
    """Test event callbacks."""
    
    def test_sl_hit_callback(self):
        """Test SL hit event callback."""
        rm = create_test_risk_manager()
        
        events_received = []
        rm.add_event_callback(lambda e, s: events_received.append(e))
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        assert RiskEvent.SL_HIT in events_received
        assert RiskEvent.COOLDOWN_START in events_received
    
    def test_max_sl_callback(self):
        """Test max SL reached callback."""
        rm = create_test_risk_manager()
        
        events_received = []
        rm.add_event_callback(lambda e, s: events_received.append(e))
        
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        rm.record_trade(-2600, -10.0, "sl_hit")
        
        assert RiskEvent.MAX_SL_REACHED in events_received


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
