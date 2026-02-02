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
    
    IDLE -> SETUP -> VALIDATION -> READY -> PULLBACK_WAIT -> CONFIRMATION -> ACTIVE -> COOLDOWN
    v3.0: Added PULLBACK_WAIT and CONFIRMATION states
    """
    IDLE = auto()           # Monitoring for Resistance Breakout
    SETUP = auto()          # Breakout occurred; monitoring for Pullback to EMA
    VALIDATION = auto()     # Pullback detected; waiting for Higher Low (HL)
    READY = auto()          # N-Structure confirmed; waiting for Entry Trigger
    PULLBACK_WAIT = auto()  # v3.0 Option B: Waiting for price to pull back to breakout level
    CONFIRMATION = auto()   # v3.0 Option E: Waiting for confirmation candle
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
    - breakout_high: The resistance level that was broken (for CE)
    - breakdown_low: The support level that was broken (for PE)
    - pullback_low: The low of the pullback (HL1) for CE
    - pullback_high: The high of the bounce (LH1) for PE
    - higher_low: The second higher low (HL2) for CE
    - lower_high: The second lower high (LH2) for PE
    - entry_trigger: breakout_high + buffer (CE) or breakdown_low - buffer (PE)
    """
    # CE (Bullish) pattern
    breakout_high: float = 0.0
    pullback_low: float = 0.0      # HL1
    higher_low: float = 0.0         # HL2
    
    # PE (Bearish) pattern  
    breakdown_low: float = 0.0
    pullback_high: float = 0.0     # LH1
    lower_high: float = 0.0        # LH2
    
    # Common
    entry_trigger: float = 0.0      # breakout_high + 1.5 (CE) or breakdown_low - 1.5 (PE)
    formation_time: Optional[datetime] = None
    is_valid: bool = False
    direction: str = "CE"           # "CE" for bullish, "PE" for bearish
    
    def validate(self, min_hl_gap: float = 2.0) -> bool:
        """
        Validate N-Structure pattern.
        
        For CE (bullish):
        - HL2 > HL1 (Higher Low confirmed)
        - Gap between HL1 and HL2 > threshold
        
        For PE (bearish):
        - LH2 < LH1 (Lower High confirmed)  
        - Gap between LH1 and LH2 > threshold
        """
        if self.direction == "CE":
            # Bullish validation
            if self.higher_low <= self.pullback_low:
                return False
            hl_gap = self.higher_low - self.pullback_low
            if hl_gap < min_hl_gap:
                return False
        else:
            # Bearish validation (PE)
            if self.lower_high >= self.pullback_high:
                return False
            lh_gap = self.pullback_high - self.lower_high
            if lh_gap < min_hl_gap:
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
    direction: str = "CE"  # CE (bullish) or PE (bearish)
    entry_candle_count: int = 0  # v1.4: Track candles since entry for grace period
    highest_price: float = 0.0  # v2.0 Sniper: Track highest price for trailing
    
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
    
    # v6.0 ADX Filter stats
    adx_skipped_trades: int = 0
    
    # Compound system results
    starting_capital: float = 0.0
    final_capital: float = 0.0
    compound_return_pct: float = 0.0


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
        initial_sl_points: float = 5.0,    # v1.8: Tight SL for quick exits
        
        # v1.9 BREAKOUT CONFIRMATION Strategy
        breakout_buffer: float = 5.0,       # Entry = Breakout + 5 points
        confirmation_target: float = 10.0,  # First target = +10 points for TSL activation
        enable_breakout_confirmation: bool = True,  # Enable new strategy
        
        # Target Parameters
        target_points: float = 30.0,        # Profit target (unlimited with TSL)
        
        # Trailing Stop Parameters (Section 6.2 - Structure Based)
        tsl_buffer: float = 2.5,            # Buffer below swing low for TSL (wider = more room)
        use_structure_tsl: bool = True,     # Use HL-based trailing
        
        # Risk Management (Section 9.1)
        max_sl_per_day: int = 1,            # v1.4: Max 1 SL per day (stop trading after 1 SL)
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
        
        # v1.7 Bulletproof Additions
        enable_sniper_mode: bool = False,      # Disabled - no trade limit
        max_trades_per_day: int = 99,          # Unlimited trades
        vix_low_threshold: float = 11.5,       # VIX < 11.5 = No trade
        vix_high_threshold: float = 18.0,      # VIX > 18 = Increase SL
        vix_golden_low: float = 12.0,          # Golden zone start
        vix_golden_high: float = 17.0,         # Golden zone end
        
        # v3.0 WIN RATE IMPROVEMENT OPTIONS
        # Option B: Pullback Entry - wait for price to return to breakout level
        enable_pullback_entry: bool = True,     # Wait for pullback instead of immediate entry
        pullback_buffer: float = 2.0,           # Entry when price within 2pt of breakout level
        max_pullback_candles: int = 10,         # Max candles to wait for pullback
        
        # Option C: Stronger Momentum Filter
        min_body_ratio: float = 0.65,           # Body must be 65% of range (was 50%)
        
        # Option E: Confirmation Candle
        enable_confirmation_candle: bool = True, # Wait for next candle to confirm direction
        confirmation_candle_count: int = 1,      # Number of confirmation candles needed
        
        # v5.0 SL REDUCTION FILTERS
        no_new_trades_after: str = "1230",       # No new trades after 12:30 PM (HHMM format)
        ce_only_mode: bool = False,              # Skip all PE trades (100% PE losses!)
        require_ema_trend: bool = False,         # Require EMA9>EMA15 for CE, EMA9<EMA15 for PE
        
        # v6.0 SIDEWAYS MARKET PROTECTION (ADX Filter)
        enable_adx_filter: bool = False,         # Skip trades in sideways market
        adx_period: int = 14,                    # ADX calculation period
        adx_trending_threshold: float = 22.0,    # ADX > 22 = trending, allow trades
        adx_sideways_threshold: float = 15.0,    # ADX < 15 = sideways, skip trades
        
        # ===== COMPOUND SYSTEM =====
        enable_compound: bool = False,          # Enable auto-compounding
        compound_risk_pct: float = 2.0,         # Risk % per trade for compounding
        min_lots: int = 1,                      # Minimum lots to trade
        max_lots: int = 20,                     # Maximum lots to cap risk
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
        self.max_sl_per_day = max_sl_per_day  # Use parameter (default 1, set 99 for unlimited)
        self.cooldown_candles = cooldown_candles
        self.divergence_threshold = divergence_threshold
        self.lot_size = lot_size
        self.num_lots = num_lots
        self.fixed_qty = lot_size * num_lots  # 65 × 4 = 260 qty
        
        # v1.9 Breakout Confirmation Strategy
        self.breakout_buffer = breakout_buffer  # Entry = Breakout + 5pt
        self.confirmation_target = confirmation_target  # +10pt for TSL activation
        self.enable_breakout_confirmation = enable_breakout_confirmation
        self.trade_phase = "ENTRY"  # ENTRY -> CONFIRMATION -> TRAILING
        self.breakout_price = None  # Price where breakout happened
        
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
        
        # v1.7 - Sniper Mode & VIX Settings
        self.enable_sniper_mode = enable_sniper_mode
        self.max_trades_per_day = max_trades_per_day
        self.vix_low_threshold = vix_low_threshold
        self.vix_high_threshold = vix_high_threshold
        
        # v3.0 WIN RATE IMPROVEMENT
        # Option B: Pullback Entry
        self.enable_pullback_entry = enable_pullback_entry
        self.pullback_buffer = pullback_buffer
        self.max_pullback_candles = max_pullback_candles
        self.pullback_wait_candles = 0  # Counter for pullback waiting
        self.breakout_level = 0.0  # Breakout level to pullback to
        
        # Option C: Stronger Momentum
        self.min_body_ratio = min_body_ratio  # 65% instead of 50%
        
        # Option E: Confirmation Candle
        self.enable_confirmation_candle = enable_confirmation_candle
        self.confirmation_candle_count = confirmation_candle_count
        self.confirmation_candles_seen = 0  # Counter for confirmation
        self.awaiting_confirmation = False  # Flag for confirmation wait
        
        # v5.0 SL REDUCTION FILTERS
        self.no_new_trades_after = no_new_trades_after  # "1230" format
        self.ce_only_mode = ce_only_mode
        self.require_ema_trend = require_ema_trend
        
        # v6.0 SIDEWAYS MARKET PROTECTION (ADX Filter)
        self.enable_adx_filter = enable_adx_filter
        self.adx_period = adx_period
        self.adx_trending_threshold = adx_trending_threshold
        self.adx_sideways_threshold = adx_sideways_threshold
        self.current_adx = 0.0
        self.adx_tr_history: deque = deque(maxlen=adx_period)
        self.adx_plus_dm_history: deque = deque(maxlen=adx_period)
        self.adx_minus_dm_history: deque = deque(maxlen=adx_period)
        self.adx_dx_history: deque = deque(maxlen=adx_period)
        self.adx_skipped_trades = 0  # Track how many trades skipped due to sideways
        
        # ===== COMPOUND SYSTEM =====
        self.enable_compound = enable_compound
        self.compound_risk_pct = compound_risk_pct
        self.min_lots = min_lots
        self.max_lots = max_lots
        self.current_capital = capital  # Running capital (updates after each trade)
        self.starting_capital = capital  # Original capital for comparison
        self.vix_golden_low = vix_golden_low
        self.vix_golden_high = vix_golden_high
        self.daily_trade_count = 0  # Track trades per day
        self.daily_first_trade_result = None  # "profit", "loss", "breakeven"
        
        # State (Section 3.3)
        self.state = TradingState.IDLE
        self.current_trade: Optional[Trade] = None
        self.n_structure = NStructure()
        
        # EMAs (Section 4.1)
        self.index_emas = EMASet(periods=[9, 15, 50])
        self.ema9_history: deque = deque(maxlen=3)
        
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
        self.reentries_today: int = 0  # Max 0 re-entries per day (DISABLED)
        self.max_reentries_per_day: int = 0  # v1.4: Disabled - re-entries hitting SL too often
        
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
        self.index_emas = EMASet(periods=[9, 15, 50])
        self.ema9_history.clear()
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
        # v3.0 Pullback Entry variables
        self.pullback_wait_candles = 0
        self.breakout_level = 0.0
        self.pending_option_price = 0.0
        self.pending_direction = ""
        self.pending_timestamp = None
        # v3.0 Confirmation Candle variables
        self.confirmation_candles_seen = 0
        self.awaiting_confirmation = False
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
        """Check if within optimal trading hours (uses no_new_trades_after setting)."""
        t = ts.time()
        # Parse cutoff time from "HHMM" format
        cutoff_hour = int(self.no_new_trades_after[:2])
        cutoff_min = int(self.no_new_trades_after[2:])
        cutoff_time = time(cutoff_hour, cutoff_min)
        
        # For new entries - 9:30 AM to cutoff time (default 12:30)
        if self.state != TradingState.ACTIVE:
            return time(9, 30) <= t <= cutoff_time
        # For managing active trades - allow till 3:10 PM (square off)
        return time(9, 30) <= t <= time(15, 10)

    def _is_ema9_slope_up(self) -> bool:
        """Check if EMA9 slope is rising over last 3 values."""
        if len(self.ema9_history) < 3:
            return False
        a, b, c = self.ema9_history[0], self.ema9_history[1], self.ema9_history[2]
        return a < b < c
    
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
            # v1.7: Reset Sniper Mode counters
            self.daily_trade_count = 0
            self.daily_first_trade_result = None
            # v1.9: Reset trade phase
            self.trade_phase = "ENTRY"
            self.breakout_price = None
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
        v2.0 Sniper Logic with Unlimited Trailing
        ------------------------------------------
        
        FIXED FROM ORIGINAL:
        Problem 1: Entry buffer 5pt too high → Fixed via entry_buffer=2.5
        Problem 2: SL to cost at +5pt causes whipsaw → Now +7pt with +1pt lock
        Problem 3: Trail buffer too tight → Now High - 5pt
        
        PHASES:
        1. RISK PHASE: Keep initial SL (Entry - 5pt)
        2. SAFE MODE (+7pt): SL = Entry + 1 (lock 1pt profit, avoid whipsaw)
        3. TRAIL MODE (+10pt): TSL = Highest - 5pt buffer (unlimited upside!)
        
        SL শুধু ওপরেই উঠবে (Never move SL down)
        """
        if not self.current_trade:
            return
        
        entry = self.current_trade.entry_price
        current_sl = self.current_trade.current_sl
        
        # ========================================
        # TRACK HIGHEST PRICE (v2.0 Sniper)
        # ========================================
        if self.current_trade.highest_price == 0:
            self.current_trade.highest_price = entry  # Initialize
        
        if current_price > self.current_trade.highest_price:
            self.current_trade.highest_price = current_price
        
        highest_price = self.current_trade.highest_price
        
        # Current profit from entry
        current_profit = current_price - entry
        # Max profit run (from highest point)
        max_profit_run = highest_price - entry
        
        # ========================================
        # PHASE 1: SAFE MODE (+7pt = Risk Free)
        # ========================================
        # শর্ত: যদি প্রাইস ৭ পয়েন্ট ওপরে যায় (আগে ছিল ৫)
        # SL কস্টে আনব না, Entry + 1 এ রাখব (১ পয়েন্ট প্রফিট লক)
        if max_profit_run >= 7.0:
            # SL কস্টে আনব, কিন্তু যদি বর্তমান SL কস্টের নিচে থাকে তবেই
            if current_sl < entry:
                new_sl = entry + 1.0  # ১ পয়েন্ট প্রফিটে লক (ব্রোকারেজ সেভ)
                self.current_trade.current_sl = new_sl
                logger.debug(f"🛡️ SAFE MODE: Price +7pt moved. SL to BE+1: {new_sl:.2f}")
                return  # This candle done
        
        # ========================================
        # PHASE 2: UNLIMITED TRAILING (+10pt)
        # ========================================
        # শর্ত: যদি প্রাইস ১০ পয়েন্ট টার্গেট হিট করে
        if max_profit_run >= 10.0:
            # ট্রেইলিং বাফার: হাই প্রাইস থেকে ৫ পয়েন্ট নিচে
            trailing_buffer = 5.0
            new_trailing_sl = highest_price - trailing_buffer
            
            # SL শুধু ওপরেই উঠবে (Never move SL down)
            if new_trailing_sl > current_sl:
                self.current_trade.current_sl = new_trailing_sl
                locked_profit = new_trailing_sl - entry
                logger.debug(f"🚀 TRAIL MODE: High {highest_price:.2f} → TSL {new_trailing_sl:.2f} (Locked: +{locked_profit:.1f}pt)")
                return
        
        # ========================================
        # FALLBACK: Structure-based TSL (optional)
        # ========================================
        # Only if we have confirmed HLs and not using pure Sniper trail
        if self.use_structure_tsl and max_profit_run >= 5.0 and max_profit_run < 10.0:
            # Between +5 and +10, use structure if available
            if len(self.trade_swing_lows) >= 2:
                recent_hl = self.trade_swing_lows[-2]
                structure_sl = recent_hl - self.tsl_buffer - 1.0
                if structure_sl > current_sl + 1.0:
                    self.current_trade.current_sl = structure_sl
                    logger.debug(f"  → Structure TSL: {structure_sl:.2f}")
        
        # v1.3 ATR-based trailing (alternative, lower priority)
        if self.enable_atr_tsl and self.atr_calculator and max_profit_run >= 10.0:
            atr_sl = self.atr_calculator.get_trailing_sl(
                current_price=current_price,
                entry_price=entry,
                current_sl=current_sl,
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
        
        v1.9 BREAKOUT CONFIRMATION MODE:
        - Phase 1 (CONFIRMATION): Wait for +10pt target, SL at entry
        - Phase 2 (TRAILING): After +10pt, TSL activates, unlimited profit!
        
        CE: profit = exit - entry (price goes up = profit)
        PE: profit = entry - exit (price goes down = profit)
        
        Returns: (should_exit, reason, exit_price)
        """
        if not self.current_trade:
            return False, "", 0.0
        
        entry = self.current_trade.entry_price
        sl = self.current_trade.current_sl
        direction = self.current_trade.direction
        current_price = candle.close
        
        # v1.4: Increment candle count for grace period tracking
        if self.current_trade:
            self.current_trade.entry_candle_count += 1
        
        # ===========================================
        # v1.9: CONFIRMATION PHASE MANAGEMENT
        # ===========================================
        if self.enable_breakout_confirmation and self.trade_phase == "CONFIRMATION":
            # Calculate current profit
            current_profit = current_price - entry  # We buy options, so up = profit
            
            # Check if +10pt confirmation target reached
            if current_profit >= self.confirmation_target:
                # TARGET HIT! Move to TRAILING phase
                self.trade_phase = "TRAILING"
                # Move SL to entry (breakeven)
                self.current_trade.current_sl = entry
                sl = entry
                logger.info(f"🎯 v1.9 CONFIRMATION HIT! +{current_profit:.1f}pt | SL moved to entry (BE) | TSL ACTIVE!")
            else:
                # Still in CONFIRMATION phase - SL is tighter
                # Exit if price drops below entry (original SL)
                if candle.low <= self.current_trade.initial_sl:
                    exit_price = self.current_trade.initial_sl
                    return True, "SL Hit (Confirmation Failed)", exit_price
                
                # Don't activate TSL yet - wait for confirmation
                # Check EOD
                if ts.time() >= time(15, 10):
                    return True, "EOD Exit", candle.close
                
                return False, "", 0.0
        
        # ===========================================
        # TRAILING PHASE or NORMAL MODE - Check SL/TSL
        # ===========================================
        if candle.low <= sl:
            # v1.4: GRACE PERIOD - skip SL check for first 5 candles (avoid whipsaw)
            if self.current_trade and self.current_trade.entry_candle_count <= 5:
                logger.debug(f"  → Grace period active (candle {self.current_trade.entry_candle_count}/5): SL wick ignored at {candle.low:.2f}")
                return False, "", 0.0
            
            exit_price = sl
            
            # We BUY options (both CE and PE), so profit = exit - entry
            # When option price drops to SL, it's always a loss
            profit = exit_price - entry
            
            if sl > self.current_trade.initial_sl:
                # Trailing SL was triggered - this is a PROFIT exit
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
        
        # Check EOD Exit (3:10 PM - square off)
        if ts.time() >= time(15, 10):
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
        IDLE State: Looking for Breakout (CE) or Breakdown (PE).
        
        CE Setup (Bullish):
        - In uptrend (EMA9 > EMA15)
        - Price breaks above recent swing high
        
        PE Setup (Bearish):
        - In downtrend (EMA9 < EMA15)
        - Price breaks below recent swing low
        """
        # v6.0: SIDEWAYS MARKET PROTECTION - Skip trades if ADX is low
        if self._is_sideways_market():
            self.adx_skipped_trades += 1
            logger.info(f"📊 ADX SKIP | ADX={self.current_adx:.1f} < {self.adx_sideways_threshold} | Sideways market - skipping setup")
            return False  # Don't look for setups in sideways market
        
        # Check for CE (Bullish) setup - Uptrend
        if self._is_uptrend(ema9, ema15) and len(self.swing_highs) >= 1:
            recent_high = self.swing_highs[-1].price
            if candle.close > recent_high:
                # v5.0: EMA trend confirmation for CE
                if self.require_ema_trend and not (ema9 > ema15):
                    return False  # Skip if EMA trend not confirming
                    
                self.n_structure = NStructure()  # Reset
                self.n_structure.direction = "CE"
                self.n_structure.breakout_high = recent_high
                self._transition_state(TradingState.SETUP, f"CE Breakout above {recent_high:.2f}")
                self.setups_detected += 1
                return True
        
        # v5.0: Skip PE trades if ce_only_mode enabled (100% of losses are PE!)
        if self.ce_only_mode:
            return False
        
        # Check for PE (Bearish) setup - Downtrend
        if self._is_downtrend(ema9, ema15) and len(self.swing_lows) >= 1:
            recent_low = self.swing_lows[-1].price
            if candle.close < recent_low:
                # v5.0: EMA trend confirmation for PE
                if self.require_ema_trend and not (ema9 < ema15):
                    return False  # Skip if EMA trend not confirming
                    
                self.n_structure = NStructure()  # Reset
                self.n_structure.direction = "PE"
                self.n_structure.breakdown_low = recent_low
                self._transition_state(TradingState.SETUP, f"PE Breakdown below {recent_low:.2f}")
                self.setups_detected += 1
                return True
        
        return False
    
    def _is_downtrend(self, ema9: float, ema15: float) -> bool:
        """Check if in downtrend (EMA9 < EMA15)."""
        return ema9 < ema15
    
    def _update_adx(self, candle: HistoricalCandle):
        """
        v6.0: Update ADX calculation for sideways market detection.
        ADX < 18 = Sideways (skip trades)
        ADX > 22 = Trending (allow trades)
        """
        if not self.enable_adx_filter:
            return
            
        if len(self.index_candles) < 2:
            return
        
        prev_candle = self.index_candles[-2]
        high = candle.high
        low = candle.low
        close = candle.close
        prev_high = prev_candle.high
        prev_low = prev_candle.low
        prev_close = prev_candle.close
        
        # Calculate True Range
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        self.adx_tr_history.append(tr)
        
        # Calculate +DM and -DM
        plus_dm = max(0, high - prev_high) if high - prev_high > prev_low - low else 0
        minus_dm = max(0, prev_low - low) if prev_low - low > high - prev_high else 0
        
        self.adx_plus_dm_history.append(plus_dm)
        self.adx_minus_dm_history.append(minus_dm)
        
        # Calculate ADX when we have enough data
        if len(self.adx_tr_history) >= self.adx_period:
            atr = sum(self.adx_tr_history) / self.adx_period
            if atr > 0:
                plus_di = (sum(self.adx_plus_dm_history) / self.adx_period) / atr * 100
                minus_di = (sum(self.adx_minus_dm_history) / self.adx_period) / atr * 100
                
                di_sum = plus_di + minus_di
                if di_sum > 0:
                    dx = abs(plus_di - minus_di) / di_sum * 100
                    self.adx_dx_history.append(dx)
                    
                    if len(self.adx_dx_history) >= self.adx_period:
                        self.current_adx = sum(self.adx_dx_history) / len(self.adx_dx_history)
    
    def _is_sideways_market(self) -> bool:
        """
        v6.0: Check if market is sideways based on ADX.
        Returns True if ADX < sideways_threshold (skip trade)
        """
        if not self.enable_adx_filter:
            return False
        
        # Need enough data for ADX
        if self.current_adx == 0:
            return False
        
        # Sideways if ADX is below threshold
        return self.current_adx < self.adx_sideways_threshold

    def _process_setup_state(
        self,
        candle: HistoricalCandle,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        SETUP State: Monitoring for Pullback/Bounce to EMA.
        
        CE (Bullish): Wait for pullback to EMA zone (low touches EMA)
        PE (Bearish): Wait for bounce to EMA zone (high touches EMA)
        
        Transition to VALIDATION when pullback/bounce detected.
        """
        direction = self.n_structure.direction
        ema_zone = min(ema9, ema15) if direction == "CE" else max(ema9, ema15)
        
        # Check for invalidation based on direction
        if direction == "CE":
            # CE: Close below EMA invalidates bullish setup
            if candle.close < ema15:
                self._transition_state(TradingState.IDLE, "CE: Close below EMA - Setup invalid")
                self.n_structure = NStructure()
                return False
            
            # Check for pullback to EMA zone (within 0.3% of EMA)
            distance_pct = abs(candle.low - ema_zone) / ema_zone
            if distance_pct < 0.003 or candle.low <= ema_zone:
                self._transition_state(TradingState.VALIDATION, "CE: Pullback to EMA detected")
                return True
        else:
            # PE: Close above EMA invalidates bearish setup
            if candle.close > ema15:
                self._transition_state(TradingState.IDLE, "PE: Close above EMA - Setup invalid")
                self.n_structure = NStructure()
                return False
            
            # Check for bounce to EMA zone (within 0.3% of EMA)
            distance_pct = abs(candle.high - ema_zone) / ema_zone
            if distance_pct < 0.003 or candle.high >= ema_zone:
                self._transition_state(TradingState.VALIDATION, "PE: Bounce to EMA detected")
                return True
        
        return False
    
    def _process_validation_state(
        self,
        candle: HistoricalCandle,
        ema9: float,
        ema15: float
    ) -> bool:
        """
        VALIDATION State: Waiting for HL/LH confirmation.
        
        CE: Wait for Higher Low (HL2 > HL1) → bullish continuation
        PE: Wait for Lower High (LH2 < LH1) → bearish continuation
        """
        direction = self.n_structure.direction
        
        # Check for invalidation based on direction
        if direction == "CE":
            if candle.close < ema15:
                self._transition_state(TradingState.IDLE, "CE: Close below EMA - Pattern invalid")
                self.n_structure = NStructure()
                return False
            
            # Check for Higher Low (CE)
            if self._check_higher_low_pattern():
                self.n_structure.entry_trigger = self.n_structure.breakout_high + self.entry_buffer
                self.n_structure.formation_time = candle.timestamp
                self._transition_state(TradingState.READY, f"CE: HL confirmed, trigger: {self.n_structure.entry_trigger:.2f}")
                return True
        else:
            if candle.close > ema15:
                self._transition_state(TradingState.IDLE, "PE: Close above EMA - Pattern invalid")
                self.n_structure = NStructure()
                return False
            
            # Check for Lower High (PE)
            if self._check_lower_high_pattern():
                self.n_structure.entry_trigger = self.n_structure.breakdown_low - self.entry_buffer
                self.n_structure.formation_time = candle.timestamp
                self._transition_state(TradingState.READY, f"PE: LH confirmed, trigger: {self.n_structure.entry_trigger:.2f}")
                return True
        
        return False
    
    def _check_lower_high_pattern(self) -> bool:
        """Check for Lower High pattern (PE setup) - LH2 < LH1 with gap."""
        if len(self.swing_highs) < 2:
            return False
        
        recent_highs = sorted(self.swing_highs, key=lambda x: x.timestamp, reverse=True)[:2]
        
        lh1 = recent_highs[1].price  # First lower high (bounce)
        lh2 = recent_highs[0].price  # Second lower high (confirmation)
        
        self.n_structure.pullback_high = lh1
        self.n_structure.lower_high = lh2
        
        # LH2 must be below LH1 with sufficient gap (>0.5 point)
        gap = lh1 - lh2
        return gap >= self.min_hl_gap
    
    def _process_ready_state(
        self,
        candle: HistoricalCandle,
        option_price: float,
        ts: datetime
    ) -> bool:
        """
        READY State: N-Structure confirmed, waiting for entry trigger.
        
        CE: Entry on breakout above trigger (breakout_high + buffer)
        PE: Entry on breakdown below trigger (breakdown_low - buffer)
        """
        trigger = self.n_structure.entry_trigger
        direction = self.n_structure.direction
        
        # Check for entry trigger based on direction
        trigger_hit = False
        if direction == "CE":
            trigger_hit = candle.high >= trigger
        else:  # PE
            trigger_hit = candle.low <= trigger
        
        if trigger_hit:
            # ===========================================
            # FILTER 1: Strong Momentum Candle (v3.0: body > 65%)
            # Option C - Stronger filter for better win rate
            # ===========================================
            candle_body = candle.close - candle.open
            candle_range = candle.high - candle.low
            
            if direction == "CE":
                if candle_body <= 0:  # Must be bullish
                    return False
            else:  # PE
                if candle_body >= 0:  # Must be bearish
                    return False
                candle_body = abs(candle_body)  # Use absolute for PE
            
            # v3.0 Option C: Stronger momentum (65% body instead of 50%)
            if candle_range > 0 and (candle_body / candle_range) < self.min_body_ratio:
                self.filter_rejections += 1
                logger.debug(f"Entry blocked: Body ratio {candle_body/candle_range:.1%} < {self.min_body_ratio:.0%}")
                return False  # Reject weak candles - need 65%+ body
            
            # ===========================================
            # FILTER 2: Minimum candle size (avoid tiny moves)
            # ===========================================
            if candle_range < 3.0:  # Min 3 points range on index
                return False
            
            # ===========================================
            # FILTER 3: Close momentum check + Wick rejection (v1.6)
            # ===========================================
            if direction == "CE":
                upper_wick = candle.high - candle.close
                lower_wick = candle.open - candle.low if candle.close > candle.open else candle.close - candle.low
                # v1.6: CE - reject if upper wick > 15% of range (sellers pressure)
                if upper_wick > candle_range * 0.15:
                    return False
                # Original: Close must be in top 20%
                if upper_wick > candle_range * 0.2:
                    return False
            else:  # PE
                lower_wick = candle.close - candle.low if candle.close < candle.open else candle.open - candle.low
                upper_wick = candle.high - max(candle.open, candle.close)
                # v1.6: PE - reject if lower wick > 15% of range (buyers pressure)
                if lower_wick > candle_range * 0.15:
                    return False
                # Original: Close must be in bottom 30%
                close_from_low = candle.close - candle.low
                if close_from_low > candle_range * 0.3:
                    return False
            
            # ===========================================
            # FILTER 4: v1.7 - Avoid first 60 mins (10:15 AM Rule)
            # 37% losses happened in 10:00-10:30, shifting to 10:15
            # ===========================================
            if ts.time() < time(10, 15):
                return False
            
            # ===========================================
            # FILTER 4.5: v1.7 - Sniper Mode (First Bullet Rule)
            # Only 1 trade per day, stop after profit or loss
            # ===========================================
            if self.enable_sniper_mode:
                if self.daily_trade_count >= self.max_trades_per_day:
                    logger.debug(f"Sniper Mode: Max trades ({self.max_trades_per_day}) reached for day")
                    return False
                # If first trade was profit or loss, stop
                if self.daily_first_trade_result in ["profit", "loss"]:
                    logger.debug(f"Sniper Mode: First trade was {self.daily_first_trade_result}, stopping")
                    return False
            
            # ===========================================
            # FILTER 5: Volume Filter - REMOVED (no volume data in backtest)
            # ===========================================
            # Volume filter removed as index data lacks volume info
            
            # ===========================================
            # FILTER 6: Trend Filter (v1.3) - check EMA alignment
            # ===========================================
            if self.trend_filter:
                ema9 = self.index_emas.get_value(9) or candle.close
                ema15 = self.index_emas.get_value(15) or candle.close
                ema50 = self.index_emas.get_value(50) if hasattr(self.index_emas, 'get_value') else None
                trend_result = self.trend_filter.analyze(candle.close, ema9, ema15)
                if not trend_result.is_favorable:
                    self.filter_rejections += 1
                    logger.debug(f"Entry blocked by trend filter: {trend_result.message}")
                    return False
                
                # v1.6: EMA GAP CHECK - CE needs 3pt, PE needs 2pt
                ema_gap = abs(ema9 - ema15)
                min_gap = 3.0 if direction == "CE" else 2.0  # v1.6: Stricter for CE
                if ema_gap < min_gap:
                    self.filter_rejections += 1
                    logger.debug(f"Entry blocked: {direction} EMA gap {ema_gap:.1f} < {min_gap}pt")
                    return False
                
                # CE-only stricter EMA alignment to avoid weak uptrends
                if direction == "CE":
                    if ema9 <= ema15:
                        self.filter_rejections += 1
                        logger.debug("Entry blocked: CE requires EMA9 > EMA15")
                        return False
                    if ema50 is not None and ema15 <= ema50:
                        self.filter_rejections += 1
                        logger.debug("Entry blocked: CE requires EMA15 > EMA50")
                        return False
                    if not self._is_ema9_slope_up():
                        self.filter_rejections += 1
                        logger.debug("Entry blocked: CE requires EMA9 rising (last 3 values)")
                        return False
                # v1.4: PE EMA50 alignment - avoid counter-trend PE entries
                else:  # PE
                    if ema9 >= ema15:
                        self.filter_rejections += 1
                        logger.debug("Entry blocked: PE requires EMA9 < EMA15")
                        return False
                    if ema50 is not None and ema15 >= ema50:
                        self.filter_rejections += 1
                        logger.debug("Entry blocked: PE requires EMA15 < EMA50")
                        return False
            
            # ===========================================
            # v3.0 OPTION B: PULLBACK ENTRY
            # Instead of immediate entry, wait for price to pull back
            # ===========================================
            if self.enable_pullback_entry:
                # Store breakout info for pullback waiting
                if direction == "CE":
                    self.breakout_level = trigger  # Breakout high level
                else:
                    self.breakout_level = trigger  # Breakdown low level
                
                self.pullback_wait_candles = 0
                self.pending_option_price = option_price  # Store current option price
                self.pending_direction = direction
                self.pending_timestamp = ts
                
                logger.info(f"⏳ v3.0 PULLBACK WAIT | {direction} | Breakout @ {self.breakout_level:.2f} | Waiting for pullback...")
                self._transition_state(TradingState.PULLBACK_WAIT, f"Waiting for pullback to {self.breakout_level:.2f}")
                return False  # Don't enter yet
            
            # ===========================================
            # v3.0 OPTION E: CONFIRMATION CANDLE
            # Wait for next candle to confirm direction
            # ===========================================
            if self.enable_confirmation_candle and not self.enable_pullback_entry:
                self.confirmation_candles_seen = 0
                self.awaiting_confirmation = True
                self.pending_option_price = option_price
                self.pending_direction = direction
                self.pending_timestamp = ts
                
                logger.info(f"⏳ v3.0 CONFIRMATION WAIT | {direction} | Waiting for {self.confirmation_candle_count} confirm candle(s)...")
                self._transition_state(TradingState.CONFIRMATION, f"Waiting for confirmation candle")
                return False  # Don't enter yet
            
            # ===========================================
            # IMMEDIATE ENTRY (if v3.0 options disabled)
            # ===========================================
            return self._execute_entry(option_price, direction, ts)
        
        # Check for pattern invalidation
        if direction == "CE":
            if len(self.swing_lows) >= 2:
                if candle.low < self.n_structure.higher_low - 5:
                    self._transition_state(TradingState.IDLE, "CE: Pattern broken - new lower low")
                    self.n_structure = NStructure()
                    return False
        else:  # PE
            if len(self.swing_highs) >= 2:
                if candle.high > self.n_structure.lower_high + 5:
                    self._transition_state(TradingState.IDLE, "PE: Pattern broken - new higher high")
                    self.n_structure = NStructure()
                    return False
        
        return False
    
    def _execute_entry(self, option_price: float, direction: str, ts: datetime) -> bool:
        """
        Execute the actual trade entry.
        Extracted to be called from READY, PULLBACK_WAIT, or CONFIRMATION states.
        """
        entry_price = option_price
        
        # ATR-based dynamic SL or fixed 5 points
        if self.enable_atr_sl and self.atr_calculator and self.atr_calculator.is_ready:
            atr_sl = self.atr_calculator.get_dynamic_sl(
                entry_price=entry_price,
                min_sl=self.min_sl_points,
                max_sl=self.max_sl_points
            )
            sl_points = entry_price - atr_sl
            initial_sl = atr_sl
            logger.debug(f"  → ATR-based SL: {sl_points:.1f}pt")
        else:
            sl_points = self.initial_sl_points  # Default 5 points
            initial_sl = entry_price - sl_points
        
        # ===== COMPOUND SYSTEM: Calculate quantity based on current capital =====
        if self.enable_compound:
            fixed_qty = self._calculate_compound_quantity(sl_points)
        else:
            fixed_qty = self.fixed_qty  # 260 qty (fixed)
        
        # Clear trade swing lows for fresh TSL tracking
        self.trade_swing_lows = []
        
        # Log EMA/trend/filter context at entry
        ema9_val = self.index_emas.get_value(9)
        ema15_val = self.index_emas.get_value(15)
        ema50_val = self.index_emas.get_value(50) if hasattr(self.index_emas, 'get_value') else None
        trend_str = f"EMA9={ema9_val:.2f}, EMA15={ema15_val:.2f}, EMA50={ema50_val:.2f}" if ema50_val else f"EMA9={ema9_val:.2f}, EMA15={ema15_val:.2f}"
        filter_str = f"VolumeFilter: {'ON' if self.volume_filter else 'OFF'}, TrendFilter: {'ON' if self.trend_filter else 'OFF'}"
        
        # Compound info
        if self.enable_compound:
            compound_str = f" | 💰 Capital: ₹{self.current_capital:,.0f}"
        else:
            compound_str = ""
        logger.info(f"ENTRY CONTEXT | {direction} | {ts} | {trend_str} | {filter_str}{compound_str}")

        self.current_trade = Trade(
            entry_time=ts,
            entry_price=entry_price,
            initial_sl=initial_sl,
            current_sl=initial_sl,
            quantity=fixed_qty,
            n_structure=self.n_structure,
            direction=direction,
            highest_price=entry_price  # v2.0 Sniper: Initialize highest_price
        )
        
        if self.partial_profit_mgr:
            self.partial_profit_mgr.open_position(entry_price, fixed_qty)
        
        self.daily_trades += 1
        self.entries_triggered += 1
        emoji = "📈" if direction == "CE" else "📉"
        num_lots = fixed_qty // self.lot_size
        self._transition_state(TradingState.ACTIVE, f"{direction} Entry @ {entry_price:.2f}")
        logger.info(f"{emoji} {direction} ENTRY @ ₹{entry_price:.2f} | SL: ₹{initial_sl:.2f} ({sl_points:.0f}pt) | Qty: {fixed_qty} ({num_lots} lots) | Risk: ₹{sl_points * fixed_qty:.0f} | {ts}")
        return True
    
    def _calculate_compound_quantity(self, sl_points: float) -> int:
        """
        Calculate position size based on current capital and risk percentage.
        
        Formula:
        - Risk Amount = Current Capital × Risk %
        - Quantity = Risk Amount / SL Points
        - Round down to nearest lot size
        - Cap between min_lots and max_lots
        
        Example:
        - Capital = ₹50,000, Risk = 2%, SL = 5 pts
        - Risk Amount = ₹1,000
        - Quantity = 1000/5 = 200
        - Lots = 200/65 = 3 lots = 195 qty
        """
        # Calculate risk amount
        risk_amount = self.current_capital * (self.compound_risk_pct / 100)
        
        # Calculate raw quantity
        raw_qty = risk_amount / sl_points
        
        # Round to lot size
        num_lots = int(raw_qty / self.lot_size)
        
        # Apply min/max constraints
        num_lots = max(self.min_lots, min(num_lots, self.max_lots))
        
        final_qty = num_lots * self.lot_size
        
        logger.debug(f"💰 COMPOUND: Capital ₹{self.current_capital:,.0f} | Risk {self.compound_risk_pct}% = ₹{risk_amount:.0f} | SL {sl_points:.1f}pt | {num_lots} lots = {final_qty} qty")
        
        return final_qty
    
    def _update_compound_capital(self, pnl: float):
        """Update capital after trade closes."""
        if self.enable_compound:
            old_capital = self.current_capital
            self.current_capital += pnl
            # Don't go below starting capital's 50%
            self.current_capital = max(self.current_capital, self.starting_capital * 0.5)
            change = "📈" if pnl > 0 else "📉"
            logger.info(f"{change} COMPOUND UPDATE | ₹{old_capital:,.0f} → ₹{self.current_capital:,.0f} (PnL: {'+' if pnl > 0 else ''}₹{pnl:,.0f})")
        return True
    
    def _process_pullback_wait_state(
        self,
        candle: HistoricalCandle,
        option_price: float,
        ts: datetime
    ) -> bool:
        """
        v3.0 Option B: PULLBACK_WAIT State
        
        After breakout, wait for price to pull back to the breakout level.
        This gives better entry price and avoids chasing.
        
        CE: Wait for index price to drop back near breakout_level
        PE: Wait for index price to rise back near breakdown_level
        """
        self.pullback_wait_candles += 1
        direction = self.n_structure.direction
        
        # Check for timeout
        if self.pullback_wait_candles > self.max_pullback_candles:
            logger.info(f"⏰ PULLBACK TIMEOUT | {direction} | Waited {self.pullback_wait_candles} candles - cancelling entry")
            self._transition_state(TradingState.COOLDOWN, "Pullback timeout")
            return False
        
        # Check for pullback based on direction
        pullback_hit = False
        if direction == "CE":
            # CE: Wait for price to drop back to breakout level
            # Consider pullback if low is within buffer of breakout level
            if candle.low <= self.breakout_level + self.pullback_buffer:
                pullback_hit = True
                logger.info(f"✅ PULLBACK HIT | CE | Low {candle.low:.2f} near breakout {self.breakout_level:.2f}")
        else:  # PE
            # PE: Wait for price to rise back to breakdown level
            if candle.high >= self.breakout_level - self.pullback_buffer:
                pullback_hit = True
                logger.info(f"✅ PULLBACK HIT | PE | High {candle.high:.2f} near breakdown {self.breakout_level:.2f}")
        
        if pullback_hit:
            # ===========================================
            # v3.0 OPTION E: CONFIRMATION CANDLE CHECK
            # After pullback, also wait for confirmation candle
            # ===========================================
            if self.enable_confirmation_candle:
                self.confirmation_candles_seen = 0
                self.awaiting_confirmation = True
                self.pending_option_price = option_price
                self.pending_direction = direction
                self.pending_timestamp = ts
                
                logger.info(f"⏳ v3.0 CONFIRMATION WAIT (after pullback) | {direction} | Waiting for {self.confirmation_candle_count} confirm candle(s)...")
                self._transition_state(TradingState.CONFIRMATION, f"Waiting for confirmation after pullback")
                return False
            
            # No confirmation needed - enter now!
            return self._execute_entry(option_price, direction, ts)
        
        # Check for pattern invalidation while waiting
        if direction == "CE":
            # If price goes too low, cancel
            if candle.close < self.breakout_level - 10:  # 10pt below breakout = failed
                logger.info(f"❌ PULLBACK FAILED | CE | Price {candle.close:.2f} fell too far below breakout {self.breakout_level:.2f}")
                self._transition_state(TradingState.COOLDOWN, "Pullback failed - price dropped")
                return False
        else:  # PE
            # If price goes too high, cancel
            if candle.close > self.breakout_level + 10:  # 10pt above breakdown = failed
                logger.info(f"❌ PULLBACK FAILED | PE | Price {candle.close:.2f} rose too far above breakdown {self.breakout_level:.2f}")
                self._transition_state(TradingState.COOLDOWN, "Pullback failed - price rose")
                return False
        
        return False
    
    def _process_confirmation_state(
        self,
        candle: HistoricalCandle,
        option_price: float,
        ts: datetime
    ) -> bool:
        """
        v3.0 Option E: CONFIRMATION State
        
        Wait for confirmation candle(s) after breakout/pullback.
        The next candle(s) must continue in the same direction.
        
        CE: Next candle must be bullish (close > open)
        PE: Next candle must be bearish (close < open)
        """
        self.confirmation_candles_seen += 1
        direction = self.pending_direction
        
        # Check if current candle confirms direction
        is_bullish = candle.close > candle.open
        is_bearish = candle.close < candle.open
        
        confirmed = False
        if direction == "CE" and is_bullish:
            confirmed = True
        elif direction == "PE" and is_bearish:
            confirmed = True
        
        if confirmed:
            if self.confirmation_candles_seen >= self.confirmation_candle_count:
                logger.info(f"✅ CONFIRMATION HIT | {direction} | Candle #{self.confirmation_candles_seen} confirmed - ENTERING!")
                return self._execute_entry(option_price, direction, ts)
            else:
                logger.debug(f"  → Confirmation {self.confirmation_candles_seen}/{self.confirmation_candle_count}")
                return False
        else:
            # Candle didn't confirm - cancel entry
            candle_type = "bearish" if is_bearish else "bullish" if is_bullish else "doji"
            logger.info(f"❌ CONFIRMATION FAILED | {direction} | Expected {'bullish' if direction == 'CE' else 'bearish'}, got {candle_type}")
            self._transition_state(TradingState.COOLDOWN, f"Confirmation failed - wrong candle")
            return False

    def _process_active_state(
        self,
        option_candle: HistoricalCandle,
        prev_option_low: float,
        ts: datetime
    ) -> bool:
        """
        ACTIVE State: Trade is live, managing position.
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
            
            # Calculate PnL based on direction (CE vs PE)
            direction = self.current_trade.direction
            entry_price = self.current_trade.entry_price
            trade_qty = self.current_trade.quantity  # Use trade's actual quantity (for compound)
            
            # v1.3: Calculate PnL including partial exits
            if self.partial_profit_mgr and self.partial_profit_mgr.position:
                # Close remaining position
                remaining_qty, remaining_pnl = self.partial_profit_mgr.close_remaining(exit_price)
                total_pnl = self.partial_profit_mgr.get_total_pnl()
                self.current_trade.pnl = total_pnl
            else:
                # We BUY options (both CE and PE)
                # Profit when option price goes UP, Loss when it goes DOWN
                self.current_trade.pnl = (exit_price - entry_price) * trade_qty
            
            self.trades.append(self.current_trade)
            self.daily_pnl += self.current_trade.pnl
            self.equity_curve.append(self.equity_curve[-1] + self.current_trade.pnl)
            
            # v1.7: Update Sniper Mode tracking
            self.daily_trade_count += 1
            if self.daily_trade_count == 1:  # First trade of the day
                if self.current_trade.pnl > 500:  # Significant profit
                    self.daily_first_trade_result = "profit"
                    logger.info(f"🎯 Sniper Mode: First trade PROFIT - day complete!")
                elif self.current_trade.pnl < -500:  # Significant loss
                    self.daily_first_trade_result = "loss"
                    logger.info(f"🎯 Sniper Mode: First trade LOSS - day complete!")
                else:
                    self.daily_first_trade_result = "breakeven"
                    logger.info(f"🎯 Sniper Mode: First trade BREAKEVEN - may take 2nd trade")
            
            # v1.3: Record with drawdown protection
            if self.drawdown_protection:
                self.drawdown_protection.record_trade(self.current_trade.pnl)
            
            # Log EMA/trend/filter context at exit
            ema9_val = self.index_emas.get_value(9)
            ema15_val = self.index_emas.get_value(15)
            ema50_val = self.index_emas.get_value(50) if hasattr(self.index_emas, 'get_value') else None
            trend_str = f"EMA9={ema9_val:.2f}, EMA15={ema15_val:.2f}, EMA50={ema50_val:.2f}" if ema50_val else f"EMA9={ema9_val:.2f}, EMA15={ema15_val:.2f}"
            filter_status = []
            if self.volume_filter:
                filter_status.append(f"VolumeFilter: {'ON' if self.volume_filter else 'OFF'}")
            if self.trend_filter:
                filter_status.append(f"TrendFilter: {'ON' if self.trend_filter else 'OFF'}")
            if self.volatility_filter:
                filter_status.append(f"VolatilityFilter: {'ON' if self.volatility_filter else 'OFF'}")
            filter_str = ", ".join(filter_status)
            emoji = "📈" if direction == "CE" else "📉"
            pnl_str = f"+₹{self.current_trade.pnl:.0f}" if self.current_trade.pnl > 0 else f"-₹{abs(self.current_trade.pnl):.0f}"
            logger.info(f"{emoji} {direction} EXIT @ ₹{exit_price:.2f} | {reason} | PnL: {pnl_str} | {ts} | {trend_str} | {filter_str}")
            
            # ===== COMPOUND SYSTEM: Update capital =====
            self._update_compound_capital(self.current_trade.pnl)
            
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
        
        # Must check full EMA alignment for re-entry
        ema50 = self.index_emas.get_value(50)
        direction = self.current_trade.direction if self.current_trade else "CE"
        
        if direction == "CE":
            # CE re-entry: need EMA9 > EMA15 > EMA50
            if not self._is_uptrend(ema9, ema15):
                return False
            if ema50 is not None and ema15 <= ema50:
                logger.debug("Re-entry blocked: CE requires EMA15 > EMA50")
                return False
        else:  # PE
            # PE re-entry: need EMA9 < EMA15 < EMA50
            if not self._is_downtrend(ema9, ema15):
                return False
            if ema50 is not None and ema15 >= ema50:
                logger.debug("Re-entry blocked: PE requires EMA15 < EMA50")
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
            if candle_range > 0 and (candle_body / candle_range) < 0.5:
                return False  # Strict: 50%+ body for re-entry
            if candle_range < 3.0:  # Strict: Min 3 points range
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
            
            # Skip pre-market and post-market hours completely
            t = ts.time()
            if t < time(9, 15) or t > time(15, 30):
                continue
            
            # ALWAYS update EMAs and indicators (even before 9:50)
            self.index_emas.update(candle.close)
            ema9 = self.index_emas.get_value(9)
            ema15 = self.index_emas.get_value(15)
            if ema9 is not None:
                self.ema9_history.append(ema9)
            
            if not all([ema9, ema15]):
                continue
            
            # v1.3: Update ATR calculator
            if self.atr_calculator:
                self.atr_calculator.update(candle.high, candle.low, candle.close)
            
            # v1.3: Update volatility filter
            if self.volatility_filter:
                self.volatility_filter.update(candle.high, candle.low, candle.close)
            
            # Update volume filter history
            if self.volume_filter and hasattr(candle, 'volume') and candle.volume:
                self.volume_filter.update(candle.volume)
            
            # Update trend filter history
            if self.trend_filter:
                self.trend_filter.analyze(candle.close, ema9, ema15)
            
            # Store candle and detect swings (always, for indicator warmup)
            self.index_candles.append(candle)
            self._detect_swing_points(candle, idx)
            
            # v6.0: Update ADX for sideways detection
            self._update_adx(candle)
            
            # NOW check trading hours for entry/exit actions
            if not self._is_trading_hours(ts):
                continue
            
            # Kill switch check - but allow exit of active trades
            if self._check_kill_switch() and self.state != TradingState.ACTIVE:
                continue
            
            # v1.3: Check volatility filter for new entries only
            if self.volatility_filter:
                if not self.volatility_filter.is_tradeable_day() and self.state != TradingState.ACTIVE:
                    continue
            
            # Simulate option premium
            # For CE: delta > 0 (index up = premium up)
            # For PE: delta < 0 (index up = premium down, index down = premium up)
            if trade_entry_nifty is not None and trade_entry_premium is not None:
                nifty_change = candle.close - trade_entry_nifty
                trade_delta = delta if self.current_trade.direction == "CE" else -delta
                current_premium = trade_entry_premium + (trade_delta * nifty_change)
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
            
            # v3.0: PULLBACK_WAIT state handler
            elif self.state == TradingState.PULLBACK_WAIT:
                import random
                entry_premium = random.uniform(min_premium, max_premium)
                
                if self._process_pullback_wait_state(candle, entry_premium, ts):
                    # Record entry state for premium tracking
                    trade_entry_nifty = candle.close
                    trade_entry_premium = entry_premium
            
            # v3.0: CONFIRMATION state handler
            elif self.state == TradingState.CONFIRMATION:
                import random
                entry_premium = random.uniform(min_premium, max_premium)
                
                if self._process_confirmation_state(candle, entry_premium, ts):
                    # Record entry state for premium tracking
                    trade_entry_nifty = candle.close
                    trade_entry_premium = entry_premium
                    
            elif self.state == TradingState.ACTIVE:
                # Update premium based on NIFTY movement
                if trade_entry_nifty is not None:
                    # Delta direction: CE +ve, PE -ve
                    trade_delta = delta if self.current_trade.direction == "CE" else -delta
                    
                    # Calculate premium at each OHLC point of the index candle
                    nifty_open_change = candle.open - trade_entry_nifty
                    nifty_high_change = candle.high - trade_entry_nifty
                    nifty_low_change = candle.low - trade_entry_nifty
                    nifty_close_change = candle.close - trade_entry_nifty
                    
                    opt_open = max(trade_entry_premium + (trade_delta * nifty_open_change), 0.5)
                    opt_high = max(trade_entry_premium + (trade_delta * nifty_high_change), 0.5)
                    opt_low = max(trade_entry_premium + (trade_delta * nifty_low_change), 0.5)
                    opt_close = max(trade_entry_premium + (trade_delta * nifty_close_change), 0.5)
                    
                    # For PE: swap high/low because index high = option low
                    if self.current_trade.direction == "PE":
                        opt_high, opt_low = opt_low, opt_high
                    
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
                    prev_opt_low = prev_option_premium - abs(candle.low - candle.close) * delta
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
                trade_delta = delta if self.current_trade.direction == "CE" else -delta
                final_premium = trade_entry_premium + (trade_delta * (last.close - trade_entry_nifty))
                final_premium = max(final_premium, 1.0)
                self.current_trade.exit_time = last.timestamp
                self.current_trade.exit_price = final_premium
                self.current_trade.exit_reason = "Backtest End"
                
                # We BUY options - profit when price rises (use trade's actual qty)
                self.current_trade.pnl = (final_premium - self.current_trade.entry_price) * self.current_trade.quantity
                
                # Update compound capital
                self._update_compound_capital(self.current_trade.pnl)
                
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
        result.adx_skipped_trades = self.adx_skipped_trades  # v6.0
        
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
        
        # ===== COMPOUND SYSTEM RESULTS =====
        result.starting_capital = self.starting_capital
        result.final_capital = self.current_capital
        if self.enable_compound and self.starting_capital > 0:
            result.compound_return_pct = ((self.current_capital - self.starting_capital) / self.starting_capital) * 100
        
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
    
    # CE/PE breakdown
    ce_trades = [t for t in result.trades if t.direction == "CE"]
    pe_trades = [t for t in result.trades if t.direction == "PE"]
    print(f"   CE Trades:         {len(ce_trades)} ({sum(1 for t in ce_trades if t.pnl > 0)} wins)")
    print(f"   PE Trades:         {len(pe_trades)} ({sum(1 for t in pe_trades if t.pnl > 0)} wins)")
    
    print(f"\n📈 Setup Statistics:")
    print(f"   Setups Detected:   {result.setups_detected}")
    print(f"   Entries Triggered: {result.entries_triggered}")
    print(f"   Filter Rejections: {result.filter_rejections}")  # v1.3
    if result.adx_skipped_trades > 0:
        print(f"   ADX Skips (v6.0):  {result.adx_skipped_trades} setups skipped (sideways market)")
    
    print(f"\n💰 P&L Analysis:")
    pnl_str = f"+₹{result.total_pnl:,.0f}" if result.total_pnl > 0 else f"-₹{abs(result.total_pnl):,.0f}"
    print(f"   Total P&L:         {pnl_str}")
    ce_pnl = sum(t.pnl for t in ce_trades)
    pe_pnl = sum(t.pnl for t in pe_trades)
    ce_pnl_str = f"+₹{ce_pnl:,.0f}" if ce_pnl > 0 else f"-₹{abs(ce_pnl):,.0f}"
    pe_pnl_str = f"+₹{pe_pnl:,.0f}" if pe_pnl > 0 else f"-₹{abs(pe_pnl):,.0f}"
    print(f"   CE P&L:            {ce_pnl_str}")
    print(f"   PE P&L:            {pe_pnl_str}")
    print(f"   Avg Win:           +₹{result.avg_win:,.0f}")
    print(f"   Avg Loss:          -₹{result.avg_loss:,.0f}")
    print(f"   Profit Factor:     {result.profit_factor:.2f}")
    print(f"   Max Drawdown:      -₹{result.max_drawdown:,.0f}")
    
    # ===== COMPOUND SYSTEM RESULTS =====
    if result.starting_capital > 0 and result.final_capital > 0:
        print(f"\n💰 Compound Growth:")
        print(f"   Starting Capital:  ₹{result.starting_capital:,.0f}")
        print(f"   Final Capital:     ₹{result.final_capital:,.0f}")
        growth = result.final_capital - result.starting_capital
        growth_str = f"+₹{growth:,.0f}" if growth > 0 else f"-₹{abs(growth):,.0f}"
        print(f"   Absolute Growth:   {growth_str}")
        print(f"   Return:            {result.compound_return_pct:+.1f}%")
    
    if result.trades:
        print(f"\n📝 Trade Log (Last 20):")
        print("-" * 75)
        for i, t in enumerate(result.trades[-20:], 1):
            pnl = f"+₹{t.pnl:.0f}" if t.pnl > 0 else f"-₹{abs(t.pnl):.0f}"
            emoji = "📈" if t.direction == "CE" else "📉"
            print(f"   {i:2}. {emoji} {t.direction} | {t.entry_time.strftime('%d-%b %H:%M')} | "
                  f"Entry: ₹{t.entry_price:.1f} | "
                  f"Exit: ₹{t.exit_price:.1f} | "
                  f"{t.exit_reason:10} | {pnl}")
    
    print("\n" + "=" * 65)
