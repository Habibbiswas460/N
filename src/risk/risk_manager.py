"""
Risk Manager Module v1.2

Implements simplified circuit breakers optimized from 3-month backtest:
- Maximum SL hits per day (ONLY LIMITER!)
- Cooldown after SL hit
- Fixed position sizing (4 lots × 65 qty = 260)
- Re-entry tracking

NO daily loss limit, NO consecutive loss limit, NO trade limit.
Let the strategy run - only stop at max SL hits.
"""

from datetime import datetime, date, time, timedelta
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from core.state_store import StateStore, get_state_store


class RiskEvent(Enum):
    """Risk-related events."""
    SL_HIT = "sl_hit"
    REENTRY_USED = "reentry_used"
    MAX_SL_REACHED = "max_sl_reached"
    COOLDOWN_START = "cooldown_start"
    COOLDOWN_END = "cooldown_end"


@dataclass
class RiskLimits:
    """Risk limit configuration (optimized from backtest)."""
    # Position Sizing - FIXED
    lot_size: int = 65                    # NIFTY lot = 65 qty
    num_lots: int = 4                     # Always 4 lots
    fixed_quantity: int = 260             # 65 × 4 = 260
    
    # Stop Loss
    sl_points: float = 10.0               # 10 point SL
    risk_per_trade: float = 2600.0        # ₹2,600 per trade (10 × 260)
    
    # ONLY LIMITER: Max SL hits per day
    max_sl_per_day: int = 3               # Stop after 3 SL hits
    
    # Cooldown
    cooldown_candles_normal: int = 15     # Normal cooldown after trade
    cooldown_candles_after_sl: int = 30   # Double cooldown after SL
    
    # Time Filters (set False for testing)
    enable_time_filter: bool = True       # Enable trading time restrictions
    
    # Re-entry
    max_reentries_per_day: int = 2        # Max 2 re-entries after SL
    
    # Capital (for paper trading / tracking)
    capital: float = 100000.0             # ₹1,00,000


@dataclass
class TradeRecord:
    """Record of a single trade."""
    entry_time: datetime
    exit_time: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: int = 260
    pnl: float = 0.0
    pnl_points: float = 0.0
    exit_reason: str = ""
    is_reentry: bool = False


@dataclass
class RiskStatus:
    """Current risk status for the day."""
    # Counters
    trades_today: int = 0
    sl_hits_today: int = 0
    reentries_today: int = 0
    
    # P&L
    daily_pnl: float = 0.0
    daily_pnl_points: float = 0.0
    
    # Win/Loss
    wins_today: int = 0
    losses_today: int = 0
    
    # Cooldown
    in_cooldown: bool = False
    cooldown_until: Optional[datetime] = None
    cooldown_candles_remaining: int = 0
    
    # Trading permission
    can_trade: bool = True
    block_reason: str = ""
    
    # Trade history
    trades: List[TradeRecord] = field(default_factory=list)
    
    def to_dict(self):
        return {
            'trades_today': self.trades_today,
            'sl_hits_today': self.sl_hits_today,
            'reentries_today': self.reentries_today,
            'daily_pnl': self.daily_pnl,
            'daily_pnl_points': self.daily_pnl_points,
            'wins_today': self.wins_today,
            'losses_today': self.losses_today,
            'can_trade': self.can_trade,
            'block_reason': self.block_reason,
            'in_cooldown': self.in_cooldown
        }


# Type for risk event callback
RiskEventCallback = Callable[[RiskEvent, RiskStatus], None]


