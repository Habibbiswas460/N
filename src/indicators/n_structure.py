"""
N-Structure Pattern Detection Module

The N-Structure is a price action pattern used for identifying 
high-probability entry points:

For Bullish (CE):
- Breakout above resistance
- Pullback to EMA creates HL1 (Higher Low 1)
- Bounce creates HL2 (Higher Low 2) confirming bullish bias
- Entry triggered on breakout above initial high

For Bearish (PE):
- Breakdown below support  
- Bounce to EMA creates LH1 (Lower High 1)
- Pullback creates LH2 (Lower High 2) confirming bearish bias
- Entry triggered on breakdown below initial low
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum, auto


class SignalDirection(Enum):
    """Signal direction for N-Structure pattern."""
    BULLISH = auto()  # CE - Call Entry
    BEARISH = auto()  # PE - Put Entry
    NEUTRAL = auto()  # No clear direction


class NStructureState(Enum):
    """State machine for N-Structure detection."""
    IDLE = auto()           # Waiting for breakout/breakdown
    SETUP = auto()          # Breakout occurred, waiting for pullback
    VALIDATION = auto()     # Pullback detected, waiting for HH/LH
    READY = auto()          # N-Structure confirmed, waiting for entry
    TRIGGERED = auto()      # Entry triggered


@dataclass
class SwingPoint:
    """Represents a swing high or low point."""
    timestamp: datetime
    price: float
    is_high: bool  # True = swing high, False = swing low
    strength: int = 1  # Number of candles confirming


@dataclass 
class NStructure:
    """
    The N-Structure pattern detection and tracking.
    
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
    entry_trigger: float = 0.0      # Entry price level
    formation_time: Optional[datetime] = None
    is_valid: bool = False
    direction: SignalDirection = SignalDirection.NEUTRAL
    state: NStructureState = NStructureState.IDLE
    
    # Tracking
    swing_points: List[SwingPoint] = field(default_factory=list)
    
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
        if self.direction == SignalDirection.BULLISH:
            # Bullish validation
            if self.higher_low <= self.pullback_low:
                return False
            hl_gap = self.higher_low - self.pullback_low
            if hl_gap < min_hl_gap:
                return False
        elif self.direction == SignalDirection.BEARISH:
            # Bearish validation (PE)
            if self.lower_high >= self.pullback_high:
                return False
            lh_gap = self.pullback_high - self.lower_high
            if lh_gap < min_hl_gap:
                return False
        else:
            return False
            
        self.is_valid = True
        self.state = NStructureState.READY
        return True
    
    def reset(self):
        """Reset N-Structure for new pattern detection."""
        self.breakout_high = 0.0
        self.pullback_low = 0.0
        self.higher_low = 0.0
        self.breakdown_low = 0.0
        self.pullback_high = 0.0
        self.lower_high = 0.0
        self.entry_trigger = 0.0
        self.formation_time = None
        self.is_valid = False
        self.direction = SignalDirection.NEUTRAL
        self.state = NStructureState.IDLE
        self.swing_points.clear()
    
    def set_bullish(self, breakout_high: float, entry_buffer: float = 1.5):
        """Initialize for bullish pattern detection."""
        self.direction = SignalDirection.BULLISH
        self.breakout_high = breakout_high
        self.entry_trigger = breakout_high + entry_buffer
        self.state = NStructureState.SETUP
    
    def set_bearish(self, breakdown_low: float, entry_buffer: float = 1.5):
        """Initialize for bearish pattern detection."""
        self.direction = SignalDirection.BEARISH
        self.breakdown_low = breakdown_low
        self.entry_trigger = breakdown_low - entry_buffer
        self.state = NStructureState.SETUP
    
    def add_swing_point(self, timestamp: datetime, price: float, is_high: bool):
        """Add a swing point for tracking."""
        self.swing_points.append(SwingPoint(
            timestamp=timestamp,
            price=price,
            is_high=is_high
        ))
        # Keep only last 10 swing points
        if len(self.swing_points) > 10:
            self.swing_points = self.swing_points[-10:]
    
    def update_pullback(self, price: float, timestamp: datetime):
        """Update pullback level based on price action."""
        if self.direction == SignalDirection.BULLISH:
            if self.state == NStructureState.SETUP:
                # Looking for HL1 (first higher low)
                if self.pullback_low == 0.0 or price < self.pullback_low:
                    self.pullback_low = price
                    self.add_swing_point(timestamp, price, False)
            elif self.state == NStructureState.VALIDATION:
                # Looking for HL2 (second higher low, must be > HL1)
                if price > self.pullback_low:
                    self.higher_low = price
                    self.add_swing_point(timestamp, price, False)
                    self.formation_time = timestamp
        
        elif self.direction == SignalDirection.BEARISH:
            if self.state == NStructureState.SETUP:
                # Looking for LH1 (first lower high)
                if self.pullback_high == 0.0 or price > self.pullback_high:
                    self.pullback_high = price
                    self.add_swing_point(timestamp, price, True)
            elif self.state == NStructureState.VALIDATION:
                # Looking for LH2 (second lower high, must be < LH1)
                if price < self.pullback_high:
                    self.lower_high = price
                    self.add_swing_point(timestamp, price, True)
                    self.formation_time = timestamp
    
    def check_entry_trigger(self, current_price: float) -> bool:
        """Check if entry trigger is hit."""
        if not self.is_valid:
            return False
            
        if self.direction == SignalDirection.BULLISH:
            return current_price >= self.entry_trigger
        elif self.direction == SignalDirection.BEARISH:
            return current_price <= self.entry_trigger
        
        return False
    
    def __str__(self) -> str:
        """String representation of N-Structure."""
        if self.direction == SignalDirection.BULLISH:
            return (
                f"N-Structure [BULLISH] | "
                f"Breakout: {self.breakout_high:.2f} | "
                f"HL1: {self.pullback_low:.2f} | "
                f"HL2: {self.higher_low:.2f} | "
                f"Trigger: {self.entry_trigger:.2f} | "
                f"Valid: {self.is_valid}"
            )
        elif self.direction == SignalDirection.BEARISH:
            return (
                f"N-Structure [BEARISH] | "
                f"Breakdown: {self.breakdown_low:.2f} | "
                f"LH1: {self.pullback_high:.2f} | "
                f"LH2: {self.lower_high:.2f} | "
                f"Trigger: {self.entry_trigger:.2f} | "
                f"Valid: {self.is_valid}"
            )
        return "N-Structure [NEUTRAL]"


