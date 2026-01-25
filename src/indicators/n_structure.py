"""
N-Structure Pattern Detection Module

Implements the core N-Structure Momentum Breakout pattern detection:
1. Setup Validator - Checks EMA support and pullback conditions
2. Structure Scanner - Detects Higher Low (HL) patterns
3. Divergence Filter - Compares Index vs Option momentum

The "N" shape represents:
- Point 1: Previous High (Breakout level)
- Point 2: Higher Low 1 (First pullback)
- Point 3: New High (Momentum continuation)
- Point 4: Higher Low 2 (Current pullback - entry zone)
"""

from datetime import datetime
from typing import Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

from loguru import logger

from data.candle_builder import Candle
from data.synchronizer import SyncedCandlePair


class SetupStatus(Enum):
    """Status of N-Structure setup."""
    NO_SETUP = "no_setup"
    WATCHING_BREAKOUT = "watching_breakout"
    TRACKING_PULLBACK = "tracking_pullback"
    HL_FORMING = "hl_forming"
    STRUCTURE_VALID = "structure_valid"
    DIVERGENCE_CONFIRMED = "divergence_confirmed"
    READY_FOR_ENTRY = "ready_for_entry"
    INVALIDATED = "invalidated"


@dataclass
class HigherLow:
    """Container for a Higher Low point."""
    price: float
    timestamp: datetime
    candle_index: int
    
    def __str__(self) -> str:
        return f"HL @ {self.price:.2f} ({self.timestamp.strftime('%H:%M')})"


@dataclass
class NStructure:
    """
    Complete N-Structure pattern data.
    
    Captures all points of the N-shape for analysis and logging.
    """
    # The breakout high that started the setup
    breakout_high: float
    breakout_time: datetime
    
    # First Higher Low (after breakout)
    hl1: Optional[HigherLow] = None
    
    # Second Higher Low (current pullback)
    hl2: Optional[HigherLow] = None
    
    # Recent high before current pullback
    recent_high: Optional[float] = None
    recent_high_time: Optional[datetime] = None
    
    # Divergence data
    divergence_confirmed: bool = False
    index_roc: Optional[float] = None
    option_roc: Optional[float] = None
    
    # Entry level
    entry_trigger: Optional[float] = None  # High + buffer
    
    # Status
    status: SetupStatus = SetupStatus.WATCHING_BREAKOUT
    
    @property
    def is_valid(self) -> bool:
        """Check if structure has valid HL pattern."""
        if not self.hl1 or not self.hl2:
            return False
        return self.hl2.price > self.hl1.price
    
    @property
    def hl_gap(self) -> Optional[float]:
        """Get point difference between HLs."""
        if not self.hl1 or not self.hl2:
            return None
        return self.hl2.price - self.hl1.price
    
    def __str__(self) -> str:
        parts = [f"N-Structure [{self.status.value}]"]
        parts.append(f"Breakout: {self.breakout_high:.2f}")
        if self.hl1:
            parts.append(f"HL1: {self.hl1.price:.2f}")
        if self.hl2:
            parts.append(f"HL2: {self.hl2.price:.2f}")
        if self.entry_trigger:
            parts.append(f"Entry: {self.entry_trigger:.2f}")
        return " | ".join(parts)


