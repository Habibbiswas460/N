"""
Additional Strategy Filters Module

Provides supplementary filters to improve trade quality:
1. Volume Filter - Rejects low volume setups
2. Trend Filter - Confirms with higher timeframe direction
3. Volatility Filter - Adapts to market conditions
"""

from typing import Optional, List, Tuple
from dataclasses import dataclass
from collections import deque
from datetime import datetime, time

from loguru import logger


@dataclass
class VolumeAnalysis:
    """Volume analysis result."""
    current_volume: int
    average_volume: float
    volume_ratio: float  # current / average
    is_sufficient: bool
    message: str


class VolumeFilter:
    """
    Volume Filter for N-Structure setups.
    
    Filters out low-volume setups which may lack conviction
    and are more prone to false breakouts.
    """
    
    def __init__(
        self,
        lookback_periods: int = 20,
        min_volume_ratio: float = 0.8,  # At least 80% of average volume
        high_volume_bonus: float = 1.5  # High volume threshold for strength
    ):
        """
        Initialize volume filter.
        
        Args:
            lookback_periods: Number of candles for average volume
            min_volume_ratio: Minimum ratio of current vs average
            high_volume_bonus: Ratio considered "high volume"
        """
        self.lookback_periods = lookback_periods
        self.min_volume_ratio = min_volume_ratio
        self.high_volume_bonus = high_volume_bonus
        
        self._volume_history: deque = deque(maxlen=lookback_periods)
        
    def update(self, volume: int) -> VolumeAnalysis:
        """
        Update volume history and analyze.
        
        Args:
            volume: Current candle volume
            
        Returns:
            VolumeAnalysis with result
        """
        # Calculate average before adding current
        avg_volume = sum(self._volume_history) / len(self._volume_history) if self._volume_history else 0
        
        # Add to history
        self._volume_history.append(volume)
        
        if avg_volume == 0:
            return VolumeAnalysis(
                current_volume=volume,
                average_volume=0,
                volume_ratio=0,
                is_sufficient=True,  # Not enough data, allow trades
                message="Insufficient volume history"
            )
        
        ratio = volume / avg_volume
        is_sufficient = ratio >= self.min_volume_ratio
        
        if ratio >= self.high_volume_bonus:
            message = f"High volume: {ratio:.2f}x average (strong conviction)"
        elif is_sufficient:
            message = f"Normal volume: {ratio:.2f}x average"
        else:
            message = f"Low volume: {ratio:.2f}x average (weak conviction)"
        
        return VolumeAnalysis(
            current_volume=volume,
            average_volume=avg_volume,
            volume_ratio=ratio,
            is_sufficient=is_sufficient,
            message=message
        )
    
    def is_high_volume(self) -> bool:
        """Check if recent volume is high."""
        if not self._volume_history:
            return False
        avg = sum(list(self._volume_history)[:-1]) / (len(self._volume_history) - 1) if len(self._volume_history) > 1 else 0
        return self._volume_history[-1] >= avg * self.high_volume_bonus if avg > 0 else False
    
    def reset(self) -> None:
        """Reset volume history."""
        self._volume_history.clear()


@dataclass
class TrendAnalysis:
    """Trend analysis result."""
    trend_direction: str  # "UP", "DOWN", "SIDEWAYS"
    trend_strength: float  # 0 to 1
    ema_alignment: bool  # Fast > Slow for uptrend
    is_favorable: bool  # Trend supports CE trading
    message: str


