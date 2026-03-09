"""
Opening Range Breakout (ORB) Strategy v1.0

Simple, proven strategy:
1. Wait first 15 minutes (9:15-9:30)
2. Mark the High and Low of this range
3. Trade breakout with volume confirmation
4. Clear SL at opposite side of range

Why it works:
- First 15 min captures institutional positioning
- Breakout indicates direction for the day
- Clear levels = clear risk management
"""

import logging
from datetime import datetime, time, date
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class ORBState(Enum):
    """ORB Strategy States"""
    WAITING_FOR_OPEN = "waiting_for_open"      # Before 9:15
    BUILDING_RANGE = "building_range"          # 9:15-9:30 (collecting range)
    RANGE_READY = "range_ready"                # Range set, waiting for breakout
    BREAKOUT_LONG = "breakout_long"            # Broke above range
    BREAKOUT_SHORT = "breakout_short"          # Broke below range
    IN_TRADE = "in_trade"                      # Currently in position
    DONE_FOR_DAY = "done_for_day"              # Max trades reached or time up


class SignalType(Enum):
    """Trade signal types"""
    NO_SIGNAL = "NO_SIGNAL"
    CE_BUY = "CE_BUY"    # Bullish breakout - Buy Call
    PE_BUY = "PE_BUY"    # Bearish breakout - Buy Put


@dataclass
class ORBSignal:
    """ORB Trade Signal"""
    signal: SignalType
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    range_high: float
    range_low: float
    range_size: float
    reason: str
    timestamp: datetime


@dataclass
class ORBLevels:
    """Opening Range Levels"""
    high: float = 0.0
    low: float = float('inf')
    open_price: float = 0.0
    close_price: float = 0.0
    candle_count: int = 0
    is_ready: bool = False
    
    @property
    def range_size(self) -> float:
        return self.high - self.low if self.high > self.low else 0
    
    @property
    def mid_point(self) -> float:
        return (self.high + self.low) / 2


