"""
Adaptive Hybrid Strategy v1.0
Professional trading strategy that adapts to market regimes

Entry Criteria:
- TRENDING: Momentum entries on pullbacks to VWAP
- SIDEWAYS: Mean reversion at VAH/VAL levels

Features:
- Dynamic SL based on ATR
- VWAP-based directional bias
- Volume Profile for key levels
- Multi-factor confirmation
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, time, date
import logging

from indicators.vwap import VWAPIndicator
from indicators.volume_profile import VolumeProfile
from indicators.market_structure import MarketStructure
from strategy.regime_detector import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal types"""
    NO_SIGNAL = "NO_SIGNAL"
    CE_BUY = "CE_BUY"  # Bullish - Buy Call
    PE_BUY = "PE_BUY"  # Bearish - Buy Put


@dataclass
class TradeSignal:
    """Complete trade signal with all parameters"""
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
    
    # Levels used
    vwap: float
    poc: float
    vah: float
    val: float
    
    timestamp: datetime


class AdaptiveHybridStrategy:
    """
    Adaptive Hybrid Trading Strategy
    
    Philosophy:
    - In TRENDING markets: Trade with trend, enter on pullbacks
    - In SIDEWAYS markets: Mean revert at value area boundaries
    - AVOID trading in VOLATILE or UNKNOWN regimes
    
    Key Indicators:
    1. VWAP - Intraday directional bias
    2. Volume Profile - Key support/resistance levels
    3. Market Structure - PDH, PDL, swing points
    4. Regime Detector - Market state classification
    
    Risk Management:
    - ATR-based dynamic stop loss
    - 1:2 minimum R:R for trades
    - Maximum 3 trades per day
    - 2% max daily loss
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize strategy with configuration"""
        config = config or {}
        
        # Strategy parameters
        self.atr_sl_multiplier = config.get('atr_sl_multiplier', 1.0)
        self.min_rr_ratio = config.get('min_rr_ratio', 2.0)
        self.max_trades_per_day = config.get('max_trades_per_day', 3)
        self.max_daily_loss_pct = config.get('max_daily_loss_pct', 2.0)
        self.signal_cooldown_minutes = config.get('signal_cooldown_minutes', 5)
        self.min_confidence = config.get('min_confidence', 0.7)
        
        # Time windows for trading
        self.morning_start = time(9, 30)  # Wait 15 min after open
        self.morning_end = time(11, 30)
        self.afternoon_start = time(13, 0)
        self.afternoon_end = time(15, 0)
        
        # Initialize indicators (1-minute)
        self.vwap = VWAPIndicator()
        self.volume_profile = VolumeProfile(tick_size=0.5)
        self.market_structure = MarketStructure(swing_lookback=5)
        self.regime_detector = RegimeDetector(atr_period=14, lookback=20)
        
        # 5-minute regime detector for confirmation
        self.regime_detector_5m = RegimeDetector(atr_period=14, lookback=20)
        self._5m_candle_count: int = 0
        self._5m_high: float = 0.0
        self._5m_low: float = float('inf')
        self._5m_open: float = 0.0
        self._5m_close: float = 0.0
        
        # State tracking
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN
        self._last_signal_date: Optional[date] = None
        self._last_signal_time: Optional[datetime] = None  # For cooldown
        self._trades_today: int = 0
        self._daily_pnl: float = 0.0
        self._in_trade: bool = False
        self._last_signal: Optional[TradeSignal] = None
        
        # Current candle data
        self._current_price: float = 0.0
        self._current_high: float = 0.0
        self._current_low: float = 0.0
        
        logger.info("AdaptiveHybridStrategy initialized with 5m confirmation")
        
    def update(self, high: float, low: float, close: float, 
               volume: int, timestamp: datetime) -> Optional[TradeSignal]:
        """
        Process new candle and generate signals
        
        Args:
            high: Candle high price
            low: Candle low price
            close: Candle close price
            volume: Candle volume
            timestamp: Candle timestamp
            
        Returns:
            TradeSignal if entry conditions met, None otherwise
        """
        # Update current price
        self._current_price = close
        self._current_high = high
        self._current_low = low
        
        # Check for new day
        current_date = timestamp.date()
        if self._last_signal_date != current_date:
            self._reset_daily()
            self._last_signal_date = current_date
            
        # Update all indicators (1-minute)
        self.vwap.update(high, low, close, volume, timestamp)
        self.volume_profile.update(high, low, close, volume, timestamp)
        self.market_structure.update(high, low, close, timestamp)
        regime_data = self.regime_detector.update(high, low, close, timestamp)
        
        # Update 5-minute candle aggregation for confirmation
        self._update_5m_candle(high, low, close, timestamp)
        
        self._current_regime = regime_data.regime
        
        # Check trading conditions
        if not self._can_trade(timestamp):
            return None
            
        # Get 5-minute regime for confirmation
        regime_5m = self.regime_detector_5m.get_regime()
        
        # Generate signal based on regime (with 5m confirmation)
        signal = self._generate_signal(timestamp, regime_data, regime_5m)
        
        if signal and signal.signal != SignalType.NO_SIGNAL:
            self._last_signal = signal
            logger.info(f"SIGNAL: {signal.signal.value} | Price={close} | "
                       f"SL={signal.stop_loss} | T1={signal.target_1} | "
                       f"Regime={signal.regime} | Reason={signal.reason}")
        
        return signal
        
    def _update_5m_candle(self, high: float, low: float, close: float, timestamp: datetime):
        """Aggregate 1-minute candles into 5-minute for confirmation"""
        self._5m_candle_count += 1
        
        if self._5m_candle_count == 1:
            self._5m_open = close  # Use close as proxy for open
            self._5m_high = high
            self._5m_low = low
        else:
            self._5m_high = max(self._5m_high, high)
            self._5m_low = min(self._5m_low, low)
            
        self._5m_close = close
        
        # Every 5 candles, update 5-min regime detector
        if self._5m_candle_count >= 5:
            self.regime_detector_5m.update(
                self._5m_high, self._5m_low, self._5m_close, timestamp
            )
            # Reset for next 5-min candle
            self._5m_candle_count = 0
            self._5m_high = 0.0
            self._5m_low = float('inf')
            
    def _can_trade(self, timestamp: datetime) -> bool:
        """Check if trading is allowed"""
        # Max trades check
        if self._trades_today >= self.max_trades_per_day:
            return False
            
        # Already in trade
        if self._in_trade:
            return False
            
        # Signal cooldown check
        if self._last_signal_time:
            time_since_signal = (timestamp - self._last_signal_time).total_seconds() / 60
            if time_since_signal < self.signal_cooldown_minutes:
                return False
            
        # Time window check
        current_time = timestamp.time()
        in_morning = self.morning_start <= current_time <= self.morning_end
        in_afternoon = self.afternoon_start <= current_time <= self.afternoon_end
        
        if not (in_morning or in_afternoon):
            return False
            
        return True
        
    def _generate_signal(self, timestamp: datetime, 
                         regime_data, regime_5m: MarketRegime = None) -> Optional[TradeSignal]:
        """Generate trading signal based on current market state"""
        
        # Skip if regime is not tradeable
        if not regime_data.is_tradeable:
            return self._no_signal(timestamp, f"Regime not tradeable: {regime_data.regime.value}")
            
        # Skip if confidence is too low
        if regime_data.confidence < self.min_confidence:
            return self._no_signal(timestamp, f"Low confidence: {regime_data.confidence:.0%}")
            
        # 5-minute confirmation: Check if 1m and 5m regimes align
        if regime_5m and regime_5m != MarketRegime.UNKNOWN:
            # For trending signals, 5m should also be trending (or at least not opposite)
            if regime_data.is_trending:
                if regime_5m == MarketRegime.VOLATILE:
                    return self._no_signal(timestamp, "5m shows VOLATILE - skip")
                # Allow if 5m is same direction or sideways
                if regime_data.regime == MarketRegime.TRENDING_UP and regime_5m == MarketRegime.TRENDING_DOWN:
                    return self._no_signal(timestamp, "5m trend conflict - 1m UP but 5m DOWN")
                if regime_data.regime == MarketRegime.TRENDING_DOWN and regime_5m == MarketRegime.TRENDING_UP:
                    return self._no_signal(timestamp, "5m trend conflict - 1m DOWN but 5m UP")
            
        # Get indicator values
        vwap_data = self.vwap.get_current_vwap()
        vp_levels = self.volume_profile._calculate_levels()
        ms_levels = self.market_structure._calculate_levels()
        atr = self.regime_detector.get_atr()
        
        if vwap_data is None or vp_levels is None or atr == 0:
            return self._no_signal(timestamp, "Insufficient indicator data")
            
        # ms_levels can be None if we don't have previous day data - that's ok
        # We only need it for PDH/PDL levels which are optional
            
        # Determine trading approach based on regime
        if regime_data.is_trending:
            return self._trending_signal(timestamp, regime_data, vwap_data, 
                                        vp_levels, ms_levels, atr)
        elif regime_data.is_sideways:
            return self._sideways_signal(timestamp, regime_data, vwap_data,
                                        vp_levels, ms_levels, atr)
        else:
            return self._no_signal(timestamp, f"Unknown regime: {regime_data.regime.value}")
            
    def _trending_signal(self, timestamp: datetime, regime_data,
                         vwap: float, vp_levels,
                         ms_levels, atr: float) -> Optional[TradeSignal]:
        """
        Generate signal for trending markets
        
        Strategy: Trade with trend on pullbacks to VWAP
        - UPTREND: Buy CE on pullback to VWAP (price near or slightly below)
        - DOWNTREND: Buy PE on pullback to VWAP (price near or slightly above)
        """
        price = self._current_price
        poc = vp_levels.poc
        vah = vp_levels.vah
        val = vp_levels.val
        
        # Calculate SL and targets
        sl_distance = atr * self.atr_sl_multiplier
        
        if regime_data.regime == MarketRegime.TRENDING_UP:
            # Look for pullback to VWAP or VAL
            vwap_distance = (price - vwap) / vwap * 100
            
            # Price should be at or slightly below VWAP
            if -0.15 <= vwap_distance <= 0.10:
                # Pullback to VWAP - good entry
                sl = price - sl_distance
                target1 = price + sl_distance * 2  # 1:2 R:R
                target2 = price + sl_distance * 3  # 1:3 R:R
                
                # Check R:R
                risk = price - sl
                reward = target1 - price
                rr = reward / risk if risk > 0 else 0
                
                if rr >= self.min_rr_ratio:
                    return TradeSignal(
                        signal=SignalType.CE_BUY,
                        entry_price=price,
                        stop_loss=round(sl, 2),
                        target_1=round(target1, 2),
                        target_2=round(target2, 2),
                        risk_points=round(risk, 2),
                        reward_ratio=round(rr, 2),
                        regime=regime_data.regime.value,
                        reason=f"Uptrend pullback to VWAP ({vwap_distance:.2f}%)",
                        confidence=regime_data.confidence,
                        vwap=round(vwap, 2),
                        poc=round(poc, 2),
                        vah=round(vah, 2),
                        val=round(val, 2),
                        timestamp=timestamp
                    )
                    
        elif regime_data.regime == MarketRegime.TRENDING_DOWN:
            # Price should be at or slightly above VWAP
            vwap_distance = (price - vwap) / vwap * 100
            
            if -0.10 <= vwap_distance <= 0.15:
                # Pullback to VWAP - good entry for short
                sl = price + sl_distance
                target1 = price - sl_distance * 2
                target2 = price - sl_distance * 3
                
                risk = sl - price
                reward = price - target1
                rr = reward / risk if risk > 0 else 0
                
                if rr >= self.min_rr_ratio:
                    return TradeSignal(
                        signal=SignalType.PE_BUY,
                        entry_price=price,
                        stop_loss=round(sl, 2),
                        target_1=round(target1, 2),
                        target_2=round(target2, 2),
                        risk_points=round(risk, 2),
                        reward_ratio=round(rr, 2),
                        regime=regime_data.regime.value,
                        reason=f"Downtrend pullback to VWAP ({vwap_distance:.2f}%)",
                        confidence=regime_data.confidence,
                        vwap=round(vwap, 2),
                        poc=round(poc, 2),
                        vah=round(vah, 2),
                        val=round(val, 2),
                        timestamp=timestamp
                    )
                    
        return self._no_signal(timestamp, "No trending entry condition met")
        
    def _sideways_signal(self, timestamp: datetime, regime_data,
                         vwap: float, vp_levels,
                         ms_levels, atr: float) -> Optional[TradeSignal]:
        """
        Generate signal for sideways markets
        
        Strategy: Mean reversion at value area boundaries
        - At VAL (Value Area Low): Buy CE (expect bounce up to POC)
        - At VAH (Value Area High): Buy PE (expect drop to POC)
        """
        price = self._current_price
        poc = vp_levels.poc
        vah = vp_levels.vah
        val = vp_levels.val
        
        # Tolerance for level detection (higher in sideways)
        tolerance = atr * 0.3
        sl_distance = atr * self.atr_sl_multiplier
        
        # Check if at VAL (potential long)
        if abs(price - val) <= tolerance:
            # Mean reversion long at VAL
            sl = val - sl_distance
            target1 = poc  # First target at POC
            target2 = vah  # Extended target at VAH
            
            risk = price - sl
            reward = target1 - price
            rr = reward / risk if risk > 0 else 0
            
            # Must be above VWAP or at least neutral for bullish setup
            vwap_bias = self.vwap.get_bias(price)
            
            if rr >= self.min_rr_ratio and vwap_bias != "BEARISH":
                return TradeSignal(
                    signal=SignalType.CE_BUY,
                    entry_price=price,
                    stop_loss=round(sl, 2),
                    target_1=round(target1, 2),
                    target_2=round(target2, 2),
                    risk_points=round(risk, 2),
                    reward_ratio=round(rr, 2),
                    regime=regime_data.regime.value,
                    reason=f"Mean reversion at VAL ({val:.2f})",
                    confidence=regime_data.confidence,
                    vwap=round(vwap, 2),
                    poc=round(poc, 2),
                    vah=round(vah, 2),
                    val=round(val, 2),
                    timestamp=timestamp
                )
                
        # Check if at VAH (potential short)
        elif abs(price - vah) <= tolerance:
            # Mean reversion short at VAH
            sl = vah + sl_distance
            target1 = poc
            target2 = val
            
            risk = sl - price
            reward = price - target1
            rr = reward / risk if risk > 0 else 0
            
            vwap_bias = self.vwap.get_bias(price)
            
            if rr >= self.min_rr_ratio and vwap_bias != "BULLISH":
                return TradeSignal(
                    signal=SignalType.PE_BUY,
                    entry_price=price,
                    stop_loss=round(sl, 2),
                    target_1=round(target1, 2),
                    target_2=round(target2, 2),
                    risk_points=round(risk, 2),
                    reward_ratio=round(rr, 2),
                    regime=regime_data.regime.value,
                    reason=f"Mean reversion at VAH ({vah:.2f})",
                    confidence=regime_data.confidence,
                    vwap=round(vwap, 2),
                    poc=round(poc, 2),
                    vah=round(vah, 2),
                    val=round(val, 2),
                    timestamp=timestamp
                )
                
        return self._no_signal(timestamp, "No sideways entry condition met")
        
    def _no_signal(self, timestamp: datetime, reason: str) -> TradeSignal:
        """Create no-signal result"""
        return TradeSignal(
            signal=SignalType.NO_SIGNAL,
            entry_price=self._current_price,
            stop_loss=0,
            target_1=0,
            target_2=0,
            risk_points=0,
            reward_ratio=0,
            regime=self._current_regime.value,
            reason=reason,
            confidence=0,
            vwap=self.vwap.get_current_vwap() or 0,
            poc=0,
            vah=0,
            val=0,
            timestamp=timestamp
        )
        
    def _reset_daily(self):
        """Reset daily counters"""
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._in_trade = False
        self._last_signal_time = None
        self._5m_candle_count = 0
        self._5m_high = 0.0
        self._5m_low = float('inf')
        logger.info("AdaptiveHybridStrategy: Daily reset")
        
    def on_trade_entry(self, timestamp: datetime = None):
        """Called when trade is entered"""
        self._in_trade = True
        self._trades_today += 1
        if timestamp:
            self._last_signal_time = timestamp
        logger.info(f"Trade entered. Trades today: {self._trades_today}")
        
    def on_trade_exit(self, pnl: float):
        """Called when trade is exited"""
        self._in_trade = False
        self._daily_pnl += pnl
        logger.info(f"Trade exited. PnL: {pnl}, Daily PnL: {self._daily_pnl}")
        
    def get_regime(self) -> MarketRegime:
        """Get current market regime"""
        return self._current_regime
        
    def get_last_signal(self) -> Optional[TradeSignal]:
        """Get last generated signal"""
        return self._last_signal
        
    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status"""
        return {
            'regime': self._current_regime.value,
            'trades_today': self._trades_today,
            'daily_pnl': self._daily_pnl,
            'in_trade': self._in_trade,
            'vwap': self.vwap.get_current_vwap(),
            'atr': self.regime_detector.get_atr()
        }
        
    def reset(self):
        """Full strategy reset"""
        self.vwap.reset()
        self.volume_profile.reset()
        self.market_structure.reset()
        self.regime_detector.reset()
        self._current_regime = MarketRegime.UNKNOWN
        self._reset_daily()
        logger.info("AdaptiveHybridStrategy: Full reset")
