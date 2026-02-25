"""
Market Structure Indicator
Tracks key price levels: PDH, PDL, swing points, and range
"""
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime, time, date
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass
class SwingPoint:
    """Represents a swing high or low"""
    price: float
    timestamp: datetime
    type: str  # "HIGH" or "LOW"
    strength: int = 1  # Number of candles confirming the swing


@dataclass
class MarketLevels:
    """Key market structure levels"""
    pdh: float  # Previous Day High
    pdl: float  # Previous Day Low
    pdc: float  # Previous Day Close
    current_high: float  # Today's high
    current_low: float  # Today's low
    
    # Calculated levels
    pivot: float  # (PDH + PDL + PDC) / 3
    r1: float  # Pivot + (PDH - PDL) * 0.382
    r2: float  # Pivot + (PDH - PDL) * 0.618
    s1: float  # Pivot - (PDH - PDL) * 0.382
    s2: float  # Pivot - (PDH - PDL) * 0.618
    
    # Range info
    daily_range: float  # PDH - PDL
    range_type: str  # "WIDE", "NORMAL", "NARROW"


@dataclass 
class SwingAnalysis:
    """Swing point analysis result"""
    recent_swing_high: Optional[SwingPoint]
    recent_swing_low: Optional[SwingPoint]
    trend: str  # "UP", "DOWN", "SIDEWAYS"
    support_zone: Tuple[float, float]  # (lower, upper)
    resistance_zone: Tuple[float, float]  # (lower, upper)


