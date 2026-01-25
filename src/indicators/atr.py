"""
ATR (Average True Range) Indicator Module

Provides volatility measurement for:
1. Dynamic Stop Loss calculation
2. Volatility-based filtering
3. ATR-based trailing stops
"""

from typing import Optional, List
from dataclasses import dataclass
from collections import deque

from loguru import logger


@dataclass
class ATRResult:
    """ATR calculation result."""
    atr: float                    # Current ATR value
    atr_percentage: float         # ATR as percentage of price
    volatility_level: str         # "LOW", "NORMAL", "HIGH", "EXTREME"
    is_tradeable: bool           # Whether volatility is suitable
    suggested_sl_points: float    # Suggested SL based on ATR
    message: str


class ATRCalculator:
    """
    Average True Range (ATR) Calculator.
    
    ATR = Average of True Range over N periods
    True Range = max(High - Low, |High - Prev Close|, |Low - Prev Close|)
    
    Used for:
    - Dynamic SL sizing (1.5-2x ATR)
    - Volatility filtering (skip low/extreme volatility)
    - Trailing stop calculation
    """
    
    def __init__(
        self,
        period: int = 14,
        sl_multiplier: float = 1.5,
        min_atr_percentage: float = 0.001,  # 0.1% minimum
        max_atr_percentage: float = 0.01,   # 1% maximum for normal trading
        extreme_atr_percentage: float = 0.015  # 1.5% = extreme
    ):
        """
        Initialize ATR calculator.
        
        Args:
            period: ATR calculation period (default 14)
            sl_multiplier: Multiplier for SL calculation (1.5-2x ATR typical)
            min_atr_percentage: Minimum ATR % for tradeable market
            max_atr_percentage: Maximum ATR % for normal volatility
            extreme_atr_percentage: ATR % threshold for extreme volatility
        """
        self.period = period
        self.sl_multiplier = sl_multiplier
        self.min_atr_percentage = min_atr_percentage
        self.max_atr_percentage = max_atr_percentage
        self.extreme_atr_percentage = extreme_atr_percentage
        
        self._tr_history: deque = deque(maxlen=period)
        self._prev_close: Optional[float] = None
        self._current_atr: float = 0.0
        
    def update(
        self,
        high: float,
        low: float,
        close: float
    ) -> ATRResult:
        """
        Update ATR with new candle data.
        
        Args:
            high: Candle high
            low: Candle low  
            close: Candle close
            
        Returns:
            ATRResult with calculations
        """
        # Calculate True Range
        if self._prev_close is None:
            tr = high - low  # First candle: just use range
        else:
            tr = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close)
            )
        
        self._tr_history.append(tr)
        self._prev_close = close
        
        # Calculate ATR (Simple Moving Average of TR)
        if len(self._tr_history) >= self.period:
            self._current_atr = sum(self._tr_history) / len(self._tr_history)
        elif len(self._tr_history) > 0:
            # Use available data for warm-up period
            self._current_atr = sum(self._tr_history) / len(self._tr_history)
        else:
            self._current_atr = high - low  # Fallback
        
        # Calculate ATR as percentage of price
        atr_pct = self._current_atr / close if close > 0 else 0
        
        # Determine volatility level
        if atr_pct < self.min_atr_percentage:
            volatility_level = "LOW"
            is_tradeable = False
            message = f"Low volatility ({atr_pct*100:.3f}%) - avoid trades"
        elif atr_pct < self.max_atr_percentage:
            volatility_level = "NORMAL"
            is_tradeable = True
            message = f"Normal volatility ({atr_pct*100:.3f}%) - good for trading"
        elif atr_pct < self.extreme_atr_percentage:
            volatility_level = "HIGH"
            is_tradeable = True
            message = f"High volatility ({atr_pct*100:.3f}%) - trade with caution"
        else:
            volatility_level = "EXTREME"
            is_tradeable = False
            message = f"Extreme volatility ({atr_pct*100:.3f}%) - avoid trades"
        
        # Calculate suggested SL
        suggested_sl = self._current_atr * self.sl_multiplier
        
        return ATRResult(
            atr=self._current_atr,
            atr_percentage=atr_pct,
            volatility_level=volatility_level,
            is_tradeable=is_tradeable,
            suggested_sl_points=suggested_sl,
            message=message
        )
    
    @property
    def current_atr(self) -> float:
        """Get current ATR value."""
        return self._current_atr
    
    @property
    def is_ready(self) -> bool:
        """Check if ATR has enough data."""
        return len(self._tr_history) >= self.period
    
    def get_dynamic_sl(self, entry_price: float, min_sl: float = 8.0, max_sl: float = 15.0) -> float:
        """
        Calculate dynamic SL based on ATR.
        
        Args:
            entry_price: Trade entry price
            min_sl: Minimum SL points (floor)
            max_sl: Maximum SL points (ceiling)
            
        Returns:
            SL price (entry_price - sl_points)
        """
        if self._current_atr == 0:
            sl_points = 10.0  # Default if ATR not ready
        else:
            sl_points = self._current_atr * self.sl_multiplier
            # Clamp between min and max
            sl_points = max(min_sl, min(max_sl, sl_points))
        
        return entry_price - sl_points
    
    def get_trailing_sl(
        self, 
        current_price: float,
        entry_price: float,
        current_sl: float,
        min_profit_for_trail: float = 10.0
    ) -> float:
        """
        Calculate ATR-based trailing SL.
        
        Args:
            current_price: Current option price
            entry_price: Trade entry price
            current_sl: Current SL level
            min_profit_for_trail: Minimum profit before ATR trail kicks in
            
        Returns:
            New SL price (only if higher than current)
        """
        profit = current_price - entry_price
        
        if profit < min_profit_for_trail or self._current_atr == 0:
            return current_sl
        
        # ATR-based trail: current_price - 1.5 * ATR
        atr_sl = current_price - (self._current_atr * 1.5)
        
        # Only move SL up, never down
        return max(current_sl, atr_sl)
    
    def reset(self) -> None:
        """Reset ATR calculator."""
        self._tr_history.clear()
        self._prev_close = None
        self._current_atr = 0.0


