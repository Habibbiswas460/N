"""
Candle Builder Module

Aggregates real-time ticks into 1-minute OHLC candles.
Uses wall-clock time bucketing for consistent candle boundaries.
"""

from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field
from collections import defaultdict

from loguru import logger

from data.market_feed import TickData


@dataclass(frozen=True)
class Candle:
    """
    Immutable OHLC candle data.
    
    Using frozen=True prevents accidental mutation.
    """
    token: str
    timestamp: datetime  # Candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0
    
    @property
    def is_bullish(self) -> bool:
        """Check if candle is bullish (close > open)."""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """Check if candle is bearish (close < open)."""
        return self.close < self.open
    
    @property
    def body_size(self) -> float:
        """Get candle body size."""
        return abs(self.close - self.open)
    
    @property
    def range(self) -> float:
        """Get candle range (high - low)."""
        return self.high - self.low
    
    @property
    def upper_wick(self) -> float:
        """Get upper wick size."""
        return self.high - max(self.open, self.close)
    
    @property
    def lower_wick(self) -> float:
        """Get lower wick size."""
        return min(self.open, self.close) - self.low
    
    def __str__(self) -> str:
        return (
            f"Candle({self.token} @ {self.timestamp.strftime('%H:%M')} | "
            f"O:{self.open:.2f} H:{self.high:.2f} L:{self.low:.2f} C:{self.close:.2f})"
        )


@dataclass
class CandleBuilder:
    """
    Mutable candle being built from ticks.
    
    This is converted to immutable Candle when complete.
    """
    token: str
    timestamp: datetime  # Candle open time
    open: float = 0.0
    high: float = 0.0
    low: float = float('inf')
    close: float = 0.0
    volume: int = 0
    tick_count: int = 0
    _initialized: bool = False
    
    def add_tick(self, tick: TickData) -> None:
        """
        Add a tick to the candle.
        
        Args:
            tick: Tick data to incorporate
        """
        price = tick.ltp
        
        if not self._initialized:
            self.open = price
            self.high = price
            self.low = price
            self._initialized = True
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
            
        self.close = price
        self.tick_count += 1
        
        if tick.volume:
            self.volume = tick.volume  # Use cumulative volume
            
    def to_candle(self) -> Candle:
        """Convert to immutable Candle."""
        return Candle(
            token=self.token,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low if self.low != float('inf') else self.open,
            close=self.close,
            volume=self.volume,
            tick_count=self.tick_count
        )
    
    @property
    def is_valid(self) -> bool:
        """Check if candle has valid data."""
        return self._initialized and self.tick_count > 0


# Type alias for candle callback
CandleCallback = Callable[[Candle], None]