class ORBStrategy:
    """
    Opening Range Breakout Strategy
    
    Rules:
    1. Range Period: 9:15-9:30 (first 15 minutes)
    2. Entry: Breakout above range high (CE) or below range low (PE)
    3. Stop Loss: Opposite side of range OR mid-point (configurable)
    4. Target: 1:1.5 to 1:2 Risk:Reward
    5. Volume: Require above-average volume on breakout (optional)
    6. Retest: Optionally wait for retest of breakout level
    
    Filters:
    - Min range size: 20 points (avoid tiny ranges)
    - Max range size: 100 points (avoid huge gap days)
    - No trade after 14:00
    - Max 2 trades per day
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        config = config or {}
        
        # ORB Time Settings
        self.range_start = time(9, 15)
        self.range_end = time(9, 30)       # 15-minute ORB
        self.trade_end = time(14, 0)       # No new trades after 2 PM
        
        # Range Filters
        self.min_range_size = config.get('min_range_size', 20.0)   # Min 20 points
        self.max_range_size = config.get('max_range_size', 100.0)  # Max 100 points
        
        # Entry Settings
        self.breakout_buffer = config.get('breakout_buffer', 2.0)  # 2 points above/below
        self.require_close = config.get('require_close', True)     # Wait for candle close
        self.require_volume = config.get('require_volume', False)  # Volume confirmation
        self.volume_multiplier = config.get('volume_multiplier', 1.5)  # 1.5x avg volume
        
        # Exit Settings
        self.sl_type = config.get('sl_type', 'opposite')  # 'opposite' or 'mid'
        self.sl_buffer = config.get('sl_buffer', 3.0)     # Buffer for SL
        self.target_rr = config.get('target_rr', 1.5)     # Risk:Reward ratio
        self.target2_rr = config.get('target2_rr', 2.0)   # Second target
        
        # Risk Settings
        self.max_trades_per_day = config.get('max_trades_per_day', 2)
        
        # State
        self._state = ORBState.WAITING_FOR_OPEN
        self._levels = ORBLevels()
        self._current_date: Optional[date] = None
        self._trades_today = 0
        self._in_trade = False
        self._daily_pnl = 0.0
        
        # Volume tracking (simple average)
        self._volume_history: List[int] = []
        self._avg_volume = 0
        
        # Candle tracking for range
        self._range_candles: List[Dict] = []
        
        logger.info(f"ORB Strategy initialized: Range {self.range_start}-{self.range_end}")
    
    def reset_daily(self):
        """Reset for new trading day"""
        self._state = ORBState.WAITING_FOR_OPEN
        self._levels = ORBLevels()
        self._trades_today = 0
        self._in_trade = False
        self._daily_pnl = 0.0
        self._range_candles = []
        self._volume_history = []
        logger.info("ORB: Daily reset complete")
    
    def update(self, high: float, low: float, close: float,
               volume: int, timestamp: datetime) -> Optional[ORBSignal]:
        """
        Process new candle and generate signals
        
        Args:
            high: Candle high
            low: Candle low
            close: Candle close
            volume: Candle volume
            timestamp: Candle timestamp
            
        Returns:
            ORBSignal if breakout detected, None otherwise
        """
        current_time = timestamp.time()
        current_date = timestamp.date()
        
        # Check for new day
        if self._current_date != current_date:
            self.reset_daily()
            self._current_date = current_date
        
        # Update volume history
        self._volume_history.append(volume)
        if len(self._volume_history) > 20:
            self._volume_history.pop(0)
        self._avg_volume = sum(self._volume_history) / len(self._volume_history) if self._volume_history else volume
        
        # State machine
        if self._state == ORBState.WAITING_FOR_OPEN:
            if current_time >= self.range_start:
                self._state = ORBState.BUILDING_RANGE
                self._levels.open_price = close
                logger.info(f"ORB: Starting range build at {timestamp}")
        
        elif self._state == ORBState.BUILDING_RANGE:
            # Update range
            self._levels.high = max(self._levels.high, high)
            self._levels.low = min(self._levels.low, low)
            self._levels.close_price = close
            self._levels.candle_count += 1
            self._range_candles.append({'high': high, 'low': low, 'close': close, 'volume': volume})
            
            # Check if range period is over
            if current_time >= self.range_end:
                self._finalize_range()
        
        elif self._state == ORBState.RANGE_READY:
            # Check for breakout
            signal = self._check_breakout(high, low, close, volume, timestamp)
            if signal:
                return signal
        
        elif self._state == ORBState.DONE_FOR_DAY:
            pass  # No more trading
        
        return None
    
    def _finalize_range(self):
        """Finalize the opening range"""
        self._levels.is_ready = True
        range_size = self._levels.range_size
        
        logger.info(f"═" * 50)
        logger.info(f"ORB RANGE SET")
        logger.info(f"═" * 50)
        logger.info(f"  High: {self._levels.high:.2f}")
        logger.info(f"  Low:  {self._levels.low:.2f}")
        logger.info(f"  Size: {range_size:.2f} points")
        logger.info(f"  Mid:  {self._levels.mid_point:.2f}")
        logger.info(f"═" * 50)
        
        # Validate range
        if range_size < self.min_range_size:
            logger.warning(f"ORB: Range too small ({range_size:.1f} < {self.min_range_size})")
            self._state = ORBState.DONE_FOR_DAY
        elif range_size > self.max_range_size:
            logger.warning(f"ORB: Range too large ({range_size:.1f} > {self.max_range_size})")
            self._state = ORBState.DONE_FOR_DAY
        else:
            self._state = ORBState.RANGE_READY
            logger.info("ORB: Range valid, waiting for breakout...")
    
    def _check_breakout(self, high: float, low: float, close: float,
                        volume: int, timestamp: datetime) -> Optional[ORBSignal]:
        """Check for breakout from the opening range"""
        
        # Time filter - no trades after cutoff
        if timestamp.time() >= self.trade_end:
            logger.info("ORB: Trading time over, done for day")
            self._state = ORBState.DONE_FOR_DAY
            return None
        
        # Max trades filter
        if self._trades_today >= self.max_trades_per_day:
            logger.info("ORB: Max trades reached, done for day")
            self._state = ORBState.DONE_FOR_DAY
            return None
        
        # Already in trade
        if self._in_trade:
            return None
        
        breakout_high = self._levels.high + self.breakout_buffer
        breakout_low = self._levels.low - self.breakout_buffer
        
        # Volume filter
        volume_ok = True
        if self.require_volume:
            volume_ok = volume >= (self._avg_volume * self.volume_multiplier)
        
        # Check for LONG breakout (CE_BUY)
        if self.require_close:
            long_breakout = close > breakout_high
            short_breakout = close < breakout_low
        else:
            long_breakout = high > breakout_high
            short_breakout = low < breakout_low
        
        if long_breakout and volume_ok:
            return self._generate_long_signal(close, timestamp)
        
        if short_breakout and volume_ok:
            return self._generate_short_signal(close, timestamp)
        
        return None
    
    def _generate_long_signal(self, entry_price: float, timestamp: datetime) -> ORBSignal:
        """Generate CE_BUY signal on upside breakout"""
        
        # Calculate SL
        if self.sl_type == 'opposite':
            sl = self._levels.low - self.sl_buffer
        else:  # mid
            sl = self._levels.mid_point - self.sl_buffer
        
        risk = entry_price - sl
        target1 = entry_price + (risk * self.target_rr)
        target2 = entry_price + (risk * self.target2_rr)
        
        self._in_trade = True
        self._trades_today += 1
        
        logger.info(f"🔥 ORB BREAKOUT - LONG!")
        logger.info(f"   Entry: {entry_price:.2f}")
        logger.info(f"   SL: {sl:.2f} ({risk:.1f} pts risk)")
        logger.info(f"   Target 1: {target1:.2f}")
        logger.info(f"   Target 2: {target2:.2f}")
        
        return ORBSignal(
            signal=SignalType.CE_BUY,
            entry_price=entry_price,
            stop_loss=round(sl, 2),
            target_1=round(target1, 2),
            target_2=round(target2, 2),
            range_high=self._levels.high,
            range_low=self._levels.low,
            range_size=self._levels.range_size,
            reason=f"ORB Breakout Above {self._levels.high:.2f}",
            timestamp=timestamp
        )
    
    def _generate_short_signal(self, entry_price: float, timestamp: datetime) -> ORBSignal:
        """Generate PE_BUY signal on downside breakout"""
        
        # Calculate SL
        if self.sl_type == 'opposite':
            sl = self._levels.high + self.sl_buffer
        else:  # mid
            sl = self._levels.mid_point + self.sl_buffer
        
        risk = sl - entry_price
        target1 = entry_price - (risk * self.target_rr)
        target2 = entry_price - (risk * self.target2_rr)
        
        self._in_trade = True
        self._trades_today += 1
        
        logger.info(f"🔥 ORB BREAKOUT - SHORT!")
        logger.info(f"   Entry: {entry_price:.2f}")
        logger.info(f"   SL: {sl:.2f} ({risk:.1f} pts risk)")
        logger.info(f"   Target 1: {target1:.2f}")
        logger.info(f"   Target 2: {target2:.2f}")
        
        return ORBSignal(
            signal=SignalType.PE_BUY,
            entry_price=entry_price,
            stop_loss=round(sl, 2),
            target_1=round(target1, 2),
            target_2=round(target2, 2),
            range_high=self._levels.high,
            range_low=self._levels.low,
            range_size=self._levels.range_size,
            reason=f"ORB Breakout Below {self._levels.low:.2f}",
            timestamp=timestamp
        )
    
    def on_trade_exit(self, pnl: float):
        """Called when trade exits"""
        self._in_trade = False
        self._daily_pnl += pnl
        logger.info(f"ORB: Trade exited, P&L: ₹{pnl:,.0f}, Daily: ₹{self._daily_pnl:,.0f}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status"""
        return {
            'state': self._state.value,
            'range_high': self._levels.high,
            'range_low': self._levels.low,
            'range_size': self._levels.range_size,
            'range_ready': self._levels.is_ready,
            'trades_today': self._trades_today,
            'daily_pnl': self._daily_pnl,
            'in_trade': self._in_trade,
        }
    
    def get_levels(self) -> ORBLevels:
        """Get opening range levels"""
        return self._levels


# Factory function
def create_orb_strategy(config: Dict = None) -> ORBStrategy:
    """Create ORB strategy instance"""
    return ORBStrategy(config)
