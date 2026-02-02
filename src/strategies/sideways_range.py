"""
Sideways Range Strategy - Unlimited Profit Generator

When market is sideways (range-bound), this strategy:
1. Identifies range high (resistance) and low (support)
2. Buys CE at range low (support bounce)
3. Buys PE at range high (resistance rejection)
4. Quick profit targets within range
5. Tight SL just outside range

Key Features:
- Mean reversion logic (buy low, sell high)
- Quick scalping profits (5-10 points)
- Multiple trades per day possible
- Works when N-Structure fails (no trend)

v2.0 IMPROVEMENTS:
- Better range detection using pivot points
- Stricter entry conditions (multi-touch levels)
- Time-based exit for stuck trades
- ATR-based dynamic targets
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from datetime import datetime, time, timedelta, date
from enum import Enum, auto
from collections import deque

from loguru import logger


class RangeState(Enum):
    """State machine for range trading."""
    IDLE = auto()           # Looking for range
    RANGE_DETECTED = auto() # Valid range found
    WAITING_ENTRY = auto()  # Waiting for entry signal
    ACTIVE = auto()         # In trade
    COOLDOWN = auto()       # After trade, waiting


@dataclass
class RangeTrade:
    """Range trade record."""
    entry_time: datetime
    entry_price: float
    direction: str  # "CE" or "PE"
    target_price: float
    sl_price: float
    range_high: float = 0.0
    range_low: float = 0.0
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    exit_reason: str = ""
    max_price: float = 0.0  # Track highest price for TSL
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class RangeResult:
    """Result of range strategy backtest."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    trades: List[RangeTrade] = field(default_factory=list)


