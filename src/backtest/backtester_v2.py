"""
N-Structure Backtester V2 - Strategic Architecture Implementation

This backtester implements the complete N-Structure trading strategy as per
the Strategic Architecture and Project Planning document.

Key Features:
1. Finite State Machine (FSM) with proper state transitions
2. Dual-chart divergence detection (Index vs Option)
3. Higher Low (HL) validation with gap threshold
4. Buffered entry (+1.5 points on breakout)
5. Hybrid trailing stop (BE at +5, then candle trail)
6. Risk management with kill switch

Author: N-Structure Trading Bot
Version: 2.0
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque
from enum import Enum, auto

from loguru import logger

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.historical_data import HistoricalCandle
from indicators.ema import EMASet
from indicators.filters import VolumeFilter, TrendFilter, CompositeFilter
from indicators.atr import ATRCalculator, VolatilityFilter
from core.risk_manager import PartialProfitManager, DrawdownProtection, ExitType


# =============================================================================
# SECTION 3.3: FINITE STATE MACHINE STATES
# =============================================================================

class TradingState(Enum):
    """
    FSM States as per Strategic Architecture Section 3.3.
    
    IDLE -> SETUP -> VALIDATION -> READY -> ACTIVE -> COOLDOWN
    """
    IDLE = auto()           # Monitoring for Resistance Breakout
    SETUP = auto()          # Breakout occurred; monitoring for Pullback to EMA
    VALIDATION = auto()     # Pullback detected; waiting for Higher Low (HL)
    READY = auto()          # N-Structure confirmed; waiting for Entry Trigger
    ACTIVE = auto()         # Trade entered; monitoring SL and Trail
    COOLDOWN = auto()       # Trade closed; waiting for reset


# =============================================================================
# SECTION 2.2: DATA STRUCTURES
# =============================================================================

@dataclass
class SwingPoint:
    """Represents a swing high or low point."""
    timestamp: datetime
    price: float
    is_high: bool  # True = swing high, False = swing low


@dataclass 
class NStructure:
    """
    The N-Structure pattern as per Strategy Definition Document.
    
    Components:
    - breakout_high: The resistance level that was broken
    - pullback_low: The low of the pullback (HL1)
    - higher_low: The second higher low (HL2)
    - entry_trigger: breakout_high + buffer
    """
    breakout_high: float = 0.0
    pullback_low: float = 0.0      # HL1
    higher_low: float = 0.0         # HL2
    entry_trigger: float = 0.0      # breakout_high + 1.5
    formation_time: Optional[datetime] = None
    is_valid: bool = False
    
    def validate(self, min_hl_gap: float = 2.0) -> bool:
        """
        Validate N-Structure as per Section 4.2.
        
        - HL2 > HL1 (Higher Low confirmed)
        - Gap between HL1 and HL2 > threshold (momentum check)
        """
        if self.higher_low <= self.pullback_low:
            return False
        
        hl_gap = self.higher_low - self.pullback_low
        if hl_gap < min_hl_gap:
            return False
            
        self.is_valid = True
        return True


@dataclass
class Trade:
    """Single trade record with detailed tracking."""
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    initial_sl: float = 0.0
    current_sl: float = 0.0
    quantity: int = 25
    pnl: float = 0.0
    exit_reason: str = ""
    n_structure: Optional[NStructure] = None
    divergence_strength: float = 0.0
    sl_breath_used: bool = False  # N-Structure v1.1: One candle breath allowance
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class BacktestResult:
    """Comprehensive backtest results."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    
    # State statistics
    setups_detected: int = 0
    divergence_confirmed: int = 0
    entries_triggered: int = 0
    filter_rejections: int = 0  # v1.3: Entries blocked by filters


# =============================================================================
# SECTION 4: THE BACKTESTER ENGINE
# =============================================================================