class MarketStructure:
    """
    Market Structure Analysis
    
    Tracks:
    1. Previous day levels (PDH, PDL, PDC)
    2. Pivot points and Fibonacci levels
    3. Swing highs and lows
    4. Support/Resistance zones
    """
    
    def __init__(self, swing_lookback: int = 5, atr_period: int = 14):
        """
        Args:
            swing_lookback: Candles to look for swing confirmation
            atr_period: Period for ATR calculation
        """
        self.swing_lookback = swing_lookback
        self.atr_period = atr_period
        
        # Daily tracking
        self._prev_day_high: Optional[float] = None
        self._prev_day_low: Optional[float] = None
        self._prev_day_close: Optional[float] = None
        self._current_day_high: float = 0.0
        self._current_day_low: float = float('inf')
        self._last_date: Optional[date] = None
        
        # Swing tracking
        self._candle_history: deque = deque(maxlen=50)
        self._swing_highs: List[SwingPoint] = []
        self._swing_lows: List[SwingPoint] = []
        
        # ATR tracking
        self._tr_values: deque = deque(maxlen=atr_period)
        self._atr: float = 0.0
        self._prev_close: Optional[float] = None
        
    def update(self, high: float, low: float, close: float, 
               timestamp: datetime) -> Optional[MarketLevels]:
        """
        Update market structure with new candle
        
        Returns MarketLevels if previous day data is available
        """
        current_date = timestamp.date()
        
        # Check for new day
        if self._last_date is not None and current_date != self._last_date:
            # Store previous day values
            self._prev_day_high = self._current_day_high
            self._prev_day_low = self._current_day_low
            self._prev_day_close = self._prev_close
            
            # Reset current day
            self._current_day_high = high
            self._current_day_low = low
            
            logger.info(f"MarketStructure: New day | PDH={self._prev_day_high:.2f} "
                       f"PDL={self._prev_day_low:.2f} PDC={self._prev_day_close:.2f}")
        else:
            # Update current day high/low
            self._current_day_high = max(self._current_day_high, high)
            self._current_day_low = min(self._current_day_low, low)
            
        self._last_date = current_date
        
        # Update ATR
        self._update_atr(high, low, close)
        self._prev_close = close
        
        # Store candle for swing detection
        self._candle_history.append({
            'high': high,
            'low': low,
            'close': close,
            'timestamp': timestamp
        })
        
        # Detect swings
        self._detect_swings(timestamp)
        
        # Return levels if we have previous day data
        if self._prev_day_high is not None:
            return self._calculate_levels()
        return None
        
    def _update_atr(self, high: float, low: float, close: float):
        """Update ATR calculation"""
        if self._prev_close is not None:
            true_range = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close)
            )
        else:
            true_range = high - low
            
        self._tr_values.append(true_range)
        
        if len(self._tr_values) >= self.atr_period:
            self._atr = sum(self._tr_values) / len(self._tr_values)
            
    def _calculate_levels(self) -> Optional[MarketLevels]:
        """Calculate all market structure levels"""
        pdh = self._prev_day_high
        pdl = self._prev_day_low
        pdc = self._prev_day_close
        
        # Return None if previous day data not available
        if pdh is None or pdl is None or pdc is None:
            return None
        
        # Pivot point
        pivot = (pdh + pdl + pdc) / 3
        daily_range = pdh - pdl
        
        # Fibonacci-based support/resistance
        r1 = pivot + daily_range * 0.382
        r2 = pivot + daily_range * 0.618
        s1 = pivot - daily_range * 0.382
        s2 = pivot - daily_range * 0.618
        
        # Classify range
        avg_range = 150  # Typical NIFTY daily range
        if daily_range > avg_range * 1.3:
            range_type = "WIDE"
        elif daily_range < avg_range * 0.7:
            range_type = "NARROW"
        else:
            range_type = "NORMAL"
            
        return MarketLevels(
            pdh=round(pdh, 2),
            pdl=round(pdl, 2),
            pdc=round(pdc, 2),
            current_high=round(self._current_day_high, 2),
            current_low=round(self._current_day_low, 2),
            pivot=round(pivot, 2),
            r1=round(r1, 2),
            r2=round(r2, 2),
            s1=round(s1, 2),
            s2=round(s2, 2),
            daily_range=round(daily_range, 2),
            range_type=range_type
        )
        
    def _detect_swings(self, timestamp: datetime):
        """Detect swing highs and lows"""
        if len(self._candle_history) < self.swing_lookback * 2 + 1:
            return
            
        candles = list(self._candle_history)
        mid_idx = len(candles) - self.swing_lookback - 1
        
        if mid_idx < self.swing_lookback:
            return
            
        mid_candle = candles[mid_idx]
        
        # Check for swing high
        is_swing_high = True
        for i in range(mid_idx - self.swing_lookback, mid_idx):
            if candles[i]['high'] >= mid_candle['high']:
                is_swing_high = False
                break
        if is_swing_high:
            for i in range(mid_idx + 1, mid_idx + self.swing_lookback + 1):
                if candles[i]['high'] >= mid_candle['high']:
                    is_swing_high = False
                    break
                    
        if is_swing_high:
            swing = SwingPoint(
                price=mid_candle['high'],
                timestamp=mid_candle['timestamp'],
                type="HIGH",
                strength=self.swing_lookback
            )
            self._swing_highs.append(swing)
            # Keep only recent swings
            self._swing_highs = self._swing_highs[-10:]
            
        # Check for swing low
        is_swing_low = True
        for i in range(mid_idx - self.swing_lookback, mid_idx):
            if candles[i]['low'] <= mid_candle['low']:
                is_swing_low = False
                break
        if is_swing_low:
            for i in range(mid_idx + 1, mid_idx + self.swing_lookback + 1):
                if candles[i]['low'] <= mid_candle['low']:
                    is_swing_low = False
                    break
                    
        if is_swing_low:
            swing = SwingPoint(
                price=mid_candle['low'],
                timestamp=mid_candle['timestamp'],
                type="LOW",
                strength=self.swing_lookback
            )
            self._swing_lows.append(swing)
            self._swing_lows = self._swing_lows[-10:]
            
    def get_swing_analysis(self) -> SwingAnalysis:
        """Get current swing point analysis"""
        recent_high = self._swing_highs[-1] if self._swing_highs else None
        recent_low = self._swing_lows[-1] if self._swing_lows else None
        
        # Determine trend from swings
        trend = "SIDEWAYS"
        if len(self._swing_highs) >= 2 and len(self._swing_lows) >= 2:
            hh = self._swing_highs[-1].price > self._swing_highs[-2].price
            hl = self._swing_lows[-1].price > self._swing_lows[-2].price
            lh = self._swing_highs[-1].price < self._swing_highs[-2].price
            ll = self._swing_lows[-1].price < self._swing_lows[-2].price
            
            if hh and hl:
                trend = "UP"
            elif lh and ll:
                trend = "DOWN"
                
        # Calculate support/resistance zones
        buffer = self._atr * 0.5 if self._atr > 0 else 5.0
        
        if recent_low:
            support_zone = (recent_low.price - buffer, recent_low.price + buffer)
        else:
            support_zone = (0.0, 0.0)
            
        if recent_high:
            resistance_zone = (recent_high.price - buffer, recent_high.price + buffer)
        else:
            resistance_zone = (0.0, 0.0)
            
        return SwingAnalysis(
            recent_swing_high=recent_high,
            recent_swing_low=recent_low,
            trend=trend,
            support_zone=support_zone,
            resistance_zone=resistance_zone
        )
        
    def get_atr(self) -> float:
        """Get current ATR value"""
        return round(self._atr, 2)
        
    def get_pdh(self) -> Optional[float]:
        """Get Previous Day High"""
        return self._prev_day_high
        
    def get_pdl(self) -> Optional[float]:
        """Get Previous Day Low"""
        return self._prev_day_low
        
    def is_at_support(self, price: float, tolerance: float = 5.0) -> bool:
        """Check if price is at a support level"""
        if self._prev_day_low:
            if abs(price - self._prev_day_low) <= tolerance:
                return True
        if self._swing_lows:
            recent_low = self._swing_lows[-1].price
            if abs(price - recent_low) <= tolerance:
                return True
        return False
        
    def is_at_resistance(self, price: float, tolerance: float = 5.0) -> bool:
        """Check if price is at a resistance level"""
        if self._prev_day_high:
            if abs(price - self._prev_day_high) <= tolerance:
                return True
        if self._swing_highs:
            recent_high = self._swing_highs[-1].price
            if abs(price - recent_high) <= tolerance:
                return True
        return False
        
    def reset(self):
        """Reset market structure"""
        self._prev_day_high = None
        self._prev_day_low = None
        self._prev_day_close = None
        self._current_day_high = 0.0
        self._current_day_low = float('inf')
        self._last_date = None
        self._candle_history.clear()
        self._swing_highs.clear()
        self._swing_lows.clear()
        self._tr_values.clear()
        self._atr = 0.0
        self._prev_close = None
