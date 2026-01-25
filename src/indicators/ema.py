"""
EMA (Exponential Moving Average) Indicator Module

Provides incremental EMA calculation for real-time data.
Supports EMA(9) and EMA(15) as per N-Structure strategy.
"""

from typing import Optional, List, Dict
from dataclasses import dataclass, field
from collections import deque

from loguru import logger


@dataclass
class EMAValue:
    """Container for EMA calculation result."""
    period: int
    value: float
    price: float  # The price used in this calculation
    data_points: int  # Total data points processed
    
    def __str__(self) -> str:
        return f"EMA({self.period}): {self.value:.2f}"


class IncrementalEMA:
    """
    Incremental EMA Calculator.
    
    Formula: EMA = Price * k + EMA_prev * (1 - k)
    where k = 2 / (period + 1)
    
    Incremental calculation is more efficient than recalculating
    from the full price history on each update.
    """
    
    def __init__(self, period: int):
        """
        Initialize EMA calculator.
        
        Args:
            period: EMA period (e.g., 9, 15, 21)
        """
        if period < 1:
            raise ValueError("Period must be >= 1")
            
        self.period = period
        self.multiplier = 2.0 / (period + 1)
        
        self._value: Optional[float] = None
        self._data_points: int = 0
        self._prices: deque = deque(maxlen=period)  # For initial SMA
        self._initialized: bool = False
        
    def update(self, price: float) -> EMAValue:
        """
        Update EMA with new price.
        
        Args:
            price: New closing price
            
        Returns:
            Updated EMA value
        """
        self._data_points += 1
        self._prices.append(price)
        
        if not self._initialized:
            # Use SMA for initial value once we have enough data
            if len(self._prices) >= self.period:
                self._value = sum(self._prices) / len(self._prices)
                self._initialized = True
            else:
                # Not enough data yet, return current SMA
                self._value = sum(self._prices) / len(self._prices)
        else:
            # Incremental EMA calculation
            self._value = (price * self.multiplier) + (self._value * (1 - self.multiplier))
        
        return EMAValue(
            period=self.period,
            value=self._value,
            price=price,
            data_points=self._data_points
        )
    
    @property
    def value(self) -> Optional[float]:
        """Get current EMA value."""
        return self._value
    
    @property
    def is_ready(self) -> bool:
        """Check if EMA has enough data for valid calculation."""
        return self._initialized
    
    @property
    def data_points(self) -> int:
        """Get number of data points processed."""
        return self._data_points
    
    def reset(self) -> None:
        """Reset the EMA calculator."""
        self._value = None
        self._data_points = 0
        self._prices.clear()
        self._initialized = False


class EMASet:
    """
    Collection of EMAs for a single instrument.
    
    Manages multiple EMA periods (e.g., 9 and 15) together.
    """
    
    def __init__(self, periods: List[int] = None):
        """
        Initialize EMA set.
        
        Args:
            periods: List of EMA periods (default: [9, 15])
        """
        self.periods = periods or [9, 15]
        self._emas: Dict[int, IncrementalEMA] = {
            period: IncrementalEMA(period) for period in self.periods
        }
        
    def update(self, price: float) -> Dict[int, EMAValue]:
        """
        Update all EMAs with new price.
        
        Args:
            price: New closing price
            
        Returns:
            Dict of {period: EMAValue}
        """
        results = {}
        for period, ema in self._emas.items():
            results[period] = ema.update(price)
        return results
    
    def get_value(self, period: int) -> Optional[float]:
        """
        Get current EMA value for a specific period.
        
        Args:
            period: EMA period
            
        Returns:
            EMA value if available
        """
        ema = self._emas.get(period)
        return ema.value if ema else None
    
    def get_ema(self, period: int) -> Optional[IncrementalEMA]:
        """Get EMA calculator for a specific period."""
        return self._emas.get(period)
    
    @property
    def ema_fast(self) -> Optional[float]:
        """Get fast EMA value (smallest period)."""
        min_period = min(self.periods)
        return self.get_value(min_period)
    
    @property
    def ema_slow(self) -> Optional[float]:
        """Get slow EMA value (largest period)."""
        max_period = max(self.periods)
        return self.get_value(max_period)
    
    @property
    def is_ready(self) -> bool:
        """Check if all EMAs are ready."""
        return all(ema.is_ready for ema in self._emas.values())
    
    def reset(self) -> None:
        """Reset all EMAs."""
        for ema in self._emas.values():
            ema.reset()


