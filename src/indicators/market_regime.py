"""
Market Regime Detector - Trend vs Sideways Detection

Detects market conditions:
1. TRENDING - Strong directional move (use N-Structure)
2. SIDEWAYS - Range-bound, no clear direction (use Range Strategy)

Indicators used:
- ADX (Average Directional Index): < 20 = Sideways, > 25 = Trending
- EMA Convergence: EMA9 close to EMA15 = Sideways
- ATR Ratio: Low ATR relative to price = Sideways
- Bollinger Band Width: Narrow = Sideways
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum
from collections import deque
import math


class MarketRegime(Enum):
    """Market regime classification."""
    TRENDING_UP = "TRENDING_UP"      # Strong uptrend - use CE N-Structure
    TRENDING_DOWN = "TRENDING_DOWN"  # Strong downtrend - use PE N-Structure
    SIDEWAYS = "SIDEWAYS"            # Range-bound - use Range Strategy
    UNKNOWN = "UNKNOWN"              # Not enough data


@dataclass
class RegimeAnalysis:
    """Result of market regime analysis."""
    regime: MarketRegime
    adx_value: float
    ema_spread_pct: float  # % spread between EMA9 and EMA15
    atr_ratio: float       # ATR as % of price
    range_high: float      # Detected range high (for sideways)
    range_low: float       # Detected range low (for sideways)
    confidence: float      # 0-100% confidence in regime detection
    message: str


class MarketRegimeDetector:
    """
    Detects whether market is Trending or Sideways.
    
    Uses multiple indicators for robust detection:
    1. ADX - Primary trend strength indicator
    2. EMA Spread - Distance between fast and slow EMA
    3. ATR Ratio - Volatility relative to price
    4. Price Range - Identifies support/resistance levels
    """
    
    def __init__(
        self,
        adx_period: int = 14,
        adx_trending_threshold: float = 25.0,
        adx_sideways_threshold: float = 20.0,
        ema_spread_sideways_pct: float = 0.15,  # 0.15% = sideways
        lookback_candles: int = 30,             # For range detection
        range_threshold_pct: float = 1.0,       # 1% range = sideways
    ):
        """Initialize regime detector."""
        self.adx_period = adx_period
        self.adx_trending_threshold = adx_trending_threshold
        self.adx_sideways_threshold = adx_sideways_threshold
        self.ema_spread_sideways_pct = ema_spread_sideways_pct
        self.lookback_candles = lookback_candles
        self.range_threshold_pct = range_threshold_pct
        
        # ADX calculation buffers
        self.tr_history: deque = deque(maxlen=adx_period * 2)
        self.plus_dm_history: deque = deque(maxlen=adx_period * 2)
        self.minus_dm_history: deque = deque(maxlen=adx_period * 2)
        self.adx_history: deque = deque(maxlen=adx_period)
        
        # Price history for range detection
        self.high_history: deque = deque(maxlen=lookback_candles)
        self.low_history: deque = deque(maxlen=lookback_candles)
        self.close_history: deque = deque(maxlen=lookback_candles)
        
        # Previous candle for TR calculation
        self.prev_high: float = 0.0
        self.prev_low: float = 0.0
        self.prev_close: float = 0.0
        
        # Smoothed values
        self.smoothed_tr: float = 0.0
        self.smoothed_plus_dm: float = 0.0
        self.smoothed_minus_dm: float = 0.0
        self.smoothed_dx: float = 0.0
        
        self.initialized = False
        self.candle_count = 0
    
    def update(
        self,
        high: float,
        low: float,
        close: float,
        ema9: float,
        ema15: float
    ) -> RegimeAnalysis:
        """
        Update with new candle and detect market regime.
        
        Args:
            high: Candle high price
            low: Candle low price  
            close: Candle close price
            ema9: 9-period EMA value
            ema15: 15-period EMA value
            
        Returns:
            RegimeAnalysis with detected regime
        """
        self.candle_count += 1
        
        # Store price history
        self.high_history.append(high)
        self.low_history.append(low)
        self.close_history.append(close)
        
        # Calculate True Range and Directional Movement
        if self.prev_close > 0:
            tr = max(
                high - low,
                abs(high - self.prev_close),
                abs(low - self.prev_close)
            )
            
            # Plus/Minus Directional Movement
            up_move = high - self.prev_high
            down_move = self.prev_low - low
            
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0
            
            self.tr_history.append(tr)
            self.plus_dm_history.append(plus_dm)
            self.minus_dm_history.append(minus_dm)
        
        # Update previous values
        self.prev_high = high
        self.prev_low = low
        self.prev_close = close
        
        # Need enough data for ADX
        if self.candle_count < self.adx_period + 1:
            return RegimeAnalysis(
                regime=MarketRegime.UNKNOWN,
                adx_value=0.0,
                ema_spread_pct=0.0,
                atr_ratio=0.0,
                range_high=high,
                range_low=low,
                confidence=0.0,
                message="Collecting data..."
            )
        
        # Calculate ADX
        adx = self._calculate_adx()
        
        # Calculate EMA spread
        ema_spread_pct = abs(ema9 - ema15) / ema15 * 100 if ema15 > 0 else 0
        
        # Calculate ATR ratio
        atr = sum(self.tr_history) / len(self.tr_history) if self.tr_history else 0
        atr_ratio = (atr / close * 100) if close > 0 else 0
        
        # Detect range (support/resistance)
        range_high = max(self.high_history) if self.high_history else high
        range_low = min(self.low_history) if self.low_history else low
        range_pct = (range_high - range_low) / range_low * 100 if range_low > 0 else 0
        
        # Determine regime
        regime, confidence, message = self._classify_regime(
            adx=adx,
            ema_spread_pct=ema_spread_pct,
            ema9=ema9,
            ema15=ema15,
            range_pct=range_pct,
            atr_ratio=atr_ratio
        )
        
        return RegimeAnalysis(
            regime=regime,
            adx_value=adx,
            ema_spread_pct=ema_spread_pct,
            atr_ratio=atr_ratio,
            range_high=range_high,
            range_low=range_low,
            confidence=confidence,
            message=message
        )
    
    def _calculate_adx(self) -> float:
        """Calculate ADX (Average Directional Index)."""
        if len(self.tr_history) < self.adx_period:
            return 0.0
        
        # Simple ADX approximation using EMA spread
        # Real ADX requires more complex Wilder smoothing
        period = self.adx_period
        
        # Calculate ATR
        atr = sum(list(self.tr_history)[-period:]) / period
        
        # Calculate +DI and -DI approximation
        plus_dm_sum = sum(list(self.plus_dm_history)[-period:])
        minus_dm_sum = sum(list(self.minus_dm_history)[-period:])
        
        if atr <= 0:
            return 0.0
        
        plus_di = (plus_dm_sum / (atr * period)) * 100
        minus_di = (minus_dm_sum / (atr * period)) * 100
        
        # Clamp DI values
        plus_di = min(100, max(0, plus_di))
        minus_di = min(100, max(0, minus_di))
        
        # Calculate DX
        di_sum = plus_di + minus_di
        if di_sum <= 0:
            return 0.0
        
        dx = abs(plus_di - minus_di) / di_sum * 100
        
        # ADX is smoothed DX (simplified - just return DX for now)
        # Clamp to valid range
        adx = min(100, max(0, dx))
        
        return adx
    
    def _classify_regime(
        self,
        adx: float,
        ema_spread_pct: float,
        ema9: float,
        ema15: float,
        range_pct: float,
        atr_ratio: float
    ) -> Tuple[MarketRegime, float, str]:
        """
        Classify market regime based on multiple indicators.
        
        Returns:
            Tuple of (regime, confidence, message)
        """
        # Score-based classification
        trending_score = 0
        sideways_score = 0
        
        # ADX Analysis (most important)
        if adx >= self.adx_trending_threshold:
            trending_score += 40
        elif adx <= self.adx_sideways_threshold:
            sideways_score += 40
        else:
            # In between - partial scores
            trending_score += 20
            sideways_score += 20
        
        # EMA Spread Analysis
        if ema_spread_pct < self.ema_spread_sideways_pct:
            sideways_score += 30
        else:
            trending_score += 30
        
        # Range Analysis
        if range_pct < self.range_threshold_pct:
            sideways_score += 20
        else:
            trending_score += 20
        
        # ATR Analysis
        if atr_ratio < 0.5:  # Very low volatility
            sideways_score += 10
        elif atr_ratio > 1.0:  # High volatility
            trending_score += 10
        
        # Determine regime
        total_score = trending_score + sideways_score
        
        if sideways_score > trending_score:
            confidence = sideways_score / total_score * 100 if total_score > 0 else 50
            return (
                MarketRegime.SIDEWAYS,
                confidence,
                f"Sideways: ADX={adx:.1f}, EMA Spread={ema_spread_pct:.2f}%, Range={range_pct:.1f}%"
            )
        else:
            # Determine trend direction
            if ema9 > ema15:
                regime = MarketRegime.TRENDING_UP
                direction = "UP"
            else:
                regime = MarketRegime.TRENDING_DOWN
                direction = "DOWN"
            
            confidence = trending_score / total_score * 100 if total_score > 0 else 50
            return (
                regime,
                confidence,
                f"Trending {direction}: ADX={adx:.1f}, EMA Spread={ema_spread_pct:.2f}%"
            )
    
    def reset(self):
        """Reset detector state."""
        self.tr_history.clear()
        self.plus_dm_history.clear()
        self.minus_dm_history.clear()
        self.adx_history.clear()
        self.high_history.clear()
        self.low_history.clear()
        self.close_history.clear()
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_close = 0.0
        self.smoothed_tr = 0.0
        self.smoothed_plus_dm = 0.0
        self.smoothed_minus_dm = 0.0
        self.smoothed_dx = 0.0
        self.initialized = False
        self.candle_count = 0