class RiskManager:
    """
    Risk Management with Max SL Only (v1.2).
    
    Optimized from 3-month backtest:
    - NO daily loss limit
    - NO consecutive loss limit
    - NO max trades limit
    - ONLY max SL hits per day (3)
    
    This lets profitable setups run while limiting downside.
    """
    
    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        state_store: Optional[StateStore] = None
    ):
        """
        Initialize risk manager.
        
        Args:
            limits: Risk limit configuration
            state_store: State persistence store
        """
        self.limits = limits or RiskLimits()
        self._store = state_store or get_state_store()
        
        self._status = RiskStatus()
        self._event_callbacks: list[RiskEventCallback] = []
        self._last_check_date: Optional[date] = None
        
        # Load today's stats
        self._load_daily_stats()
    
    def _load_daily_stats(self) -> None:
        """Load today's statistics from store."""
        today = date.today()
        
        # Reset if new day
        if self._last_check_date != today:
            self._status = RiskStatus()
            self._last_check_date = today
            logger.info("Risk manager reset for new trading day")
        
        # Load from store
        stats = self._store.get_daily_stats()
        
        self._status.daily_pnl = stats.get('total_pnl', 0)
        self._status.trades_today = stats.get('total_trades', 0)
        self._status.wins_today = stats.get('winning_trades', 0)
        self._status.losses_today = stats.get('losing_trades', 0)
        self._status.sl_hits_today = stats.get('sl_hits', 0)
        self._status.reentries_today = stats.get('reentries', 0)
        
        self._evaluate_can_trade()
    
    def _evaluate_can_trade(self) -> None:
        """Evaluate if trading is allowed. Only check max SL."""
        self._status.can_trade = True
        self._status.block_reason = ""
        
        # Time-based filters (can be disabled for testing)
        if self.limits.enable_time_filter:
            now = datetime.now().time()
            
            # No new trades before trading_start (9:50)
            trading_start = time(9, 50)
            if now < trading_start:
                self._status.can_trade = False
                self._status.block_reason = f"Too early: wait until {trading_start}"
                return
            
            # No new trades after cutoff (12:30)
            no_new_after = time(12, 30)
            if now > no_new_after:
                self._status.can_trade = False
                self._status.block_reason = f"No new trades after {no_new_after}"
                return
        
        # ONLY LIMITER: Max SL hits per day
        if self._status.sl_hits_today >= self.limits.max_sl_per_day:
            self._status.can_trade = False
            self._status.block_reason = (
                f"Max SL hits reached: {self._status.sl_hits_today}/{self.limits.max_sl_per_day}"
            )
            self._trigger_event(RiskEvent.MAX_SL_REACHED)
            return
        
        # Cooldown check
        if self._status.in_cooldown:
            self._status.can_trade = False
            self._status.block_reason = (
                f"In cooldown: {self._status.cooldown_candles_remaining} candles remaining"
            )
    
    def _trigger_event(self, event: RiskEvent) -> None:
        """Trigger event callbacks."""
        for callback in self._event_callbacks:
            try:
                callback(event, self._status)
            except Exception as e:
                logger.error(f"Risk event callback error: {e}")
    
    def record_trade(
        self, 
        pnl: float, 
        pnl_points: float,
        exit_reason: str,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        is_reentry: bool = False
    ) -> RiskStatus:
        """
        Record a completed trade.
        
        Args:
            pnl: Trade P&L in rupees
            pnl_points: Trade P&L in points
            exit_reason: Reason for exit (sl_hit, tsl_exit, manual, etc.)
            entry_price: Entry price
            exit_price: Exit price
            is_reentry: Whether this was a re-entry trade
            
        Returns:
            Updated RiskStatus
        """
        # Create trade record
        trade = TradeRecord(
            entry_time=datetime.now(),  # Should ideally be actual entry time
            exit_time=datetime.now(),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=self.limits.fixed_quantity,
            pnl=pnl,
            pnl_points=pnl_points,
            exit_reason=exit_reason,
            is_reentry=is_reentry
        )
        self._status.trades.append(trade)
        
        # Update counters
        self._status.daily_pnl += pnl
        self._status.daily_pnl_points += pnl_points
        self._status.trades_today += 1
        
        if pnl > 0:
            self._status.wins_today += 1
        else:
            self._status.losses_today += 1
        
        # Track SL hits
        if exit_reason in ['sl_hit', 'sl_hit_after_breath', 'sl_order_filled']:
            self._status.sl_hits_today += 1
            self._trigger_event(RiskEvent.SL_HIT)
            
            # Start longer cooldown after SL
            self._start_cooldown(self.limits.cooldown_candles_after_sl)
        else:
            # Normal cooldown
            self._start_cooldown(self.limits.cooldown_candles_normal)
        
        # Track re-entries
        if is_reentry:
            self._status.reentries_today += 1
            self._trigger_event(RiskEvent.REENTRY_USED)
        
        # Update store
        self._store.update_daily_stats(trade_pnl=pnl)
        
        # Re-evaluate
        self._evaluate_can_trade()
        
        # Log
        logger.info(
            f"Trade recorded: PnL=₹{pnl:.0f} ({pnl_points:+.1f}pt) | "
            f"Reason={exit_reason} | "
            f"Daily: ₹{self._status.daily_pnl:.0f} | "
            f"Trades: {self._status.trades_today} | "
            f"SL Hits: {self._status.sl_hits_today}/{self.limits.max_sl_per_day}"
        )
        
        return self._status
    
    def record_sl_hit(self) -> RiskStatus:
        """
        Record an SL hit (shorthand).
        
        Returns:
            Updated RiskStatus
        """
        return self.record_trade(
            pnl=-self.limits.risk_per_trade,
            pnl_points=-self.limits.sl_points,
            exit_reason="sl_hit"
        )
    
    def _start_cooldown(self, candles: int) -> None:
        """Start cooldown period."""
        self._status.in_cooldown = True
        self._status.cooldown_candles_remaining = candles
        self._trigger_event(RiskEvent.COOLDOWN_START)
        logger.info(f"Cooldown started: {candles} candles")
    
    def tick_cooldown(self) -> bool:
        """
        Tick cooldown counter (call on each candle).
        
        Returns:
            True if cooldown ended
        """
        if not self._status.in_cooldown:
            return False
            
        self._status.cooldown_candles_remaining -= 1
        
        if self._status.cooldown_candles_remaining <= 0:
            self._status.in_cooldown = False
            self._status.cooldown_candles_remaining = 0
            self._evaluate_can_trade()
            self._trigger_event(RiskEvent.COOLDOWN_END)
            logger.info("Cooldown ended")
            return True
            
        return False
    
    def can_enter_trade(self) -> tuple[bool, str]:
        """
        Check if a new trade can be entered.
        
        Returns:
            Tuple of (can_enter, reason)
        """
        self._load_daily_stats()  # Refresh
        
        if not self._status.can_trade:
            return False, self._status.block_reason
        
        return True, "OK"
    
    def can_reenter(self) -> tuple[bool, str]:
        """
        Check if re-entry is allowed after SL hit.
        
        Returns:
            Tuple of (can_reenter, reason)
        """
        # Must have had at least one SL hit
        if self._status.sl_hits_today == 0:
            return False, "No SL hit to re-enter from"
        
        # Check re-entry limit
        if self._status.reentries_today >= self.limits.max_reentries_per_day:
            return False, f"Max re-entries reached: {self._status.reentries_today}"
        
        # Check overall trading permission
        can_trade, reason = self.can_enter_trade()
        if not can_trade:
            return False, reason
        
        return True, "OK"
    
    def get_position_size(self) -> int:
        """
        Get fixed position size.
        
        Returns:
            Fixed quantity (260)
        """
        return self.limits.fixed_quantity
    
    def validate_position_size(self, quantity: int) -> tuple[bool, str]:
        """
        Validate position size.
        
        Args:
            quantity: Proposed quantity
            
        Returns:
            Tuple of (is_valid, reason)
        """
        if quantity != self.limits.fixed_quantity:
            return False, f"Must use fixed quantity: {self.limits.fixed_quantity}"
        return True, "OK"
    
    def get_remaining_sl_budget(self) -> int:
        """
        Get remaining SL hits allowed for today.
        
        Returns:
            Remaining SL hits
        """
        return max(0, self.limits.max_sl_per_day - self._status.sl_hits_today)
    
    def get_remaining_reentries(self) -> int:
        """
        Get remaining re-entries allowed for today.
        
        Returns:
            Remaining re-entries
        """
        return max(0, self.limits.max_reentries_per_day - self._status.reentries_today)
    
    def get_max_loss_today(self) -> float:
        """
        Get maximum possible loss today.
        
        Returns:
            Max loss in rupees (3 × ₹2,600 = ₹7,800)
        """
        return self.limits.max_sl_per_day * self.limits.risk_per_trade
    
    def add_event_callback(self, callback: RiskEventCallback) -> None:
        """Add risk event callback."""
        self._event_callbacks.append(callback)
    
    def reset_daily(self) -> None:
        """Reset for new trading day."""
        self._status = RiskStatus()
        self._last_check_date = date.today()
        logger.info("Risk manager reset for new day")
    
    def get_summary(self) -> dict:
        """Get summary of current risk status."""
        return {
            "can_trade": self._status.can_trade,
            "block_reason": self._status.block_reason,
            "trades_today": self._status.trades_today,
            "sl_hits_today": self._status.sl_hits_today,
            "sl_remaining": self.get_remaining_sl_budget(),
            "reentries_today": self._status.reentries_today,
            "reentries_remaining": self.get_remaining_reentries(),
            "daily_pnl": self._status.daily_pnl,
            "daily_pnl_points": self._status.daily_pnl_points,
            "wins": self._status.wins_today,
            "losses": self._status.losses_today,
            "win_rate": (
                self._status.wins_today / self._status.trades_today * 100
                if self._status.trades_today > 0 else 0
            ),
            "in_cooldown": self._status.in_cooldown,
            "cooldown_remaining": self._status.cooldown_candles_remaining,
            "position_size": self.limits.fixed_quantity,
            "max_loss_today": self.get_max_loss_today()
        }
    
    def get_daily_stats(self) -> dict:
        """Get daily stats for Telegram summary."""
        return {
            "total_trades": self._status.trades_today,
            "wins": self._status.wins_today,
            "losses": self._status.losses_today,
            "daily_pnl": self._status.daily_pnl,
            "sl_hits": self._status.sl_hits_today,
            "reentries_used": self._status.reentries_today
        }
    
    @property
    def status(self) -> RiskStatus:
        """Get current risk status."""
        return self._status
    
    @property
    def can_trade(self) -> bool:
        """Check if trading is allowed."""
        return self._status.can_trade
    
    @property
    def daily_pnl(self) -> float:
        """Get daily P&L."""
        return self._status.daily_pnl
    
    @property
    def sl_hits_today(self) -> int:
        """Get SL hits today."""
        return self._status.sl_hits_today
    
    @property
    def in_cooldown(self) -> bool:
        """Check if in cooldown."""
        return self._status.in_cooldown


