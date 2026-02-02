"""
Stop Loss Manager Module v1.1

Manages bot-side stop loss with N-Structure v1.1 trailing logic.

Trailing Strategy (Structure-Based):
1. Initial SL: Entry - 10 points (room to breathe)
2. Breakeven: When profit >= 8 points, move SL to entry
3. Structure TSL: After 2+ HLs, trail to HL[-2] - buffer (structure first)
4. Tight Trail: After +20 points profit, use tighter buffer
5. SL Breath: Allow 1 candle below SL if structure intact

Since Angel One's ROBO orders may be blocked, this module manages
SL as a separate order that gets modified as trailing progresses.
"""

from datetime import datetime
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from execution.order_manager import OrderManager, OrderStatus, get_order_manager
from data.candle_builder import Candle
from core.state_store import StateStore, get_state_store


class SLStatus(Enum):
    """Stop Loss status."""
    INITIAL = "initial"           # At initial SL level
    SAFE_MODE = "safe_mode"       # v2.0: SL moved to Entry + buffer
    TRAIL_MODE = "trail_mode"     # v2.0: Trailing based on highest price
    BREAKEVEN = "breakeven"       # Moved to breakeven
    STRUCTURE_TSL = "structure"   # Structure-based trailing
    TIGHT_TRAIL = "tight"         # Tight trail (big profit)
    TRIGGERED = "triggered"       # SL hit
    CANCELLED = "cancelled"       # SL cancelled (exit)


class TSLPhase(Enum):
    """Trailing Stop Loss phases."""
    PHASE_1_INITIAL = "initial"       # Waiting for safe mode
    PHASE_1_SAFE = "safe_mode"        # v2.0: Safe mode active (+7pt, SL to Entry+1)
    PHASE_2_TRAIL = "trail_mode"      # v2.0: Trail mode active (+10pt)
    PHASE_2_BREAKEVEN = "breakeven"   # At breakeven, waiting for structure
    PHASE_3_STRUCTURE = "structure"   # Structure-based trailing active
    PHASE_4_TIGHT = "tight"           # Tight trail after big profit


@dataclass
class SwingLow:
    """Tracked swing low (Higher Low) for TSL."""
    price: float
    timestamp: datetime
    candle_idx: int


@dataclass
class SLState:
    """Stop Loss state tracking."""
    entry_price: float
    initial_sl: float
    current_sl: float
    sl_order_id: str
    status: SLStatus = SLStatus.INITIAL
    tsl_phase: TSLPhase = TSLPhase.PHASE_1_INITIAL
    
    # Tracking
    breakeven_hit: bool = False
    last_trail_candle_time: Optional[datetime] = None
    trail_count: int = 0
    
    # Structure tracking
    swing_lows: List[SwingLow] = field(default_factory=list)
    highest_price: float = 0.0
    
    # SL Breath tracking
    breath_used: bool = False
    breath_candle_time: Optional[datetime] = None
    
    @property
    def risk_points(self) -> float:
        """Get current risk in points."""
        return self.entry_price - self.current_sl
    
    @property
    def locked_profit(self) -> float:
        """Get locked profit (if SL above entry)."""
        if self.current_sl > self.entry_price:
            return self.current_sl - self.entry_price
        return 0.0
    
    @property
    def current_profit(self) -> float:
        """Get current profit from highest price."""
        return self.highest_price - self.entry_price if self.highest_price > 0 else 0.0


# Type for SL trigger callback
SLTriggerCallback = Callable[[float, str], None]  # (exit_price, reason)


