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


class SignalDirection(Enum):
    """Direction of the trading signal."""
    BULLISH = "bullish"  # Buy CE
    BEARISH = "bearish"  # Buy PE
    NEUTRAL = "neutral"  # No clear direction


class SetupStatus(Enum):
    """Status of N-Structure setup."""
    NO_SETUP = "no_setup"
    WATCHING_BREAKOUT = "watching_breakout"
    TRACKING_PULLBACK = "tracking_pullback"
    HL_FORMING = "hl_forming"
    LH_FORMING = "lh_forming"  # For bearish
    STRUCTURE_VALID = "structure_valid"
    DIVERGENCE_CONFIRMED = "divergence_confirmed"
    READY_FOR_ENTRY = "ready_for_entry"
    INVALIDATED = "invalidated"


@dataclass
class SwingPoint:
    """Container for a swing point (Higher Low or Lower High)."""
    price: float
    timestamp: datetime
    candle_index: int
    
    def __str__(self) -> str:
        return f"Swing @ {self.price:.2f} ({self.timestamp.strftime('%H:%M')})"


# Alias for backward compatibility
HigherLow = SwingPoint


@dataclass
class NStructure:
    """
    Complete N-Structure pattern data.
    
    Captures all points of the N-shape for analysis and logging.
    Works for both bullish (Higher Lows) and bearish (Lower Highs).
    """
    # Direction of the structure
    direction: SignalDirection = SignalDirection.BULLISH
    
    # The breakout/breakdown level that started the setup
    breakout_level: float = 0.0
    breakout_time: Optional[datetime] = None
    
    # For bullish: Higher Lows, For bearish: Lower Highs
    swing1: Optional[SwingPoint] = None  # HL1 or LH1
    swing2: Optional[SwingPoint] = None  # HL2 or LH2
    
    # Recent high/low before current pullback
    recent_extreme: Optional[float] = None
    recent_extreme_time: Optional[datetime] = None
    
    # Divergence data
    divergence_confirmed: bool = False
    index_roc: Optional[float] = None
    option_roc: Optional[float] = None
    
    # Entry level
    entry_trigger: Optional[float] = None
    
    # Status
    status: SetupStatus = SetupStatus.WATCHING_BREAKOUT
    
    # Backward compatibility aliases
    @property
    def breakout_high(self) -> float:
        return self.breakout_level
    
    @property
    def hl1(self) -> Optional[SwingPoint]:
        return self.swing1 if self.direction == SignalDirection.BULLISH else None
    
    @property
    def hl2(self) -> Optional[SwingPoint]:
        return self.swing2 if self.direction == SignalDirection.BULLISH else None
    
    @property
    def lh1(self) -> Optional[SwingPoint]:
        return self.swing1 if self.direction == SignalDirection.BEARISH else None
    
    @property
    def lh2(self) -> Optional[SwingPoint]:
        return self.swing2 if self.direction == SignalDirection.BEARISH else None
    
    @property
    def recent_high(self) -> Optional[float]:
        return self.recent_extreme if self.direction == SignalDirection.BULLISH else None
    
    @property
    def is_valid(self) -> bool:
        """Check if structure has valid pattern."""
        if not self.swing1 or not self.swing2:
            return False
        if self.direction == SignalDirection.BULLISH:
            return self.swing2.price > self.swing1.price  # HL2 > HL1
        else:
            return self.swing2.price < self.swing1.price  # LH2 < LH1
    
    @property
    def swing_gap(self) -> Optional[float]:
        """Get point difference between swings."""
        if not self.swing1 or not self.swing2:
            return None
        return abs(self.swing2.price - self.swing1.price)
    
    # Backward compatibility
    @property
    def hl_gap(self) -> Optional[float]:
        return self.swing_gap
    
    def __str__(self) -> str:
        dir_str = "🟢 BULLISH" if self.direction == SignalDirection.BULLISH else "🔴 BEARISH"
        parts = [f"N-Structure {dir_str} [{self.status.value}]"]
        parts.append(f"Breakout: {self.breakout_level:.2f}")
        if self.swing1:
            label = "HL1" if self.direction == SignalDirection.BULLISH else "LH1"
            parts.append(f"{label}: {self.swing1.price:.2f}")
        if self.swing2:
            label = "HL2" if self.direction == SignalDirection.BULLISH else "LH2"
            parts.append(f"{label}: {self.swing2.price:.2f}")
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