class SetupValidator:
    """
    Validates setup conditions for N-Structure.
    
    Checks:
    - Price has broken previous resistance (new high)
    - Price is pulling back to EMA support (9 or 15)
    - Close remains above the slow EMA (not breaking down)
    """
    
    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 15,
        ema_touch_tolerance: float = 0.3  # Percentage
    ):
        """
        Initialize setup validator.
        
        Args:
            ema_fast: Fast EMA period
            ema_slow: Slow EMA period
            ema_touch_tolerance: Percent tolerance for "touching" EMA
        """
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_touch_tolerance = ema_touch_tolerance
        
    def is_breakout(
        self,
        current_high: float,
        previous_high: float
    ) -> bool:
        """
        Check if current candle breaks previous high.
        
        Args:
            current_high: Current candle high
            previous_high: Previous resistance level
            
        Returns:
            True if breakout occurred
        """
        return current_high > previous_high
    
    def is_pullback_to_ema(
        self,
        candle_low: float,
        ema_fast_value: float,
        ema_slow_value: float
    ) -> Tuple[bool, str]:
        """
        Check if price is pulling back to EMA support.
        
        Args:
            candle_low: Current candle low
            ema_fast_value: EMA(9) value
            ema_slow_value: EMA(15) value
            
        Returns:
            Tuple of (is_pullback, ema_touched)
        """
        # Calculate tolerance bands
        fast_tolerance = ema_fast_value * (self.ema_touch_tolerance / 100)
        slow_tolerance = ema_slow_value * (self.ema_touch_tolerance / 100)
        
        # Check if low touches/pierces fast EMA
        touches_fast = candle_low <= (ema_fast_value + fast_tolerance)
        
        # Check if low touches/pierces slow EMA
        touches_slow = candle_low <= (ema_slow_value + slow_tolerance)
        
        if touches_fast:
            return True, "EMA9"
        elif touches_slow:
            return True, "EMA15"
        
        return False, ""
    
    def is_close_above_ema(
        self,
        candle_close: float,
        ema_slow_value: float
    ) -> bool:
        """
        Check if candle closes above slow EMA (not breaking down).
        
        Args:
            candle_close: Candle close price
            ema_slow_value: EMA(15) value
            
        Returns:
            True if close is above EMA
        """
        return candle_close > ema_slow_value
    
    def validate_setup(
        self,
        candle: Candle,
        previous_high: float,
        ema_fast_value: float,
        ema_slow_value: float
    ) -> Tuple[bool, SetupStatus, str]:
        """
        Full setup validation.
        
        Args:
            candle: Current candle
            previous_high: Previous high level
            ema_fast_value: EMA(9) value
            ema_slow_value: EMA(15) value
            
        Returns:
            Tuple of (is_valid, status, reason)
        """
        # Check for breakout
        if candle.high > previous_high:
            return True, SetupStatus.WATCHING_BREAKOUT, "New high breakout"
        
        # Check for pullback
        is_pullback, ema_touched = self.is_pullback_to_ema(
            candle.low, ema_fast_value, ema_slow_value
        )
        
        if is_pullback:
            # Verify close is above slow EMA
            if self.is_close_above_ema(candle.close, ema_slow_value):
                return True, SetupStatus.TRACKING_PULLBACK, f"Pullback to {ema_touched}"
            else:
                return False, SetupStatus.INVALIDATED, "Close below EMA15 - setup killed"
        
        return True, SetupStatus.WATCHING_BREAKOUT, "Monitoring"