class EMAManager:
    """
    Manages EMAs for multiple instruments.
    
    Typical usage:
    - Track EMA(9, 15) for Index
    - Track EMA(9, 15) for Option
    """
    
    def __init__(self, default_periods: List[int] = None):
        """
        Initialize EMA manager.
        
        Args:
            default_periods: Default EMA periods for new instruments
        """
        self.default_periods = default_periods or [9, 15]
        self._ema_sets: Dict[str, EMASet] = {}
        
    def get_or_create(self, token: str, periods: List[int] = None) -> EMASet:
        """
        Get or create EMA set for a token.
        
        Args:
            token: Instrument token
            periods: EMA periods (uses default if None)
            
        Returns:
            EMASet for the token
        """
        if token not in self._ema_sets:
            self._ema_sets[token] = EMASet(periods or self.default_periods)
        return self._ema_sets[token]
    
    def update(self, token: str, price: float) -> Dict[int, EMAValue]:
        """
        Update EMAs for a token.
        
        Args:
            token: Instrument token
            price: New closing price
            
        Returns:
            Dict of {period: EMAValue}
        """
        ema_set = self.get_or_create(token)
        return ema_set.update(price)
    
    def get_values(self, token: str) -> Dict[int, Optional[float]]:
        """
        Get all EMA values for a token.
        
        Args:
            token: Instrument token
            
        Returns:
            Dict of {period: value}
        """
        ema_set = self._ema_sets.get(token)
        if not ema_set:
            return {}
        return {period: ema_set.get_value(period) for period in ema_set.periods}
    
    def is_price_above_ema(
        self,
        token: str,
        price: float,
        period: int
    ) -> Optional[bool]:
        """
        Check if price is above EMA.
        
        Args:
            token: Instrument token
            price: Price to check
            period: EMA period
            
        Returns:
            True if above, False if below, None if EMA not ready
        """
        ema_set = self._ema_sets.get(token)
        if not ema_set:
            return None
            
        ema_value = ema_set.get_value(period)
        if ema_value is None:
            return None
            
        return price > ema_value
    
    def is_price_at_ema(
        self,
        token: str,
        price: float,
        period: int,
        tolerance_percent: float = 0.1
    ) -> Optional[bool]:
        """
        Check if price is at/near EMA (within tolerance).
        
        Args:
            token: Instrument token
            price: Price to check
            period: EMA period
            tolerance_percent: Percentage tolerance (default 0.1%)
            
        Returns:
            True if at EMA, False otherwise, None if EMA not ready
        """
        ema_set = self._ema_sets.get(token)
        if not ema_set:
            return None
            
        ema_value = ema_set.get_value(period)
        if ema_value is None:
            return None
            
        tolerance = ema_value * (tolerance_percent / 100)
        return abs(price - ema_value) <= tolerance
    
    def remove(self, token: str) -> None:
        """Remove EMA tracking for a token."""
        self._ema_sets.pop(token, None)
    
    def reset(self, token: str = None) -> None:
        """
        Reset EMAs.
        
        Args:
            token: Specific token to reset, or None for all
        """
        if token:
            ema_set = self._ema_sets.get(token)
            if ema_set:
                ema_set.reset()
        else:
            for ema_set in self._ema_sets.values():
                ema_set.reset()
    
    @property
    def tokens(self) -> List[str]:
        """Get list of tracked tokens."""
        return list(self._ema_sets.keys())


# Convenience function for quick EMA calculation on a series
def calculate_ema_series(prices: List[float], period: int) -> List[float]:
    """
    Calculate EMA for an entire price series.
    
    Args:
        prices: List of prices (oldest first)
        period: EMA period
        
    Returns:
        List of EMA values (same length as prices)
    """
    ema = IncrementalEMA(period)
    return [ema.update(p).value for p in prices]
