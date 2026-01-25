"""
Risk Management Module v1.3

Provides advanced risk management features:
1. Partial Profit Booking - Scale out at profit milestones
2. Max Drawdown Protection - Stop trading on excessive daily loss
3. Position Sizing - Risk-based quantity calculation
4. ATR-based Risk - Dynamic SL and targets
"""

from typing import Optional, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum

from loguru import logger


class ExitType(Enum):
    """Types of position exits."""
    FULL_EXIT = "full"           # Exit entire position
    PARTIAL_EXIT = "partial"     # Exit part of position
    NO_EXIT = "none"             # Hold position


@dataclass
class PartialExitResult:
    """Result of partial profit check."""
    exit_type: ExitType
    exit_quantity: int           # Quantity to exit (0 if no exit)
    exit_price: float           # Price to exit at
    remaining_quantity: int      # Quantity remaining after exit
    pnl_locked: float           # P&L locked by this exit
    message: str


@dataclass 
class DrawdownStatus:
    """Daily drawdown tracking status."""
    daily_pnl: float            # Total P&L for day
    max_drawdown: float         # Maximum drawdown threshold
    drawdown_remaining: float   # How much more loss allowed
    is_breached: bool           # True if max drawdown hit
    trades_today: int           # Number of trades taken
    wins_today: int             # Winning trades
    losses_today: int           # Losing trades
    message: str


@dataclass
class PositionState:
    """Tracks position state for partial exits."""
    entry_price: float
    initial_quantity: int
    current_quantity: int
    exits: List[Tuple[float, int, float]] = field(default_factory=list)  # (price, qty, pnl)
    first_target_hit: bool = False
    second_target_hit: bool = False
    
    @property
    def total_pnl(self) -> float:
        """Calculate total P&L from all exits."""
        return sum(pnl for _, _, pnl in self.exits)
    
    @property
    def is_fully_closed(self) -> bool:
        """Check if position is fully closed."""
        return self.current_quantity == 0


class PartialProfitManager:
    """
    Manages partial profit booking.
    
    Strategy:
    - Exit 50% at +15 points profit
    - Trail remaining 50% with structure-based TSL
    
    Benefits:
    - Locks in profits on winners
    - Lets remaining run for bigger moves
    - Reduces emotional decision making
    """
    
    def __init__(
        self,
        first_target_points: float = 15.0,
        first_exit_percentage: float = 50.0,
        second_target_points: float = 30.0,  # Optional second target
        second_exit_percentage: float = 25.0,  # Exit 25% more at second target
        lot_size: int = 65
    ):
        """
        Initialize partial profit manager.
        
        Args:
            first_target_points: Points profit for first exit
            first_exit_percentage: Percentage to exit at first target
            second_target_points: Points profit for second exit (optional)
            second_exit_percentage: Percentage for second exit
            lot_size: Lot size for rounding
        """
        self.first_target_points = first_target_points
        self.first_exit_percentage = first_exit_percentage / 100
        self.second_target_points = second_target_points
        self.second_exit_percentage = second_exit_percentage / 100
        self.lot_size = lot_size
        
        self.position: Optional[PositionState] = None
        
    def open_position(self, entry_price: float, quantity: int) -> None:
        """
        Register a new position for tracking.
        
        Args:
            entry_price: Entry price
            quantity: Total quantity
        """
        self.position = PositionState(
            entry_price=entry_price,
            initial_quantity=quantity,
            current_quantity=quantity
        )
        logger.debug(f"Partial profit tracking: Entry @ {entry_price:.2f}, Qty: {quantity}")
        
    def check_exit(self, current_price: float) -> PartialExitResult:
        """
        Check if partial profit should be booked.
        
        Args:
            current_price: Current option price
            
        Returns:
            PartialExitResult with action to take
        """
        if not self.position or self.position.current_quantity == 0:
            return PartialExitResult(
                exit_type=ExitType.NO_EXIT,
                exit_quantity=0,
                exit_price=0,
                remaining_quantity=0,
                pnl_locked=0,
                message="No active position"
            )
        
        profit_points = current_price - self.position.entry_price
        
        # Check first target (50% exit at +15 points)
        if not self.position.first_target_hit and profit_points >= self.first_target_points:
            exit_qty = self._round_to_lots(
                self.position.initial_quantity * self.first_exit_percentage
            )
            
            if exit_qty > 0 and exit_qty <= self.position.current_quantity:
                pnl = profit_points * exit_qty
                self.position.first_target_hit = True
                self.position.current_quantity -= exit_qty
                self.position.exits.append((current_price, exit_qty, pnl))
                
                return PartialExitResult(
                    exit_type=ExitType.PARTIAL_EXIT,
                    exit_quantity=exit_qty,
                    exit_price=current_price,
                    remaining_quantity=self.position.current_quantity,
                    pnl_locked=pnl,
                    message=f"🎯 T1 Hit! Exit 50% @ {current_price:.2f} | Locked ₹{pnl:,.0f}"
                )
        
        # Check second target (25% more at +30 points)
        if (self.position.first_target_hit and 
            not self.position.second_target_hit and 
            profit_points >= self.second_target_points):
            
            exit_qty = self._round_to_lots(
                self.position.initial_quantity * self.second_exit_percentage
            )
            
            if exit_qty > 0 and exit_qty <= self.position.current_quantity:
                pnl = profit_points * exit_qty
                self.position.second_target_hit = True
                self.position.current_quantity -= exit_qty
                self.position.exits.append((current_price, exit_qty, pnl))
                
                return PartialExitResult(
                    exit_type=ExitType.PARTIAL_EXIT,
                    exit_quantity=exit_qty,
                    exit_price=current_price,
                    remaining_quantity=self.position.current_quantity,
                    pnl_locked=pnl,
                    message=f"🎯 T2 Hit! Exit 25% @ {current_price:.2f} | Locked ₹{pnl:,.0f}"
                )
        
        return PartialExitResult(
            exit_type=ExitType.NO_EXIT,
            exit_quantity=0,
            exit_price=0,
            remaining_quantity=self.position.current_quantity,
            pnl_locked=0,
            message=f"Profit: {profit_points:.1f}pt | Targets: T1={self.first_target_points}pt, T2={self.second_target_points}pt"
        )
    
    def close_remaining(self, exit_price: float) -> Tuple[int, float]:
        """
        Close remaining position (on SL or EOD).
        
        Args:
            exit_price: Exit price
            
        Returns:
            (quantity closed, pnl from this exit)
        """
        if not self.position or self.position.current_quantity == 0:
            return 0, 0.0
        
        qty = self.position.current_quantity
        pnl = (exit_price - self.position.entry_price) * qty
        
        self.position.exits.append((exit_price, qty, pnl))
        self.position.current_quantity = 0
        
        return qty, pnl
    
    def get_total_pnl(self) -> float:
        """Get total P&L from all exits."""
        return self.position.total_pnl if self.position else 0.0
    
    def _round_to_lots(self, quantity: float) -> int:
        """Round quantity to nearest lot size."""
        lots = round(quantity / self.lot_size)
        return lots * self.lot_size
    
    def reset(self) -> None:
        """Reset for new trade."""
        self.position = None


