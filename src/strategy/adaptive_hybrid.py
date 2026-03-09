"""
VWAP + PDH/PDL Fusion Strategy

High probability strategy (55-60% win rate) that combines:
1. VWAP for directional bias confirmation
2. PDH/PDL levels for entry triggers

Entry Logic:
- LONG: Price > VWAP (stable) AND breaks above PDH
- SHORT: Price < VWAP (stable) AND breaks below PDL
"""

import logging
from datetime import datetime, time, date
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any
from collections import deque

from indicators.vwap import VWAPIndicator, VWAPData
from indicators.market_structure import MarketStructure, MarketLevels
from strategy.regime_detector import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trade signal types"""
    NO_SIGNAL = "NO_SIGNAL"
    CE_BUY = "CE_BUY"    # Bullish - Buy Call
    PE_BUY = "PE_BUY"    # Bearish - Buy Put


@dataclass
class TradeSignal:
    """Trade signal with entry/exit parameters"""
    signal: SignalType
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    
    # Risk info
    risk_points: float
    reward_ratio: float
    
    # Context
    regime: str
    reason: str
    confidence: float
    
    # Levels
    vwap: float
    poc: float
    vah: float
    val: float
    
    timestamp: datetime


class AdaptiveHybridStrategy:
    """
    VWAP + PDH/PDL Fusion Strategy
    
    Philosophy:
    - Use VWAP to confirm directional BIAS (bullish/bearish)
    - Use PDH/PDL break as TRIGGER for entry
    - Combine both for high-probability setups
    
    Entry Rules:
    LONG (CE_BUY):
        1. Price > VWAP for 3+ consecutive candles (bias confirmed)
        2. Price breaks above PDH + buffer (trigger)
        3. Time window: 9:30 - 14:00
        
    SHORT (PE_BUY):
        1. Price < VWAP for 3+ consecutive candles (bias confirmed)
        2. Price breaks below PDL - buffer (trigger)
        3. Time window: 9:30 - 14:00
    
    Risk Management:
    - SL at opposite PDH/PDL (conservative) or mid-point (aggressive)
    - T1: 1.5x risk (exit 50%)
    - T2: 2.5x risk (exit remaining)
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize strategy with configuration"""
        config = config or {}
        
        # === Strategy Parameters ===
        self.vwap_buffer = config.get('vwap_buffer', 5)           # Points above/below VWAP for bias
        self.pdhl_buffer = config.get('pdhl_buffer', 5)           # Breakout buffer for PDH/PDL
        self.vwap_stability = config.get('vwap_stability', 3)     # Candles to confirm VWAP position
        self.sl_type = config.get('sl_type', 'conservative')      # 'conservative' or 'aggressive'
        self.target_rr_1 = config.get('target_rr_1', 1.5)         # First target R:R
        self.target_rr_2 = config.get('target_rr_2', 2.5)         # Second target R:R
        self.min_rr_ratio = config.get('min_rr_ratio', 1.5)       # Minimum R:R to take trade
        self.max_trades_per_day = config.get('max_trades_per_day', 3)
        self.atr_sl_multiplier = config.get('atr_sl_multiplier', 0.5)  # For ATR-based SL
        
        # Multi-breakout mode: reset broken flags after trade exit to catch multiple breakouts
        self.multi_breakout = config.get('multi_breakout', False)
        self.cooldown_candles = config.get('cooldown_candles', 15)  # Candles to wait after exit
        self.cooldown_candles = config.get('cooldown_candles', 15)  # Wait N candles after exit before re-entry
        
        # Fallback PDH/PDL levels
        fallback_pdh = config.get('fallback_pdh', 0.0)
        fallback_pdl = config.get('fallback_pdl', 0.0)
        fallback_pdc = config.get('fallback_pdc', 0.0)
        use_opening_range = config.get('use_opening_range', True)
        
        # Dynamic levels (rolling intraday high/low)
        dynamic_levels = config.get('dynamic_levels', False)
        dynamic_lookback = config.get('dynamic_lookback', 60)  # 60 candles = 1 hour
        
        # Time filters
        self.entry_start = time(9, 30)    # Wait 15 min after open
        self.entry_end = time(14, 0)      # No new trades after 2 PM
        self.best_window_end = time(11, 30)  # Best trading window
        
        # === Initialize Indicators ===
        self.vwap = VWAPIndicator(band_multiplier=1.0)
        self.market_structure = MarketStructure(
            swing_lookback=5, 
            atr_period=14,
            fallback_pdh=fallback_pdh,
            fallback_pdl=fallback_pdl,
            fallback_pdc=fallback_pdc,
            use_opening_range=use_opening_range,
            dynamic_levels=dynamic_levels,
            dynamic_lookback=dynamic_lookback
        )
        self.regime_detector = RegimeDetector(atr_period=14, lookback=20)
        
        # === State Tracking ===
        self._vwap_above_count: int = 0       # Consecutive candles above VWAP
        self._vwap_below_count: int = 0       # Consecutive candles below VWAP
        self._pdh_broken: bool = False        # Has PDH been broken today?
        self._pdl_broken: bool = False        # Has PDL been broken today?
        self._trades_today: int = 0
        self._daily_pnl: float = 0.0
        self._in_trade: bool = False
        self._last_date: Optional[date] = None
        self._cooldown_remaining: int = 0     # Candles remaining before re-entry allowed
        
        # Current levels cache
        self._current_vwap: float = 0.0
        self._current_pdh: float = 0.0
        self._current_pdl: float = 0.0
        self._current_atr: float = 0.0
        
        logger.info(f"Strategy initialized | VWAP buffer={self.vwap_buffer} | "
                   f"PDH/PDL buffer={self.pdhl_buffer} | SL type={self.sl_type}")
    
    def update(self, high: float, low: float, close: float, 
               volume: int, timestamp: datetime) -> Optional[TradeSignal]:
        """
        Process new candle and check for entry signals
        
        Args:
            high: Candle high
            low: Candle low
            close: Candle close
            volume: Volume
            timestamp: Candle timestamp
            
        Returns:
            TradeSignal if entry condition met, None otherwise
        """
        # Reset for new day
        self._check_day_reset(timestamp)
        
        # Update indicators
        vwap_data = self.vwap.update(high, low, close, volume, timestamp)
        levels = self.market_structure.update(high, low, close, timestamp)
        regime_data = self.regime_detector.update(high, low, close, timestamp)
        
        # Cache current values
        if vwap_data:
            self._current_vwap = vwap_data.vwap
        if levels:
            self._current_pdh = levels.pdh
            self._current_pdl = levels.pdl
        self._current_atr = self.regime_detector.get_atr()
        
        # Need levels to trade
        if not levels or not vwap_data:
            return None
        
        # Check if we should trade
        if not self._should_trade(timestamp, regime_data):
            return None
        
        # Update VWAP position tracking
        self._update_vwap_position(close, vwap_data.vwap)
        
        # Check for entry signals
        signal = self._check_entry_signal(close, vwap_data, levels, regime_data, timestamp)
        
        return signal
    
    def _check_day_reset(self, timestamp: datetime):
        """Reset state for new trading day"""
        current_date = timestamp.date()
        
        if self._last_date is not None and current_date != self._last_date:
            logger.info(f"New trading day detected - resetting state")
            self._vwap_above_count = 0
            self._vwap_below_count = 0
            self._pdh_broken = False
            self._pdl_broken = False
            self._trades_today = 0
            self._daily_pnl = 0.0
            self._cooldown_remaining = 0
            
        self._last_date = current_date
    
    def _should_trade(self, timestamp: datetime, regime_data) -> bool:
        """Check if we should look for trades"""
        current_time = timestamp.time()
        
        # Time filter
        if current_time < self.entry_start or current_time > self.entry_end:
            return False
        
        # Max trades check
        if self._trades_today >= self.max_trades_per_day:
            return False
        
        # Already in trade
        if self._in_trade:
            return False
        
        # Cooldown after previous trade exit
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return False
        
        # Avoid volatile regime
        if regime_data and regime_data.regime == MarketRegime.VOLATILE:
            logger.debug(f"Skipping - VOLATILE regime detected")
            return False
        
        return True
    
    def _update_vwap_position(self, close: float, vwap: float):
        """Track consecutive candles above/below VWAP"""
        if close > vwap + self.vwap_buffer:
            self._vwap_above_count += 1
            self._vwap_below_count = 0
        elif close < vwap - self.vwap_buffer:
            self._vwap_below_count += 1
            self._vwap_above_count = 0
        else:
            # At VWAP - reset both (no clear bias)
            self._vwap_above_count = 0
            self._vwap_below_count = 0
    
    def _check_entry_signal(self, close: float, vwap_data: VWAPData, 
                            levels: MarketLevels, regime_data,
                            timestamp: datetime) -> Optional[TradeSignal]:
        """
        Check for VWAP + PDH/PDL confluence entry
        
        Returns TradeSignal if conditions met
        """
        vwap = vwap_data.vwap
        pdh = levels.pdh
        pdl = levels.pdl
        atr = self._current_atr or 20  # Default ATR if not calculated
        
        # ===== LONG ENTRY (CE_BUY) =====
        # Condition 1: Price above VWAP for stability period (bullish bias)
        # Condition 2: Price breaks above PDH (trigger)
        if self._vwap_above_count >= self.vwap_stability:
            if close > pdh + self.pdhl_buffer and not self._pdh_broken:
                self._pdh_broken = True
                
                # Calculate SL
                if self.sl_type == 'aggressive':
                    sl = (pdh + pdl) / 2 - 3  # Midpoint with buffer
                else:
                    sl = pdl - 5  # Conservative - below PDL
                
                # ATR-based SL if tighter
                atr_sl = close - (atr * self.atr_sl_multiplier)
                sl = max(sl, atr_sl)  # Use whichever is closer/tighter
                
                risk = close - sl
                
                # Skip if R:R not favorable
                potential_target = close + risk * self.target_rr_1
                if risk <= 0 or risk > 50:  # Sanity check
                    logger.warning(f"Skipping LONG - invalid risk: {risk:.2f}")
                    return None
                
                # Calculate targets
                target_1 = close + risk * self.target_rr_1
                target_2 = close + risk * self.target_rr_2
                
                # Confidence based on regime
                confidence = 0.70
                if regime_data and regime_data.is_trending:
                    confidence = 0.80
                
                # Boost confidence in best window
                if timestamp.time() <= self.best_window_end:
                    confidence = min(0.90, confidence + 0.10)
                
                reason = f"VWAP+PDH Breakout | VWAP bias {self._vwap_above_count} candles | PDH={pdh:.2f}"
                
                logger.info(f"🟢 LONG SIGNAL | Entry={close:.2f} | SL={sl:.2f} | "
                           f"T1={target_1:.2f} | T2={target_2:.2f} | Risk={risk:.2f}")
                
                return TradeSignal(
                    signal=SignalType.CE_BUY,
                    entry_price=close,
                    stop_loss=sl,
                    target_1=target_1,
                    target_2=target_2,
                    risk_points=risk,
                    reward_ratio=self.target_rr_1,
                    regime=regime_data.regime.value if regime_data else "UNKNOWN",
                    reason=reason,
                    confidence=confidence,
                    vwap=vwap,
                    poc=(pdh + pdl) / 2,  # Using midpoint as POC proxy
                    vah=pdh,
                    val=pdl,
                    timestamp=timestamp
                )
        
        # ===== SHORT ENTRY (PE_BUY) =====
        # Condition 1: Price below VWAP for stability period (bearish bias)
        # Condition 2: Price breaks below PDL (trigger)
        if self._vwap_below_count >= self.vwap_stability:
            if close < pdl - self.pdhl_buffer and not self._pdl_broken:
                self._pdl_broken = True
                
                # Calculate SL
                if self.sl_type == 'aggressive':
                    sl = (pdh + pdl) / 2 + 3  # Midpoint with buffer
                else:
                    sl = pdh + 5  # Conservative - above PDH
                
                # ATR-based SL if tighter
                atr_sl = close + (atr * self.atr_sl_multiplier)
                sl = min(sl, atr_sl)  # Use whichever is closer/tighter
                
                risk = sl - close
                
                # Skip if R:R not favorable
                if risk <= 0 or risk > 50:
                    logger.warning(f"Skipping SHORT - invalid risk: {risk:.2f}")
                    return None
                
                # Calculate targets
                target_1 = close - risk * self.target_rr_1
                target_2 = close - risk * self.target_rr_2
                
                # Confidence based on regime
                confidence = 0.70
                if regime_data and regime_data.is_trending:
                    confidence = 0.80
                
                # Boost confidence in best window
                if timestamp.time() <= self.best_window_end:
                    confidence = min(0.90, confidence + 0.10)
                
                reason = f"VWAP+PDL Breakdown | VWAP bias {self._vwap_below_count} candles | PDL={pdl:.2f}"
                
                logger.info(f"🔴 SHORT SIGNAL | Entry={close:.2f} | SL={sl:.2f} | "
                           f"T1={target_1:.2f} | T2={target_2:.2f} | Risk={risk:.2f}")
                
                return TradeSignal(
                    signal=SignalType.PE_BUY,
                    entry_price=close,
                    stop_loss=sl,
                    target_1=target_1,
                    target_2=target_2,
                    risk_points=risk,
                    reward_ratio=self.target_rr_1,
                    regime=regime_data.regime.value if regime_data else "UNKNOWN",
                    reason=reason,
                    confidence=confidence,
                    vwap=vwap,
                    poc=(pdh + pdl) / 2,
                    vah=pdh,
                    val=pdl,
                    timestamp=timestamp
                )
        
        return None
    
    def on_trade_entry(self):
        """Called when trade is entered"""
        self._in_trade = True
        self._trades_today += 1
        logger.info(f"Trade entered | Trades today: {self._trades_today}")
    
    def on_trade_exit(self, pnl_or_is_win=0.0):
        """
        Called when trade is exited
        
        Args:
            pnl_or_is_win: Either PnL amount (float) or is_win flag (bool)
        """
        self._in_trade = False
        
        # Support both pnl (float) and is_win (bool) parameters
        if isinstance(pnl_or_is_win, bool):
            pnl = 0.0  # Backtest doesn't track PnL here
            is_win = pnl_or_is_win
        else:
            pnl = pnl_or_is_win
            is_win = pnl > 0
            
        self._daily_pnl += pnl
        
        # Multi-breakout mode: reset broken flags to allow catching more breakouts
        if self.multi_breakout:
            self._pdh_broken = False
            self._pdl_broken = False
            self._cooldown_remaining = self.cooldown_candles  # Wait N candles before re-entry
            logger.debug(f"Multi-breakout mode: Reset flags, cooldown={self.cooldown_candles}")
        
        logger.info(f"Trade exited | PnL: {pnl:.2f} | Daily PnL: {self._daily_pnl:.2f}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status"""
        # Get current regime from detector
        regime_data = self.regime_detector.get_regime()
        regime_str = regime_data.value if regime_data else "UNKNOWN"
        
        return {
            'trades_today': self._trades_today,
            'daily_pnl': self._daily_pnl,
            'in_trade': self._in_trade,
            'vwap_above_count': self._vwap_above_count,
            'vwap_below_count': self._vwap_below_count,
            'pdh_broken': self._pdh_broken,
            'pdl_broken': self._pdl_broken,
            'current_vwap': self._current_vwap,
            'current_pdh': self._current_pdh,
            'current_pdl': self._current_pdl,
            'current_atr': self._current_atr,
            # For main.py compatibility
            'regime': regime_str,
            'vwap': self._current_vwap,
            'atr': self._current_atr
        }
    
    def reset_daily(self):
        """Reset for new day"""
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._in_trade = False
        self._vwap_above_count = 0
        self._vwap_below_count = 0
        self._pdh_broken = False
        self._pdl_broken = False
        logger.info("Strategy daily reset complete")
    
    def get_levels(self) -> Dict[str, float]:
        """Get current key levels for display"""
        return {
            'vwap': self._current_vwap,
            'pdh': self._current_pdh,
            'pdl': self._current_pdl,
            'atr': self._current_atr
        }