class StructureScanner:
    """
    Scans for Higher Low (HL) patterns.
    
    The N-Structure requires:
    - HL1: First higher low after breakout
    - HL2: Second higher low (must be > HL1)
    - Minimum gap between HLs (time and price)
    """
    
    def __init__(
        self,
        min_hl_gap_candles: int = 5,
        min_hl_gap_points: float = 2.0
    ):
        """
        Initialize structure scanner.
        
        Args:
            min_hl_gap_candles: Minimum candles between HL1 and HL2
            min_hl_gap_points: Minimum point difference for valid HL
        """
        self.min_hl_gap_candles = min_hl_gap_candles
        self.min_hl_gap_points = min_hl_gap_points
        
        # Track swing lows - use deque for O(1) append and automatic size limiting
        self._candle_count = 0
        self._recent_lows: deque = deque(maxlen=20)
        self._hl1: Optional[HigherLow] = None
        self._hl2: Optional[HigherLow] = None
        
    def process_candle(
        self,
        candle: Candle,
        is_pullback: bool
    ) -> Optional[HigherLow]:
        """
        Process a candle for HL detection.
        
        Args:
            candle: Current candle
            is_pullback: Whether we're in pullback phase
            
        Returns:
            HigherLow if detected, None otherwise
        """
        self._candle_count += 1
        
        # Only track lows during pullback
        if not is_pullback:
            return None
        
        # Add to recent lows - deque automatically maintains size limit
        self._recent_lows.append((
            candle.low,
            candle.timestamp,
            self._candle_count
        ))
        
        # Detect swing low (current low is higher than surrounding)
        if len(self._recent_lows) >= 3:
            # Simple swing low: middle is lowest
            if (self._recent_lows[-2][0] < self._recent_lows[-3][0] and
                self._recent_lows[-2][0] < self._recent_lows[-1][0]):
                
                swing_low = HigherLow(
                    price=self._recent_lows[-2][0],
                    timestamp=self._recent_lows[-2][1],
                    candle_index=self._recent_lows[-2][2]
                )
                
                return swing_low
        
        return None
    
    def set_hl1(self, hl: HigherLow) -> None:
        """Set the first Higher Low."""
        self._hl1 = hl
        logger.info(f"HL1 set: {hl}")
    
    def check_hl2(self, potential_hl: HigherLow) -> Tuple[bool, str]:
        """
        Check if potential swing low qualifies as HL2.
        
        Args:
            potential_hl: Potential HL2 candidate
            
        Returns:
            Tuple of (is_valid, reason)
        """
        if not self._hl1:
            return False, "HL1 not set"
        
        # Check price is higher
        if potential_hl.price <= self._hl1.price:
            return False, f"Lower low: {potential_hl.price:.2f} <= {self._hl1.price:.2f}"
        
        # Check minimum gap
        candle_gap = potential_hl.candle_index - self._hl1.candle_index
        if candle_gap < self.min_hl_gap_candles:
            return False, f"Candle gap too small: {candle_gap} < {self.min_hl_gap_candles}"
        
        # Check point difference
        point_gap = potential_hl.price - self._hl1.price
        if point_gap < self.min_hl_gap_points:
            return False, f"Point gap too small: {point_gap:.2f} < {self.min_hl_gap_points}"
        
        return True, "Valid HL2"
    
    def set_hl2(self, hl: HigherLow) -> None:
        """Set the second Higher Low."""
        self._hl2 = hl
        logger.info(f"HL2 set: {hl}")
    
    @property
    def hl1(self) -> Optional[HigherLow]:
        """Get HL1."""
        return self._hl1
    
    @property
    def hl2(self) -> Optional[HigherLow]:
        """Get HL2."""
        return self._hl2
    
    @property
    def has_valid_structure(self) -> bool:
        """Check if valid N-Structure exists."""
        return self._hl1 is not None and self._hl2 is not None
    
    def reset(self) -> None:
        """Reset scanner state."""
        self._candle_count = 0
        self._recent_lows.clear()
        self._hl1 = None
        self._hl2 = None


class DivergenceFilter:
    """
    Filters for positive divergence between Index and Option.
    
    Positive Divergence (Bullish):
    - Index: Sideways or slightly down (consolidating)
    - Option: Moving up (showing hidden strength)
    
    This indicates smart money accumulation.
    """
    
    def __init__(
        self,
        roc_period: int = 3,
        index_sideways_threshold: float = 0.0005,  # 0.05%
        option_strength_threshold: float = 0.0005   # 0.05%
    ):
        """
        Initialize divergence filter.
        
        Args:
            roc_period: Number of candles for Rate of Change
            index_sideways_threshold: Max ROC for "sideways" (0.05%)
            option_strength_threshold: Min ROC for "strength" (0.05%)
        """
        self.roc_period = roc_period
        self.index_sideways_threshold = index_sideways_threshold
        self.option_strength_threshold = option_strength_threshold
        
    def calculate_roc(self, prices: List[float]) -> Optional[float]:
        """
        Calculate Rate of Change.
        
        ROC = (Current - Previous) / Previous
        
        Args:
            prices: List of prices (most recent last)
            
        Returns:
            ROC as decimal (e.g., 0.01 = 1%)
        """
        if len(prices) < self.roc_period + 1:
            return None
            
        current = prices[-1]
        previous = prices[-(self.roc_period + 1)]
        
        if previous == 0:
            return None
            
        return (current - previous) / previous
    
    def check_divergence(
        self,
        index_prices: List[float],
        option_prices: List[float]
    ) -> Tuple[bool, float, float, str]:
        """
        Check for positive divergence.
        
        Args:
            index_prices: Recent index close prices
            option_prices: Recent option close prices
            
        Returns:
            Tuple of (is_divergence, index_roc, option_roc, reason)
        """
        index_roc = self.calculate_roc(index_prices)
        option_roc = self.calculate_roc(option_prices)
        
        if index_roc is None or option_roc is None:
            return False, 0, 0, "Insufficient data for ROC"
        
        # Check Index is sideways (ROC near zero or negative)
        index_sideways = abs(index_roc) < self.index_sideways_threshold or index_roc < 0
        
        # Check Option shows strength (positive ROC)
        option_strength = option_roc > self.option_strength_threshold
        
        if index_sideways and option_strength:
            return True, index_roc, option_roc, (
                f"Positive divergence: Index ROC={index_roc*100:.3f}% | "
                f"Option ROC={option_roc*100:.3f}%"
            )
        
        reason_parts = []
        if not index_sideways:
            reason_parts.append(f"Index not sideways (ROC={index_roc*100:.3f}%)")
        if not option_strength:
            reason_parts.append(f"Option weak (ROC={option_roc*100:.3f}%)")
            
        return False, index_roc, option_roc, " | ".join(reason_parts)