class NStructureDetector:
    """
    N-Structure Pattern Detector
    
    Monitors price action and detects N-Structure formations.
    """
    
    def __init__(self, 
                 lookback_period: int = 20,
                 min_breakout_gap: float = 5.0,
                 entry_buffer: float = 1.5,
                 min_hl_gap: float = 2.0):
        """
        Initialize detector.
        
        Args:
            lookback_period: Candles to look back for resistance/support
            min_breakout_gap: Minimum gap for breakout confirmation
            entry_buffer: Buffer above/below breakout for entry trigger
            min_hl_gap: Minimum gap between HL1/HL2 or LH1/LH2
        """
        self.lookback_period = lookback_period
        self.min_breakout_gap = min_breakout_gap
        self.entry_buffer = entry_buffer
        self.min_hl_gap = min_hl_gap
        
        self.n_structure = NStructure()
        self._highs: List[float] = []
        self._lows: List[float] = []
        self._resistance: float = 0.0
        self._support: float = 0.0
    
    def update(self, high: float, low: float, close: float, 
               timestamp: datetime) -> Optional[NStructure]:
        """
        Update detector with new candle data.
        
        Returns NStructure if pattern is confirmed and ready for entry.
        """
        self._highs.append(high)
        self._lows.append(low)
        
        # Keep only lookback period
        if len(self._highs) > self.lookback_period:
            self._highs = self._highs[-self.lookback_period:]
            self._lows = self._lows[-self.lookback_period:]
        
        # Update resistance/support
        if len(self._highs) >= 5:
            self._resistance = max(self._highs[:-1])  # Exclude current
            self._support = min(self._lows[:-1])
        
        # State machine logic
        if self.n_structure.state == NStructureState.IDLE:
            self._check_breakout(high, low, close, timestamp)
        
        elif self.n_structure.state == NStructureState.SETUP:
            self._check_pullback(high, low, close, timestamp)
        
        elif self.n_structure.state == NStructureState.VALIDATION:
            self._check_validation(high, low, close, timestamp)
        
        elif self.n_structure.state == NStructureState.READY:
            if self.n_structure.check_entry_trigger(close):
                self.n_structure.state = NStructureState.TRIGGERED
                return self.n_structure
        
        return None
    
    def _check_breakout(self, high: float, low: float, close: float, 
                        timestamp: datetime):
        """Check for breakout above resistance or below support."""
        # Bullish breakout
        if self._resistance > 0 and high > self._resistance + self.min_breakout_gap:
            self.n_structure.reset()
            self.n_structure.set_bullish(self._resistance, self.entry_buffer)
        
        # Bearish breakdown
        elif self._support > 0 and low < self._support - self.min_breakout_gap:
            self.n_structure.reset()
            self.n_structure.set_bearish(self._support, self.entry_buffer)
    
    def _check_pullback(self, high: float, low: float, close: float,
                        timestamp: datetime):
        """Check for pullback after breakout."""
        if self.n_structure.direction == SignalDirection.BULLISH:
            # Look for pullback (price coming down)
            self.n_structure.update_pullback(low, timestamp)
            # If price bounced and made higher high, move to validation
            if self.n_structure.pullback_low > 0 and high > self.n_structure.breakout_high:
                self.n_structure.state = NStructureState.VALIDATION
        
        elif self.n_structure.direction == SignalDirection.BEARISH:
            # Look for bounce (price going up)
            self.n_structure.update_pullback(high, timestamp)
            # If price dropped and made lower low, move to validation
            if self.n_structure.pullback_high > 0 and low < self.n_structure.breakdown_low:
                self.n_structure.state = NStructureState.VALIDATION
    
    def _check_validation(self, high: float, low: float, close: float,
                          timestamp: datetime):
        """Check for pattern validation (HL2 or LH2)."""
        self.n_structure.update_pullback(low if self.n_structure.direction == SignalDirection.BULLISH else high, timestamp)
        
        # Try to validate
        if self.n_structure.validate(self.min_hl_gap):
            self.n_structure.formation_time = timestamp
    
    def reset(self):
        """Reset detector for new pattern detection."""
        self.n_structure.reset()
        self._highs.clear()
        self._lows.clear()
        self._resistance = 0.0
        self._support = 0.0
    
    @property
    def current_structure(self) -> NStructure:
        """Get current N-Structure state."""
        return self.n_structure
    
    @property
    def is_ready(self) -> bool:
        """Check if N-Structure is ready for entry."""
        return self.n_structure.is_valid and self.n_structure.state == NStructureState.READY