class TrendFilter:
    """
    Trend Filter using EMA alignment.
    
    For CE options (calls), we want:
    - Uptrend or sideways (not strong downtrend)
    - Fast EMA above Slow EMA
    """
    
    def __init__(
        self,
        trend_strength_threshold: float = 0.002,  # 0.2% movement for trend
        sideways_threshold: float = 0.001  # 0.1% for sideways
    ):
        """
        Initialize trend filter.
        
        Args:
            trend_strength_threshold: Min change for trend detection
            sideways_threshold: Max change for sideways detection
        """
        self.trend_strength_threshold = trend_strength_threshold
        self.sideways_threshold = sideways_threshold
        
        self._price_history: deque = deque(maxlen=50)
        
    def analyze(
        self,
        current_price: float,
        ema_fast: float,
        ema_slow: float
    ) -> TrendAnalysis:
        """
        Analyze current trend.
        
        Args:
            current_price: Current close price
            ema_fast: Fast EMA value (e.g., EMA 9)
            ema_slow: Slow EMA value (e.g., EMA 15)
            
        Returns:
            TrendAnalysis with result
        """
        self._price_history.append(current_price)
        
        # EMA alignment check
        ema_alignment = ema_fast > ema_slow
        
        # Calculate trend using price vs EMAs and EMA slope
        if len(self._price_history) < 2:
            return TrendAnalysis(
                trend_direction="SIDEWAYS",
                trend_strength=0,
                ema_alignment=ema_alignment,
                is_favorable=True,
                message="Insufficient data for trend"
            )
        
        # Price change from 10 candles ago
        lookback = min(10, len(self._price_history) - 1)
        old_price = self._price_history[-lookback - 1]
        price_change = (current_price - old_price) / old_price if old_price > 0 else 0
        
        # Determine trend direction
        if price_change > self.trend_strength_threshold:
            trend_direction = "UP"
            trend_strength = min(1.0, price_change / (self.trend_strength_threshold * 5))
        elif price_change < -self.trend_strength_threshold:
            trend_direction = "DOWN"
            trend_strength = min(1.0, abs(price_change) / (self.trend_strength_threshold * 5))
        else:
            trend_direction = "SIDEWAYS"
            trend_strength = 0
        
        # For CE options, uptrend or sideways is favorable
        is_favorable = trend_direction != "DOWN" or trend_strength < 0.5
        
        if trend_direction == "UP" and ema_alignment:
            message = f"Strong uptrend (EMA aligned) - favorable for CE"
        elif trend_direction == "UP":
            message = f"Uptrend but EMAs not aligned - caution"
        elif trend_direction == "SIDEWAYS":
            message = f"Sideways market - watch for breakout"
        else:
            message = f"Downtrend - avoid new CE positions" if not is_favorable else "Mild downtrend - proceed with caution"
        
        return TrendAnalysis(
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            ema_alignment=ema_alignment,
            is_favorable=is_favorable,
            message=message
        )
    
    def reset(self) -> None:
        """Reset price history."""
        self._price_history.clear()


@dataclass
class TimeAnalysis:
    """Trading time analysis result."""
    current_time: time
    market_phase: str  # "OPENING", "ACTIVE", "MIDDAY", "CLOSING", "CLOSED"
    is_optimal: bool  # Best trading window
    message: str


class TimeFilter:
    """
    Time-based filter for optimal trading windows.
    
    Market phases:
    - 9:15-9:45: Opening volatility (avoid)
    - 9:45-11:30: Prime trading window
    - 11:30-13:30: Midday lull (reduced conviction)
    - 13:30-14:40: Afternoon activity
    - 14:40+: Position management only
    """
    
    def __init__(
        self,
        optimal_start: time = time(9, 50),
        optimal_end: time = time(11, 30),
        no_new_trades_after: time = time(12, 30),
        market_close: time = time(15, 30)
    ):
        """
        Initialize time filter.
        
        Args:
            optimal_start: Best trading window start
            optimal_end: Best trading window end
            no_new_trades_after: Stop new trades after this
            market_close: Market closing time
        """
        self.optimal_start = optimal_start
        self.optimal_end = optimal_end
        self.no_new_trades_after = no_new_trades_after
        self.market_close = market_close
        
    def analyze(self, current_time: time) -> TimeAnalysis:
        """
        Analyze current trading time.
        
        Args:
            current_time: Current time of day
            
        Returns:
            TimeAnalysis with result
        """
        # Determine market phase
        if current_time < time(9, 15):
            phase = "CLOSED"
            is_optimal = False
            message = "Market not open"
        elif current_time < time(9, 45):
            phase = "OPENING"
            is_optimal = False
            message = "Opening volatility - avoid new trades"
        elif current_time < self.optimal_end:
            phase = "ACTIVE"
            is_optimal = current_time >= self.optimal_start
            message = "Prime trading window" if is_optimal else "Approaching optimal window"
        elif current_time < time(13, 30):
            phase = "MIDDAY"
            is_optimal = current_time <= self.no_new_trades_after
            message = "Midday trading - reduced conviction" if is_optimal else "Avoid new trades"
        elif current_time < time(14, 40):
            phase = "CLOSING"
            is_optimal = False
            message = "Position management only"
        else:
            phase = "CLOSED"
            is_optimal = False
            message = "Market closing - exit positions"
        
        return TimeAnalysis(
            current_time=current_time,
            market_phase=phase,
            is_optimal=is_optimal,
            message=message
        )


