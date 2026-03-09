"""
Trading Filters for N-Structure Strategy
Volume, Trend, and Composite filters for trade validation
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Result of a filter check"""
    passed: bool
    reason: str = ""
    score: float = 1.0  # 0.0 to 1.0 confidence


class VolumeFilter:
    """
    Volume Filter - Validates trades based on volume activity
    
    Ensures we're trading in liquid conditions with sufficient
    volume to avoid slippage and false breakouts.
    """
    
    def __init__(self, lookback_periods: int = 20, min_volume_ratio: float = 1.0):
        """
        Args:
            lookback_periods: Number of candles to calculate average volume
            min_volume_ratio: Minimum ratio of current volume to average (1.0 = average)
        """
        self.lookback_periods = lookback_periods
        self.min_volume_ratio = min_volume_ratio
        self.volume_history: deque = deque(maxlen=lookback_periods)
        self._avg_volume: float = 0.0
    
    def update(self, volume: int) -> None:
        """Update volume history with new candle volume"""
        self.volume_history.append(volume)
        if len(self.volume_history) > 0:
            self._avg_volume = sum(self.volume_history) / len(self.volume_history)
    
    def get_average_volume(self) -> float:
        """Get current average volume"""
        return self._avg_volume
    
    def check(self, current_volume: int) -> FilterResult:
        """
        Check if current volume passes filter
        
        Args:
            current_volume: Volume of current candle
            
        Returns:
            FilterResult with pass/fail status
        """
        if self._avg_volume == 0 or len(self.volume_history) < self.lookback_periods // 2:
            # Not enough data, allow trade
            return FilterResult(passed=True, reason="Insufficient volume data", score=0.5)
        
        volume_ratio = current_volume / self._avg_volume
        
        if volume_ratio >= self.min_volume_ratio:
            return FilterResult(
                passed=True, 
                reason=f"Volume OK ({volume_ratio:.2f}x avg)",
                score=min(volume_ratio / 2.0, 1.0)  # Cap at 1.0
            )
        else:
            return FilterResult(
                passed=False,
                reason=f"Low volume ({volume_ratio:.2f}x avg < {self.min_volume_ratio}x required)",
                score=volume_ratio / self.min_volume_ratio
            )
    
    def reset(self) -> None:
        """Reset filter state"""
        self.volume_history.clear()
        self._avg_volume = 0.0


class TrendFilter:
    """
    Trend Filter - Validates trades align with higher timeframe trend
    
    Uses EMA relationship to determine trend direction and strength.
    Helps avoid counter-trend trades.
    """
    
    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        """
        Args:
            fast_period: Fast EMA period
            slow_period: Slow EMA period
        """
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.fast_ema: float = 0.0
        self.slow_ema: float = 0.0
        self._price_count: int = 0
        self._fast_multiplier = 2 / (fast_period + 1)
        self._slow_multiplier = 2 / (slow_period + 1)
    
    def update(self, close: float) -> None:
        """Update EMAs with new price"""
        self._price_count += 1
        
        if self._price_count == 1:
            self.fast_ema = close
            self.slow_ema = close
        else:
            self.fast_ema = (close - self.fast_ema) * self._fast_multiplier + self.fast_ema
            self.slow_ema = (close - self.slow_ema) * self._slow_multiplier + self.slow_ema
    
    def get_trend(self) -> str:
        """Get current trend direction"""
        if self._price_count < self.slow_period:
            return "NEUTRAL"
        
        if self.fast_ema > self.slow_ema * 1.001:  # 0.1% buffer
            return "BULLISH"
        elif self.fast_ema < self.slow_ema * 0.999:
            return "BEARISH"
        else:
            return "NEUTRAL"
    
    def check(self, direction: str = "LONG") -> FilterResult:
        """
        Check if trade direction aligns with trend
        
        Args:
            direction: Trade direction ("LONG" or "SHORT")
            
        Returns:
            FilterResult with pass/fail status
        """
        if self._price_count < self.slow_period:
            return FilterResult(passed=True, reason="Trend data insufficient", score=0.5)
        
        trend = self.get_trend()
        
        # Calculate trend strength
        if self.slow_ema > 0:
            strength = abs(self.fast_ema - self.slow_ema) / self.slow_ema
        else:
            strength = 0
        
        # Check alignment
        if direction == "LONG":
            if trend == "BULLISH":
                return FilterResult(passed=True, reason=f"Aligned with bullish trend", score=min(0.5 + strength * 10, 1.0))
            elif trend == "NEUTRAL":
                return FilterResult(passed=True, reason="Neutral trend", score=0.6)
            else:
                return FilterResult(passed=False, reason="Counter-trend trade (bearish)", score=0.3)
        else:  # SHORT
            if trend == "BEARISH":
                return FilterResult(passed=True, reason=f"Aligned with bearish trend", score=min(0.5 + strength * 10, 1.0))
            elif trend == "NEUTRAL":
                return FilterResult(passed=True, reason="Neutral trend", score=0.6)
            else:
                return FilterResult(passed=False, reason="Counter-trend trade (bullish)", score=0.3)
    
    def reset(self) -> None:
        """Reset filter state"""
        self.fast_ema = 0.0
        self.slow_ema = 0.0
        self._price_count = 0