class StopLossManager:
    """
    Bot-managed Stop Loss with N-Structure v2.0 Sniper Mode Trailing.
    
    Strategy v2.0 Sniper Mode:
    1. Place initial SL at entry - 5 points (tight SL)
    2. Safe Mode: At +7pt profit, SL → Entry + 1pt (lock small profit)
    3. Trail Mode: At +10pt profit, TSL = Highest - 5pt (trail the high)
    4. Update TSL on every new high
    
    Legacy Structure TSL (optional):
    - Track swing lows (HLs) during trade
    - Trail to HL[-2] minus buffer
    """
    
    # Configuration constants (v2.0 Sniper Mode)
    DEFAULT_INITIAL_SL = 5.0          # v2.0: 5 point SL (tight)
    DEFAULT_SAFE_MODE_TRIGGER = 7.0   # v2.0: Safe mode at +7pt
    DEFAULT_SAFE_MODE_BUFFER = 1.0    # v2.0: SL = Entry + 1pt in safe mode
    DEFAULT_TRAIL_MODE_TRIGGER = 10.0 # v2.0: Trail mode at +10pt
    DEFAULT_TRAIL_MODE_BUFFER = 5.0   # v2.0: TSL = High - 5pt
    
    # Legacy constants
    DEFAULT_BREAKEVEN_TRIGGER = 7.0   # Maps to safe mode
    DEFAULT_TSL_BUFFER = 2.5          # Buffer below swing low
    DEFAULT_TIGHT_TRIGGER = 20.0      # Tight trail at +20 points
    DEFAULT_TIGHT_BUFFER = 1.5        # Tighter buffer
    DEFAULT_BREATH_RANGE = 3.0        # Max breach allowed for breath
    
    def __init__(
        self,
        order_manager: Optional[OrderManager] = None,
        state_store: Optional[StateStore] = None,
        initial_sl_points: float = DEFAULT_INITIAL_SL,
        breakeven_trigger_points: float = DEFAULT_BREAKEVEN_TRIGGER,
        tsl_buffer: float = DEFAULT_TSL_BUFFER,
        tight_trigger_points: float = DEFAULT_TIGHT_TRIGGER,
        tight_buffer: float = DEFAULT_TIGHT_BUFFER,
        breath_range: float = DEFAULT_BREATH_RANGE,
        enable_breath_rule: bool = True,
        # v2.0 Sniper Mode parameters
        safe_mode_trigger: float = DEFAULT_SAFE_MODE_TRIGGER,
        safe_mode_buffer: float = DEFAULT_SAFE_MODE_BUFFER,
        trail_mode_trigger: float = DEFAULT_TRAIL_MODE_TRIGGER,
        trail_mode_buffer: float = DEFAULT_TRAIL_MODE_BUFFER,
        enable_sniper_mode: bool = True  # v2.0: Enable sniper mode by default
    ):
        """
        Initialize SL manager with v2.0 Sniper Mode configuration.
        
        Args:
            order_manager: Order manager for placing/modifying orders
            state_store: State persistence store
            initial_sl_points: Points below entry for initial SL (default: 5)
            safe_mode_trigger: Profit points to trigger safe mode (default: 7)
            safe_mode_buffer: Buffer above entry in safe mode (default: 1)
            trail_mode_trigger: Profit to activate trail mode (default: 10)
            trail_mode_buffer: Buffer below high in trail mode (default: 5)
            enable_sniper_mode: Use v2.0 sniper mode TSL (default: True)
        """
        self._order_manager = order_manager or get_order_manager()
        self._store = state_store or get_state_store()
        
        # v2.0 Sniper Mode Configuration
        self.initial_sl_points = initial_sl_points
        self.safe_mode_trigger = safe_mode_trigger
        self.safe_mode_buffer = safe_mode_buffer
        self.trail_mode_trigger = trail_mode_trigger
        self.trail_mode_buffer = trail_mode_buffer
        self.enable_sniper_mode = enable_sniper_mode
        
        # Legacy configuration
        self.breakeven_trigger_points = breakeven_trigger_points
        self.tsl_buffer = tsl_buffer
        self.tight_trigger_points = tight_trigger_points
        self.tight_buffer = tight_buffer
        self.breath_range = breath_range
        self.enable_breath_rule = enable_breath_rule
        
        # State
        self._state: Optional[SLState] = None
        self._symbol: str = ""
        self._token: str = ""
        self._exchange: str = ""
        self._quantity: int = 0
        self._candle_idx: int = 0
        
        # Callbacks
        self._on_trigger_callbacks: list[SLTriggerCallback] = []
        
    def initialize_sl(
        self,
        symbol: str,
        token: str,
        exchange: str,
        quantity: int,
        entry_price: float,
        initial_swing_lows: Optional[List[SwingLow]] = None
    ) -> Optional[str]:
        """
        Initialize and place initial SL order.
        
        Args:
            symbol: Trading symbol
            token: Instrument token
            exchange: Exchange
            quantity: Position quantity
            entry_price: Entry price
            initial_swing_lows: Pre-entry swing lows from N-Structure
            
        Returns:
            SL order ID if successful, None otherwise
        """
        self._symbol = symbol
        self._token = token
        self._exchange = exchange
        self._quantity = quantity
        
        initial_sl = entry_price - self.initial_sl_points
        
        # Place SL order
        response = self._order_manager.place_sl_order(
            symbol=symbol,
            token=token,
            exchange=exchange,
            quantity=quantity,
            trigger_price=initial_sl
        )
        
        if not response.success:
            logger.error(f"Failed to place SL order: {response.message}")
            return None
        
        self._state = SLState(
            entry_price=entry_price,
            initial_sl=initial_sl,
            current_sl=initial_sl,
            sl_order_id=response.order_id,
            status=SLStatus.INITIAL,
            tsl_phase=TSLPhase.PHASE_1_INITIAL,
            highest_price=entry_price,
            swing_lows=initial_swing_lows or []
        )
        
        # Persist state
        self._persist_state()
        
        logger.success(
            f"SL initialized: Entry={entry_price:.2f}, "
            f"SL={initial_sl:.2f} (-{self.initial_sl_points}pt), "
            f"OrderID={response.order_id}"
        )
        
        return response.order_id
    
    def _persist_state(self) -> None:
        """Persist SL state to store."""
        if self._state:
            self._store.update_trade_sl(
                new_sl=self._state.current_sl,
                sl_order_id=self._state.sl_order_id,
                trailing_active=self._state.status in [
                    SLStatus.STRUCTURE_TSL, SLStatus.TIGHT_TRAIL
                ],
                breakeven_hit=self._state.breakeven_hit
            )
    
    def update_highest_price(self, price: float) -> None:
        """Track highest price during trade."""
        if self._state and price > self._state.highest_price:
            self._state.highest_price = price
    
    def add_swing_low(self, price: float, timestamp: datetime) -> None:
        """
        Add a new swing low (Higher Low) detected during trade.
        
        Args:
            price: Swing low price
            timestamp: Time of swing low
        """
        if not self._state:
            return
            
        self._candle_idx += 1
        swing_low = SwingLow(
            price=price,
            timestamp=timestamp,
            candle_idx=self._candle_idx
        )
        self._state.swing_lows.append(swing_low)
        
        logger.debug(
            f"Swing low added: {price:.2f} at {timestamp}, "
            f"total HLs: {len(self._state.swing_lows)}"
        )
        
        # Check if we can activate structure TSL
        self._check_structure_tsl_activation()
    
    def _check_structure_tsl_activation(self) -> None:
        """Check and activate structure-based TSL if conditions met."""
        if not self._state:
            return
            
        # Need at least 2 swing lows for structure TSL
        if len(self._state.swing_lows) < 2:
            return
            
        # Must be at breakeven or later (but v1.1 says structure-first!)
        # So we can activate even before breakeven if structure is there
        if self._state.tsl_phase == TSLPhase.PHASE_1_INITIAL:
            # Structure-first: activate TSL even without breakeven
            self._state.tsl_phase = TSLPhase.PHASE_3_STRUCTURE
            self._state.status = SLStatus.STRUCTURE_TSL
            logger.info(
                f"Structure TSL activated! {len(self._state.swing_lows)} HLs tracked"
            )
    
    def check_breakeven(self, current_price: float) -> bool:
        """
        Check and apply v2.0 Sniper Mode trailing (Safe Mode → Trail Mode).
        
        v2.0 Sniper Mode Logic:
        1. Safe Mode: At +7pt profit → SL = Entry + 1pt
        2. Trail Mode: At +10pt profit → TSL = Highest - 5pt
        
        Falls back to legacy breakeven if sniper mode disabled.
        
        Args:
            current_price: Current market price
            
        Returns:
            True if SL was modified
        """
        if not self._state:
            return False
        
        self.update_highest_price(current_price)
        profit = current_price - self._state.entry_price
        
        # v2.0 Sniper Mode
        if self.enable_sniper_mode:
            return self._check_sniper_mode_trail(current_price, profit)
        
        # Legacy breakeven logic
        return self._check_legacy_breakeven(current_price, profit)
    
    def _check_sniper_mode_trail(self, current_price: float, profit: float) -> bool:
        """
        v2.0 Sniper Mode trailing logic.
        
        Phase 1 (Safe Mode): At +7pt → SL = Entry + 1pt
        Phase 2 (Trail Mode): At +10pt → TSL = Highest - 5pt
        
        Returns:
            True if SL was modified
        """
        modified = False
        
        # Check Trail Mode first (higher priority)
        if profit >= self.trail_mode_trigger:
            # Trail Mode: TSL = Highest - buffer
            new_sl = self._state.highest_price - self.trail_mode_buffer
            
            # Only trail up, never down
            if new_sl > self._state.current_sl:
                success = self._modify_sl(new_sl)
                if success:
                    if self._state.tsl_phase != TSLPhase.PHASE_2_TRAIL:
                        logger.success(
                            f"🎯 TRAIL MODE activated! +{profit:.1f}pt profit | "
                            f"TSL = High({self._state.highest_price:.2f}) - {self.trail_mode_buffer}pt = {new_sl:.2f}"
                        )
                    else:
                        logger.info(
                            f"📈 TSL trailed: {new_sl:.2f} | High={self._state.highest_price:.2f} | "
                            f"Profit: +{profit:.1f}pt"
                        )
                    
                    self._state.tsl_phase = TSLPhase.PHASE_2_TRAIL
                    self._state.status = SLStatus.TRAIL_MODE
                    self._persist_state()
                    modified = True
        
        # Check Safe Mode (if not yet in trail mode)
        elif profit >= self.safe_mode_trigger and self._state.tsl_phase == TSLPhase.PHASE_1_INITIAL:
            # Safe Mode: SL = Entry + buffer (lock small profit)
            new_sl = self._state.entry_price + self.safe_mode_buffer
            
            if new_sl > self._state.current_sl:
                success = self._modify_sl(new_sl)
                if success:
                    logger.success(
                        f"🛡️ SAFE MODE activated! +{profit:.1f}pt profit | "
                        f"SL = Entry({self._state.entry_price:.2f}) + {self.safe_mode_buffer}pt = {new_sl:.2f}"
                    )
                    self._state.tsl_phase = TSLPhase.PHASE_1_SAFE
                    self._state.status = SLStatus.SAFE_MODE
                    self._state.breakeven_hit = True  # Treat as breakeven for compatibility
                    self._persist_state()
                    modified = True
        
        return modified
    
    def _check_legacy_breakeven(self, current_price: float, profit: float) -> bool:
        """Legacy breakeven logic (fallback if sniper mode disabled)."""
        if self._state.breakeven_hit:
            return False
        
        if profit >= self.breakeven_trigger_points:
            # Move SL to breakeven
            success = self._modify_sl(self._state.entry_price)
            
            if success:
                self._state.breakeven_hit = True
                
                # Update phase based on structure
                if len(self._state.swing_lows) >= 2:
                    self._state.tsl_phase = TSLPhase.PHASE_3_STRUCTURE
                    self._state.status = SLStatus.STRUCTURE_TSL
                else:
                    self._state.tsl_phase = TSLPhase.PHASE_2_BREAKEVEN
                    self._state.status = SLStatus.BREAKEVEN
                    
                self._persist_state()
                
                logger.success(
                    f"Breakeven hit! SL moved to {self._state.entry_price:.2f} "
                    f"(profit was {profit:.2f} pts)"
                )
                return True
                
        return False
    
    def trail_structure_based(self, current_price: float) -> bool:
        """
        Trail SL based on structure (swing lows / HLs).
        
        Logic (v1.1):
        - Use HL[-2] (second-last HL) for more breathing room
        - Apply buffer below the HL
        - Only trail if new SL is higher than current
        
        Args:
            current_price: Current market price
            
        Returns:
            True if SL was trailed
        """
        if not self._state:
            return False
            
        # Need at least 2 swing lows
        if len(self._state.swing_lows) < 2:
            return False
            
        # Must be in structure or tight phase
        if self._state.tsl_phase not in [
            TSLPhase.PHASE_3_STRUCTURE, TSLPhase.PHASE_4_TIGHT
        ]:
            return False
        
        self.update_highest_price(current_price)
        
        # Check for tight trail activation
        profit = current_price - self._state.entry_price
        if profit >= self.tight_trigger_points:
            if self._state.tsl_phase != TSLPhase.PHASE_4_TIGHT:
                self._state.tsl_phase = TSLPhase.PHASE_4_TIGHT
                self._state.status = SLStatus.TIGHT_TRAIL
                logger.info(f"Tight trail activated at +{profit:.2f} points profit")
        
        # Determine buffer based on phase
        buffer = (
            self.tight_buffer 
            if self._state.tsl_phase == TSLPhase.PHASE_4_TIGHT 
            else self.tsl_buffer
        )
        
        # Get HL[-2] (second-last) for more room
        # If only 2 HLs, use HL[-1] (last one)
        hl_idx = -2 if len(self._state.swing_lows) > 2 else -1
        reference_hl = self._state.swing_lows[hl_idx]
        new_sl = reference_hl.price - buffer
        
        # Check new SL is higher than current
        if new_sl <= self._state.current_sl:
            return False
        
        # Modify SL
        success = self._modify_sl(new_sl)
        
        if success:
            self._state.trail_count += 1
            self._persist_state()
            
            phase_name = "tight" if self._state.tsl_phase == TSLPhase.PHASE_4_TIGHT else "structure"
            logger.info(
                f"SL trailed ({phase_name}): {self._state.current_sl:.2f} "
                f"(HL[{hl_idx}]={reference_hl.price:.2f} - {buffer}pt buffer, "
                f"trail #{self._state.trail_count})"
            )
            return True
            
        return False
    
    def _modify_sl(self, new_sl: float) -> bool:
        """
        Modify SL order to new level.
        
        Args:
            new_sl: New SL price
            
        Returns:
            True if modification successful
        """
        if not self._state:
            return False
            
        response = self._order_manager.modify_order(
            order_id=self._state.sl_order_id,
            new_trigger_price=new_sl,
            new_price=new_sl - 0.5  # Limit slightly below trigger
        )
        
        if response.success:
            self._state.current_sl = new_sl
            return True
        else:
            logger.error(f"Failed to modify SL: {response.message}")
            return False
    
    def check_sl_triggered(
        self, 
        current_price: float,
        candle: Optional[Candle] = None
    ) -> tuple[bool, str]:
        """
        Check if SL has been triggered, with breath rule support.
        
        Args:
            current_price: Current market price
            candle: Current candle (for breath rule check)
            
        Returns:
            Tuple of (triggered, reason)
        """
        if not self._state:
            return False, ""
        
        # Check price hit SL
        if current_price <= self._state.current_sl:
            
            # SL Breath Rule (v1.1)
            if self.enable_breath_rule and candle and not self._state.breath_used:
                breach_amount = self._state.current_sl - current_price
                
                # Allow breath if within range and structure intact
                if breach_amount <= self.breath_range:
                    # Check if candle closes above SL (structure intact)
                    if candle.close > self._state.current_sl:
                        self._state.breath_used = True
                        self._state.breath_candle_time = candle.timestamp
                        
                        logger.warning(
                            f"SL Breath used! Price {current_price:.2f} breached SL "
                            f"{self._state.current_sl:.2f} by {breach_amount:.2f}pt "
                            f"but recovered. Continuing trade."
                        )
                        return False, ""
            
            # SL actually triggered
            self._state.status = SLStatus.TRIGGERED
            reason = "sl_hit"
            
            if self._state.breath_used:
                reason = "sl_hit_after_breath"
            
            # Notify callbacks
            for callback in self._on_trigger_callbacks:
                try:
                    callback(self._state.current_sl, reason)
                except Exception as e:
                    logger.error(f"SL trigger callback error: {e}")
            
            return True, reason
        
        # Also check order status
        status = self._order_manager.get_order_status(self._state.sl_order_id)
        if status == OrderStatus.COMPLETE:
            self._state.status = SLStatus.TRIGGERED
            return True, "sl_order_filled"
            
        return False, ""
    
    def cancel_sl(self) -> bool:
        """
        Cancel SL order (for manual exit or target hit).
        
        Returns:
            True if cancellation successful
        """
        if not self._state:
            return True
            
        response = self._order_manager.cancel_order(self._state.sl_order_id)
        
        if response.success:
            self._state.status = SLStatus.CANCELLED
            logger.info(f"SL order cancelled: {self._state.sl_order_id}")
            return True
        else:
            logger.warning(f"Failed to cancel SL: {response.message}")
            return False
    
    def update_on_tick(
        self,
        current_price: float,
        candle: Optional[Candle] = None,
        new_swing_low: Optional[float] = None,
        swing_low_time: Optional[datetime] = None
    ) -> tuple[SLStatus, str]:
        """
        Update SL state based on current price/candle.
        
        Args:
            current_price: Current market price
            candle: Current candle (for breath rule)
            new_swing_low: New swing low detected (if any)
            swing_low_time: Time of swing low
            
        Returns:
            Tuple of (current SL status, reason if triggered)
        """
        if not self._state:
            return SLStatus.CANCELLED, ""
        
        # Update highest price
        self.update_highest_price(current_price)
        
        # Add new swing low if detected
        if new_swing_low and swing_low_time:
            self.add_swing_low(new_swing_low, swing_low_time)
        
        # Check if triggered (with breath rule)
        triggered, reason = self.check_sl_triggered(current_price, candle)
        if triggered:
            return SLStatus.TRIGGERED, reason
        
        # Check for breakeven
        if not self._state.breakeven_hit:
            self.check_breakeven(current_price)
        
        # Check for structure-based trail
        if self._state.tsl_phase in [
            TSLPhase.PHASE_3_STRUCTURE, TSLPhase.PHASE_4_TIGHT
        ]:
            self.trail_structure_based(current_price)
        
        return self._state.status, ""
    
    def add_trigger_callback(self, callback: SLTriggerCallback) -> None:
        """Add callback for SL trigger."""
        self._on_trigger_callbacks.append(callback)
    
    def reset(self) -> None:
        """Reset SL manager state."""
        self._state = None
        self._symbol = ""
        self._token = ""
        self._exchange = ""
        self._quantity = 0
        self._candle_idx = 0
    
    def get_status_summary(self) -> dict:
        """Get summary of current SL state."""
        if not self._state:
            return {"active": False}
            
        return {
            "active": self.is_active,
            "entry_price": self._state.entry_price,
            "current_sl": self._state.current_sl,
            "status": self._state.status.value,
            "phase": self._state.tsl_phase.value,
            "breakeven_hit": self._state.breakeven_hit,
            "trail_count": self._state.trail_count,
            "swing_lows_count": len(self._state.swing_lows),
            "highest_price": self._state.highest_price,
            "current_profit": self._state.current_profit,
            "locked_profit": self._state.locked_profit,
            "breath_used": self._state.breath_used
        }
    
    @property
    def state(self) -> Optional[SLState]:
        """Get current SL state."""
        return self._state
    
    @property
    def current_sl(self) -> float:
        """Get current SL level."""
        return self._state.current_sl if self._state else 0.0
    
    @property
    def is_active(self) -> bool:
        """Check if SL is active."""
        return (
            self._state is not None and
            self._state.status not in [SLStatus.TRIGGERED, SLStatus.CANCELLED]
        )
    
    @property
    def is_trailing(self) -> bool:
        """Check if SL is in trailing mode."""
        return (
            self._state is not None and 
            self._state.status in [SLStatus.STRUCTURE_TSL, SLStatus.TIGHT_TRAIL]
        )
    
    @property
    def swing_lows(self) -> List[SwingLow]:
        """Get tracked swing lows."""
        return self._state.swing_lows if self._state else []


