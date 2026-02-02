"""
Unit Tests for Risk Manager v2.0 - PRODUCTION READY

Tests the production-ready risk management:
- Position sizing with capital validation
- Max SL per day (Sniper Mode: 1)
- Daily loss limits (absolute + percentage)
- Time-based trading windows
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
    initialize_risk_manager, reset_risk_manager
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
    """Create a RiskManager with time filter disabled and sufficient capital for testing."""
    # Set defaults for testing (disable time filter, provide sufficient capital)
    defaults = {
        'enable_time_filter': False,  # Disable time restrictions for tests
        'capital': 100000.0,          # ₹1L capital for tests
        'margin_per_lot': 15000.0,    # ₹15K per lot
        'num_lots': 6,                # 6 lots
    }
    defaults.update(kwargs)
    limits = RiskLimits(**defaults)
    store = MockStateStore()
    rm = RiskManager(limits=limits, state_store=store)
    store.set_risk_manager(rm)  # Link back for syncing
    return rm


class TestRiskLimits:
    """Test risk limits configuration."""
    
    def test_default_limits(self):
        """Test default risk limits (v2.0 production defaults)."""
        limits = RiskLimits()
        
        assert limits.lot_size == 65
        assert limits.num_lots == 6            # v2.0: Moderate mode (6 lots)
        assert limits.fixed_quantity == 390    # 65 × 6
        assert limits.sl_points == 5.0         # v2.0: Tight 5pt SL
        assert limits.risk_per_trade == 1950.0 # 5 × 390
        assert limits.max_sl_per_day == 1      # SNIPER MODE: 1 SL/day
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
        
        # Uses our test defaults (6 lots, 390 qty)
        assert rm.limits.fixed_quantity == 390
        assert rm.limits.max_sl_per_day == 1  # v2.0 Sniper Mode
        assert rm.can_trade == True
    
    def test_initialize_function(self):
        """Test initialize_risk_manager function."""
        reset_risk_manager()  # Reset singleton first
        rm = initialize_risk_manager(
            lot_size=50,
            num_lots=3,
            sl_points=15.0,
            max_sl_per_day=4,
            capital=100000.0  # Sufficient capital
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
        """Test trading blocked after max SL hits (Sniper Mode: 1 SL)."""
        rm = create_test_risk_manager(max_sl_per_day=1)  # Sniper Mode
        
        # Hit 1 SL - should block trading
        rm.record_trade(-1950, -5.0, "sl_hit")
        
        can_trade, reason = rm.can_enter_trade()
        
        assert can_trade == False
        assert "SNIPER MODE" in reason or "SL limit" in reason
    
    def test_unlimited_profitable_trades(self):
        """Test trades allowed when profitable (until max_trades_per_day)."""
        rm = create_test_risk_manager(max_sl_per_day=3, max_trades_per_day=15)
        
        # 10 profitable trades
        for i in range(10):
            rm.record_trade(5000, 20.0, "tsl_exit")
            # Clear cooldown
            for _ in range(20):
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
            pnl=-1950.0,  # v2.0: 5pt SL × 390 qty
            pnl_points=-5.0,
            exit_reason="sl_hit"
        )
        
        assert status.losses_today == 1
        assert status.sl_hits_today == 1
        assert status.daily_pnl == -1950.0
    
    def test_record_reentry(self):
        """Test recording a re-entry trade."""
        rm = create_test_risk_manager(max_sl_per_day=3)  # Allow more SLs for this test
        
        # First trade SL hit
        rm.record_trade(-1950, -5.0, "sl_hit")
        
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
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        
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
        """Test can re-enter after SL hit (with sufficient daily loss headroom)."""
        rm = create_test_risk_manager(
            max_sl_per_day=3,  # Allow more SLs for test
            max_daily_loss=10000.0  # High limit so 1 SL doesn't block
        )
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        
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
        rm = create_test_risk_manager(max_sl_per_day=5)  # Allow more SLs
        
        # First SL hit and 2 re-entries
        rm.record_trade(-1950, -5.0, "sl_hit")
        for _ in range(30): rm.tick_cooldown()
        
        rm.record_trade(3000, 12.0, "tsl_exit", is_reentry=True)
        for _ in range(15): rm.tick_cooldown()
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        for _ in range(30): rm.tick_cooldown()
        
        rm.record_trade(3000, 12.0, "tsl_exit", is_reentry=True)
        for _ in range(15): rm.tick_cooldown()
        
        # Third re-entry should be blocked
        can_reenter, reason = rm.can_reenter()
        
        assert can_reenter == False
        assert "Max re-entries" in reason


class TestPositionSize:
    """Test position sizing."""
    
    def test_fixed_position_size(self):
        """Test fixed position size returned (v2.0: 390 qty)."""
        rm = create_test_risk_manager()
        
        qty = rm.get_position_size()
        
        assert qty == 390  # 6 lots × 65
    
    def test_validate_correct_size(self):
        """Test validation accepts correct size."""
        rm = create_test_risk_manager()
        
        is_valid, reason = rm.validate_position_size(390)
        
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
        """Test remaining SL budget calculation (Sniper Mode: 1 SL)."""
        rm = create_test_risk_manager(max_sl_per_day=1)
        
        assert rm.get_remaining_sl_budget() == 1
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        assert rm.get_remaining_sl_budget() == 0
    
    def test_max_loss_today(self):
        """Test max loss calculation (v2.0: 1 SL × ₹1,950)."""
        rm = create_test_risk_manager(max_sl_per_day=1)
        
        max_loss = rm.get_max_loss_today()
        
        # 1 SL × ₹1,950 = ₹1,950
        assert max_loss == 1950.0


class TestSummary:
    """Test summary and status."""
    
    def test_get_summary(self):
        """Test summary generation."""
        rm = create_test_risk_manager(max_sl_per_day=3)  # Allow more SLs
        
        rm.record_trade(5000, 20.0, "tsl_exit")
        for _ in range(15): rm.tick_cooldown()
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        
        # Clear cooldown
        for _ in range(30):
            rm.tick_cooldown()
        
        summary = rm.get_summary()
        
        assert summary["trades_today"] == 2
        assert summary["sl_hits_today"] == 1
        assert summary["sl_remaining"] == 2
        assert summary["daily_pnl"] == 3050.0  # 5000 - 1950
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["win_rate"] == 50.0
        assert summary["position_size"] == 390  # v2.0: 6 lots
    
    def test_daily_reset(self):
        """Test daily reset."""
        rm = create_test_risk_manager(max_sl_per_day=3)
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        for _ in range(30): rm.tick_cooldown()
        rm.record_trade(-1950, -5.0, "sl_hit")
        
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
        
        rm.record_trade(-1950, -5.0, "sl_hit")
        
        assert RiskEvent.SL_HIT in events_received
        assert RiskEvent.COOLDOWN_START in events_received
    
    def test_max_sl_callback(self):
        """Test max SL reached callback (Sniper Mode: 1 SL)."""
        rm = create_test_risk_manager(max_sl_per_day=1)
        
        events_received = []
        rm.add_event_callback(lambda e, s: events_received.append(e))
        
        # Just 1 SL should trigger MAX_SL_REACHED
        rm.record_trade(-1950, -5.0, "sl_hit")
        
        assert RiskEvent.MAX_SL_REACHED in events_received


class TestProductionFeatures:
    """Test v2.0 production-specific features."""
    
    def test_halt_trading(self):
        """Test emergency halt functionality."""
        rm = create_test_risk_manager()
        
        rm.halt_trading("Market crash!")
        
        assert rm.status.halted == True
        assert rm.can_trade == False
        assert "HALTED" in rm.status.block_reason
    
    def test_resume_trading(self):
        """Test resume after halt."""
        rm = create_test_risk_manager()
        
        rm.halt_trading("Test halt")
        rm.resume_trading()
        
        assert rm.status.halted == False
        # Should be tradeable again (no other blockers)
        assert rm.can_trade == True
    
    def test_daily_loss_pct_limit(self):
        """Test daily loss percentage limit."""
        rm = create_test_risk_manager(
            max_daily_loss_pct=5.0,
            capital=100000.0,
            max_sl_per_day=10  # High to not trigger SL limit
        )
        
        # 5% of 1L = ₹5,000 loss
        rm.record_trade(-5000, -20.0, "sl_hit")
        for _ in range(30): rm.tick_cooldown()
        
        can_trade, reason = rm.can_enter_trade()
        
        assert can_trade == False
        assert "Capital protection" in reason or "loss" in reason.lower()
    
    def test_position_tracking(self):
        """Test position status tracking."""
        rm = create_test_risk_manager()
        
        rm.set_in_position(True)
        rm.update_position_pnl(500.0)
        
        assert rm.status.in_position == True
        assert rm.status.unrealized_pnl == 500.0
        
        rm.set_in_position(False)
        
        assert rm.status.in_position == False
        assert rm.status.unrealized_pnl == 0.0
    
    def test_drawdown_tracking(self):
        """Test max drawdown tracking."""
        rm = create_test_risk_manager(max_sl_per_day=5)
        
        # Win first
        rm.record_trade(5000, 20.0, "tsl_exit")
        for _ in range(15): rm.tick_cooldown()
        
        # Then lose - creates drawdown
        rm.record_trade(-2000, -8.0, "sl_hit")
        
        assert rm.status.peak_pnl == 5000.0
        assert rm.status.max_drawdown == 2000.0  # From peak 5000 to 3000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
