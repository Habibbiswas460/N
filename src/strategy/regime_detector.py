"""
Market Regime Detector
Classifies market into TRENDING, SIDEWAYS, or VOLATILE regimes
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification"""
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    SIDEWAYS = "SIDEWAYS"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeData:
    """Regime detection result"""
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    atr: float
    atr_percentile: float  # Where ATR is relative to history
    trend_strength: float  # ADX-like measure 0-100
    range_bound_score: float  # 0 = trending, 100 = ranging
    
    @property
    def is_trending(self) -> bool:
        return self.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)
    
    @property
    def is_sideways(self) -> bool:
        return self.regime == MarketRegime.SIDEWAYS
        
    @property
    def is_tradeable(self) -> bool:
        """Check if regime is suitable for trading"""
        return self.regime != MarketRegime.VOLATILE and self.confidence >= 0.6


class RegimeDetector:
    """
    Market Regime Detection System
    
    Uses multiple factors to classify market regime:
    1. ADX-like trend strength
    2. Price range relative to ATR
    3. Higher high/lower low analysis
    4. Bollinger Band width
    5. Recent price action patterns
    
    Regimes:
    - TRENDING_UP: Clear upward momentum, HH/HL pattern
    - TRENDING_DOWN: Clear downward momentum, LH/LL pattern
    - SIDEWAYS: Range-bound, price oscillating between levels
    - VOLATILE: High ATR, no clear direction (avoid trading)
    """
    
    def __init__(self, atr_period: int = 14, lookback: int = 20, 
                 ema_fast: int = 8, ema_slow: int = 21):
        """
        Args:
            atr_period: Period for ATR calculation
            lookback: Bars to look back for analysis
            ema_fast: Fast EMA period
            ema_slow: Slow EMA period
        """
        self.atr_period = atr_period
        self.lookback = lookback
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        
        # Price data
        self._highs: deque = deque(maxlen=lookback)
        self._lows: deque = deque(maxlen=lookback)
        self._closes: deque = deque(maxlen=lookback)
        
        # ATR tracking
        self._tr_values: deque = deque(maxlen=atr_period)
        self._atr_history: deque = deque(maxlen=100)  # For percentile
        self._atr: float = 0.0
        self._prev_close: Optional[float] = None
        
        # EMA tracking
        self._ema_fast: float = 0.0
        self._ema_slow: float = 0.0
        self._ema_multiplier_fast = 2 / (ema_fast + 1)
        self._ema_multiplier_slow = 2 / (ema_slow + 1)
        self._ema_initialized: bool = False
        self._candle_count: int = 0
        
        # Directional movement
        self._plus_dm: deque = deque(maxlen=atr_period)
        self._minus_dm: deque = deque(maxlen=atr_period)
        
    def update(self, high: float, low: float, close: float,
               timestamp: datetime) -> RegimeData:
        """
        Update regime detector with new candle
        
        Returns current regime classification
        """
        self._candle_count += 1
        
        # Store prices
        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)
        
        # Calculate ATR
        self._update_atr(high, low, close)
        
        # Calculate directional movement
        self._update_dm(high, low)
        
        # Calculate EMAs
        self._update_ema(close)
        
        self._prev_close = close
        
        # Need enough data for analysis
        if len(self._closes) < self.lookback // 2:
            return RegimeData(
                regime=MarketRegime.UNKNOWN,
                confidence=0.0,
                atr=self._atr,
                atr_percentile=50.0,
                trend_strength=0.0,
                range_bound_score=50.0
            )
            
        # Calculate regime metrics
        trend_strength = self._calculate_trend_strength()
        range_score = self._calculate_range_score()
        atr_percentile = self._calculate_atr_percentile()
        
        # Classify regime
        regime, confidence = self._classify_regime(
            trend_strength, range_score, atr_percentile
        )
        
        return RegimeData(
            regime=regime,
            confidence=round(confidence, 2),
            atr=round(self._atr, 2),
            atr_percentile=round(atr_percentile, 1),
            trend_strength=round(trend_strength, 1),
            range_bound_score=round(range_score, 1)
        )
        
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
            self._atr_history.append(self._atr)
            
    def _update_dm(self, high: float, low: float):
        """Update directional movement"""
        if len(self._highs) < 2:
            return
            
        prev_high = self._highs[-2]
        prev_low = self._lows[-2]
        
        up_move = high - prev_high
        down_move = prev_low - low
        
        if up_move > down_move and up_move > 0:
            self._plus_dm.append(up_move)
            self._minus_dm.append(0)
        elif down_move > up_move and down_move > 0:
            self._plus_dm.append(0)
            self._minus_dm.append(down_move)
        else:
            self._plus_dm.append(0)
            self._minus_dm.append(0)
            
    def _update_ema(self, close: float):
        """Update EMAs"""
        if not self._ema_initialized:
            if self._candle_count == self.ema_slow:
                # Initialize with SMA
                closes = list(self._closes)
                self._ema_fast = sum(closes[-self.ema_fast:]) / self.ema_fast
                self._ema_slow = sum(closes) / len(closes)
                self._ema_initialized = True
        else:
            self._ema_fast = close * self._ema_multiplier_fast + \
                            self._ema_fast * (1 - self._ema_multiplier_fast)
            self._ema_slow = close * self._ema_multiplier_slow + \
                            self._ema_slow * (1 - self._ema_multiplier_slow)
                            
    def _calculate_trend_strength(self) -> float:
        """
        Calculate ADX-like trend strength (0-100)
        
        Higher values = stronger trend
        <20 = weak/no trend (sideways)
        20-40 = developing trend
        >40 = strong trend
        """
        if len(self._plus_dm) < self.atr_period or self._atr == 0:
            return 0.0
            
        # Calculate +DI and -DI
        plus_di = 100 * (sum(self._plus_dm) / max(sum(self._tr_values), 0.001))
        minus_di = 100 * (sum(self._minus_dm) / max(sum(self._tr_values), 0.001))
        
        # Calculate ADX
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0
            
        dx = 100 * abs(plus_di - minus_di) / di_sum
        
        return dx
        
    def _calculate_range_score(self) -> float:
        """
        Calculate range-bound score (0-100)
        
        Higher = more range-bound (sideways)
        Lower = more trending
        """
        if len(self._highs) < 10:
            return 50.0
            
        highs = list(self._highs)
        lows = list(self._lows)
        closes = list(self._closes)
        
        # Check for HH/HL or LH/LL patterns
        hh_count = 0
        hl_count = 0
        lh_count = 0
        ll_count = 0
        
        for i in range(5, len(highs)):
            if highs[i] > max(highs[i-5:i]):
                hh_count += 1
            if highs[i] < min(highs[i-5:i]):
                lh_count += 1
            if lows[i] > max(lows[i-5:i]):
                hl_count += 1
            if lows[i] < min(lows[i-5:i]):
                ll_count += 1
                
        # Uptrend: HH + HL
        # Downtrend: LH + LL
        # Sideways: Mixed or none
        
        trend_signals = hh_count + hl_count + lh_count + ll_count
        if trend_signals == 0:
            return 80.0  # Very range-bound
            
        # Check if price is oscillating (mean reversion)
        price_range = max(highs) - min(lows)
        recent_close = closes[-1]
        range_mid = (max(highs) + min(lows)) / 2
        
        # If price keeps returning to middle, it's range-bound
        crosses = 0
        for i in range(1, len(closes)):
            if (closes[i-1] < range_mid and closes[i] > range_mid) or \
               (closes[i-1] > range_mid and closes[i] < range_mid):
                crosses += 1
                
        cross_ratio = crosses / len(closes) * 100
        
        # Combine factors
        range_score = min(100, cross_ratio * 3 + (1 / max(trend_signals, 1)) * 30)
        
        return range_score
        
    def _calculate_atr_percentile(self) -> float:
        """Calculate where current ATR is relative to historical"""
        if len(self._atr_history) < 10:
            return 50.0
            
        atr_values = list(self._atr_history)
        below_count = sum(1 for v in atr_values if v < self._atr)
        
        return (below_count / len(atr_values)) * 100
        
    def _classify_regime(self, trend_strength: float, range_score: float,
                         atr_percentile: float) -> tuple:
        """
        Classify market regime based on metrics
        
        Returns (regime, confidence)
        """
        # Check for high volatility first
        if atr_percentile > 85:
            return MarketRegime.VOLATILE, 0.7
            
        closes = list(self._closes)
        
        # Determine trend direction
        if self._ema_initialized:
            ema_diff = self._ema_fast - self._ema_slow
            ema_diff_pct = ema_diff / self._ema_slow * 100
        else:
            ema_diff_pct = 0
            
        # Strong trend
        if trend_strength > 35:
            confidence = min(0.95, 0.6 + trend_strength / 100)
            if ema_diff_pct > 0.1:
                return MarketRegime.TRENDING_UP, confidence
            elif ema_diff_pct < -0.1:
                return MarketRegime.TRENDING_DOWN, confidence
            else:
                # Strong ADX but EMAs close - check price
                if len(closes) >= 5:
                    if closes[-1] > closes[-5]:
                        return MarketRegime.TRENDING_UP, confidence * 0.8
                    else:
                        return MarketRegime.TRENDING_DOWN, confidence * 0.8
                        
        # Moderate trend
        if trend_strength > 20:
            confidence = 0.5 + (trend_strength - 20) / 30
            if ema_diff_pct > 0.05:
                return MarketRegime.TRENDING_UP, confidence
            elif ema_diff_pct < -0.05:
                return MarketRegime.TRENDING_DOWN, confidence
                
        # Sideways market
        if range_score > 60 or trend_strength < 20:
            confidence = min(0.9, 0.5 + range_score / 100)
            return MarketRegime.SIDEWAYS, confidence
            
        # Weak trend / uncertain
        if ema_diff_pct > 0:
            return MarketRegime.TRENDING_UP, 0.4
        elif ema_diff_pct < 0:
            return MarketRegime.TRENDING_DOWN, 0.4
        else:
            return MarketRegime.SIDEWAYS, 0.5
            
    def get_regime(self) -> MarketRegime:
        """Get current regime without updating"""
        if len(self._closes) < self.lookback // 2:
            return MarketRegime.UNKNOWN
            
        trend_strength = self._calculate_trend_strength()
        range_score = self._calculate_range_score()
        atr_percentile = self._calculate_atr_percentile()
        
        regime, _ = self._classify_regime(trend_strength, range_score, atr_percentile)
        return regime
        
    def get_atr(self) -> float:
        """Get current ATR"""
        return self._atr
        
    def get_ema_fast(self) -> float:
        """Get fast EMA"""
        return self._ema_fast
        
    def get_ema_slow(self) -> float:
        """Get slow EMA"""
        return self._ema_slow
        
    def reset(self):
        """Reset detector"""
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._tr_values.clear()
        self._atr_history.clear()
        self._atr = 0.0
        self._prev_close = None
        self._ema_fast = 0.0
        self._ema_slow = 0.0
        self._ema_initialized = False
        self._candle_count = 0
        self._plus_dm.clear()
        self._minus_dm.clear()