class NStructureDetector:
    """
    Main N-Structure pattern detector.
    
    Integrates all components:
    - Setup Validator
    - Structure Scanner
    - Divergence Filter
    
    Tracks the complete pattern formation process.
    """
    
    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 15,
        min_hl_gap_candles: int = 5,
        min_hl_gap_points: float = 2.0,
        roc_period: int = 3,
        index_sideways_threshold: float = 0.0005,
        option_strength_threshold: float = 0.0005,
        entry_buffer: float = 1.5
    ):
        """
        Initialize N-Structure detector.
        
        Args:
            ema_fast: Fast EMA period
            ema_slow: Slow EMA period
            min_hl_gap_candles: Min candles between HLs
            min_hl_gap_points: Min point difference for HLs
            roc_period: Candles for ROC calculation
            index_sideways_threshold: Index ROC threshold
            option_strength_threshold: Option ROC threshold
            entry_buffer: Points to add to high for entry
        """
        self.entry_buffer = entry_buffer
        
        self.setup_validator = SetupValidator(ema_fast, ema_slow)
        self.structure_scanner = StructureScanner(min_hl_gap_candles, min_hl_gap_points)
        self.divergence_filter = DivergenceFilter(
            roc_period, index_sideways_threshold, option_strength_threshold
        )
        
        self._current_structure: Optional[NStructure] = None
        self._previous_high: Optional[float] = None
        self._in_setup: bool = False
        
        # Price history for divergence - use deque for O(1) append/pop
        self._max_history = 20
        self._index_prices: deque = deque(maxlen=self._max_history)
        self._option_prices: deque = deque(maxlen=self._max_history)
        
    def process_synced_pair(
        self,
        pair: SyncedCandlePair,
        ema_fast_value: float,
        ema_slow_value: float
    ) -> Tuple[SetupStatus, Optional[NStructure], str]:
        """
        Process a synchronized candle pair.
        
        Args:
            pair: Synced Index + Option candle pair
            ema_fast_value: Current EMA(9) value
            ema_slow_value: Current EMA(15) value
            
        Returns:
            Tuple of (status, structure, message)
        """
        index_candle = pair.index_candle
        option_candle = pair.option_candle
        
        # Update price history - deque automatically maintains maxlen
        self._index_prices.append(index_candle.close)
        self._option_prices.append(option_candle.close)
        
        # Track previous high
        if self._previous_high is None:
            self._previous_high = index_candle.high
            return SetupStatus.NO_SETUP, None, "Initializing previous high"
        
        # === State Machine Logic ===
        
        # State: No active setup - looking for breakout
        if not self._in_setup:
            if index_candle.high > self._previous_high:
                # Breakout detected!
                self._current_structure = NStructure(
                    breakout_high=index_candle.high,
                    breakout_time=index_candle.timestamp,
                    status=SetupStatus.WATCHING_BREAKOUT
                )
                self._in_setup = True
                self._previous_high = index_candle.high
                return SetupStatus.WATCHING_BREAKOUT, self._current_structure, "Breakout detected"
            
            # Update previous high if making new highs
            self._previous_high = max(self._previous_high, index_candle.high)
            return SetupStatus.NO_SETUP, None, "Waiting for breakout"
        
        # State: In setup - tracking pullback and structure
        if self._current_structure:
            # Update recent high
            if index_candle.high > (self._current_structure.recent_high or 0):
                self._current_structure.recent_high = index_candle.high
                self._current_structure.recent_high_time = index_candle.timestamp
            
            # Check for pullback to EMA
            is_pullback, ema_touched = self.setup_validator.is_pullback_to_ema(
                index_candle.low, ema_fast_value, ema_slow_value
            )
            
            if is_pullback:
                # Validate close above EMA
                if not self.setup_validator.is_close_above_ema(index_candle.close, ema_slow_value):
                    # Setup killed
                    self._reset_setup()
                    return SetupStatus.INVALIDATED, None, "Close below EMA15 - setup killed"
                
                self._current_structure.status = SetupStatus.TRACKING_PULLBACK
                
                # Check for HL
                swing_low = self.structure_scanner.process_candle(index_candle, True)
                
                if swing_low:
                    if not self.structure_scanner.hl1:
                        # First HL
                        self.structure_scanner.set_hl1(swing_low)
                        self._current_structure.hl1 = swing_low
                        self._current_structure.status = SetupStatus.HL_FORMING
                        return SetupStatus.HL_FORMING, self._current_structure, f"HL1 detected: {swing_low}"
                    else:
                        # Check if valid HL2
                        is_valid_hl2, reason = self.structure_scanner.check_hl2(swing_low)
                        
                        if is_valid_hl2:
                            self.structure_scanner.set_hl2(swing_low)
                            self._current_structure.hl2 = swing_low
                            self._current_structure.status = SetupStatus.STRUCTURE_VALID
                            
                            # Now check divergence
                            has_div, idx_roc, opt_roc, div_reason = self.divergence_filter.check_divergence(
                                self._index_prices, self._option_prices
                            )
                            
                            self._current_structure.index_roc = idx_roc
                            self._current_structure.option_roc = opt_roc
                            
                            if has_div:
                                self._current_structure.divergence_confirmed = True
                                self._current_structure.status = SetupStatus.DIVERGENCE_CONFIRMED
                                
                                # Calculate entry trigger
                                entry_level = (self._current_structure.recent_high or 0) + self.entry_buffer
                                self._current_structure.entry_trigger = entry_level
                                self._current_structure.status = SetupStatus.READY_FOR_ENTRY
                                
                                return (
                                    SetupStatus.READY_FOR_ENTRY,
                                    self._current_structure,
                                    f"N-Structure complete! Entry: {entry_level:.2f}"
                                )
                            else:
                                return (
                                    SetupStatus.STRUCTURE_VALID,
                                    self._current_structure,
                                    f"Structure valid but no divergence: {div_reason}"
                                )
                        else:
                            return SetupStatus.HL_FORMING, self._current_structure, reason
            
            return self._current_structure.status, self._current_structure, "Monitoring"
        
        return SetupStatus.NO_SETUP, None, "No structure"
    
    def check_entry_trigger(self, current_price: float) -> Tuple[bool, float]:
        """
        Check if entry trigger price is hit.
        
        Args:
            current_price: Current market price
            
        Returns:
            Tuple of (triggered, entry_price)
        """
        if not self._current_structure:
            return False, 0
            
        if self._current_structure.status != SetupStatus.READY_FOR_ENTRY:
            return False, 0
            
        trigger = self._current_structure.entry_trigger or 0
        
        if current_price >= trigger:
            return True, trigger
            
        return False, trigger
    
    def _reset_setup(self) -> None:
        """Reset current setup."""
        self._current_structure = None
        self._in_setup = False
        self.structure_scanner.reset()
    
    def reset(self) -> None:
        """Full reset of detector."""
        self._reset_setup()
        self._previous_high = None
        self._index_prices.clear()
        self._option_prices.clear()
    
    @property
    def current_structure(self) -> Optional[NStructure]:
        """Get current N-Structure if any."""
        return self._current_structure
    
    @property
    def is_ready_for_entry(self) -> bool:
        """Check if we're ready for entry."""
        return (
            self._current_structure is not None and
            self._current_structure.status == SetupStatus.READY_FOR_ENTRY
        )