class CandleAggregator:
    """
    Aggregates ticks into time-based candles.
    
    Features:
    - Wall-clock time bucketing (e.g., 09:15:00 - 09:15:59)
    - Automatic candle completion on boundary
    - Candle history buffer per token
    - Callbacks on candle completion
    """
    
    def __init__(
        self,
        timeframe_seconds: int = 60,
        buffer_size: int = 100
    ):
        """
        Initialize candle aggregator.
        
        Args:
            timeframe_seconds: Candle duration in seconds (60 for 1-min)
            buffer_size: Number of candles to keep in history
        """
        self.timeframe_seconds = timeframe_seconds
        self.buffer_size = buffer_size
        
        # Current candles being built (per token)
        self._building: Dict[str, CandleBuilder] = {}
        
        # Completed candle history (per token)
        self._history: Dict[str, List[Candle]] = defaultdict(list)
        
        # Callbacks for completed candles
        self._callbacks: List[CandleCallback] = []
        
    def _get_candle_timestamp(self, dt: datetime) -> datetime:
        """
        Get candle open timestamp for a given datetime.
        
        Rounds down to the nearest candle boundary.
        
        Args:
            dt: Datetime to round
            
        Returns:
            Candle open timestamp
        """
        seconds = dt.second + dt.minute * 60 + dt.hour * 3600
        candle_seconds = (seconds // self.timeframe_seconds) * self.timeframe_seconds
        
        return dt.replace(
            hour=candle_seconds // 3600,
            minute=(candle_seconds % 3600) // 60,
            second=candle_seconds % 60,
            microsecond=0
        )
    
    def _complete_candle(self, token: str) -> Optional[Candle]:
        """
        Complete the current candle for a token.
        
        Args:
            token: Instrument token
            
        Returns:
            Completed Candle if valid, None otherwise
        """
        builder = self._building.get(token)
        if not builder or not builder.is_valid:
            return None
            
        candle = builder.to_candle()
        
        # Add to history
        self._history[token].append(candle)
        
        # Trim history if needed
        if len(self._history[token]) > self.buffer_size:
            self._history[token] = self._history[token][-self.buffer_size:]
        
        # Clear builder
        del self._building[token]
        
        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(candle)
            except Exception as e:
                logger.error(f"Candle callback error: {e}")
        
        logger.debug(f"Candle complete: {candle}")
        return candle
    
    def process_tick(self, tick: TickData) -> Optional[Candle]:
        """
        Process a tick, potentially completing a candle.
        
        Args:
            tick: Tick data to process
            
        Returns:
            Completed Candle if boundary crossed, None otherwise
        """
        token = tick.token
        tick_candle_time = self._get_candle_timestamp(tick.timestamp)
        
        completed = None
        
        # Check if we need to complete existing candle
        if token in self._building:
            builder = self._building[token]
            if builder.timestamp != tick_candle_time:
                # New candle period - complete the old one
                completed = self._complete_candle(token)
        
        # Create or update candle builder
        if token not in self._building:
            self._building[token] = CandleBuilder(
                token=token,
                timestamp=tick_candle_time
            )
            
        self._building[token].add_tick(tick)
        
        return completed
    
    def force_complete(self, token: str) -> Optional[Candle]:
        """
        Force complete a candle (e.g., at EOD).
        
        Args:
            token: Instrument token
            
        Returns:
            Completed Candle if valid
        """
        return self._complete_candle(token)
    
    def force_complete_all(self) -> List[Candle]:
        """
        Force complete all candles.
        
        Returns:
            List of completed candles
        """
        completed = []
        tokens = list(self._building.keys())
        for token in tokens:
            candle = self._complete_candle(token)
            if candle:
                completed.append(candle)
        return completed
    
    def add_callback(self, callback: CandleCallback) -> None:
        """
        Add callback for completed candles.
        
        Args:
            callback: Function to call with completed Candle
        """
        self._callbacks.append(callback)
        
    def remove_callback(self, callback: CandleCallback) -> None:
        """Remove a candle callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def get_candles(self, token: str, count: Optional[int] = None) -> List[Candle]:
        """
        Get completed candles for a token.
        
        Args:
            token: Instrument token
            count: Number of candles to return (None for all)
            
        Returns:
            List of candles (oldest first)
        """
        candles = self._history.get(token, [])
        if count:
            return candles[-count:]
        return candles.copy()
    
    def get_latest_candle(self, token: str) -> Optional[Candle]:
        """
        Get most recent completed candle.
        
        Args:
            token: Instrument token
            
        Returns:
            Latest candle if available
        """
        candles = self._history.get(token, [])
        return candles[-1] if candles else None
    
    def get_building_candle(self, token: str) -> Optional[Candle]:
        """
        Get current candle being built (incomplete).
        
        Args:
            token: Instrument token
            
        Returns:
            Current incomplete candle as Candle object
        """
        builder = self._building.get(token)
        if builder and builder.is_valid:
            return builder.to_candle()
        return None
    
    def get_ohlc_arrays(self, token: str, count: Optional[int] = None) -> Dict[str, List[float]]:
        """
        Get OHLC data as separate arrays (useful for indicators).
        
        Args:
            token: Instrument token
            count: Number of candles
            
        Returns:
            Dict with 'open', 'high', 'low', 'close' arrays
        """
        candles = self.get_candles(token, count)
        
        return {
            'open': [c.open for c in candles],
            'high': [c.high for c in candles],
            'low': [c.low for c in candles],
            'close': [c.close for c in candles],
            'volume': [c.volume for c in candles],
            'timestamp': [c.timestamp for c in candles],
        }
    
    def clear(self, token: Optional[str] = None) -> None:
        """
        Clear candle data.
        
        Args:
            token: Specific token to clear, or None for all
        """
        if token:
            self._building.pop(token, None)
            self._history.pop(token, None)
        else:
            self._building.clear()
            self._history.clear()
    
    @property
    def tokens(self) -> List[str]:
        """Get list of tokens with candle data."""
        return list(set(self._building.keys()) | set(self._history.keys()))