class VolatilityFilter:
    """
    Volatility-based trade filter using ATR.
    
    Skips trading on days with:
    - Very low volatility (no movement = no profit potential)
    - Extreme volatility (high risk of whipsaws)
    """
    
    def __init__(
        self,
        atr_period: int = 14,
        min_daily_atr: float = 50.0,   # Minimum points movement
        max_daily_atr: float = 200.0   # Maximum points movement
    ):
        """
        Initialize volatility filter.
        
        Args:
            atr_period: ATR calculation period
            min_daily_atr: Minimum ATR for tradeable day
            max_daily_atr: Maximum ATR for safe trading
        """
        self.atr_calculator = ATRCalculator(period=atr_period)
        self.min_daily_atr = min_daily_atr
        self.max_daily_atr = max_daily_atr
        
    def update(self, high: float, low: float, close: float) -> ATRResult:
        """Update with new candle data."""
        return self.atr_calculator.update(high, low, close)
    
    def is_tradeable_day(self) -> bool:
        """
        Check if current volatility is suitable for trading.
        
        Returns:
            True if volatility is within acceptable range or not enough data yet
        """
        # During warmup, allow trading
        if not self.atr_calculator.is_ready:
            return True
        
        atr = self.atr_calculator.current_atr
        return self.min_daily_atr <= atr <= self.max_daily_atr
    
    @property
    def current_atr(self) -> float:
        """Get current ATR."""
        return self.atr_calculator.current_atr
    
    def reset(self) -> None:
        """Reset filter."""
        self.atr_calculator.reset()


# Export
__all__ = ['ATRCalculator', 'ATRResult', 'VolatilityFilter']