class CompositeFilter:
    """
    Combines all filters for trade decision.
    
    Provides a single interface for all filter checks.
    """
    
    def __init__(
        self,
        enable_volume_filter: bool = True,
        enable_trend_filter: bool = True,
        enable_time_filter: bool = True,
        volume_lookback: int = 20,
        min_volume_ratio: float = 0.8
    ):
        """
        Initialize composite filter.
        
        Args:
            enable_volume_filter: Whether to use volume filter
            enable_trend_filter: Whether to use trend filter
            enable_time_filter: Whether to use time filter
            volume_lookback: Lookback for volume average
            min_volume_ratio: Minimum volume ratio
        """
        self.enable_volume_filter = enable_volume_filter
        self.enable_trend_filter = enable_trend_filter
        self.enable_time_filter = enable_time_filter
        
        self.volume_filter = VolumeFilter(
            lookback_periods=volume_lookback,
            min_volume_ratio=min_volume_ratio
        ) if enable_volume_filter else None
        
        self.trend_filter = TrendFilter() if enable_trend_filter else None
        self.time_filter = TimeFilter() if enable_time_filter else None
        
    def check_all(
        self,
        volume: Optional[int] = None,
        price: Optional[float] = None,
        ema_fast: Optional[float] = None,
        ema_slow: Optional[float] = None,
        current_time: Optional[time] = None
    ) -> Tuple[bool, List[str]]:
        """
        Run all enabled filters.
        
        Args:
            volume: Current candle volume
            price: Current close price
            ema_fast: Fast EMA value
            ema_slow: Slow EMA value
            current_time: Current time of day
            
        Returns:
            Tuple of (all_passed, list of messages)
        """
        all_passed = True
        messages = []
        
        # Volume filter
        if self.volume_filter and volume is not None:
            vol_result = self.volume_filter.update(volume)
            if not vol_result.is_sufficient:
                all_passed = False
            messages.append(f"Volume: {vol_result.message}")
        
        # Trend filter
        if self.trend_filter and all(x is not None for x in [price, ema_fast, ema_slow]):
            trend_result = self.trend_filter.analyze(price, ema_fast, ema_slow)
            if not trend_result.is_favorable:
                all_passed = False
            messages.append(f"Trend: {trend_result.message}")
        
        # Time filter
        if self.time_filter and current_time is not None:
            time_result = self.time_filter.analyze(current_time)
            if not time_result.is_optimal:
                all_passed = False
            messages.append(f"Time: {time_result.message}")
        
        return all_passed, messages
    
    def reset(self) -> None:
        """Reset all filters."""
        if self.volume_filter:
            self.volume_filter.reset()
        if self.trend_filter:
            self.trend_filter.reset()


# Export all filter classes
__all__ = [
    'VolumeFilter',
    'VolumeAnalysis',
    'TrendFilter',
    'TrendAnalysis',
    'TimeFilter',
    'TimeAnalysis',
    'CompositeFilter'
]