# Singleton instance
_sl_manager: Optional[StopLossManager] = None


def get_sl_manager(
    initial_sl_points: float = StopLossManager.DEFAULT_INITIAL_SL,
    breakeven_trigger_points: float = StopLossManager.DEFAULT_BREAKEVEN_TRIGGER,
    tsl_buffer: float = StopLossManager.DEFAULT_TSL_BUFFER,
    tight_trigger_points: float = StopLossManager.DEFAULT_TIGHT_TRIGGER,
    tight_buffer: float = StopLossManager.DEFAULT_TIGHT_BUFFER,
    enable_breath_rule: bool = True
) -> StopLossManager:
    """Get the global SL manager instance."""
    global _sl_manager
    if _sl_manager is None:
        _sl_manager = StopLossManager(
            initial_sl_points=initial_sl_points,
            breakeven_trigger_points=breakeven_trigger_points,
            tsl_buffer=tsl_buffer,
            tight_trigger_points=tight_trigger_points,
            tight_buffer=tight_buffer,
            enable_breath_rule=enable_breath_rule
        )
    return _sl_manager


def initialize_sl_manager(
    initial_sl_points: float = 5.0,       # v2.0: 5pt SL
    breakeven_trigger_points: float = 7.0,
    tsl_buffer: float = 2.5,
    tight_trigger_points: float = 20.0,
    tight_buffer: float = 1.5,
    enable_breath_rule: bool = True,
    # v2.0 Sniper Mode parameters
    safe_mode_trigger: float = 7.0,       # Safe mode at +7pt
    safe_mode_buffer: float = 1.0,        # SL = Entry + 1pt
    trail_mode_trigger: float = 10.0,     # Trail mode at +10pt
    trail_mode_buffer: float = 5.0,       # TSL = High - 5pt
    enable_sniper_mode: bool = True       # Enable sniper mode
) -> StopLossManager:
    """
    Initialize SL manager with v2.0 Sniper Mode configuration.
    
    v2.0 Sniper Mode:
    - Safe Mode: At +7pt → SL = Entry + 1pt
    - Trail Mode: At +10pt → TSL = High - 5pt
    
    Args:
        initial_sl_points: Points below entry for initial SL (default: 5)
        safe_mode_trigger: Profit to trigger safe mode (default: 7)
        safe_mode_buffer: Buffer above entry in safe mode (default: 1)
        trail_mode_trigger: Profit to activate trail mode (default: 10)
        trail_mode_buffer: Buffer below high in trail mode (default: 5)
        enable_sniper_mode: Use v2.0 sniper mode (default: True)
        
    Returns:
        Initialized StopLossManager
    """
    global _sl_manager
    _sl_manager = StopLossManager(
        initial_sl_points=initial_sl_points,
        breakeven_trigger_points=breakeven_trigger_points,
        tsl_buffer=tsl_buffer,
        tight_trigger_points=tight_trigger_points,
        tight_buffer=tight_buffer,
        enable_breath_rule=enable_breath_rule,
        safe_mode_trigger=safe_mode_trigger,
        safe_mode_buffer=safe_mode_buffer,
        trail_mode_trigger=trail_mode_trigger,
        trail_mode_buffer=trail_mode_buffer,
        enable_sniper_mode=enable_sniper_mode
    )
    return _sl_manager