class NStructureBacktesterV2:
    """
    N-Structure Backtester V2 - Complete Implementation.
    
    Implements the full strategic architecture with:
    - FSM-based state management
    - Dual-chart divergence detection
    - Buffered entries
    - Hybrid trailing stops
    - Risk management
    """
    
    # ==========================================================================
    # SECTION 2.2: CONFIGURATION PARAMETERS
    # ==========================================================================
    
    def __init__(
        self,
        # Capital & Risk Management (NEW)
        capital: float = 30000.0,           # Total trading capital
        risk_per_day_pct: float = 5.0,      # Max daily risk = 5% of capital
        risk_per_trade_pct: float = 2.0,    # Risk per trade = 2% of capital
        
        # Entry Parameters (Section 4.4)
        entry_buffer: float = 1.5,          # +1.5 points on breakout
        min_hl_gap: float = 3.0,            # Min gap between HL1 and HL2
        
        # Stop Loss Parameters (Section 6.1)
        initial_sl_points: float = 10.0,    # Default SL (will be calculated from risk)
        
        # Target Parameters
        target_points: float = 30.0,        # Profit target (unlimited with TSL)
        
        # Trailing Stop Parameters (Section 6.2 - Structure Based)
        tsl_buffer: float = 2.5,            # Buffer below swing low for TSL (wider = more room)
        use_structure_tsl: bool = True,     # Use HL-based trailing
        
        # Risk Management (Section 9.1)
        max_sl_per_day: int = 3,            # Max SL hits per day (only limiter!)
        cooldown_candles: int = 15,         # Wait after trade
        
        # Divergence Parameters (Section 4.3)
        divergence_threshold: float = 0.0005,  # 0.05% ROC threshold
        
        # General - Angel One NIFTY lot size
        lot_size: int = 65,               # NIFTY lot = 65 qty
        num_lots: int = 4,                # Always trade 4 lots = 260 qty
        
        # Strategy Filters (v1.3)
        enable_volume_filter: bool = True,
        enable_trend_filter: bool = True,
        volume_lookback: int = 20,
        min_volume_ratio: float = 0.8,
        
        # v1.3 Optimizations
        enable_atr_sl: bool = True,           # Use ATR-based dynamic SL
        atr_period: int = 14,                 # ATR calculation period
        atr_sl_multiplier: float = 1.5,       # ATR multiplier for SL
        min_sl_points: float = 8.0,           # Minimum SL points
        max_sl_points: float = 15.0,          # Maximum SL points
        
        enable_partial_profits: bool = True,  # Enable partial profit booking
        first_target_points: float = 15.0,    # First target for 50% exit
        second_target_points: float = 30.0,   # Second target for 25% exit
        
        enable_volatility_filter: bool = True, # Skip low/extreme volatility
        min_atr_for_trading: float = 50.0,    # Min ATR for tradeable day
        max_atr_for_trading: float = 200.0,   # Max ATR for safe trading
        
        enable_drawdown_protection: bool = True, # Max daily loss protection
        max_daily_loss: float = 5000.0,          # Stop at this loss
        max_consecutive_losses: int = 3,          # Stop after consecutive losses
        
        enable_atr_tsl: bool = True,          # Use ATR-based trailing
    ):
        """Initialize backtester with strategy parameters."""
        # Capital & Risk
        self.capital = capital
        self.risk_per_day = capital * (risk_per_day_pct / 100)   # ₹1,500
        self.risk_per_trade = capital * (risk_per_trade_pct / 100)  # ₹600
        self.max_daily_loss = self.risk_per_day
        
        self.entry_buffer = entry_buffer
        self.min_hl_gap = min_hl_gap
        self.initial_sl_points = initial_sl_points
        self.target_points = target_points
        self.tsl_buffer = tsl_buffer
        self.use_structure_tsl = use_structure_tsl
        self.max_sl_per_day = max_sl_per_day  # Only limiter - unlimited trades until max SL hit
        self.cooldown_candles = cooldown_candles
        self.divergence_threshold = divergence_threshold
        self.lot_size = lot_size
        self.num_lots = num_lots
        self.fixed_qty = lot_size * num_lots  # 65 × 4 = 260 qty
        
        # Strategy Filters (v1.3)
        self.enable_volume_filter = enable_volume_filter
        self.enable_trend_filter = enable_trend_filter
        self.volume_filter = VolumeFilter(
            lookback_periods=volume_lookback,
            min_volume_ratio=min_volume_ratio
        ) if enable_volume_filter else None
        self.trend_filter = TrendFilter() if enable_trend_filter else None
        self.filter_rejections = 0  # Track filter rejections
        
        # v1.3 Optimizations - ATR-based SL
        self.enable_atr_sl = enable_atr_sl
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.min_sl_points = min_sl_points
        self.max_sl_points = max_sl_points
        self.atr_calculator = ATRCalculator(
            period=atr_period,
            sl_multiplier=atr_sl_multiplier
        ) if enable_atr_sl else None
        
        # v1.3 - Partial Profit Booking
        self.enable_partial_profits = enable_partial_profits
        self.first_target_points = first_target_points
        self.second_target_points = second_target_points
        self.partial_profit_mgr = PartialProfitManager(
            first_target_points=first_target_points,
            second_target_points=second_target_points,
            lot_size=lot_size
        ) if enable_partial_profits else None
        
        # v1.3 - Volatility Filter
        self.enable_volatility_filter = enable_volatility_filter
        self.min_atr_for_trading = min_atr_for_trading
        self.max_atr_for_trading = max_atr_for_trading
        self.volatility_filter = VolatilityFilter(
            atr_period=atr_period,
            min_daily_atr=min_atr_for_trading,
            max_daily_atr=max_atr_for_trading
        ) if enable_volatility_filter else None
        
        # v1.3 - Drawdown Protection
        self.enable_drawdown_protection = enable_drawdown_protection
        self.drawdown_protection = DrawdownProtection(
            max_daily_loss=max_daily_loss,
            max_consecutive_losses=max_consecutive_losses
        ) if enable_drawdown_protection else None
        
        # v1.3 - ATR-based TSL
        self.enable_atr_tsl = enable_atr_tsl
        
        # State (Section 3.3)
        self.state = TradingState.IDLE
        self.current_trade: Optional[Trade] = None
        self.n_structure = NStructure()
        
        # EMAs (Section 4.1)
        self.index_emas = EMASet(periods=[9, 15])
        
        # Candle history
        self.index_candles: deque = deque(maxlen=50)
        
        # Swing point tracking
        self.swing_highs: List[SwingPoint] = []
        self.swing_lows: List[SwingPoint] = []
        self.trade_swing_lows: List[float] = []  # Swing lows during active trade for TSL
        
        # Daily tracking
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.daily_sl_hits: int = 0  # Track SL hits per day
        self.current_date: Optional[datetime] = None
        self.cooldown_counter: int = 0
        
        # RE-ENTRY TRACKING (Option A: HH Breakout Re-entry)
        self.awaiting_reentry: bool = False  # True after SL hit, waiting for new HH
        self.reentry_hh_level: float = 0.0   # HH level to break for re-entry
        self.sl_exit_candle_high: float = 0.0  # High of candle when SL hit
        self.reentries_today: int = 0  # Max 2 re-entries per day
        self.max_reentries_per_day: int = 2
        
        # Results
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = [0.0]
        
        # Statistics
        self.setups_detected: int = 0
        self.divergence_confirmed: int = 0
        self.entries_triggered: int = 0
        self.reentry_trades: int = 0  # Track re-entry success
    
    def reset(self):
        """Reset backtester state for new run."""
        self.state = TradingState.IDLE
        self.current_trade = None
        self.n_structure = NStructure()
        self.index_emas = EMASet(periods=[9, 15])
        self.index_candles.clear()
        self.swing_highs.clear()
        self.swing_lows.clear()
        self.trade_swing_lows = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_sl_hits = 0
        self.current_date = None
        self.cooldown_counter = 0
        # Re-entry reset
        self.awaiting_reentry = False
        self.reentry_hh_level = 0.0
        self.sl_exit_candle_high = 0.0
        self.reentries_today = 0
        self.trades = []
        self.equity_curve = [0.0]
        self.setups_detected = 0
        self.divergence_confirmed = 0
        self.entries_triggered = 0
        self.reentry_trades = 0
        self.filter_rejections = 0
        # Reset filters
        if self.volume_filter:
            self.volume_filter.reset()
        if self.trend_filter:
            self.trend_filter.reset()
        # Reset v1.3 components
        if self.atr_calculator:
            self.atr_calculator.reset()
        if self.volatility_filter:
            self.volatility_filter.reset()
        if self.partial_profit_mgr:
            self.partial_profit_mgr.reset()
        if self.drawdown_protection:
            self.drawdown_protection.reset()
    
    # ==========================================================================
    # SECTION 4.1: MODULE 1 - SETUP VALIDATOR (EMA & Support)
    # ==========================================================================
    
    def _is_trading_hours(self, ts: datetime) -> bool:
        """Check if within optimal trading hours (9:50 AM - 12:30 PM for entries)."""
        t = ts.time()
        # For new entries - morning session only
        if self.state != TradingState.ACTIVE:
            return time(9, 50) <= t <= time(12, 30)
        # For managing active trades - allow till 2:40 PM
        return time(9, 50) <= t <= time(14, 40)
    
    def _check_day_change(self, ts: datetime):
        """Reset daily counters on new day."""
        if self.current_date is None or ts.date() != self.current_date:
            self.current_date = ts.date()
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_sl_hits = 0  # Reset SL hits for new day
            self.reentries_today = 0  # Reset re-entries for new day
            self.awaiting_reentry = False  # Reset re-entry state
            self.reentry_hh_level = 0.0
            self.sl_exit_candle_high = 0.0
            self.swing_highs.clear()
            self.swing_lows.clear()
            self.state = TradingState.IDLE
            self.n_structure = NStructure()
            # v1.3: Reset drawdown protection for new day
            if self.drawdown_protection:
                self.drawdown_protection.new_day(ts)
            # v1.3: Reset partial profit manager
            if self.partial_profit_mgr:
                self.partial_profit_mgr.reset()
    
    def _check_kill_switch(self) -> bool:
        """
        Section 9.1: Kill Switch Protocol.
        
        Returns True if trading should stop.
        
        v1.3 Update: Added drawdown protection
        """
        # ONLY check max SL hits - this is the ONLY limiter!
        if self.daily_sl_hits >= self.max_sl_per_day:
            logger.warning(f"🛑 KILL SWITCH: Daily SL hits {self.daily_sl_hits} >= {self.max_sl_per_day}")
            return True
        
        # v1.3: Check drawdown protection
        if self.drawdown_protection:
            can_trade, reason = self.drawdown_protection.can_trade()
            if not can_trade:
                logger.warning(f"🛑 DRAWDOWN PROTECTION: {reason}")
                return True
        
        return False
    
    def _is_price_above_ema(self, price: float, ema9: float, ema15: float) -> bool:
        """Check if price is supported by EMAs (Section 4.1)."""
        return price > min(ema9, ema15)
    
    def _is_uptrend(self, ema9: float, ema15: float) -> bool:
        """Check if in uptrend (EMA9 > EMA15)."""
        return ema9 > ema15
    
    # ==========================================================================
    # SECTION 4.2: MODULE 2 - STRUCTURE SCANNER (The N-Shape)
    # ==========================================================================
    
    def _detect_swing_points(self, candle: HistoricalCandle, idx: int):
        """Detect swing highs and lows in price action."""
        if len(self.index_candles) < 5:
            return
        
        candles = list(self.index_candles)
        
        # Check for swing high (higher than 2 candles on each side)
        if idx >= 2:
            mid_idx = len(candles) - 3
            if mid_idx >= 0 and mid_idx < len(candles):
                mid = candles[mid_idx]
                left1 = candles[mid_idx - 1] if mid_idx > 0 else None
                left2 = candles[mid_idx - 2] if mid_idx > 1 else None
                right1 = candles[mid_idx + 1] if mid_idx + 1 < len(candles) else None
                right2 = candles[mid_idx + 2] if mid_idx + 2 < len(candles) else None
                
                if all([left1, left2, right1, right2]):
                    if (mid.high > left1.high and mid.high > left2.high and
                        mid.high > right1.high and mid.high > right2.high):
                        self.swing_highs.append(SwingPoint(mid.timestamp, mid.high, True))
                        # Keep only recent swing highs
                        if len(self.swing_highs) > 10:
                            self.swing_highs = self.swing_highs[-10:]
        
        # Check for swing low
        if idx >= 2:
            mid_idx = len(candles) - 3
            if mid_idx >= 0 and mid_idx < len(candles):
                mid = candles[mid_idx]
                left1 = candles[mid_idx - 1] if mid_idx > 0 else None
                left2 = candles[mid_idx - 2] if mid_idx > 1 else None
                right1 = candles[mid_idx + 1] if mid_idx + 1 < len(candles) else None
                right2 = candles[mid_idx + 2] if mid_idx + 2 < len(candles) else None
                
                if all([left1, left2, right1, right2]):
                    if (mid.low < left1.low and mid.low < left2.low and
                        mid.low < right1.low and mid.low < right2.low):
                        self.swing_lows.append(SwingPoint(mid.timestamp, mid.low, False))
                        if len(self.swing_lows) > 10:
                            self.swing_lows = self.swing_lows[-10:]
    
    def _check_higher_low_pattern(self) -> bool:
        """
        Section 4.2: Check for Higher Low pattern.
        
        Returns True if HL2 > HL1 with sufficient gap.
        """
        if len(self.swing_lows) < 2:
            return False
        
        hl1 = self.swing_lows[-2].price
        hl2 = self.swing_lows[-1].price
        
        # HL2 must be higher than HL1
        if hl2 <= hl1:
            return False
        
        # Gap must be sufficient (momentum check)
        gap = hl2 - hl1
        if gap < self.min_hl_gap:
            return False
        
        # Update N-Structure
        self.n_structure.pullback_low = hl1
        self.n_structure.higher_low = hl2
        
        return True
    
    # ==========================================================================
    # SECTION 4.3: MODULE 3 - DIVERGENCE FILTER
    # ==========================================================================
    
    def _calculate_roc(self, candles: List[HistoricalCandle], periods: int = 5) -> float:
        """Calculate Rate of Change for divergence detection."""
        if len(candles) < periods + 1:
            return 0.0
        
        current = candles[-1].close
        previous = candles[-(periods + 1)].close
        
        if previous == 0:
            return 0.0
        
        return (current - previous) / previous
    
    def _check_divergence(
        self,
        index_candles: List[HistoricalCandle],
        option_premium: float,
        option_premium_prev: float
    ) -> Tuple[bool, float]:
        """
        Section 4.3: Check for positive divergence.
        
        Condition: Index = Sideways/Down AND Option = Strength/Up
        
        Returns: (is_divergent, strength)
        """
        if len(index_candles) < 6:
            return False, 0.0
        
        # Calculate Index ROC
        index_roc = self._calculate_roc(list(index_candles))
        
        # Calculate Option ROC (simplified)
        if option_premium_prev > 0:
            option_roc = (option_premium - option_premium_prev) / option_premium_prev
        else:
            option_roc = 0.0
        
        # Divergence: Index flat/down, Option up
        index_is_flat_or_down = index_roc < self.divergence_threshold
        option_is_up = option_roc > self.divergence_threshold
        
        is_divergent = index_is_flat_or_down and option_is_up
        strength = option_roc - index_roc if is_divergent else 0.0
        
        return is_divergent, strength
    
    # ==========================================================================
    # SECTION 6: RISK MANAGEMENT AND EXIT STRATEGY
    # ==========================================================================
    
    def _update_trailing_sl(self, current_price: float, prev_candle_low: float):
        """
        Section 6.2: Structure-Based Trailing Stop (HL Trail).
        
        UNLIMITED TRAILING based on market structure:
        1. Initial SL = Entry - risk_points (calculated from 2% risk)
        2. When profit >= +5 points AND we have 2+ confirmed HLs:
           - Move TSL to recent Higher Low (HL) - buffer
        3. Keep trailing as new HLs form
        4. Exit only when HL breaks (trend reversal)
        
        N-Structure v1.1: Structure first, PnL second!
        """
        if not self.current_trade:
            return
        
        entry = self.current_trade.entry_price
        profit = current_price - entry
        
        # Phase 1: Move to Breakeven at +8 points (protect capital)
        if profit >= 8.0 and self.current_trade.current_sl < entry:
            new_sl = entry + 0.5  # Lock in 0.5 point
            self.current_trade.current_sl = new_sl
            logger.debug(f"  → BE triggered: SL moved to {new_sl:.2f}")
            return  # Don't do anything else this candle
        
        # Phase 2: STRUCTURE-FIRST TSL (N-Structure v1.1)
        # Trigger: 2+ HLs confirmed AND not in loss (structure primary, profit secondary)
        min_hls_required = 2  # Need at least 2 confirmed HLs
        not_in_loss = profit >= 0  # Trade not in loss
        
        if (self.use_structure_tsl and 
            not_in_loss and  # Structure-first: just need to not be in loss
            len(self.trade_swing_lows) >= min_hls_required):
            
            # Get the SECOND most recent swing low (more reliable)
            # This gives more room to breathe
            recent_hl = self.trade_swing_lows[-2] if len(self.trade_swing_lows) >= 2 else self.trade_swing_lows[-1]
            structure_sl = recent_hl - self.tsl_buffer - 1.0  # Extra 1 point buffer
            
            # Only move SL up if it's meaningfully higher (at least 1 point)
            if structure_sl > self.current_trade.current_sl + 1.0:
                self.current_trade.current_sl = structure_sl
                logger.debug(f"  → TSL moved to HL-2: {structure_sl:.2f}")
        
        # Phase 3: Tight trail after BIG profit (+20 points) - let winners run!
        # N-Structure v1.1: Pushed from +15 to +20 to capture 20-40pt expansion moves
        if profit >= 20.0 and len(self.trade_swing_lows) >= 1:
            # Use most recent HL for tighter trail
            recent_hl = self.trade_swing_lows[-1]
            tight_sl = recent_hl - 1.5  # Tighter buffer after big profit
            if tight_sl > self.current_trade.current_sl:
                self.current_trade.current_sl = tight_sl
                logger.debug(f"  → Tight TSL: {tight_sl:.2f}")
        
        # Phase 4: v1.3 ATR-based trailing (alternative approach)
        if self.enable_atr_tsl and self.atr_calculator and profit >= 10.0:
            atr_sl = self.atr_calculator.get_trailing_sl(
                current_price=current_price,
                entry_price=entry,
                current_sl=self.current_trade.current_sl,
                min_profit_for_trail=10.0
            )
            if atr_sl > self.current_trade.current_sl:
                self.current_trade.current_sl = atr_sl
                logger.debug(f"  → ATR TSL: {atr_sl:.2f}")
    
    def _check_exit_conditions(
        self,
        candle: HistoricalCandle,
        ts: datetime
    ) -> Tuple[bool, str, float]:
        """
        Check all exit conditions.
        
        UNLIMITED PROFIT MODE:
        - No fixed target (let winners run with TSL)
        - Exit only on TSL hit or EOD
        
        Returns: (should_exit, reason, exit_price)
        """
        if not self.current_trade:
            return False, "", 0.0
        
        entry = self.current_trade.entry_price
        sl = self.current_trade.current_sl
        
        # NO FIXED TARGET - Let profits run with trailing SL!
        # (Uncomment below if you want a safety target)
        # target = entry + self.target_points
        # if candle.high >= target:
        #     return True, "Target Hit", target
        
        # Check SL/TSL Hit - with N-Structure v1.1 BREATH RULE
        if candle.low <= sl:
            exit_price = sl
            profit = exit_price - entry
            
            if sl > self.current_trade.initial_sl:
                # Trailing SL was triggered
                if profit >= 0:
                    return True, f"TSL Hit (+₹{profit*self.fixed_qty:.0f})", exit_price
                else:
                    return True, "Trailing SL", exit_price
            else:
                # Initial SL hit - apply N-Structure v1.1 BREATH RULE
                # If index structure intact AND within 3pt of SL, allow ONE candle breath
                if not self.current_trade.sl_breath_used:
                    # Check if we have recent swing lows (structure intact)
                    structure_intact = len(self.trade_swing_lows) >= 1
                    sl_within_range = abs(candle.low - sl) <= 3.0  # Within 3pt of SL
                    
                    if structure_intact and sl_within_range:
                        # Grant one candle breath - don't exit yet
                        self.current_trade.sl_breath_used = True
                        logger.debug(f"  → SL Breath granted: structure intact, wick {candle.low:.2f} near SL {sl:.2f}")
                        return False, "", 0.0
                
                return True, "SL Hit", exit_price
        
        # Check EOD Exit (3:25 PM)
        if ts.time() >= time(15, 25):
            return True, "EOD Exit", candle.close
        
        return False, "", 0.0
    
    # ==========================================================================
    # SECTION 3.3: STATE MACHINE TRANSITIONS
    # ==========================================================================
    
    def _transition_state(self, new_state: TradingState, reason: str = ""):
        """Transition to a new state with logging."""
        old_state = self.state
        self.state = new_state
        if reason:
            logger.debug(f"  State: {old_state.name} → {new_state.name} ({reason})")
    
    def _process_idle_state(
        self,
        candle: HistoricalCandle,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        IDLE State: Looking for Resistance Breakout.
        
        Transition to SETUP when:
        - Price breaks above recent swing high
        - In uptrend (EMA9 > EMA15)
        """
        if not self._is_uptrend(ema9, ema15):
            return False
        
        if len(self.swing_highs) < 1:
            return False
        
        recent_high = self.swing_highs[-1].price
        
        # Check for breakout
        if candle.close > recent_high:
            self.n_structure.breakout_high = recent_high
            self._transition_state(TradingState.SETUP, f"Breakout above {recent_high:.2f}")
            self.setups_detected += 1
            return True
        
        return False
    
    def _process_setup_state(
        self,
        candle: HistoricalCandle,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        SETUP State: Monitoring for Pullback to EMA.
        
        Transition to VALIDATION when:
        - Price pulls back to EMA zone
        - Price must NOT close below EMA (hard kill)
        """
        # Check for invalidation (close below EMA)
        if candle.close < ema15:
            self._transition_state(TradingState.IDLE, "Close below EMA - Setup invalid")
            self.n_structure = NStructure()
            return False
        
        # Check for pullback to EMA zone (within 0.3% of EMA)
        ema_zone = min(ema9, ema15)
        distance_pct = abs(candle.low - ema_zone) / ema_zone
        
        if distance_pct < 0.003 or candle.low <= ema_zone:
            self._transition_state(TradingState.VALIDATION, "Pullback to EMA detected")
            return True
        
        return False
    
    def _process_validation_state(
        self,
        candle: HistoricalCandle,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        VALIDATION State: Waiting for Higher Low confirmation.
        
        Transition to READY when:
        - Higher Low pattern confirmed (HL2 > HL1)
        - Gap between HLs is sufficient
        """
        # Check for invalidation
        if candle.close < ema15:
            self._transition_state(TradingState.IDLE, "Close below EMA - Pattern invalid")
            self.n_structure = NStructure()
            return False
        
        # Check for Higher Low
        if self._check_higher_low_pattern():
            # Set entry trigger with buffer
            self.n_structure.entry_trigger = self.n_structure.breakout_high + self.entry_buffer
            self.n_structure.formation_time = candle.timestamp
            self._transition_state(TradingState.READY, f"HL confirmed, trigger: {self.n_structure.entry_trigger:.2f}")
            return True
        
        return False
    
    def _process_ready_state(
        self,
        candle: HistoricalCandle,
        option_price: float,
        ts: datetime
    ) -> bool:
        """
        READY State: N-Structure confirmed, waiting for entry trigger.
        
        Transition to ACTIVE when:
        - Price breaks entry trigger (breakout_high + 1.5)
        
        Entry uses Stop-Limit logic (Section 4.4).
        """
        trigger = self.n_structure.entry_trigger
        
        # Check for entry trigger
        if candle.high >= trigger:
            # ===========================================
            # FILTER 1: Strong Bullish Candle (body > 50%)
            # ===========================================
            candle_body = candle.close - candle.open
            candle_range = candle.high - candle.low
            if candle_body <= 0:  # Must be bullish
                return False
            if candle_range > 0 and (candle_body / candle_range) < 0.5:
                return False  # Reject weak candles - need 50%+ body
            
            # ===========================================
            # FILTER 2: Minimum candle size (avoid tiny moves)
            # ===========================================
            if candle_range < 3.0:  # Min 3 points range on index
                return False
            
            # ===========================================
            # FILTER 3: Close near high (momentum)
            # ===========================================
            close_from_high = candle.high - candle.close
            if close_from_high > candle_range * 0.3:  # Close must be in top 30% of candle
                return False
            
            # ===========================================
            # FILTER 4: Avoid first 20 mins (high volatility)
            # ===========================================
            if ts.time() < time(9, 35):
                return False
            
            # ===========================================
            # FILTER 5: Volume Filter (v1.3) - reject low volume
            # ===========================================
            if self.volume_filter:
                vol_result = self.volume_filter.update(candle.volume if hasattr(candle, 'volume') and candle.volume else 1000)
                if not vol_result.is_sufficient:
                    self.filter_rejections += 1
                    logger.debug(f"Entry blocked by volume filter: {vol_result.message}")
                    return False
            
            # ===========================================
            # FILTER 6: Trend Filter (v1.3) - check EMA alignment
            # ===========================================
            if self.trend_filter:
                ema9 = self.index_emas.get_value(9) or candle.close
                ema15 = self.index_emas.get_value(15) or candle.close
                trend_result = self.trend_filter.analyze(candle.close, ema9, ema15)
                if not trend_result.is_favorable:
                    self.filter_rejections += 1
                    logger.debug(f"Entry blocked by trend filter: {trend_result.message}")
                    return False
            
            # Enter trade with FIXED QUANTITY (4 lots = 260 qty)
            entry_price = option_price
            
            # ===========================================
            # v1.3: ATR-based dynamic SL or fixed 10 points
            # ===========================================
            if self.enable_atr_sl and self.atr_calculator and self.atr_calculator.is_ready:
                # Get ATR-based SL, clamped between min and max
                atr_sl = self.atr_calculator.get_dynamic_sl(
                    entry_price=entry_price,
                    min_sl=self.min_sl_points,
                    max_sl=self.max_sl_points
                )
                sl_points = entry_price - atr_sl
                initial_sl = atr_sl
                logger.debug(f"  → ATR-based SL: {sl_points:.1f}pt (ATR={self.atr_calculator.current_atr:.2f})")
            else:
                # Fallback to fixed SL
                sl_points = self.initial_sl_points  # Default 10 points
                initial_sl = entry_price - sl_points
            
            # FIXED QUANTITY: Always 4 lots (65 × 4 = 260 qty)
            fixed_qty = self.fixed_qty  # 260 qty
            
            # Clear trade swing lows for fresh TSL tracking
            self.trade_swing_lows = []
            
            self.current_trade = Trade(
                entry_time=ts,
                entry_price=entry_price,
                initial_sl=initial_sl,
                current_sl=initial_sl,
                quantity=fixed_qty,  # Fixed 4 lots = 260 qty
                n_structure=self.n_structure
            )
            
            # v1.3: Register with partial profit manager
            if self.partial_profit_mgr:
                self.partial_profit_mgr.open_position(entry_price, fixed_qty)
            
            self.daily_trades += 1
            self.entries_triggered += 1
            self._transition_state(TradingState.ACTIVE, f"Entry @ {entry_price:.2f}")
            logger.info(f"📈 ENTRY @ ₹{entry_price:.2f} | SL: ₹{initial_sl:.2f} ({sl_points:.0f}pt) | Qty: {fixed_qty} ({self.num_lots} lots) | Risk: ₹{sl_points * fixed_qty:.0f} | {ts}")
            return True
        
        # Check for pattern invalidation (new lower low)
        if len(self.swing_lows) >= 2:
            if candle.low < self.n_structure.higher_low - 5:
                self._transition_state(TradingState.IDLE, "Pattern broken - new lower low")
                self.n_structure = NStructure()
                return False
        
        return False
    
    def _process_active_state(
        self,
        option_candle: HistoricalCandle,
        prev_option_low: float,
        ts: datetime
    ) -> bool:
        """
        ACTIVE State: Trade is live, managing position.
        
        Structure-based TSL: Track swing lows during trade
        - When new HL forms, move TSL below it
        - Unlimited profit potential!
        
        Transition to COOLDOWN when:
        - TSL hit (structure break)
        - EOD exit
        """
        if not self.current_trade:
            self._transition_state(TradingState.COOLDOWN, "No trade found")
            return False
        
        # Track swing lows during trade for TSL
        # STRICTER swing low detection:
        # 1. Middle candle low must be lower than both neighbors
        # 2. Must be a HIGHER LOW (above previous swing low) - confirms uptrend
        # 3. Minimum 2 points above entry to be relevant
        if len(self.index_candles) >= 3:
            candles = list(self.index_candles)
            if len(candles) >= 3:
                prev_low = candles[-3].low
                curr_low = candles[-2].low  
                next_low = candles[-1].low
                
                # Swing low confirmed when middle candle is lowest
                if curr_low < prev_low and curr_low <= next_low:
                    if self.current_trade:
                        # Only track swing low if:
                        # 1. It's above entry price (we're in profit territory)
                        # 2. It's higher than previous swing low (actual HL structure)
                        if prev_option_low > self.current_trade.entry_price:
                            # Check if it's a genuine Higher Low
                            if len(self.trade_swing_lows) == 0:
                                self.trade_swing_lows.append(prev_option_low)
                                logger.debug(f"  → First HL tracked: {prev_option_low:.2f}")
                            elif prev_option_low > self.trade_swing_lows[-1] + 1.0:
                                # Must be at least 1 point higher than last HL
                                self.trade_swing_lows.append(prev_option_low)
                                logger.debug(f"  → New HL tracked: {prev_option_low:.2f} (#{len(self.trade_swing_lows)})")
                                # Keep only last 5 swing lows
                                if len(self.trade_swing_lows) > 5:
                                    self.trade_swing_lows = self.trade_swing_lows[-5:]
        
        # Update trailing stop using structure
        self._update_trailing_sl(option_candle.close, prev_option_low)
        
        # ===========================================
        # v1.3: Check for partial profit exit
        # ===========================================
        if self.partial_profit_mgr and self.current_trade:
            partial_result = self.partial_profit_mgr.check_exit(option_candle.close)
            if partial_result.exit_type == ExitType.PARTIAL_EXIT:
                logger.info(partial_result.message)
                # Update trade quantity but don't close position yet
                self.current_trade.quantity = partial_result.remaining_quantity
        
        # Check exit conditions on OPTION candle (NO fixed target - unlimited!)
        should_exit, reason, exit_price = self._check_exit_conditions(option_candle, ts)
        
        if should_exit:
            self.current_trade.exit_time = ts
            self.current_trade.exit_price = exit_price
            self.current_trade.exit_reason = reason
            
            # v1.3: Calculate PnL including partial exits
            if self.partial_profit_mgr and self.partial_profit_mgr.position:
                # Close remaining position
                remaining_qty, remaining_pnl = self.partial_profit_mgr.close_remaining(exit_price)
                total_pnl = self.partial_profit_mgr.get_total_pnl()
                self.current_trade.pnl = total_pnl
            else:
                self.current_trade.pnl = (exit_price - self.current_trade.entry_price) * self.fixed_qty
            
            self.trades.append(self.current_trade)
            self.daily_pnl += self.current_trade.pnl
            self.equity_curve.append(self.equity_curve[-1] + self.current_trade.pnl)
            
            # v1.3: Record with drawdown protection
            if self.drawdown_protection:
                self.drawdown_protection.record_trade(self.current_trade.pnl)
            
            pnl_str = f"+₹{self.current_trade.pnl:.0f}" if self.current_trade.pnl > 0 else f"-₹{abs(self.current_trade.pnl):.0f}"
            logger.info(f"📉 EXIT @ ₹{exit_price:.2f} | {reason} | PnL: {pnl_str} | {ts}")
            
            # Track SL hits - this is the ONLY limiter!
            is_sl_hit = "SL Hit" in reason and self.current_trade.pnl <= -self.risk_per_trade * 0.5
            if is_sl_hit:
                self.daily_sl_hits += 1
                logger.info(f"⚠️ SL Hit #{self.daily_sl_hits}/{self.max_sl_per_day} for today")
                self.cooldown_counter = self.cooldown_candles * 2  # Double cooldown after SL
                
                # OPTION A: Set up RE-ENTRY on HH breakout
                # Only if we haven't hit max SL and max re-entries
                if (self.daily_sl_hits < self.max_sl_per_day and 
                    self.reentries_today < self.max_reentries_per_day):
                    self.awaiting_reentry = True
                    self.sl_exit_candle_high = option_candle.high  # Track high of exit candle
                    self.reentry_hh_level = 0.0  # Will be set when new HH forms
                    logger.info(f"🔄 RE-ENTRY ARMED: Looking for new HH above {self.sl_exit_candle_high:.2f}")
            else:
                self.cooldown_counter = self.cooldown_candles
                self.awaiting_reentry = False  # No re-entry after profitable exit
            
            self.current_trade = None
            self._transition_state(TradingState.COOLDOWN, reason)
            return True
        
        return False
    
    def _process_cooldown_state(self) -> bool:
        """
        COOLDOWN State: Waiting before next setup.
        
        Transition to IDLE when cooldown period expires.
        """
        if self.cooldown_counter > 0:
            self.cooldown_counter -= 1
            return False
        
        self.n_structure = NStructure()
        self._transition_state(TradingState.IDLE, "Cooldown complete")
        return True
    
    def _check_reentry_opportunity(
        self,
        candle: HistoricalCandle,
        option_price: float,
        ts: datetime,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        OPTION A: HH Breakout Re-entry after SL Hit.
        
        After SL hit:
        1. Track for new Higher High (HH) above SL exit candle high
        2. When HH forms, set reentry_hh_level
        3. On HH breakout + premium in range → RE-ENTRY
        
        Returns True if re-entry executed.
        """
        if not self.awaiting_reentry:
            return False
        
        if self.reentries_today >= self.max_reentries_per_day:
            self.awaiting_reentry = False
            return False
        
        # Must be in uptrend for re-entry
        if not self._is_uptrend(ema9, ema15):
            return False
        
        # Step 1: Find new HH (higher than SL exit candle high)
        if self.reentry_hh_level == 0.0:
            # Look for new swing high above exit candle high
            if len(self.swing_highs) > 0:
                recent_hh = self.swing_highs[-1].price
                if recent_hh > self.sl_exit_candle_high + 2.0:  # At least 2 points higher
                    self.reentry_hh_level = recent_hh
                    logger.debug(f"  → New HH detected for re-entry: {recent_hh:.2f}")
            return False  # Wait for HH breakout
        
        # Step 2: Check for HH breakout
        if candle.high >= self.reentry_hh_level + self.entry_buffer:
            # Validate entry candle (same filters as normal entry)
            candle_body = candle.close - candle.open
            candle_range = candle.high - candle.low
            
            if candle_body <= 0:  # Must be bullish
                return False
            if candle_range > 0 and (candle_body / candle_range) < 0.4:
                return False  # Slightly relaxed from 0.5 for re-entry
            if candle_range < 2.0:  # Min 2 points range (relaxed from 3)
                return False
            
            # EXECUTE RE-ENTRY!
            entry_price = option_price
            sl_points = 10.0
            initial_sl = entry_price - sl_points
            
            # Clear trade swing lows for fresh TSL tracking
            self.trade_swing_lows = []
            
            self.current_trade = Trade(
                entry_time=ts,
                entry_price=entry_price,
                initial_sl=initial_sl,
                current_sl=initial_sl,
                quantity=self.fixed_qty,
                n_structure=self.n_structure
            )
            
            self.daily_trades += 1
            self.entries_triggered += 1
            self.reentries_today += 1
            self.reentry_trades += 1
            
            # Reset re-entry state
            self.awaiting_reentry = False
            self.reentry_hh_level = 0.0
            self.sl_exit_candle_high = 0.0
            
            self._transition_state(TradingState.ACTIVE, f"RE-ENTRY @ {entry_price:.2f}")
            logger.info(f"🔄 RE-ENTRY #{self.reentries_today} @ ₹{entry_price:.2f} | SL: ₹{initial_sl:.2f} | HH Break: {self.reentry_hh_level:.2f} | {ts}")
            return True
        
        return False
    
    # ==========================================================================
    # MAIN RUN METHOD
    # ==========================================================================
    
    def run_index_only(
        self,
        index_candles: List[HistoricalCandle],
        entry_premium_range: Tuple[float, float] = (90.0, 110.0),
        delta: float = 0.5
    ) -> BacktestResult:
        """
        Run backtest using INDEX data with dynamic premium calculation.
        
        At each entry signal, simulates picking a CE option with premium
        in the specified range. More realistic than single-strike backtest.
        """
        self.reset()
        
        min_premium, max_premium = entry_premium_range
        target_premium = (min_premium + max_premium) / 2
        
        logger.info(f"🚀 Starting N-Structure V2 Backtest")
        logger.info(f"   Candles: {len(index_candles)} | Premium: ₹{min_premium}-₹{max_premium}")
        logger.info(f"   Entry Buffer: +{self.entry_buffer}pt | HL Gap: {self.min_hl_gap}pt")
        logger.info(f"   SL: {self.initial_sl_points}pt | Target: {self.target_points}pt")
        
        # Track option premium simulation
        trade_entry_nifty: Optional[float] = None
        trade_entry_premium: Optional[float] = None
        prev_option_premium: float = target_premium
        
        for idx, candle in enumerate(index_candles):
            ts = candle.timestamp
            
            # Day change check
            self._check_day_change(ts)
            
            # Skip non-trading hours
            if not self._is_trading_hours(ts):
                continue
            
            # Kill switch check - but allow exit of active trades
            if self._check_kill_switch() and self.state != TradingState.ACTIVE:
                continue
            
            # Update EMAs
            self.index_emas.update(candle.close)
            ema9 = self.index_emas.get_value(9)
            ema15 = self.index_emas.get_value(15)
            
            if not all([ema9, ema15]):
                continue
            
            # v1.3: Update ATR calculator
            if self.atr_calculator:
                self.atr_calculator.update(candle.high, candle.low, candle.close)
            
            # v1.3: Update volatility filter and check if tradeable
            if self.volatility_filter:
                self.volatility_filter.update(candle.high, candle.low, candle.close)
                if not self.volatility_filter.is_tradeable_day() and self.state != TradingState.ACTIVE:
                    # Skip low/extreme volatility periods for new entries
                    continue
            
            # Update volume filter history (even if not in READY state)
            if self.volume_filter and hasattr(candle, 'volume') and candle.volume:
                self.volume_filter.update(candle.volume)
            
            # Update trend filter history
            if self.trend_filter:
                self.trend_filter.analyze(candle.close, ema9, ema15)
            
            # Store candle and detect swings
            self.index_candles.append(candle)
            self._detect_swing_points(candle, idx)
            
            # Simulate option premium
            if trade_entry_nifty is not None and trade_entry_premium is not None:
                nifty_change = candle.close - trade_entry_nifty
                current_premium = trade_entry_premium + (delta * nifty_change)
                current_premium = max(current_premium, 1.0)
            else:
                current_premium = target_premium
            
            # Process current state
            if self.state == TradingState.IDLE:
                self._process_idle_state(candle, ema9, ema15)
                
            elif self.state == TradingState.SETUP:
                self._process_setup_state(candle, ema9, ema15)
                
            elif self.state == TradingState.VALIDATION:
                self._process_validation_state(candle, ema9, ema15)
                
            elif self.state == TradingState.READY:
                # Get fresh option premium for entry
                import random
                entry_premium = random.uniform(min_premium, max_premium)
                
                if self._process_ready_state(candle, entry_premium, ts):
                    # Record entry state for premium tracking
                    trade_entry_nifty = candle.close
                    trade_entry_premium = entry_premium
                    
            elif self.state == TradingState.ACTIVE:
                # Update premium based on NIFTY movement
                if trade_entry_nifty is not None:
                    # Calculate premium at each OHLC point of the index candle
                    nifty_open_change = candle.open - trade_entry_nifty
                    nifty_high_change = candle.high - trade_entry_nifty
                    nifty_low_change = candle.low - trade_entry_nifty
                    nifty_close_change = candle.close - trade_entry_nifty
                    
                    opt_open = max(trade_entry_premium + (delta * nifty_open_change), 0.5)
                    opt_high = max(trade_entry_premium + (delta * nifty_high_change), 0.5)
                    opt_low = max(trade_entry_premium + (delta * nifty_low_change), 0.5)
                    opt_close = max(trade_entry_premium + (delta * nifty_close_change), 0.5)
                    
                    current_premium = opt_close
                    
                    # Create synthetic candle for exit checks
                    synthetic_opt = HistoricalCandle(
                        timestamp=ts,
                        open=opt_open,
                        high=opt_high,
                        low=opt_low,
                        close=opt_close,
                        volume=0
                    )
                    
                    # Get previous option premium low for trailing
                    prev_opt_low = prev_option_premium - (candle.low - candle.close) * delta
                    prev_opt_low = max(prev_opt_low, 0.5)
                    
                    if self._process_active_state(synthetic_opt, prev_opt_low, ts):
                        trade_entry_nifty = None
                        trade_entry_premium = None
                        
            elif self.state == TradingState.COOLDOWN:
                # Check for RE-ENTRY opportunity on HH breakout
                if self.awaiting_reentry:
                    import random
                    entry_premium = random.uniform(min_premium, max_premium)
                    if self._check_reentry_opportunity(candle, entry_premium, ts, ema9, ema15):
                        # Record entry state for premium tracking
                        trade_entry_nifty = candle.close
                        trade_entry_premium = entry_premium
                        continue  # Skip normal cooldown processing
                
                self._process_cooldown_state()
            
            prev_option_premium = current_premium
        
        # Close any open trade
        if self.current_trade and index_candles:
            last = index_candles[-1]
            if trade_entry_nifty is not None:
                final_premium = trade_entry_premium + (delta * (last.close - trade_entry_nifty))
                final_premium = max(final_premium, 1.0)
                self.current_trade.exit_time = last.timestamp
                self.current_trade.exit_price = final_premium
                self.current_trade.exit_reason = "Backtest End"
                self.current_trade.pnl = (final_premium - self.current_trade.entry_price) * self.fixed_qty
                self.trades.append(self.current_trade)
                self.equity_curve.append(self.equity_curve[-1] + self.current_trade.pnl)
        
        return self._calculate_results()
    
    def _calculate_results(self) -> BacktestResult:
        """Calculate comprehensive backtest statistics."""
        result = BacktestResult(trades=self.trades)
        
        if not self.trades:
            return result
        
        result.total_trades = len(self.trades)
        result.setups_detected = self.setups_detected
        result.divergence_confirmed = self.divergence_confirmed
        result.entries_triggered = self.entries_triggered
        result.filter_rejections = self.filter_rejections  # v1.3
        
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.total_pnl = sum(t.pnl for t in self.trades)
        
        if result.total_trades > 0:
            result.win_rate = (result.winning_trades / result.total_trades) * 100
        
        if wins:
            result.avg_win = sum(t.pnl for t in wins) / len(wins)
        
        if losses:
            result.avg_loss = abs(sum(t.pnl for t in losses) / len(losses))
        
        # Profit factor
        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Max drawdown
        peak = 0
        max_dd = 0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd
        
        return result


# =============================================================================
# RESULTS PRINTER
# =============================================================================

def print_results_v2(result: BacktestResult):
    """Print comprehensive backtest results."""
    print("\n" + "=" * 65)
    print("      N-STRUCTURE V2 BACKTEST RESULTS (Strategic Architecture)")
    print("=" * 65)
    
    print(f"\n📊 Trade Statistics:")
    print(f"   Total Trades:      {result.total_trades}")
    print(f"   Winning Trades:    {result.winning_trades}")
    print(f"   Losing Trades:     {result.losing_trades}")
    print(f"   Win Rate:          {result.win_rate:.1f}%")
    
    print(f"\n📈 Setup Statistics:")
    print(f"   Setups Detected:   {result.setups_detected}")
    print(f"   Entries Triggered: {result.entries_triggered}")
    print(f"   Filter Rejections: {result.filter_rejections}")  # v1.3
    
    print(f"\n💰 P&L Analysis:")
    pnl_str = f"+₹{result.total_pnl:,.0f}" if result.total_pnl > 0 else f"-₹{abs(result.total_pnl):,.0f}"
    print(f"   Total P&L:         {pnl_str}")
    print(f"   Avg Win:           +₹{result.avg_win:,.0f}")
    print(f"   Avg Loss:          -₹{result.avg_loss:,.0f}")
    print(f"   Profit Factor:     {result.profit_factor:.2f}")
    print(f"   Max Drawdown:      -₹{result.max_drawdown:,.0f}")
    
    if result.trades:
        print(f"\n📝 Trade Log (Last 20):")
        print("-" * 65)
        for i, t in enumerate(result.trades[-20:], 1):
            pnl = f"+₹{t.pnl:.0f}" if t.pnl > 0 else f"-₹{abs(t.pnl):.0f}"
            print(f"   {i:2}. {t.entry_time.strftime('%d-%b %H:%M')} | "
                  f"Entry: ₹{t.entry_price:.1f} | "
                  f"Exit: ₹{t.exit_price:.1f} | "
                  f"{t.exit_reason:10} | {pnl}")
    
    print("\n" + "=" * 65)
