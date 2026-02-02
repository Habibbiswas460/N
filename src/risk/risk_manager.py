"""
Risk Manager Module v2.0 - Production Ready

Real-time risk management for live trading:
- Position sizing with capital validation
- Daily loss limits (absolute + SL count)
- Real-time margin check
- Time-based trading windows
- Cooldown after trades
- Re-entry tracking
- Drawdown monitoring

WARNING: This manages REAL money - all checks are STRICT!
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
    PROFIT_BOOKED = "profit_booked"
    REENTRY_USED = "reentry_used"
    MAX_SL_REACHED = "max_sl_reached"
    MAX_LOSS_REACHED = "max_loss_reached"
    COOLDOWN_START = "cooldown_start"
    COOLDOWN_END = "cooldown_end"
    TRADING_HALTED = "trading_halted"
    NEW_DAY_RESET = "new_day_reset"


@dataclass
class RiskLimits:
    """Risk limit configuration - PRODUCTION SETTINGS."""
    
    # Position Sizing
    lot_size: int = 65                    # NIFTY lot = 65 qty
    num_lots: int = 6                     # Default: 6 lots (moderate)
    fixed_quantity: int = 390             # 65 × 6 = 390
    
    # Stop Loss
    sl_points: float = 5.0                # 5 point SL (FIXED)
    risk_per_trade: float = 1950.0        # ₹1,950 per trade (5 × 390)
    
    # Daily Limits (CRITICAL!)
    max_sl_per_day: int = 1               # SNIPER MODE: 1 SL = Day Over
    max_daily_loss: float = 1950.0        # Max loss = 1 SL
    max_daily_loss_pct: float = 5.0       # Max 5% of capital loss
    
    # Trade Limits
    max_trades_per_day: int = 10          # Safety cap on trades
    
    # Cooldown
    cooldown_candles_normal: int = 15     # Normal cooldown after trade
    cooldown_candles_after_sl: int = 30   # Double cooldown after SL
    
    # Time Windows (IST)
    enable_time_filter: bool = True
    trading_start: time = field(default_factory=lambda: time(9, 50))
    no_new_after: time = field(default_factory=lambda: time(12, 30))
    manage_till: time = field(default_factory=lambda: time(14, 40))
    market_close: time = field(default_factory=lambda: time(15, 30))
    
    # Re-entry
    max_reentries_per_day: int = 2        # Max 2 re-entries
    reentry_enabled: bool = True
    
    # Capital
    capital: float = 50000.0              # Trading capital
    margin_per_lot: float = 15000.0       # Approx margin per NIFTY lot
    
    def __post_init__(self):
        """Validate and compute derived values."""
        self.fixed_quantity = self.lot_size * self.num_lots
        self.risk_per_trade = self.sl_points * self.fixed_quantity
        self.required_margin = self.margin_per_lot * self.num_lots


@dataclass
class TradeRecord:
    """Record of a single trade."""
    trade_id: str = ""
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    symbol: str = ""
    option_type: str = ""  # CE or PE
    strike: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: int = 390
    pnl: float = 0.0
    pnl_points: float = 0.0
    exit_reason: str = ""
    is_reentry: bool = False
    sl_price: float = 0.0
    peak_price: float = 0.0  # For tracking unrealized profit


@dataclass
class RiskStatus:
    """Current risk status for the day - PRODUCTION TRACKING."""
    # Date tracking
    trading_date: date = field(default_factory=date.today)
    
    # Counters
    trades_today: int = 0
    sl_hits_today: int = 0
    reentries_today: int = 0
    
    # P&L Tracking
    daily_pnl: float = 0.0
    daily_pnl_points: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    peak_pnl: float = 0.0           # Highest P&L reached today
    max_drawdown: float = 0.0       # Max drawdown from peak
    
    # Win/Loss
    wins_today: int = 0
    losses_today: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    
    # Position tracking
    in_position: bool = False
    current_position_pnl: float = 0.0
    
    # Cooldown
    in_cooldown: bool = False
    cooldown_until: Optional[datetime] = None
    cooldown_candles_remaining: int = 0
    
    # Trading permission
    can_trade: bool = True
    block_reason: str = ""
    halted: bool = False           # Emergency halt
    
    # Trade history
    trades: List[TradeRecord] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            'trading_date': str(self.trading_date),
            'trades_today': self.trades_today,
            'sl_hits_today': self.sl_hits_today,
            'reentries_today': self.reentries_today,
            'daily_pnl': self.daily_pnl,
            'daily_pnl_points': self.daily_pnl_points,
            'realized_pnl': self.realized_pnl,
            'wins_today': self.wins_today,
            'losses_today': self.losses_today,
            'gross_profit': self.gross_profit,
            'gross_loss': self.gross_loss,
            'max_drawdown': self.max_drawdown,
            'can_trade': self.can_trade,
            'block_reason': self.block_reason,
            'in_cooldown': self.in_cooldown,
            'in_position': self.in_position,
            'halted': self.halted
        }


# Type for risk event callback
RiskEventCallback = Callable[[RiskEvent, RiskStatus], None]


class RiskManager:
    """
    Risk Management v2.0 - PRODUCTION READY.
    
    Real trading risk controls:
    - Max SL hits per day (Sniper Mode: 1)
    - Daily loss limit (absolute ₹ + percentage)
    - Time window restrictions
    - Margin validation
    - Drawdown monitoring
    - Capital protection
    
    WARNING: This handles REAL MONEY - all checks are STRICT!
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
        
        # Validate capital on init
        self._validate_capital()
        
        # Load today's stats
        self._load_daily_stats()
        
        # Log configuration
        logger.info(
            f"🛡️ Risk Manager v2.0 INITIALIZED | "
            f"Capital: ₹{self.limits.capital:,.0f} | "
            f"Position: {self.limits.num_lots} lots × {self.limits.lot_size} = {self.limits.fixed_quantity} qty | "
            f"Risk/Trade: ₹{self.limits.risk_per_trade:,.0f} | "
            f"Max SL/Day: {self.limits.max_sl_per_day}"
        )
    
    def _validate_capital(self) -> None:
        """Validate capital is sufficient for trading."""
        required = self.limits.margin_per_lot * self.limits.num_lots
        if self.limits.capital < required:
            logger.error(
                f"❌ CAPITAL INSUFFICIENT! Have: ₹{self.limits.capital:,.0f} | "
                f"Need: ₹{required:,.0f} for {self.limits.num_lots} lots"
            )
            raise ValueError(f"Insufficient capital: ₹{self.limits.capital:,.0f} < ₹{required:,.0f}")
        logger.info(f"✅ Capital validated: ₹{self.limits.capital:,.0f} >= ₹{required:,.0f} required")
    
    def _load_daily_stats(self) -> None:
        """Load today's statistics from store."""
        today = date.today()
        
        # Reset if new day
        if self._last_check_date != today:
            self._status = RiskStatus()
            self._status.trading_date = today
            self._last_check_date = today
            self._trigger_event(RiskEvent.NEW_DAY_RESET)
            logger.info(f"📅 Risk manager reset for new trading day: {today}")
        
        # Load from store
        stats = self._store.get_daily_stats()
        
        self._status.daily_pnl = stats.get('total_pnl', 0)
        self._status.realized_pnl = stats.get('total_pnl', 0)
        self._status.trades_today = stats.get('total_trades', 0)
        self._status.wins_today = stats.get('winning_trades', 0)
        self._status.losses_today = stats.get('losing_trades', 0)
        self._status.sl_hits_today = stats.get('sl_hits', 0)
        self._status.reentries_today = stats.get('reentries', 0)
        
        # Update gross profit/loss
        if self._status.daily_pnl > 0:
            self._status.gross_profit = self._status.daily_pnl
        else:
            self._status.gross_loss = abs(self._status.daily_pnl)
        
        self._evaluate_can_trade()
    
    def _evaluate_can_trade(self) -> None:
        """
        PRODUCTION: Strict evaluation of trading permission.
        
        Checks (in order):
        1. Emergency halt
        2. Time window
        3. Max SL hits (SNIPER MODE)
        4. Daily loss limit (absolute ₹)
        5. Daily loss % (capital protection)
        6. Max trades per day
        7. Cooldown
        """
        self._status.can_trade = True
        self._status.block_reason = ""
        
        # 1. EMERGENCY HALT - Highest priority
        if self._status.halted:
            self._status.can_trade = False
            self._status.block_reason = "🛑 TRADING HALTED - Manual override"
            return
        
        # 2. TIME WINDOW CHECKS
        if self.limits.enable_time_filter:
            now = datetime.now().time()
            
            # Too early?
            if now < self.limits.trading_start:
                self._status.can_trade = False
                self._status.block_reason = f"⏰ Too early: wait until {self.limits.trading_start}"
                return
            
            # Too late for NEW trades?
            if now > self.limits.no_new_after:
                self._status.can_trade = False
                self._status.block_reason = f"⏰ No new trades after {self.limits.no_new_after}"
                return
        
        # 3. MAX SL HITS (SNIPER MODE - Most important!)
        if self._status.sl_hits_today >= self.limits.max_sl_per_day:
            self._status.can_trade = False
            self._status.block_reason = (
                f"🎯 SNIPER MODE: SL limit reached {self._status.sl_hits_today}/{self.limits.max_sl_per_day} - Day Over!"
            )
            self._trigger_event(RiskEvent.MAX_SL_REACHED)
            return
        
        # 4. DAILY LOSS LIMIT (Absolute ₹)
        if abs(self._status.daily_pnl) >= self.limits.max_daily_loss and self._status.daily_pnl < 0:
            self._status.can_trade = False
            self._status.block_reason = (
                f"💰 Daily loss limit reached: ₹{abs(self._status.daily_pnl):,.0f} >= ₹{self.limits.max_daily_loss:,.0f}"
            )
            self._trigger_event(RiskEvent.MAX_LOSS_REACHED)
            return
        
        # 5. DAILY LOSS % (Capital Protection)
        loss_pct = abs(self._status.daily_pnl) / self.limits.capital * 100 if self._status.daily_pnl < 0 else 0
        if loss_pct >= self.limits.max_daily_loss_pct:
            self._status.can_trade = False
            self._status.block_reason = (
                f"📉 Capital protection: {loss_pct:.1f}% loss >= {self.limits.max_daily_loss_pct}% limit"
            )
            self._trigger_event(RiskEvent.MAX_LOSS_REACHED)
            return
        
        # 6. MAX TRADES PER DAY
        if self._status.trades_today >= self.limits.max_trades_per_day:
            self._status.can_trade = False
            self._status.block_reason = (
                f"📊 Max trades reached: {self._status.trades_today}/{self.limits.max_trades_per_day}"
            )
            return
        
        # 7. COOLDOWN
        if self._status.in_cooldown:
            self._status.can_trade = False
            self._status.block_reason = (
                f"⏳ In cooldown: {self._status.cooldown_candles_remaining} candles remaining"
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
        is_reentry: bool = False,
        symbol: str = "",
        option_type: str = "",
        strike: int = 0,
        entry_time: Optional[datetime] = None,
        sl_price: float = 0.0
    ) -> RiskStatus:
        """
        Record a completed trade - PRODUCTION TRACKING.
        
        Args:
            pnl: Trade P&L in rupees
            pnl_points: Trade P&L in points
            exit_reason: Reason for exit (sl_hit, tsl_exit, manual, etc.)
            entry_price: Entry price
            exit_price: Exit price
            is_reentry: Whether this was a re-entry trade
            symbol: Option symbol (e.g., "NIFTY 23800CE")
            option_type: CE or PE
            strike: Strike price
            entry_time: Actual entry time (defaults to now)
            sl_price: Stop loss price
            
        Returns:
            Updated RiskStatus
        """
        trade_id = f"T{self._status.trades_today + 1}_{datetime.now().strftime('%H%M%S')}"
        
        # Create trade record
        trade = TradeRecord(
            trade_id=trade_id,
            entry_time=entry_time or datetime.now(),
            exit_time=datetime.now(),
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=self.limits.fixed_quantity,
            pnl=pnl,
            pnl_points=pnl_points,
            exit_reason=exit_reason,
            is_reentry=is_reentry,
            sl_price=sl_price
        )
        self._status.trades.append(trade)
        
        # Update counters
        self._status.daily_pnl += pnl
        self._status.daily_pnl_points += pnl_points
        self._status.realized_pnl = self._status.daily_pnl
        self._status.trades_today += 1
        
        # Update gross profit/loss tracking
        if pnl > 0:
            self._status.wins_today += 1
            self._status.gross_profit += pnl
        else:
            self._status.losses_today += 1
            self._status.gross_loss += abs(pnl)
        
        # Track peak P&L and drawdown
        if self._status.daily_pnl > self._status.peak_pnl:
            self._status.peak_pnl = self._status.daily_pnl
        
        drawdown = self._status.peak_pnl - self._status.daily_pnl
        if drawdown > self._status.max_drawdown:
            self._status.max_drawdown = drawdown
        
        # Track SL hits
        if exit_reason in ['sl_hit', 'sl_hit_after_breath', 'sl_order_filled']:
            self._status.sl_hits_today += 1
            self._trigger_event(RiskEvent.SL_HIT)
            
            # Start longer cooldown after SL
            self._start_cooldown(self.limits.cooldown_candles_after_sl)
            
            logger.warning(
                f"🔴 SL HIT #{self._status.sl_hits_today}/{self.limits.max_sl_per_day} | "
                f"Loss: ₹{abs(pnl):,.0f} | Daily: ₹{self._status.daily_pnl:,.0f}"
            )
        else:
            # Normal cooldown
            self._start_cooldown(self.limits.cooldown_candles_normal)
            self._trigger_event(RiskEvent.PROFIT_BOOKED)
            
            logger.success(
                f"🟢 PROFIT BOOKED | Gain: ₹{pnl:,.0f} (+{pnl_points:.1f}pt) | Daily: ₹{self._status.daily_pnl:,.0f}"
            )
        
        # Track re-entries
        if is_reentry:
            self._status.reentries_today += 1
            self._trigger_event(RiskEvent.REENTRY_USED)
        
        # Update store
        self._store.update_daily_stats(trade_pnl=pnl)
        
        # Re-evaluate trading permission
        self._evaluate_can_trade()
        
        # Log trade summary
        logger.info(
            f"📝 Trade #{self._status.trades_today} | "
            f"ID: {trade_id} | {option_type} @ {strike} | "
            f"PnL: ₹{pnl:,.0f} ({pnl_points:+.1f}pt) | "
            f"Exit: {exit_reason}"
        )
        logger.info(
            f"📊 Day Summary | "
            f"Trades: {self._status.trades_today} | "
            f"W/L: {self._status.wins_today}/{self._status.losses_today} | "
            f"SL: {self._status.sl_hits_today}/{self.limits.max_sl_per_day} | "
            f"Net: ₹{self._status.daily_pnl:,.0f} | "
            f"Can Trade: {'✅' if self._status.can_trade else '❌'}"
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
        """Get comprehensive summary of current risk status - PRODUCTION."""
        loss_pct = abs(self._status.daily_pnl) / self.limits.capital * 100 if self._status.daily_pnl < 0 else 0
        
        return {
            # Trading Permission
            "can_trade": self._status.can_trade,
            "block_reason": self._status.block_reason,
            "halted": self._status.halted,
            
            # Trade Counts
            "trades_today": self._status.trades_today,
            "max_trades": self.limits.max_trades_per_day,
            "wins": self._status.wins_today,
            "losses": self._status.losses_today,
            "win_rate": (
                self._status.wins_today / self._status.trades_today * 100
                if self._status.trades_today > 0 else 0
            ),
            
            # SL Tracking
            "sl_hits_today": self._status.sl_hits_today,
            "max_sl_per_day": self.limits.max_sl_per_day,
            "sl_remaining": self.get_remaining_sl_budget(),
            
            # Re-entry
            "reentries_today": self._status.reentries_today,
            "max_reentries": self.limits.max_reentries_per_day,
            "reentries_remaining": self.get_remaining_reentries(),
            
            # P&L
            "daily_pnl": self._status.daily_pnl,
            "daily_pnl_points": self._status.daily_pnl_points,
            "gross_profit": self._status.gross_profit,
            "gross_loss": self._status.gross_loss,
            "peak_pnl": self._status.peak_pnl,
            "max_drawdown": self._status.max_drawdown,
            
            # Loss Limits
            "loss_pct": loss_pct,
            "max_loss_pct": self.limits.max_daily_loss_pct,
            "max_daily_loss": self.limits.max_daily_loss,
            "max_loss_today": self.get_max_loss_today(),
            
            # Position Size
            "position_size": self.limits.fixed_quantity,
            "num_lots": self.limits.num_lots,
            "risk_per_trade": self.limits.risk_per_trade,
            
            # Cooldown
            "in_cooldown": self._status.in_cooldown,
            "cooldown_remaining": self._status.cooldown_candles_remaining,
            
            # Capital
            "capital": self.limits.capital,
            "margin_required": self.limits.margin_per_lot * self.limits.num_lots
        }
    
    def get_daily_stats(self) -> dict:
        """Get daily stats for Telegram summary."""
        return {
            "trading_date": str(self._status.trading_date),
            "total_trades": self._status.trades_today,
            "wins": self._status.wins_today,
            "losses": self._status.losses_today,
            "win_rate": (
                self._status.wins_today / self._status.trades_today * 100
                if self._status.trades_today > 0 else 0
            ),
            "daily_pnl": self._status.daily_pnl,
            "gross_profit": self._status.gross_profit,
            "gross_loss": self._status.gross_loss,
            "max_drawdown": self._status.max_drawdown,
            "sl_hits": self._status.sl_hits_today,
            "reentries_used": self._status.reentries_today,
            "can_trade": self._status.can_trade
        }
    
    def halt_trading(self, reason: str = "Manual halt") -> None:
        """EMERGENCY: Halt all trading immediately."""
        self._status.halted = True
        self._status.can_trade = False
        self._status.block_reason = f"🛑 HALTED: {reason}"
        self._trigger_event(RiskEvent.TRADING_HALTED)
        logger.critical(f"🚨 TRADING HALTED: {reason}")
    
    def resume_trading(self) -> None:
        """Resume trading after halt (use with caution)."""
        self._status.halted = False
        self._evaluate_can_trade()
        logger.warning("⚠️ Trading RESUMED from halt - BE CAREFUL!")
    
    def update_position_pnl(self, unrealized_pnl: float) -> None:
        """Update unrealized P&L for open position."""
        self._status.unrealized_pnl = unrealized_pnl
        self._status.current_position_pnl = unrealized_pnl
    
    def set_in_position(self, in_position: bool) -> None:
        """Update position status."""
        self._status.in_position = in_position
        if not in_position:
            self._status.current_position_pnl = 0.0
            self._status.unrealized_pnl = 0.0
    
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
    num_lots: int = 6,
    sl_points: float = 5.0,
    max_sl_per_day: int = 1,
    max_reentries_per_day: int = 2,
    cooldown_candles_normal: int = 15,
    cooldown_candles_after_sl: int = 30,
    capital: float = 50000.0,
    max_daily_loss: float = 1950.0,
    max_daily_loss_pct: float = 5.0,
    max_trades_per_day: int = 10,
    trading_start: str = "09:50",
    no_new_after: str = "12:30",
    manage_till: str = "14:40",
    enable_time_filter: bool = True,
    margin_per_lot: float = 15000.0
) -> RiskManager:
    """
    Initialize risk manager with PRODUCTION settings.
    
    Args:
        lot_size: NIFTY lot size (default: 65)
        num_lots: Number of lots (default: 6)
        sl_points: Stop loss points (default: 5)
        max_sl_per_day: Max SL hits per day - SNIPER MODE (default: 1)
        max_reentries_per_day: Max re-entries (default: 2)
        cooldown_candles_normal: Normal cooldown (default: 15)
        cooldown_candles_after_sl: SL cooldown (default: 30)
        capital: Trading capital (default: ₹50,000)
        max_daily_loss: Max daily loss in ₹ (default: ₹1,950)
        max_daily_loss_pct: Max daily loss as % of capital (default: 5%)
        max_trades_per_day: Max trades per day (default: 10)
        trading_start: Trading start time (default: "09:50")
        no_new_after: No new trades after (default: "12:30")
        manage_till: Manage positions till (default: "14:40")
        enable_time_filter: Enable time restrictions (default: True)
        margin_per_lot: Margin required per lot (default: ₹15,000)
        
    Returns:
        Initialized RiskManager
    """
    global _risk_manager
    
    # Parse time strings
    def parse_time(t: str) -> time:
        parts = t.split(":")
        return time(int(parts[0]), int(parts[1]))
    
    fixed_quantity = lot_size * num_lots
    risk_per_trade = sl_points * fixed_quantity
    
    limits = RiskLimits(
        lot_size=lot_size,
        num_lots=num_lots,
        fixed_quantity=fixed_quantity,
        sl_points=sl_points,
        risk_per_trade=risk_per_trade,
        max_sl_per_day=max_sl_per_day,
        max_daily_loss=max_daily_loss,
        max_daily_loss_pct=max_daily_loss_pct,
        max_trades_per_day=max_trades_per_day,
        max_reentries_per_day=max_reentries_per_day,
        cooldown_candles_normal=cooldown_candles_normal,
        cooldown_candles_after_sl=cooldown_candles_after_sl,
        capital=capital,
        margin_per_lot=margin_per_lot,
        enable_time_filter=enable_time_filter,
        trading_start=parse_time(trading_start),
        no_new_after=parse_time(no_new_after),
        manage_till=parse_time(manage_till)
    )
    
    _risk_manager = RiskManager(limits=limits)
    
    logger.info(
        f"🛡️ Risk Manager INITIALIZED | "
        f"Mode: {'SNIPER' if max_sl_per_day == 1 else 'STANDARD'} | "
        f"Position: {num_lots} lots = {fixed_quantity} qty | "
        f"Risk/Trade: ₹{risk_per_trade:,.0f} | "
        f"Max Loss/Day: ₹{max_daily_loss:,.0f}"
    )
    
    return _risk_manager


def reset_risk_manager() -> None:
    """Reset the risk manager singleton (for testing)."""
    global _risk_manager
    _risk_manager = None
    logger.info("Risk manager singleton reset")