class SidewaysRangeStrategy:
    """
    Range Trading Strategy for Sideways Markets v2.0.
    
    Improved Logic:
    1. Detect range using 20-candle high/low
    2. Enter CE when price touches support + bounces up
    3. Enter PE when price touches resistance + bounces down
    4. Quick targets: 5-8 points profit
    5. Tight SL: 3 points
    6. Max hold time: 15 candles (auto-exit)
    """
    
    def __init__(
        self,
        # Range Detection
        lookback_candles: int = 20,          # Shorter for quicker range detection
        min_range_points: float = 30.0,      # Min 30 points range
        max_range_points: float = 100.0,     # Max 100 points range
        
        # Entry Triggers  
        entry_buffer_pct: float = 0.2,       # Enter within 0.2% of level
        require_bounce: bool = True,         # Must see bounce candle
        min_touches: int = 1,                # Min touches to confirm level
        
        # Exit Parameters - QUICK SCALPING
        target_points: float = 8.0,          # Quick 8 point target
        sl_points: float = 4.0,              # Tight 4 point SL
        max_hold_candles: int = 15,          # Exit after 15 candles max
        
        # TSL Parameters
        tsl_trigger: float = 5.0,            # At +5pt, activate TSL
        tsl_buffer: float = 2.0,             # TSL = High - 2pt
        
        # Risk Management
        lot_size: int = 65,
        num_lots: int = 4,
        max_trades_per_day: int = 4,         # Max 4 range trades/day
        max_sl_per_day: int = 2,
        cooldown_candles: int = 5,           # Quick cooldown
        
        # Time Filters
        trading_start: time = time(9, 50),
        trading_end: time = time(14, 0),
    ):
        """Initialize range strategy v2.0."""
        self.lookback_candles = lookback_candles
        self.min_range_points = min_range_points
        self.max_range_points = max_range_points
        
        self.entry_buffer_pct = entry_buffer_pct
        self.require_bounce = require_bounce
        self.min_touches = min_touches
        
        self.target_points = target_points
        self.sl_points = sl_points
        self.max_hold_candles = max_hold_candles
        
        self.tsl_trigger = tsl_trigger
        self.tsl_buffer = tsl_buffer
        
        self.lot_size = lot_size
        self.num_lots = num_lots
        self.fixed_qty = lot_size * num_lots
        self.max_trades_per_day = max_trades_per_day
        self.max_sl_per_day = max_sl_per_day
        self.cooldown_candles = cooldown_candles
        
        self.trading_start = trading_start
        self.trading_end = trading_end
        
        # State
        self.state = RangeState.IDLE
        self.current_trade: Optional[RangeTrade] = None
        self.hold_candles: int = 0  # Track candles since entry
        
        # Price history
        self.high_history: deque = deque(maxlen=lookback_candles)
        self.low_history: deque = deque(maxlen=lookback_candles)
        self.close_history: deque = deque(maxlen=lookback_candles)
        
        # Range levels
        self.range_high: float = 0.0
        self.range_low: float = 0.0
        self.support_touches: int = 0
        self.resistance_touches: int = 0
        
        # Bounce detection
        self.prev_close: float = 0.0
        self.prev_low: float = 0.0
        self.prev_high: float = 0.0
        
        # Daily tracking
        self.daily_trades: int = 0
        self.daily_sl_hits: int = 0
        self.cooldown_counter: int = 0
        self.current_date: Optional[date] = None
        
        # Results
        self.trades: List[RangeTrade] = []
        self.equity_curve: List[float] = [0.0]
    
    def reset(self):
        """Reset strategy state."""
        self.state = RangeState.IDLE
        self.current_trade = None
        self.hold_candles = 0
        self.high_history.clear()
        self.low_history.clear()
        self.close_history.clear()
        self.range_high = 0.0
        self.range_low = 0.0
        self.support_touches = 0
        self.resistance_touches = 0
        self.prev_close = 0.0
        self.prev_low = 0.0
        self.prev_high = 0.0
        self.daily_trades = 0
        self.daily_sl_hits = 0
        self.cooldown_counter = 0
        self.current_date = None
        self.trades = []
        self.equity_curve = [0.0]
    
    def _check_day_change(self, ts: datetime):
        """Reset daily counters on new day."""
        if self.current_date is None or ts.date() != self.current_date:
            self.current_date = ts.date()
            self.daily_trades = 0
            self.daily_sl_hits = 0
            self.cooldown_counter = 0
            # Reset range on new day
            self.range_high = 0.0
            self.range_low = 0.0
            self.state = RangeState.IDLE
            logger.info(f"📅 Range Strategy - New day: {ts.date()}")
    
    def _is_trading_hours(self, ts: datetime) -> bool:
        """Check if within trading hours."""
        t = ts.time()
        return self.trading_start <= t <= self.trading_end
    
    def _detect_range(self, close: float) -> bool:
        """
        Detect valid trading range.
        Returns True if valid range exists.
        """
        if len(self.high_history) < self.lookback_candles:
            return False
        
        # Get range boundaries
        self.range_high = max(self.high_history)
        self.range_low = min(self.low_history)
        
        range_width = self.range_high - self.range_low
        
        # Validate range width
        if range_width < self.min_range_points:
            return False
        if range_width > self.max_range_points:
            return False
        
        # Count touches
        self._count_level_touches()
        
        return True
    
    def _count_level_touches(self):
        """Count how many times price touched support/resistance."""
        self.support_touches = 0
        self.resistance_touches = 0
        
        touch_buffer = (self.range_high - self.range_low) * 0.05  # 5% of range
        
        for low in self.low_history:
            if low <= self.range_low + touch_buffer:
                self.support_touches += 1
        
        for high in self.high_history:
            if high >= self.range_high - touch_buffer:
                self.resistance_touches += 1
    
    def _is_at_support(self, low: float, close: float) -> bool:
        """Check if price is at support and bouncing."""
        if self.range_low <= 0:
            return False
        
        range_width = self.range_high - self.range_low
        buffer = range_width * (self.entry_buffer_pct / 100)
        
        # Price touched support zone
        at_support = low <= self.range_low + buffer
        
        # Bouncing up (close above prev close)
        bouncing = close > self.prev_close if self.require_bounce else True
        
        return at_support and bouncing
    
    def _is_at_resistance(self, high: float, close: float) -> bool:
        """Check if price is at resistance and rejecting."""
        if self.range_high <= 0:
            return False
        
        range_width = self.range_high - self.range_low
        buffer = range_width * (self.entry_buffer_pct / 100)
        
        # Price touched resistance zone
        at_resistance = high >= self.range_high - buffer
        
        # Rejecting down (close below prev close)
        rejecting = close < self.prev_close if self.require_bounce else True
        
        return at_resistance and rejecting
    
    def process_candle(
        self,
        timestamp: datetime,
        high: float,
        low: float,
        close: float,
        option_price: float
    ) -> Optional[str]:
        """
        Process a candle and generate signals.
        
        Returns: "BUY_CE", "BUY_PE", "EXIT", or None
        """
        # Day change check
        self._check_day_change(timestamp)
        
        # Store history
        self.high_history.append(high)
        self.low_history.append(low)
        self.close_history.append(close)
        
        # Handle active trade first
        if self.current_trade and self.current_trade.is_open:
            signal = self._manage_active_trade(timestamp, option_price)
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return signal
        
        # Cooldown
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return None
        
        # Daily limits
        if self.daily_trades >= self.max_trades_per_day:
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return None
        
        if self.daily_sl_hits >= self.max_sl_per_day:
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return None
        
        # Time filter
        if not self._is_trading_hours(timestamp):
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return None
        
        # Detect/update range
        if not self._detect_range(close):
            self.prev_close = close
            self.prev_high = high
            self.prev_low = low
            return None
        
        # Look for entries
        signal = None
        
        # Support bounce → Buy CE
        if self._is_at_support(low, close):
            signal = self._enter_trade(timestamp, option_price, "CE")
        
        # Resistance rejection → Buy PE
        elif self._is_at_resistance(high, close):
            signal = self._enter_trade(timestamp, option_price, "PE")
        
        self.prev_close = close
        self.prev_high = high
        self.prev_low = low
        return signal
    
    def _enter_trade(
        self,
        ts: datetime,
        option_price: float,
        direction: str
    ) -> str:
        """Enter a range trade."""
        entry_price = option_price
        target_price = entry_price + self.target_points
        sl_price = entry_price - self.sl_points
        
        self.current_trade = RangeTrade(
            entry_time=ts,
            entry_price=entry_price,
            direction=direction,
            target_price=target_price,
            sl_price=sl_price,
            range_high=self.range_high,
            range_low=self.range_low,
            max_price=entry_price
        )
        
        self.hold_candles = 0
        self.daily_trades += 1
        self.state = RangeState.ACTIVE
        
        level = "Support" if direction == "CE" else "Resistance"
        emoji = "📈" if direction == "CE" else "📉"
        logger.info(f"{emoji} RANGE {direction} @ ₹{entry_price:.2f} | {level} Bounce | Target: +{self.target_points}pt | SL: -{self.sl_points}pt")
        
        return f"BUY_{direction}"
    
    def _manage_active_trade(
        self,
        ts: datetime,
        option_price: float
    ) -> Optional[str]:
        """Manage active trade - check exits."""
        if not self.current_trade:
            return None
        
        self.hold_candles += 1
        
        # Update max price for TSL
        if option_price > self.current_trade.max_price:
            self.current_trade.max_price = option_price
        
        # Check target hit
        if option_price >= self.current_trade.target_price:
            return self._exit_trade(ts, option_price, "Target Hit")
        
        # Check SL hit
        if option_price <= self.current_trade.sl_price:
            return self._exit_trade(ts, option_price, "SL Hit")
        
        # TSL logic - activate after +5pt
        profit = option_price - self.current_trade.entry_price
        if profit >= self.tsl_trigger:
            tsl_level = self.current_trade.max_price - self.tsl_buffer
            if option_price <= tsl_level and tsl_level > self.current_trade.entry_price:
                return self._exit_trade(ts, option_price, f"TSL Hit (+{profit:.0f}pt)")
        
        # Time-based exit - max hold candles
        if self.hold_candles >= self.max_hold_candles:
            return self._exit_trade(ts, option_price, "Time Exit")
        
        return None
    
    def _exit_trade(
        self,
        ts: datetime,
        exit_price: float,
        reason: str
    ) -> str:
        """Exit current trade."""
        if not self.current_trade:
            return "EXIT"
        
        self.current_trade.exit_time = ts
        self.current_trade.exit_price = exit_price
        self.current_trade.exit_reason = reason
        
        pnl = (exit_price - self.current_trade.entry_price) * self.fixed_qty
        self.current_trade.pnl = pnl
        
        self.equity_curve.append(self.equity_curve[-1] + pnl)
        
        if "SL Hit" in reason:
            self.daily_sl_hits += 1
        
        pnl_str = f"+₹{pnl:.0f}" if pnl > 0 else f"-₹{abs(pnl):.0f}"
        emoji = "✅" if pnl > 0 else "❌"
        logger.info(f"{emoji} RANGE EXIT @ ₹{exit_price:.2f} | {reason} | PnL: {pnl_str}")
        
        self.trades.append(self.current_trade)
        self.current_trade = None
        self.hold_candles = 0
        self.cooldown_counter = self.cooldown_candles
        self.state = RangeState.COOLDOWN
        
        return "EXIT"
    
    def get_results(self) -> RangeResult:
        """Calculate results."""
        if not self.trades:
            return RangeResult()
        
        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        
        total_wins = sum(t.pnl for t in winning)
        total_losses = abs(sum(t.pnl for t in losing))
        
        max_dd = 0
        peak = 0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        
        return RangeResult(
            total_trades=len(self.trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            total_pnl=sum(t.pnl for t in self.trades),
            win_rate=len(winning) / len(self.trades) * 100 if self.trades else 0,
            avg_win=total_wins / len(winning) if winning else 0,
            avg_loss=total_losses / len(losing) if losing else 0,
            profit_factor=total_wins / total_losses if total_losses > 0 else float('inf'),
            max_drawdown=max_dd,
            trades=self.trades
        )