class DualDirectionDetector:
    """
    Detects both Bullish and Bearish N-Structure patterns simultaneously.
    
    - Bullish: Higher Lows (HL1 < HL2) after breakout HIGH → Buy CE
    - Bearish: Lower Highs (LH1 > LH2) after breakdown LOW → Buy PE
    
    v5.1 Features:
    - Volume Confirmation: Breakout candle must have volume > 1.5x average
    - Gap Filter: Skip signals if market gaps > 50 points at open
    
    Returns the first confirmed signal direction.
    """
    
    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 15,
        min_swing_gap_candles: int = 5,
        min_swing_gap_points: float = 2.0,
        entry_buffer: float = 1.5,
        trade_direction: str = "BOTH",  # CE_ONLY, PE_ONLY, BOTH
        # v5.1: Volume confirmation
        volume_confirmation_enabled: bool = True,
        min_volume_ratio: float = 1.5,  # Breakout volume must be 1.5x average
        volume_lookback: int = 20,
        # v5.1: Gap filter
        gap_filter_enabled: bool = True,
        max_gap_points: float = 50.0,  # Skip if gap > 50 points
        # v5.2: Confirmation candle (patience for entry)
        confirmation_candles: int = 1,  # Wait X candles after READY_FOR_ENTRY
        require_direction_candle: bool = True  # Confirm candle must be in trade direction
    ):
        """
        Initialize dual-direction detector.
        
        Args:
            trade_direction: Which signals to look for
            volume_confirmation_enabled: Require volume confirmation on breakout
            min_volume_ratio: Min volume ratio for breakout (1.5x = 150% of average)
            volume_lookback: Candles for volume average calculation
            gap_filter_enabled: Enable gap filter
            max_gap_points: Max allowed gap at market open
            confirmation_candles: Wait X candles after pattern detected before entry
            require_direction_candle: Confirmation candle must close in trade direction
        """
        self.entry_buffer = entry_buffer
        self.trade_direction = trade_direction
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.min_swing_gap_candles = min_swing_gap_candles
        self.min_swing_gap_points = min_swing_gap_points
        
        # v5.1: Volume confirmation settings
        self.volume_confirmation_enabled = volume_confirmation_enabled
        self.min_volume_ratio = min_volume_ratio
        self.volume_lookback = volume_lookback
        self._volume_history: deque = deque(maxlen=volume_lookback)
        
        # v5.1: Gap filter settings
        self.gap_filter_enabled = gap_filter_enabled
        self.max_gap_points = max_gap_points
        self._prev_day_close: Optional[float] = None
        self._gap_detected: bool = False
        self._gap_filter_active: bool = False  # True if large gap detected today
        
        # v5.2: Confirmation candle settings (patience for entry)
        self.confirmation_candles = confirmation_candles
        self.require_direction_candle = require_direction_candle
        self._bullish_confirmation_count: int = 0
        self._bearish_confirmation_count: int = 0
        self._bullish_pending_entry: bool = False  # Waiting for confirmation
        self._bearish_pending_entry: bool = False  # Waiting for confirmation
        
        # Bullish tracking (existing logic)
        self._bullish_setup: Optional[NStructure] = None
        self._previous_high: Optional[float] = None
        self._in_bullish_setup: bool = False
        self._bullish_swing_count: int = 0
        self._bullish_last_swing: Optional[SwingPoint] = None
        
        # Bearish tracking (mirror of bullish)
        self._bearish_setup: Optional[NStructure] = None
        self._previous_low: Optional[float] = None
        self._in_bearish_setup: bool = False
        self._bearish_swing_count: int = 0
        self._bearish_last_swing: Optional[SwingPoint] = None
        
        # Candle counter for swing gap validation
        self._candle_index: int = 0
        self._first_candle_of_day: bool = True
        
        # Price history
        self._max_history = 20
        self._index_prices: deque = deque(maxlen=self._max_history)
        self._option_prices: deque = deque(maxlen=self._max_history)
    
    def _check_volume_confirmation(self, current_volume: int) -> Tuple[bool, str]:
        """
        Check if current candle has sufficient volume for breakout.
        
        Returns:
            Tuple of (is_confirmed, reason)
        """
        if not self.volume_confirmation_enabled:
            return True, "Volume check disabled"
        
        if len(self._volume_history) < 5:
            # Not enough history, allow trade
            return True, "Insufficient volume history"
        
        avg_volume = sum(self._volume_history) / len(self._volume_history)
        if avg_volume == 0:
            return True, "No volume data"
        
        volume_ratio = current_volume / avg_volume
        
        if volume_ratio >= self.min_volume_ratio:
            return True, f"✅ Volume confirmed: {volume_ratio:.2f}x average"
        else:
            return False, f"❌ Low volume: {volume_ratio:.2f}x (need {self.min_volume_ratio}x)"
    
    def _check_gap_filter(self, candle: Candle) -> Tuple[bool, str]:
        """
        Check for large gap at market open.
        
        Returns:
            Tuple of (can_trade, reason)
        """
        if not self.gap_filter_enabled:
            return True, "Gap filter disabled"
        
        # Only check on first candle of day
        if not self._first_candle_of_day:
            if self._gap_filter_active:
                return False, f"⚠️ Gap filter active - skip first signal"
            return True, "Not first candle"
        
        # Check if this is first candle (around 9:15-9:20)
        candle_time = candle.timestamp.time()
        from datetime import time
        if candle_time > time(9, 25):
            self._first_candle_of_day = False
            return True, "Past opening window"
        
        # Calculate gap
        if self._prev_day_close is not None:
            gap = abs(candle.open - self._prev_day_close)
            
            if gap > self.max_gap_points:
                self._gap_filter_active = True
                logger.warning(
                    f"🚨 LARGE GAP DETECTED: {gap:.2f} points! "
                    f"Prev Close: {self._prev_day_close:.2f}, Open: {candle.open:.2f}. "
                    f"Skipping first signal."
                )
                return False, f"Large gap: {gap:.2f}pt > {self.max_gap_points}pt"
            else:
                logger.info(f"✅ Gap OK: {gap:.2f} points")
        
        self._first_candle_of_day = False
        return True, "Gap check passed"
    
    def set_prev_day_close(self, close_price: float) -> None:
        """Set previous day's closing price for gap calculation."""
        self._prev_day_close = close_price
        logger.info(f"📊 Prev day close set: ₹{close_price:.2f}")
    
    def reset_daily(self) -> None:
        """Reset daily tracking (call at start of each trading day)."""
        self._first_candle_of_day = True
        self._gap_filter_active = False
        self._gap_detected = False
        logger.info("🔄 Daily tracking reset for gap filter")
    
    def process_synced_pair(
        self,
        pair: SyncedCandlePair,
        ema_fast_value: float,
        ema_slow_value: float,
        volume: int = 0  # v5.1: Volume for confirmation
    ) -> Tuple[SetupStatus, Optional[NStructure], str]:
        """
        Process candle and detect both bullish and bearish patterns.
        
        Args:
            pair: Synced candle pair (index + option)
            ema_fast_value: EMA 9 value
            ema_slow_value: EMA 15 value
            volume: Current candle volume (for breakout confirmation)
        
        Returns the first confirmed signal.
        """
        self._candle_index += 1
        index_candle = pair.index_candle
        
        # v5.1: Update volume history
        if volume > 0:
            self._volume_history.append(volume)
        
        # v5.1: Check gap filter on first candle
        can_trade_gap, gap_reason = self._check_gap_filter(index_candle)
        if not can_trade_gap:
            return SetupStatus.NO_SETUP, None, gap_reason
        
        # Update price history
        self._index_prices.append(index_candle.close)
        self._option_prices.append(pair.option_candle.close)
        
        # Initialize tracking levels
        if self._previous_high is None:
            self._previous_high = index_candle.high
        if self._previous_low is None:
            self._previous_low = index_candle.low
        
        results = []
        
        # Process Bullish (CE) setup if enabled
        if self.trade_direction in ["CE_ONLY", "BOTH"]:
            bullish_result = self._process_bullish(index_candle, ema_fast_value, ema_slow_value, volume)
            if bullish_result[0] != SetupStatus.NO_SETUP:
                results.append(bullish_result)
        
        # Process Bearish (PE) setup if enabled
        if self.trade_direction in ["PE_ONLY", "BOTH"]:
            bearish_result = self._process_bearish(index_candle, ema_fast_value, ema_slow_value, volume)
            if bearish_result[0] != SetupStatus.NO_SETUP:
                results.append(bearish_result)
        
        # Return the most advanced signal (READY_FOR_ENTRY > others)
        if results:
            # Prioritize READY_FOR_ENTRY
            for r in results:
                if r[0] == SetupStatus.READY_FOR_ENTRY:
                    return r
            return results[0]
        
        return SetupStatus.NO_SETUP, None, "No pattern detected"
    
    def _process_bullish(
        self,
        candle: Candle,
        ema_fast: float,
        ema_slow: float,
        volume: int = 0  # v5.1: Volume for breakout confirmation
    ) -> Tuple[SetupStatus, Optional[NStructure], str]:
        """Process bullish N-Structure (Higher Lows → CE)."""
        
        # Not in setup - looking for breakout high
        if not self._in_bullish_setup:
            if candle.high > self._previous_high:
                # v5.1: Check volume confirmation for breakout
                vol_confirmed, vol_reason = self._check_volume_confirmation(volume)
                if not vol_confirmed:
                    logger.info(f"🟢 Bullish breakout rejected: {vol_reason}")
                    self._previous_high = max(self._previous_high, candle.high)
                    return SetupStatus.NO_SETUP, None, vol_reason
                
                # New high breakout with volume!
                logger.info(f"🟢 Bullish breakout CONFIRMED! {vol_reason}")
                self._bullish_setup = NStructure(
                    direction=SignalDirection.BULLISH,
                    breakout_level=candle.high,
                    breakout_time=candle.timestamp,
                    status=SetupStatus.WATCHING_BREAKOUT
                )
                self._in_bullish_setup = True
                self._previous_high = candle.high
                self._bullish_swing_count = 0
                self._bullish_last_swing = None
                return SetupStatus.WATCHING_BREAKOUT, self._bullish_setup, f"🟢 Bullish breakout detected | {vol_reason}"
            
            self._previous_high = max(self._previous_high, candle.high)
            return SetupStatus.NO_SETUP, None, ""
        
        # In bullish setup - tracking for Higher Lows
        if self._bullish_setup:
            # Update recent high
            if candle.high > (self._bullish_setup.recent_extreme or 0):
                self._bullish_setup.recent_extreme = candle.high
                self._bullish_setup.recent_extreme_time = candle.timestamp
            
            # Check for pullback to EMA (candle low touches EMA)
            # v5.1 FIX: Use absolute points (5pt) instead of percentage for accuracy
            ema_touch_tolerance = 5.0  # 5 points tolerance
            touches_ema = candle.low <= ema_fast + ema_touch_tolerance
            
            if touches_ema:
                # Validate close above slow EMA
                if candle.close < ema_slow:
                    self._reset_bullish()
                    return SetupStatus.INVALIDATED, None, "Close below EMA15 - bullish setup killed"
                
                # Potential swing low (Higher Low)
                potential_swing = SwingPoint(
                    price=candle.low,
                    timestamp=candle.timestamp,
                    candle_index=self._candle_index
                )
                
                if self._bullish_swing_count == 0:
                    # First swing (HL1)
                    self._bullish_setup.swing1 = potential_swing
                    self._bullish_last_swing = potential_swing
                    self._bullish_swing_count = 1
                    self._bullish_setup.status = SetupStatus.HL_FORMING
                    return SetupStatus.HL_FORMING, self._bullish_setup, f"🟢 HL1 forming @ {potential_swing.price:.2f}"
                
                elif self._bullish_swing_count == 1:
                    # Check if valid HL2 (higher than HL1, enough gap)
                    hl1 = self._bullish_last_swing
                    if hl1 and potential_swing.price > hl1.price + self.min_swing_gap_points:
                        candle_gap = self._candle_index - hl1.candle_index
                        if candle_gap >= self.min_swing_gap_candles:
                            # Valid HL2!
                            self._bullish_setup.swing2 = potential_swing
                            self._bullish_swing_count = 2
                            
                            # Calculate entry: above recent high
                            entry_level = (self._bullish_setup.recent_extreme or candle.high) + self.entry_buffer
                            self._bullish_setup.entry_trigger = entry_level
                            self._bullish_setup.status = SetupStatus.READY_FOR_ENTRY
                            self._bullish_setup.divergence_confirmed = True  # Enable FSM to arm
                            # Note: is_valid is computed from swing1 and swing2
                            
                            # v5.2: Check if confirmation needed
                            if self.confirmation_candles > 0:
                                self._bullish_pending_entry = True
                                self._bullish_confirmation_count = 0
                                logger.info(f"🟢 Bullish pattern detected, waiting {self.confirmation_candles} confirmation candles")
                                return SetupStatus.HL_FORMING, self._bullish_setup, \
                                    f"🟢 N-Structure detected, waiting {self.confirmation_candles} confirmation candles"
                            
                            return SetupStatus.READY_FOR_ENTRY, self._bullish_setup, \
                                f"🟢 BULLISH N-Structure READY! Entry: {entry_level:.2f} | Buy CE"
            
            # v5.2: Process confirmation candles for bullish
            if self._bullish_pending_entry:
                self._bullish_confirmation_count += 1
                is_green = candle.close > candle.open  # Bullish candle
                
                if self._bullish_confirmation_count >= self.confirmation_candles:
                    # Check if confirmation candle is in right direction
                    if self.require_direction_candle and not is_green:
                        logger.info(f"🟢 Bullish confirmation candle not green, waiting more...")
                        self._bullish_confirmation_count = self.confirmation_candles - 1  # Keep waiting
                        return self._bullish_setup.status, self._bullish_setup, "Waiting for green confirmation"
                    
                    # Confirmed!
                    self._bullish_pending_entry = False
                    self._bullish_setup.status = SetupStatus.READY_FOR_ENTRY
                    entry_level = self._bullish_setup.entry_trigger
                    logger.info(f"🟢 BULLISH CONFIRMED after {self._bullish_confirmation_count} candles!")
                    return SetupStatus.READY_FOR_ENTRY, self._bullish_setup, \
                        f"🟢 BULLISH N-Structure CONFIRMED! Entry: {entry_level:.2f} | Buy CE"
                else:
                    return self._bullish_setup.status, self._bullish_setup, \
                        f"Waiting for confirmation ({self._bullish_confirmation_count}/{self.confirmation_candles})"
            
            return self._bullish_setup.status, self._bullish_setup, "Monitoring bullish"
        
        return SetupStatus.NO_SETUP, None, ""
    
    def _process_bearish(
        self,
        candle: Candle,
        ema_fast: float,
        ema_slow: float,
        volume: int = 0  # v5.1: Volume for breakdown confirmation
    ) -> Tuple[SetupStatus, Optional[NStructure], str]:
        """Process bearish N-Structure (Lower Highs → PE)."""
        
        # Not in setup - looking for breakdown low
        if not self._in_bearish_setup:
            if candle.low < self._previous_low:
                # v5.1: Check volume confirmation for breakdown
                vol_confirmed, vol_reason = self._check_volume_confirmation(volume)
                if not vol_confirmed:
                    logger.info(f"🔴 Bearish breakdown rejected: {vol_reason}")
                    self._previous_low = min(self._previous_low, candle.low)
                    return SetupStatus.NO_SETUP, None, vol_reason
                
                # New low breakdown with volume!
                logger.info(f"🔴 Bearish breakdown CONFIRMED! {vol_reason}")
                self._bearish_setup = NStructure(
                    direction=SignalDirection.BEARISH,
                    breakout_level=candle.low,
                    breakout_time=candle.timestamp,
                    status=SetupStatus.WATCHING_BREAKOUT
                )
                self._in_bearish_setup = True
                self._previous_low = candle.low
                self._bearish_swing_count = 0
                self._bearish_last_swing = None
                return SetupStatus.WATCHING_BREAKOUT, self._bearish_setup, f"🔴 Bearish breakdown detected | {vol_reason}"
            
            self._previous_low = min(self._previous_low, candle.low)
            return SetupStatus.NO_SETUP, None, ""
        
        # In bearish setup - tracking for Lower Highs
        if self._bearish_setup:
            # Update recent low (the extreme we're tracking)
            if candle.low < (self._bearish_setup.recent_extreme or float('inf')):
                self._bearish_setup.recent_extreme = candle.low
                self._bearish_setup.recent_extreme_time = candle.timestamp
            
            # Check for pullback UP to EMA (bearish bounce)
            # v5.1 FIX: Use absolute points (5pt) for accuracy
            ema_touch_tolerance = 5.0  # 5 points tolerance
            ema_zone_low = ema_fast - ema_touch_tolerance
            ema_zone_high = ema_fast + ema_touch_tolerance
            touches_ema = ema_zone_low <= candle.high <= ema_zone_high
            
            # Also detect if candle wicks into EMA zone
            candle_enters_ema_zone = candle.high >= ema_zone_low
            
            if touches_ema or candle_enters_ema_zone:
                # Validate: close should stay below slow EMA (bearish)
                # v5.1 FIX: Use 1% tolerance for bearish bounces (they can wick above)
                if candle.close > ema_slow * 1.01:
                    self._reset_bearish()
                    return SetupStatus.INVALIDATED, None, "Close above EMA15 - bearish setup killed"
                
                # Potential swing high (Lower High)
                potential_swing = SwingPoint(
                    price=candle.high,
                    timestamp=candle.timestamp,
                    candle_index=self._candle_index
                )
                
                if self._bearish_swing_count == 0:
                    # First swing (LH1)
                    self._bearish_setup.swing1 = potential_swing
                    self._bearish_last_swing = potential_swing
                    self._bearish_swing_count = 1
                    self._bearish_setup.status = SetupStatus.LH_FORMING
                    return SetupStatus.LH_FORMING, self._bearish_setup, f"🔴 LH1 forming @ {potential_swing.price:.2f}"
                
                elif self._bearish_swing_count == 1:
                    # Check if valid LH2 (lower than LH1, enough gap)
                    lh1 = self._bearish_last_swing
                    if lh1 and potential_swing.price < lh1.price - self.min_swing_gap_points:
                        candle_gap = self._candle_index - lh1.candle_index
                        if candle_gap >= self.min_swing_gap_candles:
                            # Valid LH2!
                            self._bearish_setup.swing2 = potential_swing
                            self._bearish_swing_count = 2
                            
                            # Calculate entry: below recent low
                            entry_level = (self._bearish_setup.recent_extreme or candle.low) - self.entry_buffer
                            self._bearish_setup.entry_trigger = entry_level
                            self._bearish_setup.status = SetupStatus.READY_FOR_ENTRY
                            self._bearish_setup.divergence_confirmed = True  # Enable FSM to arm
                            # Note: is_valid is computed from swing1 and swing2
                            
                            # v5.2: Check if confirmation needed
                            if self.confirmation_candles > 0:
                                self._bearish_pending_entry = True
                                self._bearish_confirmation_count = 0
                                logger.info(f"🔴 Bearish pattern detected, waiting {self.confirmation_candles} confirmation candles")
                                return SetupStatus.LH_FORMING, self._bearish_setup, \
                                    f"🔴 N-Structure detected, waiting {self.confirmation_candles} confirmation candles"
                            
                            return SetupStatus.READY_FOR_ENTRY, self._bearish_setup, \
                                f"🔴 BEARISH N-Structure READY! Entry: {entry_level:.2f} | Buy PE"
            
            # v5.2: Process confirmation candles for bearish
            if self._bearish_pending_entry:
                self._bearish_confirmation_count += 1
                is_red = candle.close < candle.open  # Bearish candle
                
                if self._bearish_confirmation_count >= self.confirmation_candles:
                    # Check if confirmation candle is in right direction
                    if self.require_direction_candle and not is_red:
                        logger.info(f"🔴 Bearish confirmation candle not red, waiting more...")
                        self._bearish_confirmation_count = self.confirmation_candles - 1  # Keep waiting
                        return self._bearish_setup.status, self._bearish_setup, "Waiting for red confirmation"
                    
                    # Confirmed!
                    self._bearish_pending_entry = False
                    self._bearish_setup.status = SetupStatus.READY_FOR_ENTRY
                    entry_level = self._bearish_setup.entry_trigger
                    logger.info(f"🔴 BEARISH CONFIRMED after {self._bearish_confirmation_count} candles!")
                    return SetupStatus.READY_FOR_ENTRY, self._bearish_setup, \
                        f"🔴 BEARISH N-Structure CONFIRMED! Entry: {entry_level:.2f} | Buy PE"
                else:
                    return self._bearish_setup.status, self._bearish_setup, \
                        f"Waiting for confirmation ({self._bearish_confirmation_count}/{self.confirmation_candles})"
            
            return self._bearish_setup.status, self._bearish_setup, "Monitoring bearish"
        
        return SetupStatus.NO_SETUP, None, ""
    
    def _reset_bullish(self) -> None:
        """Reset bullish setup."""
        self._bullish_setup = None
        self._in_bullish_setup = False
        self._bullish_swing_count = 0
        self._bullish_last_swing = None
        # v5.2: Reset confirmation tracking
        self._bullish_pending_entry = False
        self._bullish_confirmation_count = 0
    
    def _reset_bearish(self) -> None:
        """Reset bearish setup."""
        self._bearish_setup = None
        self._in_bearish_setup = False
        self._bearish_swing_count = 0
        self._bearish_last_swing = None
        # v5.2: Reset confirmation tracking
        self._bearish_pending_entry = False
        self._bearish_confirmation_count = 0
    
    def reset(self) -> None:
        """Full reset."""
        self._reset_bullish()
        self._reset_bearish()
        self._previous_high = None
        self._previous_low = None
        self._candle_index = 0
        self._index_prices.clear()
        self._option_prices.clear()
        # v5.1: Reset volume and gap tracking
        self._volume_history.clear()
        self._first_candle_of_day = True
        self._gap_filter_active = False
    
    @property
    def current_structure(self) -> Optional[NStructure]:
        """Get the most advanced current structure."""
        # Prefer READY_FOR_ENTRY
        if self._bullish_setup and self._bullish_setup.status == SetupStatus.READY_FOR_ENTRY:
            return self._bullish_setup
        if self._bearish_setup and self._bearish_setup.status == SetupStatus.READY_FOR_ENTRY:
            return self._bearish_setup
        # Return any active setup
        return self._bullish_setup or self._bearish_setup
    
    @property
    def is_ready_for_entry(self) -> bool:
        """Check if any direction is ready for entry."""
        return (
            (self._bullish_setup and self._bullish_setup.status == SetupStatus.READY_FOR_ENTRY) or
            (self._bearish_setup and self._bearish_setup.status == SetupStatus.READY_FOR_ENTRY)
        )
