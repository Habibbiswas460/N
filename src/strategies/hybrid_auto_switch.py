"""
Hybrid Auto-Switch Strategy - The Ultimate Profit Generator

Automatically switches between:
1. N-Structure Strategy (when trending) - Pullback entries on breakouts
2. Sideways Range Strategy (when ranging) - Support/Resistance bounces

The system detects market regime and applies the appropriate strategy
for maximum profitability in all market conditions.

Key Features:
- ADX-based regime detection
- Seamless strategy switching
- Optimal entry in both trending and sideways markets
- Risk management across both strategies
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from datetime import datetime, time
from enum import Enum, auto
from collections import deque

from loguru import logger

# Import our modules
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.market_regime import MarketRegimeDetector, MarketRegime, RegimeAnalysis
from strategies.sideways_range import SidewaysRangeStrategy, RangeTrade, RangeResult


class HybridState(Enum):
    """Hybrid strategy states."""
    DETECTING_REGIME = auto()    # Determining market type
    TRENDING_MODE = auto()       # Using N-Structure
    SIDEWAYS_MODE = auto()       # Using Range Strategy
    ACTIVE_TRADE = auto()        # Trade in progress
    COOLDOWN = auto()


@dataclass
class HybridTrade:
    """Trade from either strategy."""
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    direction: str = "CE"
    strategy: str = "N-Structure"  # "N-Structure" or "Range"
    pnl: float = 0.0
    exit_reason: str = ""
    regime_at_entry: str = ""
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class HybridResult:
    """Combined results from hybrid strategy."""
    # Overall
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    
    # By strategy
    n_structure_trades: int = 0
    n_structure_pnl: float = 0.0
    n_structure_win_rate: float = 0.0
    
    range_trades: int = 0
    range_pnl: float = 0.0
    range_win_rate: float = 0.0
    
    # Regime stats
    trending_periods: int = 0
    sideways_periods: int = 0
    regime_switches: int = 0
    
    trades: List[HybridTrade] = field(default_factory=list)


class HybridAutoSwitchStrategy:
    """
    Hybrid Strategy with Automatic Regime Switching.
    
    Market Trending (ADX > 25):
        → Use N-Structure with Pullback Entry
        → CE on uptrend breakout, PE on downtrend breakdown
        
    Market Sideways (ADX < 20):
        → Use Range Strategy
        → CE at support bounce, PE at resistance rejection
    """
    
    def __init__(
        self,
        # Regime Detection
        adx_trending_threshold: float = 25.0,
        adx_sideways_threshold: float = 20.0,
        regime_confirmation_candles: int = 3,  # Confirm regime for 3 candles
        
        # N-Structure Parameters
        n_entry_buffer: float = 2.5,
        n_min_hl_gap: float = 3.2,
        n_min_body_ratio: float = 0.80,
        n_enable_pullback: bool = True,
        
        # Range Strategy Parameters
        range_lookback: int = 20,
        range_target_points: float = 8.0,
        range_sl_points: float = 4.0,
        
        # Risk Management
        lot_size: int = 65,
        num_lots: int = 4,
        sl_points: float = 5.0,
        max_sl_per_day: int = 2,  # Combined for both strategies
        cooldown_candles: int = 10,
        
        # Time Filters
        trading_start: time = time(9, 45),
        trading_end: time = time(14, 30),
    ):
        """Initialize hybrid strategy."""
        # Regime detection
        self.adx_trending_threshold = adx_trending_threshold
        self.adx_sideways_threshold = adx_sideways_threshold
        self.regime_confirmation_candles = regime_confirmation_candles
        
        # Initialize regime detector
        self.regime_detector = MarketRegimeDetector(
            adx_trending_threshold=adx_trending_threshold,
            adx_sideways_threshold=adx_sideways_threshold
        )
        
        # Range strategy params - use new v2.0 directly
        self.range_target_points = range_target_points
        self.range_sl_points = range_sl_points
        
        # Initialize range strategy with v2.0 params
        self.range_strategy = SidewaysRangeStrategy(
            lookback_candles=range_lookback,
            target_points=range_target_points,
            sl_points=range_sl_points,
            lot_size=lot_size,
            num_lots=num_lots,
            max_sl_per_day=max_sl_per_day,
            cooldown_candles=cooldown_candles
        )
        
        # N-Structure parameters (used when in trending mode)
        self.n_entry_buffer = n_entry_buffer
        self.n_min_hl_gap = n_min_hl_gap
        self.n_min_body_ratio = n_min_body_ratio
        self.n_enable_pullback = n_enable_pullback
        
        # Position sizing
        self.lot_size = lot_size
        self.num_lots = num_lots
        self.fixed_qty = lot_size * num_lots
        self.sl_points = sl_points
        self.max_sl_per_day = max_sl_per_day
        self.cooldown_candles = cooldown_candles
        
        # Time filters
        self.trading_start = trading_start
        self.trading_end = trading_end
        
        # State
        self.state = HybridState.DETECTING_REGIME
        self.current_regime = MarketRegime.UNKNOWN
        self.regime_candle_count = 0  # For confirmation
        self.pending_regime = MarketRegime.UNKNOWN
        
        self.current_trade: Optional[HybridTrade] = None
        
        # Price history
        self.high_history: deque = deque(maxlen=50)
        self.low_history: deque = deque(maxlen=50)
        self.close_history: deque = deque(maxlen=50)
        
        # EMA values
        self.ema9: float = 0.0
        self.ema15: float = 0.0
        
        # Daily tracking
        self.daily_sl_hits: int = 0
        self.daily_trades: int = 0
        self.cooldown_counter: int = 0
        self.current_date: Optional[datetime] = None
        
        # Regime tracking
        self.regime_switches: int = 0
        self.trending_periods: int = 0
        self.sideways_periods: int = 0
        
        # Results
        self.trades: List[HybridTrade] = []
        self.equity_curve: List[float] = [0.0]
        
        # N-Structure specific state
        self.swing_highs: List[float] = []
        self.swing_lows: List[float] = []
        self.breakout_level: float = 0.0
        self.pullback_wait_candles: int = 0
        self.awaiting_pullback: bool = False
        self.pending_direction: str = ""
    
    def reset(self):
        """Reset strategy state."""
        self.state = HybridState.DETECTING_REGIME
        self.current_regime = MarketRegime.UNKNOWN
        self.regime_candle_count = 0
        self.pending_regime = MarketRegime.UNKNOWN
        self.current_trade = None
        
        self.high_history.clear()
        self.low_history.clear()
        self.close_history.clear()
        
        self.ema9 = 0.0
        self.ema15 = 0.0
        
        self.daily_sl_hits = 0
        self.daily_trades = 0
        self.cooldown_counter = 0
        self.current_date = None
        
        self.regime_switches = 0
        self.trending_periods = 0
        self.sideways_periods = 0
        
        self.trades = []
        self.equity_curve = [0.0]
        
        self.swing_highs = []
        self.swing_lows = []
        self.breakout_level = 0.0
        self.pullback_wait_candles = 0
        self.awaiting_pullback = False
        self.pending_direction = ""
        
        self.regime_detector.reset()
        self.range_strategy.reset()
    
    def in_active_trade(self) -> bool:
        """Check if there's an active trade."""
        return self.current_trade is not None and self.current_trade.is_open
    
    def _check_day_change(self, ts: datetime):
        """Reset daily counters on new day."""
        if self.current_date is None or ts.date() != self.current_date:
            self.current_date = ts.date()
            self.daily_sl_hits = 0
            self.daily_trades = 0
            self.cooldown_counter = 0
            self.range_strategy.daily_sl_hits = 0
            self.range_strategy.daily_trades = 0
            logger.info(f"📅 New trading day: {ts.date()}")
    
    def _is_trading_hours(self, ts: datetime) -> bool:
        """Check if within trading hours."""
        t = ts.time()
        return self.trading_start <= t <= self.trading_end
    
    def _update_ema(self, close: float):
        """Update EMA values."""
        if self.ema9 == 0:
            self.ema9 = close
            self.ema15 = close
        else:
            # EMA calculation
            self.ema9 = close * (2/10) + self.ema9 * (1 - 2/10)
            self.ema15 = close * (2/16) + self.ema15 * (1 - 2/16)
    
    def _update_swings(self, high: float, low: float):
        """Update swing high/low detection."""
        if len(self.high_history) >= 5:
            # Simple swing detection using 5-candle lookback
            highs = list(self.high_history)[-5:]
            lows = list(self.low_history)[-5:]
            
            mid_idx = 2
            # Swing high: middle candle is highest
            if highs[mid_idx] == max(highs):
                if not self.swing_highs or highs[mid_idx] != self.swing_highs[-1]:
                    self.swing_highs.append(highs[mid_idx])
                    if len(self.swing_highs) > 5:
                        self.swing_highs.pop(0)
            
            # Swing low: middle candle is lowest
            if lows[mid_idx] == min(lows):
                if not self.swing_lows or lows[mid_idx] != self.swing_lows[-1]:
                    self.swing_lows.append(lows[mid_idx])
                    if len(self.swing_lows) > 5:
                        self.swing_lows.pop(0)
    
    def process_candle(
        self,
        timestamp: datetime,
        high: float,
        low: float,
        close: float,
        option_price: float
    ) -> Optional[Dict]:
        """
        Process candle and return signal if any.
        
        Returns:
            Dict with signal info or None
            {
                'action': 'BUY_CE' | 'BUY_PE' | 'EXIT',
                'strategy': 'N-Structure' | 'Range',
                'regime': 'TRENDING' | 'SIDEWAYS',
                'price': float,
                ...
            }
        """
        # Day change check
        self._check_day_change(timestamp)
        
        # Store price history
        self.high_history.append(high)
        self.low_history.append(low)
        self.close_history.append(close)
        
        # Update EMAs
        self._update_ema(close)
        
        # Update swings
        self._update_swings(high, low)
        
        # Cooldown
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            return None
        
        # Daily limits
        if self.daily_sl_hits >= self.max_sl_per_day:
            return None
        
        # Time filter
        if not self._is_trading_hours(timestamp):
            return None
        
        # Update regime detection
        regime_analysis = self.regime_detector.update(
            high=high,
            low=low,
            close=close,
            ema9=self.ema9,
            ema15=self.ema15
        )
        
        # Handle regime confirmation
        self._process_regime(regime_analysis, timestamp)
        
        # Process based on current regime
        if self.current_trade and self.current_trade.is_open:
            return self._manage_active_trade(timestamp, high, low, close, option_price)
        
        if self.current_regime == MarketRegime.SIDEWAYS:
            return self._process_sideways(timestamp, high, low, close, option_price, regime_analysis)
        
        elif self.current_regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]:
            return self._process_trending(timestamp, high, low, close, option_price, regime_analysis)
        
        return None
    
    def _process_regime(self, analysis: RegimeAnalysis, ts: datetime):
        """Process and confirm regime changes."""
        detected = analysis.regime
        
        # Skip unknown
        if detected == MarketRegime.UNKNOWN:
            return
        
        # Same regime - increase confirmation
        if detected == self.pending_regime:
            self.regime_candle_count += 1
        else:
            # New regime detected - start confirmation
            self.pending_regime = detected
            self.regime_candle_count = 1
        
        # Confirm regime after N candles
        if self.regime_candle_count >= self.regime_confirmation_candles:
            if self.current_regime != self.pending_regime:
                old_regime = self.current_regime
                self.current_regime = self.pending_regime
                self.regime_switches += 1
                
                # Track regime periods
                if self.current_regime == MarketRegime.SIDEWAYS:
                    self.sideways_periods += 1
                    logger.info(f"🔄 REGIME SWITCH: {old_regime.value} → SIDEWAYS | ADX: {analysis.adx_value:.1f}")
                    logger.info(f"   Range: {analysis.range_low:.2f} - {analysis.range_high:.2f}")
                else:
                    self.trending_periods += 1
                    direction = "UP" if self.current_regime == MarketRegime.TRENDING_UP else "DOWN"
                    logger.info(f"🔄 REGIME SWITCH: {old_regime.value} → TRENDING_{direction} | ADX: {analysis.adx_value:.1f}")
    
    def _process_sideways(
        self,
        ts: datetime,
        high: float,
        low: float,
        close: float,
        option_price: float,
        regime_analysis: RegimeAnalysis
    ) -> Optional[Dict]:
        """Process in sideways/range mode using improved logic."""
        # Use detected range from regime analysis
        range_high = regime_analysis.range_high
        range_low = regime_analysis.range_low
        
        if range_high <= 0 or range_low <= 0 or range_high <= range_low:
            return None
        
        range_width = range_high - range_low
        
        # Entry buffer - within 20% of range from level
        entry_buffer = range_width * 0.20
        
        # Check for support bounce (CE entry)
        at_support = low <= range_low + entry_buffer
        bouncing_up = close > self.range_strategy.prev_close if self.range_strategy.prev_close > 0 else False
        
        if at_support and bouncing_up:
            entry_price = option_price
            target_price = entry_price + self.range_target_points
            sl_price = entry_price - self.range_sl_points
            
            self.current_trade = HybridTrade(
                entry_time=ts,
                entry_price=entry_price,
                direction="CE",
                strategy="Range",
                regime_at_entry="SIDEWAYS"
            )
            
            self._trade_target = target_price
            self._trade_sl = sl_price
            self._trade_max = entry_price  # Track max for TSL
            self._trade_hold_candles = 0
            
            self.daily_trades += 1
            
            logger.info(f"📈 RANGE CE ENTRY @ ₹{entry_price:.2f} | Support Bounce | Target: +{self.range_target_points}pt | SL: -{self.range_sl_points}pt")
            
            return {
                'action': 'BUY_CE',
                'strategy': 'Range',
                'regime': 'SIDEWAYS',
                'price': entry_price,
                'target': target_price,
                'sl': sl_price
            }
        
        # Check for resistance rejection (PE entry)
        at_resistance = high >= range_high - entry_buffer
        bouncing_down = close < self.range_strategy.prev_close if self.range_strategy.prev_close > 0 else False
        
        if at_resistance and bouncing_down:
            entry_price = option_price
            target_price = entry_price + self.range_target_points
            sl_price = entry_price - self.range_sl_points
            
            self.current_trade = HybridTrade(
                entry_time=ts,
                entry_price=entry_price,
                direction="PE",
                strategy="Range",
                regime_at_entry="SIDEWAYS"
            )
            
            self._trade_target = target_price
            self._trade_sl = sl_price
            self._trade_max = entry_price
            self._trade_hold_candles = 0
            
            self.daily_trades += 1
            
            logger.info(f"📉 RANGE PE ENTRY @ ₹{entry_price:.2f} | Resistance Rejection | Target: +{self.range_target_points}pt | SL: -{self.range_sl_points}pt")
            
            return {
                'action': 'BUY_PE',
                'strategy': 'Range',
                'regime': 'SIDEWAYS',
                'price': entry_price,
                'target': target_price,
                'sl': sl_price
            }
        
        # Update prev close for bounce detection
        self.range_strategy.prev_close = close
        return None
    
    def _process_trending(
        self,
        ts: datetime,
        high: float,
        low: float,
        close: float,
        option_price: float,
        regime_analysis: RegimeAnalysis
    ) -> Optional[Dict]:
        """Process in trending mode using N-Structure logic."""
        
        # Check if awaiting pullback
        if self.awaiting_pullback:
            self.pullback_wait_candles += 1
            
            # Timeout
            if self.pullback_wait_candles > 15:
                logger.info(f"⏰ PULLBACK TIMEOUT | Waited {self.pullback_wait_candles} candles")
                self.awaiting_pullback = False
                return None
            
            # Check for pullback
            if self.pending_direction == "CE":
                # For CE: Price pulls back near breakout level
                if low <= self.breakout_level + 3.0:  # Within 3pts
                    return self._enter_trending_trade(ts, option_price, "CE", regime_analysis)
            else:  # PE
                # For PE: Price bounces back near breakdown level
                if high >= self.breakout_level - 3.0:
                    return self._enter_trending_trade(ts, option_price, "PE", regime_analysis)
            
            return None
        
        # Look for N-Structure setup
        if self.current_regime == MarketRegime.TRENDING_UP and len(self.swing_highs) >= 1:
            # CE Setup: Breakout above recent swing high
            recent_high = self.swing_highs[-1]
            if close > recent_high:
                # Check body ratio
                candle_range = high - low
                body = abs(close - self.close_history[-2]) if len(self.close_history) >= 2 else candle_range
                body_ratio = body / candle_range if candle_range > 0 else 0
                
                if body_ratio >= self.n_min_body_ratio:
                    if self.n_enable_pullback:
                        # Wait for pullback
                        self.breakout_level = recent_high
                        self.awaiting_pullback = True
                        self.pending_direction = "CE"
                        self.pullback_wait_candles = 0
                        logger.info(f"⏳ N-STRUCTURE SETUP | CE | Breakout @ {recent_high:.2f} | Waiting for pullback...")
                    else:
                        # Immediate entry
                        return self._enter_trending_trade(ts, option_price, "CE", regime_analysis)
        
        elif self.current_regime == MarketRegime.TRENDING_DOWN and len(self.swing_lows) >= 1:
            # PE Setup: Breakdown below recent swing low
            recent_low = self.swing_lows[-1]
            if close < recent_low:
                # Check body ratio
                candle_range = high - low
                body = abs(close - self.close_history[-2]) if len(self.close_history) >= 2 else candle_range
                body_ratio = body / candle_range if candle_range > 0 else 0
                
                if body_ratio >= self.n_min_body_ratio:
                    if self.n_enable_pullback:
                        # Wait for pullback
                        self.breakout_level = recent_low
                        self.awaiting_pullback = True
                        self.pending_direction = "PE"
                        self.pullback_wait_candles = 0
                        logger.info(f"⏳ N-STRUCTURE SETUP | PE | Breakdown @ {recent_low:.2f} | Waiting for pullback...")
                    else:
                        # Immediate entry
                        return self._enter_trending_trade(ts, option_price, "PE", regime_analysis)
        
        return None
    
    def _enter_trending_trade(
        self,
        ts: datetime,
        option_price: float,
        direction: str,
        regime_analysis: RegimeAnalysis
    ) -> Dict:
        """Enter a trending (N-Structure) trade."""
        entry_price = option_price
        sl_price = option_price - self.sl_points
        
        self.current_trade = HybridTrade(
            entry_time=ts,
            entry_price=entry_price,
            direction=direction,
            strategy="N-Structure",
            regime_at_entry=f"TRENDING_{direction}"
        )
        
        self._trade_sl = sl_price
        self._trade_target = 0  # TSL mode - no fixed target
        self._trade_highest = entry_price  # For TSL tracking
        
        self.awaiting_pullback = False
        self.daily_trades += 1
        
        emoji = "📈" if direction == "CE" else "📉"
        logger.info(f"{emoji} N-STRUCTURE {direction} ENTRY @ ₹{entry_price:.2f} | SL: ₹{sl_price:.2f} | Pullback Confirmed")
        
        return {
            'action': f'BUY_{direction}',
            'strategy': 'N-Structure',
            'regime': f'TRENDING_{direction}',
            'price': entry_price,
            'sl': sl_price,
            'target': 'TSL'
        }
    
    def _manage_active_trade(
        self,
        ts: datetime,
        high: float,
        low: float,
        close: float,
        option_price: float
    ) -> Optional[Dict]:
        """Manage active trade - check SL/Target/TSL."""
        if not self.current_trade:
            return None
        
        # Track hold candles for time exit
        if hasattr(self, '_trade_hold_candles'):
            self._trade_hold_candles += 1
        else:
            self._trade_hold_candles = 0
        
        # Range strategy trades - fixed target with time exit
        if self.current_trade.strategy == "Range":
            # Update max price for potential TSL
            if not hasattr(self, '_trade_max') or option_price > self._trade_max:
                self._trade_max = option_price
            
            # Target hit
            if option_price >= self._trade_target:
                return self._exit_trade(ts, option_price, "Target Hit")
            
            # SL hit  
            if option_price <= self._trade_sl:
                return self._exit_trade(ts, option_price, "SL Hit")
            
            # TSL for Range - at +5pt activate TSL
            profit = option_price - self.current_trade.entry_price
            if profit >= 5.0:
                tsl_level = self._trade_max - 2.0  # Trail by 2 points
                if option_price <= tsl_level and tsl_level > self.current_trade.entry_price:
                    return self._exit_trade(ts, option_price, f"TSL +{profit:.0f}pt")
            
            # Time exit - 15 candles max hold
            if self._trade_hold_candles >= 15:
                return self._exit_trade(ts, option_price, "Time Exit")
        
        # N-Structure trades - TSL mode
        else:
            # Check SL
            if option_price <= self._trade_sl:
                return self._exit_trade(ts, option_price, "SL Hit")
            
            # Update highest for TSL
            if not hasattr(self, '_trade_highest'):
                self._trade_highest = self.current_trade.entry_price
                
            if option_price > self._trade_highest:
                self._trade_highest = option_price
                
                # Move SL using TSL logic
                profit = option_price - self.current_trade.entry_price
                
                # Safe Mode: At +7pt, move SL to entry + 1
                if profit >= 7.0 and self._trade_sl < self.current_trade.entry_price + 1:
                    self._trade_sl = self.current_trade.entry_price + 1
                    logger.info(f"🔒 SAFE MODE | SL → Entry + 1 = ₹{self._trade_sl:.2f}")
                
                # Trail Mode: At +10pt, TSL = High - 5
                if profit >= 10.0:
                    new_sl = self._trade_highest - 5.0
                    if new_sl > self._trade_sl:
                        self._trade_sl = new_sl
                        logger.info(f"📈 TSL UPDATE | SL → ₹{self._trade_sl:.2f}")
            
            # Check TSL hit
            if option_price <= self._trade_sl and self._trade_sl > self.current_trade.entry_price:
                profit = (option_price - self.current_trade.entry_price) * self.fixed_qty
                return self._exit_trade(ts, option_price, f"TSL Hit (+₹{profit:.0f})")
        
        return None
    
    def _exit_trade(
        self,
        ts: datetime,
        exit_price: float,
        reason: str
    ) -> Dict:
        """Exit current trade."""
        if not self.current_trade:
            return {'action': 'EXIT'}
        
        self.current_trade.exit_time = ts
        self.current_trade.exit_price = exit_price
        self.current_trade.exit_reason = reason
        
        # Calculate PnL
        pnl = (exit_price - self.current_trade.entry_price) * self.fixed_qty
        self.current_trade.pnl = pnl
        
        # Update equity
        self.equity_curve.append(self.equity_curve[-1] + pnl)
        
        # Track SL hits
        if "SL Hit" in reason and "TSL" not in reason:
            self.daily_sl_hits += 1
            logger.info(f"⚠️ SL Hit #{self.daily_sl_hits}/{self.max_sl_per_day}")
        
        # Log
        pnl_str = f"+₹{pnl:.0f}" if pnl > 0 else f"-₹{abs(pnl):.0f}"
        strategy = self.current_trade.strategy
        logger.info(f"🔚 {strategy} EXIT @ ₹{exit_price:.2f} | {reason} | PnL: {pnl_str}")
        
        # Store trade
        self.trades.append(self.current_trade)
        self.current_trade = None
        
        # Cooldown
        self.cooldown_counter = self.cooldown_candles
        
        return {
            'action': 'EXIT',
            'price': exit_price,
            'pnl': pnl,
            'reason': reason,
            'strategy': strategy
        }
    
    def get_results(self) -> HybridResult:
        """Calculate and return combined results."""
        if not self.trades:
            return HybridResult()
        
        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        
        total_wins = sum(t.pnl for t in winning)
        total_losses = abs(sum(t.pnl for t in losing))
        
        # By strategy
        n_trades = [t for t in self.trades if t.strategy == "N-Structure"]
        range_trades = [t for t in self.trades if t.strategy == "Range"]
        
        n_wins = len([t for t in n_trades if t.pnl > 0])
        range_wins = len([t for t in range_trades if t.pnl > 0])
        
        # Max drawdown
        max_dd = 0
        peak = 0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        
        return HybridResult(
            total_trades=len(self.trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            total_pnl=sum(t.pnl for t in self.trades),
            win_rate=len(winning) / len(self.trades) * 100 if self.trades else 0,
            profit_factor=total_wins / total_losses if total_losses > 0 else float('inf'),
            max_drawdown=max_dd,
            
            n_structure_trades=len(n_trades),
            n_structure_pnl=sum(t.pnl for t in n_trades),
            n_structure_win_rate=n_wins / len(n_trades) * 100 if n_trades else 0,
            
            range_trades=len(range_trades),
            range_pnl=sum(t.pnl for t in range_trades),
            range_win_rate=range_wins / len(range_trades) * 100 if range_trades else 0,
            
            trending_periods=self.trending_periods,
            sideways_periods=self.sideways_periods,
            regime_switches=self.regime_switches,
            
            trades=self.trades
        )