class DrawdownProtection:
    """
    Maximum drawdown protection for daily risk management.
    
    Features:
    - Tracks daily P&L
    - Stops trading when max loss reached
    - Prevents revenge trading
    - Provides daily stats
    """
    
    def __init__(
        self,
        max_daily_loss: float = 5000.0,      # Stop trading at ₹5000 loss
        max_consecutive_losses: int = 3,      # Stop after 3 consecutive losses
        warning_threshold_pct: float = 70.0   # Warn at 70% of max loss
    ):
        """
        Initialize drawdown protection.
        
        Args:
            max_daily_loss: Maximum daily loss before stopping
            max_consecutive_losses: Max consecutive losses allowed
            warning_threshold_pct: Percentage of max loss to trigger warning
        """
        self.max_daily_loss = max_daily_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.warning_threshold = max_daily_loss * (warning_threshold_pct / 100)
        
        # Daily tracking
        self.daily_pnl: float = 0.0
        self.trades_today: int = 0
        self.wins_today: int = 0
        self.losses_today: int = 0
        self.consecutive_losses: int = 0
        self.is_stopped: bool = False
        self.current_date: Optional[datetime] = None
        
    def new_day(self, date: datetime) -> None:
        """
        Reset for new trading day.
        
        Args:
            date: Current date
        """
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self.is_stopped = False
        self.current_date = date
        logger.info(f"📅 New trading day: {date.strftime('%Y-%m-%d')} | Max loss: ₹{self.max_daily_loss:,.0f}")
        
    def record_trade(self, pnl: float) -> DrawdownStatus:
        """
        Record a completed trade.
        
        Args:
            pnl: Trade P&L (positive = profit, negative = loss)
            
        Returns:
            DrawdownStatus with current state
        """
        self.daily_pnl += pnl
        self.trades_today += 1
        
        if pnl >= 0:
            self.wins_today += 1
            self.consecutive_losses = 0
        else:
            self.losses_today += 1
            self.consecutive_losses += 1
        
        # Check drawdown breach
        if self.daily_pnl <= -self.max_daily_loss:
            self.is_stopped = True
            message = f"🛑 MAX DRAWDOWN REACHED: Daily loss ₹{abs(self.daily_pnl):,.0f} >= ₹{self.max_daily_loss:,.0f}"
            logger.warning(message)
        elif self.consecutive_losses >= self.max_consecutive_losses:
            self.is_stopped = True
            message = f"🛑 CONSECUTIVE LOSS LIMIT: {self.consecutive_losses} losses in a row"
            logger.warning(message)
        elif self.daily_pnl <= -self.warning_threshold:
            message = f"⚠️ APPROACHING MAX LOSS: ₹{abs(self.daily_pnl):,.0f} / ₹{self.max_daily_loss:,.0f}"
            logger.warning(message)
        else:
            message = f"Daily P&L: ₹{self.daily_pnl:,.0f} | W/L: {self.wins_today}/{self.losses_today}"
        
        return self.get_status(message)
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            (can_trade, reason)
        """
        if self.is_stopped:
            return False, f"Trading stopped: Daily loss ₹{abs(self.daily_pnl):,.0f}"
        
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"Trading stopped: {self.consecutive_losses} consecutive losses"
        
        return True, "Trading allowed"
    
    def get_status(self, message: str = "") -> DrawdownStatus:
        """Get current drawdown status."""
        return DrawdownStatus(
            daily_pnl=self.daily_pnl,
            max_drawdown=self.max_daily_loss,
            drawdown_remaining=self.max_daily_loss + self.daily_pnl,  # Positive if still allowed
            is_breached=self.is_stopped,
            trades_today=self.trades_today,
            wins_today=self.wins_today,
            losses_today=self.losses_today,
            message=message or f"W/L: {self.wins_today}/{self.losses_today} | P&L: ₹{self.daily_pnl:,.0f}"
        )
    
    def reset(self) -> None:
        """Full reset."""
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self.is_stopped = False
        self.current_date = None


class RiskManager:
    """
    Unified risk management combining all features.
    
    Provides single interface for:
    - Partial profit booking
    - Drawdown protection
    - Position sizing
    - ATR-based risk
    """
    
    def __init__(
        self,
        # Partial profit settings
        enable_partial_profits: bool = True,
        first_target_points: float = 15.0,
        second_target_points: float = 30.0,
        
        # Drawdown settings  
        enable_drawdown_protection: bool = True,
        max_daily_loss: float = 5000.0,
        max_consecutive_losses: int = 3,
        
        # Position sizing
        lot_size: int = 65,
        default_lots: int = 4
    ):
        """Initialize risk manager with all components."""
        self.enable_partial_profits = enable_partial_profits
        self.enable_drawdown_protection = enable_drawdown_protection
        self.lot_size = lot_size
        self.default_lots = default_lots
        self.default_qty = lot_size * default_lots
        
        # Initialize components
        self.partial_profit_mgr = PartialProfitManager(
            first_target_points=first_target_points,
            second_target_points=second_target_points,
            lot_size=lot_size
        ) if enable_partial_profits else None
        
        self.drawdown_protection = DrawdownProtection(
            max_daily_loss=max_daily_loss,
            max_consecutive_losses=max_consecutive_losses
        ) if enable_drawdown_protection else None
        
    def can_trade(self) -> Tuple[bool, str]:
        """Check if trading is allowed based on risk rules."""
        if self.drawdown_protection:
            return self.drawdown_protection.can_trade()
        return True, "Risk checks passed"
    
    def open_position(self, entry_price: float, quantity: int) -> None:
        """Register new position for tracking."""
        if self.partial_profit_mgr:
            self.partial_profit_mgr.open_position(entry_price, quantity)
    
    def check_partial_exit(self, current_price: float) -> PartialExitResult:
        """Check for partial profit opportunity."""
        if self.partial_profit_mgr:
            return self.partial_profit_mgr.check_exit(current_price)
        return PartialExitResult(
            exit_type=ExitType.NO_EXIT,
            exit_quantity=0, exit_price=0,
            remaining_quantity=0, pnl_locked=0,
            message="Partial profits disabled"
        )
    
    def close_position(self, exit_price: float, pnl: float) -> DrawdownStatus:
        """Record closed position."""
        if self.partial_profit_mgr:
            self.partial_profit_mgr.close_remaining(exit_price)
        
        if self.drawdown_protection:
            return self.drawdown_protection.record_trade(pnl)
        
        return DrawdownStatus(
            daily_pnl=pnl, max_drawdown=0,
            drawdown_remaining=0, is_breached=False,
            trades_today=1, wins_today=1 if pnl > 0 else 0,
            losses_today=0 if pnl > 0 else 1,
            message="Risk tracking disabled"
        )
    
    def new_day(self, date: datetime) -> None:
        """Reset for new trading day."""
        if self.drawdown_protection:
            self.drawdown_protection.new_day(date)
        if self.partial_profit_mgr:
            self.partial_profit_mgr.reset()
    
    def reset(self) -> None:
        """Full reset."""
        if self.drawdown_protection:
            self.drawdown_protection.reset()
        if self.partial_profit_mgr:
            self.partial_profit_mgr.reset()


# Export
__all__ = [
    'PartialProfitManager',
    'PartialExitResult',
    'ExitType',
    'DrawdownProtection', 
    'DrawdownStatus',
    'PositionState',
    'RiskManager'
]