class CompositeFilter:
    """
    Composite Filter - Combines multiple filters with weighted scoring
    
    Aggregates results from volume, trend, and other filters
    to provide an overall trade quality score.
    """
    
    def __init__(self, filters: Optional[List] = None, weights: Optional[Dict[str, float]] = None):
        """
        Args:
            filters: List of filter instances
            weights: Dict of filter name to weight (must sum to 1.0)
        """
        self.filters: List = filters or []
        self.weights: Dict[str, float] = weights or {}
        self._default_weight = 1.0 / max(len(self.filters), 1)
    
    def add_filter(self, filter_instance, name: str, weight: float = None) -> None:
        """Add a filter to the composite"""
        self.filters.append((name, filter_instance))
        if weight is not None:
            self.weights[name] = weight
    
    def check_all(self, direction: str = "LONG", current_volume: int = 0) -> FilterResult:
        """
        Run all filters and aggregate results
        
        Args:
            direction: Trade direction
            current_volume: Current candle volume for volume filter
            
        Returns:
            Aggregated FilterResult
        """
        if not self.filters:
            return FilterResult(passed=True, reason="No filters configured", score=1.0)
        
        results = []
        failed_reasons = []
        total_weight = 0.0
        weighted_score = 0.0
        
        for name, filter_obj in self.filters:
            weight = self.weights.get(name, self._default_weight)
            
            # Call appropriate check method
            if isinstance(filter_obj, VolumeFilter):
                result = filter_obj.check(current_volume)
            elif isinstance(filter_obj, TrendFilter):
                result = filter_obj.check(direction)
            else:
                # Generic filter with check() method
                result = filter_obj.check() if hasattr(filter_obj, 'check') else FilterResult(passed=True)
            
            results.append((name, result))
            total_weight += weight
            weighted_score += result.score * weight
            
            if not result.passed:
                failed_reasons.append(f"{name}: {result.reason}")
        
        # Normalize score
        final_score = weighted_score / total_weight if total_weight > 0 else 0.0
        
        # All filters must pass
        all_passed = all(r.passed for _, r in results)
        
        if all_passed:
            return FilterResult(
                passed=True,
                reason=f"All {len(self.filters)} filters passed",
                score=final_score
            )
        else:
            return FilterResult(
                passed=False,
                reason="; ".join(failed_reasons),
                score=final_score
            )
    
    def reset_all(self) -> None:
        """Reset all filters"""
        for _, filter_obj in self.filters:
            if hasattr(filter_obj, 'reset'):
                filter_obj.reset()
