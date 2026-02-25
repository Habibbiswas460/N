"""
Volume Profile Indicator
Calculates Point of Control (POC), Value Area High (VAH), Value Area Low (VAL)
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime, date, time
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class VolumeProfileLevels:
    """Volume Profile key levels"""
    poc: float  # Point of Control - highest volume price
    vah: float  # Value Area High - upper 70% volume boundary
    val: float  # Value Area Low - lower 70% volume boundary
    
    # Additional info
    total_volume: int
    poc_volume: int  # Volume at POC
    value_area_volume: int  # Volume within value area
    profile_type: str  # "P" (normal), "b" (double bottom), "D" (double top), "B" (flat)


@dataclass
class PriceLevel:
    """Volume at a price level"""
    price: float
    volume: int
    buy_volume: int = 0
    sell_volume: int = 0


class VolumeProfile:
    """
    Volume Profile Calculator
    
    Creates volume distribution profile and identifies:
    - POC (Point of Control) - Price with highest volume
    - VAH (Value Area High) - Upper boundary of 70% volume
    - VAL (Value Area Low) - Lower boundary of 70% volume
    
    Professional traders use these levels for:
    - POC acts as magnet in sideways markets
    - VAH/VAL are mean reversion zones
    - Breakout beyond VAH/VAL signals trend
    """
    
    def __init__(self, tick_size: float = 0.5, value_area_pct: float = 0.70):
        """
        Args:
            tick_size: Price grouping size (0.5 for NIFTY)
            value_area_pct: Percentage for value area (default 70%)
        """
        self.tick_size = tick_size
        self.value_area_pct = value_area_pct
        
        # Volume at each price level
        self._volume_map: Dict[float, int] = defaultdict(int)
        self._buy_volume_map: Dict[float, int] = defaultdict(int)
        self._sell_volume_map: Dict[float, int] = defaultdict(int)
        
        # Tracking
        self._last_date: Optional[date] = None
        self._prev_close: Optional[float] = None
        self._total_volume: int = 0
        
        # Previous day's profile
        self._prev_poc: Optional[float] = None
        self._prev_vah: Optional[float] = None
        self._prev_val: Optional[float] = None
        
    def update(self, high: float, low: float, close: float, 
               volume: int, timestamp: datetime) -> Optional[VolumeProfileLevels]:
        """
        Update volume profile with new candle
        
        Distributes volume across price levels touched by candle
        Returns calculated levels if enough data
        """
        current_date = timestamp.date()
        
        # Check for new day
        if self._last_date is not None and current_date != self._last_date:
            # Calculate and store previous day's profile
            levels = self._calculate_levels()
            if levels:
                self._prev_poc = levels.poc
                self._prev_vah = levels.vah
                self._prev_val = levels.val
                logger.info(f"VolumeProfile: New day | POC={self._prev_poc:.2f} "
                           f"VAH={self._prev_vah:.2f} VAL={self._prev_val:.2f}")
            
            # Reset for new day
            self._volume_map.clear()
            self._buy_volume_map.clear()
            self._sell_volume_map.clear()
            self._total_volume = 0
            
        self._last_date = current_date
        
        # Distribute volume across price levels
        self._distribute_volume(high, low, close, volume)
        self._prev_close = close
        
        # Return current levels
        return self._calculate_levels()
        
    def _round_to_tick(self, price: float) -> float:
        """Round price to nearest tick size"""
        return round(price / self.tick_size) * self.tick_size
        
    def _distribute_volume(self, high: float, low: float, close: float, volume: int):
        """
        Distribute candle volume across touched price levels
        
        Uses TPO-like distribution where volume is spread
        across all prices between high and low
        """
        if volume <= 0:
            return
            
        # Round to tick size
        high_tick = self._round_to_tick(high)
        low_tick = self._round_to_tick(low)
        close_tick = self._round_to_tick(close)
        
        # Calculate number of ticks
        num_ticks = max(1, int((high_tick - low_tick) / self.tick_size) + 1)
        volume_per_tick = volume // num_ticks
        remainder = volume % num_ticks
        
        # Distribute volume
        current_price = low_tick
        while current_price <= high_tick:
            # Give remainder to close price level
            extra = remainder if current_price == close_tick else 0
            tick_volume = volume_per_tick + extra
            
            self._volume_map[current_price] += tick_volume
            self._total_volume += tick_volume
            
            # Estimate buy/sell volume based on close location
            if self._prev_close is not None:
                if close > self._prev_close:
                    self._buy_volume_map[current_price] += tick_volume
                else:
                    self._sell_volume_map[current_price] += tick_volume
                    
            current_price += self.tick_size
            
    def _calculate_levels(self) -> Optional[VolumeProfileLevels]:
        """Calculate POC, VAH, VAL from current volume profile"""
        if not self._volume_map or self._total_volume == 0:
            return None
            
        # Find POC (price with highest volume)
        poc_price = max(self._volume_map.keys(), key=lambda p: self._volume_map[p])
        poc_volume = self._volume_map[poc_price]
        
        # Calculate Value Area (70% of volume)
        target_volume = int(self._total_volume * self.value_area_pct)
        
        # Start from POC and expand outward
        sorted_prices = sorted(self._volume_map.keys())
        poc_idx = sorted_prices.index(poc_price)
        
        # Initialize value area
        value_area_volume = poc_volume
        lower_idx = poc_idx
        upper_idx = poc_idx
        
        # Expand value area until we capture target volume
        while value_area_volume < target_volume:
            # Get volumes at next levels
            lower_vol = 0
            upper_vol = 0
            
            if lower_idx > 0:
                lower_vol = self._volume_map[sorted_prices[lower_idx - 1]]
            if upper_idx < len(sorted_prices) - 1:
                upper_vol = self._volume_map[sorted_prices[upper_idx + 1]]
                
            # If no more levels to expand
            if lower_vol == 0 and upper_vol == 0:
                break
                
            # Expand toward higher volume side
            if lower_vol >= upper_vol and lower_idx > 0:
                lower_idx -= 1
                value_area_volume += lower_vol
            elif upper_idx < len(sorted_prices) - 1:
                upper_idx += 1
                value_area_volume += upper_vol
            elif lower_idx > 0:
                lower_idx -= 1
                value_area_volume += lower_vol
            else:
                break
                
        val = sorted_prices[lower_idx]
        vah = sorted_prices[upper_idx]
        
        # Determine profile type
        profile_type = self._classify_profile(sorted_prices)
        
        return VolumeProfileLevels(
            poc=round(poc_price, 2),
            vah=round(vah, 2),
            val=round(val, 2),
            total_volume=self._total_volume,
            poc_volume=poc_volume,
            value_area_volume=value_area_volume,
            profile_type=profile_type
        )
        
    def _classify_profile(self, sorted_prices: List[float]) -> str:
        """
        Classify the shape of the volume profile
        
        P = Normal (bell curve) - most volume in middle
        b = Bottom heavy - more volume at lower prices
        D = Top heavy - more volume at upper prices
        B = Balanced/Flat - even distribution
        """
        if len(sorted_prices) < 5:
            return "P"
            
        # Split into thirds
        third = len(sorted_prices) // 3
        
        lower_vol = sum(self._volume_map[p] for p in sorted_prices[:third])
        middle_vol = sum(self._volume_map[p] for p in sorted_prices[third:2*third])
        upper_vol = sum(self._volume_map[p] for p in sorted_prices[2*third:])
        
        total = lower_vol + middle_vol + upper_vol
        if total == 0:
            return "P"
            
        lower_pct = lower_vol / total
        middle_pct = middle_vol / total
        upper_pct = upper_vol / total
        
        if middle_pct > 0.4:
            return "P"  # Normal distribution
        elif lower_pct > upper_pct * 1.5:
            return "b"  # Bottom heavy
        elif upper_pct > lower_pct * 1.5:
            return "D"  # Top heavy
        else:
            return "B"  # Balanced/Flat
            
    def get_previous_levels(self) -> Optional[VolumeProfileLevels]:
        """Get previous day's volume profile levels"""
        if self._prev_poc is None:
            return None
            
        return VolumeProfileLevels(
            poc=self._prev_poc,
            vah=self._prev_vah,
            val=self._prev_val,
            total_volume=0,
            poc_volume=0,
            value_area_volume=0,
            profile_type="P"
        )
        
    def get_poc(self) -> Optional[float]:
        """Get current Point of Control"""
        levels = self._calculate_levels()
        return levels.poc if levels else None
        
    def get_vah(self) -> Optional[float]:
        """Get current Value Area High"""
        levels = self._calculate_levels()
        return levels.vah if levels else None
        
    def get_val(self) -> Optional[float]:
        """Get current Value Area Low"""
        levels = self._calculate_levels()
        return levels.val if levels else None
        
    def is_in_value_area(self, price: float) -> bool:
        """Check if price is within value area"""
        levels = self._calculate_levels()
        if not levels:
            return False
        return levels.val <= price <= levels.vah
        
    def is_above_value_area(self, price: float) -> bool:
        """Check if price is above value area"""
        levels = self._calculate_levels()
        if not levels:
            return False
        return price > levels.vah
        
    def is_below_value_area(self, price: float) -> bool:
        """Check if price is below value area"""
        levels = self._calculate_levels()
        if not levels:
            return False
        return price < levels.val
        
    def get_trading_bias(self, current_price: float) -> str:
        """
        Get trading bias based on volume profile
        
        Returns:
            "LONG_AT_VAL" - Price at VAL, look for longs
            "SHORT_AT_VAH" - Price at VAH, look for shorts  
            "LONG_ABOVE_VAH" - Breakout above value area
            "SHORT_BELOW_VAL" - Breakdown below value area
            "NEUTRAL" - Price in middle of value area
        """
        levels = self._calculate_levels()
        if not levels:
            return "NEUTRAL"
            
        tolerance = self.tick_size * 3
        
        # Check proximity to levels
        at_val = abs(current_price - levels.val) <= tolerance
        at_vah = abs(current_price - levels.vah) <= tolerance
        at_poc = abs(current_price - levels.poc) <= tolerance
        
        if at_val:
            return "LONG_AT_VAL"
        elif at_vah:
            return "SHORT_AT_VAH"
        elif current_price > levels.vah + tolerance:
            return "LONG_ABOVE_VAH"
        elif current_price < levels.val - tolerance:
            return "SHORT_BELOW_VAL"
        elif at_poc:
            return "NEUTRAL_AT_POC"
        else:
            return "NEUTRAL"
            
    def reset(self):
        """Reset volume profile"""
        self._volume_map.clear()
        self._buy_volume_map.clear()
        self._sell_volume_map.clear()
        self._last_date = None
        self._prev_close = None
        self._total_volume = 0
        self._prev_poc = None
        self._prev_vah = None
        self._prev_val = None