# Singleton instance
_risk_manager: Optional[RiskManager] = None


def get_risk_manager(limits: Optional[RiskLimits] = None) -> RiskManager:
    """Get the global risk manager instance."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager(limits=limits)
    return _risk_manager


def initialize_risk_manager(
    lot_size: int = 65,
    num_lots: int = 4,
    sl_points: float = 10.0,
    max_sl_per_day: int = 3,
    max_reentries_per_day: int = 2,
    cooldown_candles_normal: int = 15,
    cooldown_candles_after_sl: int = 30,
    capital: float = 100000.0
) -> RiskManager:
    """
    Initialize risk manager with custom limits.
    
    Args:
        lot_size: NIFTY lot size (default: 65)
        num_lots: Number of lots (default: 4)
        sl_points: Stop loss points (default: 10)
        max_sl_per_day: Max SL hits per day (default: 3)
        max_reentries_per_day: Max re-entries (default: 2)
        cooldown_candles_normal: Normal cooldown (default: 15)
        cooldown_candles_after_sl: SL cooldown (default: 30)
        capital: Trading capital (default: ₹1,00,000)
        
    Returns:
        Initialized RiskManager
    """
    global _risk_manager
    
    fixed_quantity = lot_size * num_lots
    risk_per_trade = sl_points * fixed_quantity
    
    limits = RiskLimits(
        lot_size=lot_size,
        num_lots=num_lots,
        fixed_quantity=fixed_quantity,
        sl_points=sl_points,
        risk_per_trade=risk_per_trade,
        max_sl_per_day=max_sl_per_day,
        max_reentries_per_day=max_reentries_per_day,
        cooldown_candles_normal=cooldown_candles_normal,
        cooldown_candles_after_sl=cooldown_candles_after_sl,
        capital=capital
    )
    
    _risk_manager = RiskManager(limits=limits)
    return _risk_manager
