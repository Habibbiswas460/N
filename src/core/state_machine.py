"""State Machine Module v1.2

Finite State Machine (FSM) for N-Structure Trading with Re-entry Support.

States:
- IDLE: Waiting for market conditions
- WATCHING_BREAKOUT: Monitoring for resistance breakout
- TRACKING_PULLBACK: Breakout occurred, tracking pullback to EMA
- VALIDATING_HL: Identifying Higher Low pattern
- CHECKING_DIVERGENCE: Verifying Index vs Option divergence
- ARMED: N-Structure complete, waiting for entry trigger
- IN_POSITION: Trade active, managing position
- PENDING_REENTRY: SL hit, waiting for HH breakout re-entry opportunity
- COOLDOWN: Trade closed, waiting before next setup

Transitions are triggered by market events and pattern detection.
"""

from enum import Enum, auto
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field

from loguru import logger

from core.state_store import StateStore, get_state_store
from indicators.n_structure import NStructure, SetupStatus


class TradingState(Enum):
    """Trading FSM states."""
    IDLE = auto()
    WATCHING_BREAKOUT = auto()
    TRACKING_PULLBACK = auto()
    VALIDATING_HL = auto()
    CHECKING_DIVERGENCE = auto()
    ARMED = auto()
    IN_POSITION = auto()
    PENDING_REENTRY = auto()  # SL hit, waiting for HH re-entry
    COOLDOWN = auto()
    PAUSED = auto()  # Manual pause or circuit breaker
    ERROR = auto()   # Error state


@dataclass
class StateContext:
    """Context data passed between states."""
    # Market data
    index_price: float = 0.0
    option_price: float = 0.0
    
    # Pattern data
    n_structure: Optional[NStructure] = None
    entry_trigger_price: float = 0.0
    
    # Trade data
    entry_price: float = 0.0
    current_sl: float = 0.0
    position_pnl: float = 0.0
    
    # Timing
    last_state_change: datetime = field(default_factory=datetime.now)
    cooldown_until: Optional[datetime] = None
    
    # Flags
    divergence_confirmed: bool = False
    breakeven_hit: bool = False
    
    # Re-entry tracking (v1.2)
    sl_exit_price: float = 0.0           # Price where SL was hit
    sl_exit_time: Optional[datetime] = None
    reentry_hh_trigger: float = 0.0      # HH breakout trigger for re-entry
    reentry_count: int = 0                # Re-entries used today
    is_reentry_trade: bool = False        # Current trade is a re-entry
    last_high_after_sl: float = 0.0       # Track highest high after SL
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            'index_price': self.index_price,
            'option_price': self.option_price,
            'entry_trigger_price': self.entry_trigger_price,
            'entry_price': self.entry_price,
            'current_sl': self.current_sl,
            'position_pnl': self.position_pnl,
            'divergence_confirmed': self.divergence_confirmed,
            'breakeven_hit': self.breakeven_hit,
            'last_state_change': self.last_state_change.isoformat() if self.last_state_change else None,
            'cooldown_until': self.cooldown_until.isoformat() if self.cooldown_until else None,
            # Re-entry fields (v1.2)
            'sl_exit_price': self.sl_exit_price,
            'sl_exit_time': self.sl_exit_time.isoformat() if self.sl_exit_time else None,
            'reentry_hh_trigger': self.reentry_hh_trigger,
            'reentry_count': self.reentry_count,
            'is_reentry_trade': self.is_reentry_trade,
            'last_high_after_sl': self.last_high_after_sl,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateContext":
        """Create from dictionary."""
        ctx = cls()
        ctx.index_price = data.get('index_price', 0.0)
        ctx.option_price = data.get('option_price', 0.0)
        ctx.entry_trigger_price = data.get('entry_trigger_price', 0.0)
        ctx.entry_price = data.get('entry_price', 0.0)
        ctx.current_sl = data.get('current_sl', 0.0)
        ctx.position_pnl = data.get('position_pnl', 0.0)
        ctx.divergence_confirmed = data.get('divergence_confirmed', False)
        ctx.breakeven_hit = data.get('breakeven_hit', False)
        
        if data.get('last_state_change'):
            ctx.last_state_change = datetime.fromisoformat(data['last_state_change'])
        if data.get('cooldown_until'):
            ctx.cooldown_until = datetime.fromisoformat(data['cooldown_until'])
        
        # Re-entry fields (v1.2)
        ctx.sl_exit_price = data.get('sl_exit_price', 0.0)
        if data.get('sl_exit_time'):
            ctx.sl_exit_time = datetime.fromisoformat(data['sl_exit_time'])
        ctx.reentry_hh_trigger = data.get('reentry_hh_trigger', 0.0)
        ctx.reentry_count = data.get('reentry_count', 0)
        ctx.is_reentry_trade = data.get('is_reentry_trade', False)
        ctx.last_high_after_sl = data.get('last_high_after_sl', 0.0)
            
        return ctx


# Type alias for state transition callback
StateCallback = Callable[[TradingState, TradingState, StateContext], None]


class TradingStateMachine:
    """
    Finite State Machine for N-Structure Trading.
    
    Manages state transitions and persists state for crash recovery.
    """
    
    # Valid state transitions
    VALID_TRANSITIONS = {
        TradingState.IDLE: [
            TradingState.WATCHING_BREAKOUT,
            TradingState.ARMED,  # v2.0: Direct entry from DualDirectionDetector
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.WATCHING_BREAKOUT: [
            TradingState.TRACKING_PULLBACK,
            TradingState.ARMED,  # v2.0: Direct entry when pattern ready
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.TRACKING_PULLBACK: [
            TradingState.VALIDATING_HL,
            TradingState.WATCHING_BREAKOUT,
            TradingState.ARMED,  # v2.0: Direct entry when pattern ready
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.VALIDATING_HL: [
            TradingState.CHECKING_DIVERGENCE,
            TradingState.TRACKING_PULLBACK,
            TradingState.ARMED,  # v2.0: Direct entry when pattern ready
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.CHECKING_DIVERGENCE: [
            TradingState.ARMED,
            TradingState.VALIDATING_HL,
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.ARMED: [
            TradingState.IN_POSITION,
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.IN_POSITION: [
            TradingState.COOLDOWN,
            TradingState.PENDING_REENTRY,  # SL hit -> check for re-entry
            TradingState.ERROR
        ],
        TradingState.PENDING_REENTRY: [
            TradingState.ARMED,            # HH breakout -> re-enter
            TradingState.COOLDOWN,         # No re-entry opportunity
            TradingState.IDLE,             # Setup invalidated
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.COOLDOWN: [
            TradingState.IDLE,
            TradingState.PAUSED,
            TradingState.ERROR
        ],
        TradingState.PAUSED: [
            TradingState.IDLE,
            TradingState.ERROR
        ],
        TradingState.ERROR: [
            TradingState.IDLE,
            TradingState.PAUSED
        ]
    }
    
    def __init__(
        self,
        state_store: Optional[StateStore] = None,
        cooldown_seconds: int = 60,
        auto_persist: bool = True
    ):
        """
        Initialize state machine.
        
        Args:
            state_store: State persistence store
            cooldown_seconds: Seconds to wait after trade close
            auto_persist: Automatically persist state changes
        """
        self._store = state_store or get_state_store()
        self.cooldown_seconds = cooldown_seconds
        self.auto_persist = auto_persist
        
        self._state = TradingState.IDLE
        self._context = StateContext()
        self._callbacks: List[StateCallback] = []
        
        # Try to restore from persisted state
        self._restore_state()
        
    def _restore_state(self) -> None:
        """Restore state from persistence."""
        saved = self._store.get_fsm_state()
        
        if saved:
            try:
                state_name = saved.get('state', 'IDLE')
                self._state = TradingState[state_name]
                
                data = saved.get('data', {})
                if data:
                    self._context = StateContext.from_dict(data)
                    
                logger.info(f"Restored state: {self._state.name}")
                
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to restore state: {e}, starting fresh")
                self._state = TradingState.IDLE
                self._context = StateContext()
    
    def _persist_state(self) -> None:
        """Persist current state."""
        if self.auto_persist:
            self._store.save_fsm_state(
                state=self._state.name,
                data=self._context.to_dict()
            )
    
    def _notify_callbacks(self, old_state: TradingState, new_state: TradingState) -> None:
        """Notify registered callbacks of state change."""
        for callback in self._callbacks:
            try:
                callback(old_state, new_state, self._context)
            except Exception as e:
                logger.error(f"State callback error: {e}")
    
    def can_transition(self, to_state: TradingState) -> bool:
        """
        Check if transition to state is valid.
        
        Args:
            to_state: Target state
            
        Returns:
            True if transition is allowed
        """
        valid_targets = self.VALID_TRANSITIONS.get(self._state, [])
        return to_state in valid_targets
    
    def transition_to(
        self,
        new_state: TradingState,
        reason: str = "",
        force: bool = False
    ) -> bool:
        """
        Transition to a new state.
        
        Args:
            new_state: Target state
            reason: Reason for transition (for logging)
            force: Force transition even if not normally valid
            
        Returns:
            True if transition successful
        """
        if not force and not self.can_transition(new_state):
            logger.warning(
                f"Invalid transition: {self._state.name} -> {new_state.name}"
            )
            return False
        
        old_state = self._state
        self._state = new_state
        self._context.last_state_change = datetime.now()
        
        # Handle special state transitions
        if new_state == TradingState.COOLDOWN:
            self._context.cooldown_until = (
                datetime.now() + timedelta(seconds=self.cooldown_seconds)
            )
        elif new_state == TradingState.IDLE:
            # Reset context for new setup
            self._context = StateContext()
            
        logger.info(
            f"State transition: {old_state.name} -> {new_state.name}"
            + (f" ({reason})" if reason else "")
        )
        
        self._persist_state()
        self._notify_callbacks(old_state, new_state)
        
        return True
    
    def add_callback(self, callback: StateCallback) -> None:
        """Add state transition callback."""
        self._callbacks.append(callback)
        
    def remove_callback(self, callback: StateCallback) -> None:
        """Remove state transition callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    # === State-specific transitions ===
    
    def on_breakout_detected(self, breakout_high: float) -> bool:
        """
        Handle breakout detection.
        
        Args:
            breakout_high: The breakout price level
            
        Returns:
            True if transition successful
        """
        if self._state not in [TradingState.IDLE, TradingState.WATCHING_BREAKOUT]:
            return False
            
        self._context.entry_trigger_price = breakout_high
        return self.transition_to(
            TradingState.WATCHING_BREAKOUT,
            f"Breakout at {breakout_high:.2f}"
        )
    
    def on_pullback_started(self) -> bool:
        """Handle pullback to EMA detected."""
        return self.transition_to(
            TradingState.TRACKING_PULLBACK,
            "Pullback to EMA"
        )
    
    def on_hl_detected(self, hl_price: float) -> bool:
        """
        Handle Higher Low detection.
        
        Args:
            hl_price: Higher Low price
            
        Returns:
            True if transition successful
        """
        return self.transition_to(
            TradingState.VALIDATING_HL,
            f"HL at {hl_price:.2f}"
        )
    
    def on_structure_valid(self, n_structure: NStructure) -> bool:
        """
        Handle valid N-Structure.
        
        Args:
            n_structure: Complete N-Structure data
            
        Returns:
            True if transition successful
        """
        self._context.n_structure = n_structure
        return self.transition_to(
            TradingState.CHECKING_DIVERGENCE,
            "N-Structure valid"
        )
    
    def on_divergence_confirmed(self, entry_trigger: float) -> bool:
        """
        Handle divergence confirmation.
        
        Args:
            entry_trigger: Entry trigger price (high + buffer)
            
        Returns:
            True if transition successful
        """
        self._context.divergence_confirmed = True
        self._context.entry_trigger_price = entry_trigger
        return self.transition_to(
            TradingState.ARMED,
            f"Armed for entry at {entry_trigger:.2f}"
        )
    
    def on_entry_triggered(self, entry_price: float, initial_sl: float) -> bool:
        """
        Handle entry execution.
        
        Args:
            entry_price: Actual entry price
            initial_sl: Initial stop loss level
            
        Returns:
            True if transition successful
        """
        self._context.entry_price = entry_price
        self._context.current_sl = initial_sl
        return self.transition_to(
            TradingState.IN_POSITION,
            f"Entered at {entry_price:.2f}, SL: {initial_sl:.2f}"
        )
    
    def on_exit(self, exit_price: float, reason: str = "") -> bool:
        """
        Handle position exit.
        
        Args:
            exit_price: Exit price
            reason: Exit reason
            
        Returns:
            True if transition successful
        """
        pnl = exit_price - self._context.entry_price
        self._context.position_pnl = pnl
        
        return self.transition_to(
            TradingState.COOLDOWN,
            f"Exit at {exit_price:.2f}, PnL: {pnl:.2f} ({reason})"
        )
    
    def on_sl_hit(
        self, 
        exit_price: float, 
        can_reenter: bool = True,
        max_reentries: int = 2
    ) -> bool:
        """
        Handle SL hit with re-entry consideration.
        
        Args:
            exit_price: Exit price (SL level)
            can_reenter: Whether re-entry is allowed
            max_reentries: Maximum re-entries per day
            
        Returns:
            True if transition successful
        """
        pnl = exit_price - self._context.entry_price
        self._context.position_pnl = pnl
        self._context.sl_exit_price = exit_price
        self._context.sl_exit_time = datetime.now()
        
        # Check if re-entry is possible
        if can_reenter and self._context.reentry_count < max_reentries:
            # Go to PENDING_REENTRY to look for HH breakout
            return self.transition_to(
                TradingState.PENDING_REENTRY,
                f"SL hit at {exit_price:.2f}, watching for re-entry opportunity"
            )
        else:
            # No re-entry allowed, go to cooldown
            reason = "max reentries reached" if self._context.reentry_count >= max_reentries else "re-entry disabled"
            return self.transition_to(
                TradingState.COOLDOWN,
                f"SL hit at {exit_price:.2f}, {reason}"
            )
    
    def on_reentry_hh_detected(self, hh_price: float, entry_trigger: float) -> bool:
        """
        Handle Higher High detection for re-entry.
        
        Args:
            hh_price: Higher High price
            entry_trigger: Entry trigger price (HH + buffer)
            
        Returns:
            True if transition successful
        """
        if self._state != TradingState.PENDING_REENTRY:
            return False
        
        # HH must be above SL exit price
        min_hh_gap = 2.0  # Minimum gap above SL exit
        if hh_price <= self._context.sl_exit_price + min_hh_gap:
            logger.debug(
                f"HH {hh_price:.2f} not high enough above SL exit "
                f"{self._context.sl_exit_price:.2f}"
            )
            return False
        
        self._context.reentry_hh_trigger = entry_trigger
        self._context.is_reentry_trade = True
        self._context.entry_trigger_price = entry_trigger
        
        return self.transition_to(
            TradingState.ARMED,
            f"Re-entry armed at HH={hh_price:.2f}, trigger={entry_trigger:.2f}"
        )
    
    def on_reentry_executed(self, entry_price: float, initial_sl: float) -> bool:
        """
        Handle re-entry execution.
        
        Args:
            entry_price: Re-entry price
            initial_sl: Initial stop loss
            
        Returns:
            True if transition successful
        """
        self._context.entry_price = entry_price
        self._context.current_sl = initial_sl
        self._context.reentry_count += 1
        
        return self.transition_to(
            TradingState.IN_POSITION,
            f"Re-entry #{self._context.reentry_count} at {entry_price:.2f}, SL: {initial_sl:.2f}"
        )
    
    def on_reentry_timeout(self, candles_waited: int = 30) -> bool:
        """
        Handle re-entry timeout (no HH breakout found).
        
        Args:
            candles_waited: Number of candles waited
            
        Returns:
            True if transition successful
        """
        if self._state != TradingState.PENDING_REENTRY:
            return False
        
        self._context.is_reentry_trade = False
        
        return self.transition_to(
            TradingState.COOLDOWN,
            f"Re-entry timeout after {candles_waited} candles"
        )
    
    def update_high_after_sl(self, high: float) -> None:
        """
        Update highest high seen after SL hit (for HH detection).
        
        Args:
            high: Candle high
        """
        if self._state == TradingState.PENDING_REENTRY:
            if high > self._context.last_high_after_sl:
                self._context.last_high_after_sl = high
    
    def on_setup_invalidated(self, reason: str = "") -> bool:
        """Handle setup invalidation (e.g., close below EMA)."""
        return self.transition_to(
            TradingState.IDLE,
            f"Setup invalidated: {reason}"
        )
    
    def on_cooldown_complete(self) -> bool:
        """Handle cooldown period completion."""
        if self._state != TradingState.COOLDOWN:
            return False
            
        if self._context.cooldown_until and datetime.now() >= self._context.cooldown_until:
            return self.transition_to(TradingState.IDLE, "Cooldown complete")
            
        return False
    
    def pause(self, reason: str = "Manual pause") -> bool:
        """Pause trading."""
        return self.transition_to(TradingState.PAUSED, reason)
    
    def resume(self) -> bool:
        """Resume trading from pause."""
        if self._state == TradingState.PAUSED:
            return self.transition_to(TradingState.IDLE, "Resumed")
        return False
    
    def error(self, reason: str = "Unknown error") -> bool:
        """Enter error state."""
        return self.transition_to(TradingState.ERROR, reason, force=True)
    
    def reset(self) -> None:
        """Full reset to IDLE."""
        self._state = TradingState.IDLE
        self._context = StateContext()
        self._persist_state()
        self._store.clear_n_structure()
        logger.info("State machine reset")
    
    # === Properties ===
    
    @property
    def state(self) -> TradingState:
        """Get current state."""
        return self._state
    
    @property
    def context(self) -> StateContext:
        """Get current context."""
        return self._context
    
    @property
    def is_idle(self) -> bool:
        """Check if in IDLE state."""
        return self._state == TradingState.IDLE
    
    @property
    def is_in_position(self) -> bool:
        """Check if in active position."""
        return self._state == TradingState.IN_POSITION
    
    @property
    def is_armed(self) -> bool:
        """Check if armed for entry."""
        return self._state == TradingState.ARMED
    
    @property
    def is_active(self) -> bool:
        """Check if actively monitoring (not paused/error)."""
        return self._state not in [TradingState.PAUSED, TradingState.ERROR]
    
    @property
    def can_enter_trade(self) -> bool:
        """Check if can enter a new trade."""
        return self._state == TradingState.ARMED
    
    @property
    def is_pending_reentry(self) -> bool:
        """Check if waiting for re-entry opportunity."""
        return self._state == TradingState.PENDING_REENTRY
    
    @property
    def pending_entry(self) -> bool:
        """Check if entry is pending (ARMED with valid trigger)."""
        return (
            self._state == TradingState.ARMED and 
            self._context.entry_trigger_price > 0
        )
    
    @property
    def is_reentry_trade(self) -> bool:
        """Check if current/next trade is a re-entry."""
        return self._context.is_reentry_trade
    
    @property
    def reentry_count(self) -> int:
        """Get re-entry count for today."""
        return self._context.reentry_count
    
    @property
    def time_in_state(self) -> timedelta:
        """Get time in current state."""
        return datetime.now() - self._context.last_state_change
    
    def update_context(self, **kwargs) -> None:
        """
        Update context values.
        
        Args:
            **kwargs: Context field updates
        """
        for key, value in kwargs.items():
            if hasattr(self._context, key):
                setattr(self._context, key, value)
        self._persist_state()
    
    async def process_candle(
        self,
        index_candle,
        option_candle,
        ema_9: float,
        ema_15: float,
        n_structure=None
    ) -> None:
        """
        Process a new candle and handle state transitions.
        
        This is the main entry point for candle-based state updates.
        
        Args:
            index_candle: NIFTY index candle
            option_candle: Option candle
            ema_9: EMA 9 value
            ema_15: EMA 15 value
            n_structure: Detected N-Structure pattern (if any)
        """
        # Update context with latest prices
        self._context.index_price = index_candle.close
        self._context.option_price = option_candle.close
        
        # v2.0: PRIORITY - Universal READY_FOR_ENTRY handling (DualDirectionDetector)
        # Check this FIRST before any legacy state-based processing
        if (self._state not in [TradingState.ARMED, TradingState.IN_POSITION, TradingState.PENDING_REENTRY] and
            n_structure and n_structure.divergence_confirmed and n_structure.entry_trigger):
            self._context.entry_trigger_price = n_structure.entry_trigger
            self._context.divergence_confirmed = True
            self._context.n_structure = n_structure
            self.transition_to(
                TradingState.ARMED,
                f"N-Structure READY! Entry trigger: {n_structure.entry_trigger:.2f}"
            )
            self._persist_state()
            return  # Exit early - armed for entry
        
        # Handle states based on N-Structure detection (legacy path)
        if self._state == TradingState.IDLE:
            # Legacy: step-by-step detection
            if n_structure and n_structure.status.value == "watching_breakout":
                self.transition_to(
                    TradingState.WATCHING_BREAKOUT,
                    f"Breakout detected at {n_structure.breakout_high:.2f}"
                )
                
        elif self._state == TradingState.WATCHING_BREAKOUT:
            # Check for pullback to EMA
            if option_candle.low <= ema_9 and option_candle.close > ema_15:
                self.transition_to(
                    TradingState.TRACKING_PULLBACK,
                    f"Pullback to EMA, close={option_candle.close:.2f}"
                )
            elif option_candle.close < ema_15:
                self.on_setup_invalidated("Close below EMA15")
                
        elif self._state == TradingState.TRACKING_PULLBACK:
            # Check for Higher Low formation
            if n_structure and n_structure.hl1 is not None:
                self.transition_to(
                    TradingState.VALIDATING_HL,
                    f"HL1 detected at {n_structure.hl1.price:.2f}"
                )
            elif option_candle.close < ema_15:
                self.on_setup_invalidated("Close below EMA15")
                
        elif self._state == TradingState.VALIDATING_HL:
            # Check for second Higher Low and divergence
            if n_structure and n_structure.is_valid:
                self.transition_to(
                    TradingState.CHECKING_DIVERGENCE,
                    f"HL2 confirmed at {n_structure.hl2.price:.2f}"
                )
            elif option_candle.close < ema_15:
                self.on_setup_invalidated("Close below EMA15")
                
        elif self._state == TradingState.CHECKING_DIVERGENCE:
            # Check for divergence confirmation
            if n_structure and n_structure.divergence_confirmed:
                self._context.entry_trigger_price = n_structure.entry_trigger
                self._context.divergence_confirmed = True
                self.transition_to(
                    TradingState.ARMED,
                    f"Divergence confirmed, trigger={n_structure.entry_trigger:.2f}"
                )
            elif option_candle.close < ema_15:
                self.on_setup_invalidated("Close below EMA15")
                
        elif self._state == TradingState.COOLDOWN:
            # Check if cooldown is complete
            self.on_cooldown_complete()
        
        # Persist any context changes
        self._persist_state()


# Singleton instance
_fsm_instance: Optional[TradingStateMachine] = None


def get_trading_fsm() -> TradingStateMachine:
    """Get the global FSM instance."""
    global _fsm_instance
    if _fsm_instance is None:
        _fsm_instance = TradingStateMachine()
    return _fsm_instance


def initialize_fsm(
    state_store: Optional[StateStore] = None,
    **kwargs
) -> TradingStateMachine:
    """
    Initialize the global FSM with custom settings.
    
    Args:
        state_store: State persistence store
        **kwargs: Additional FSM parameters
        
    Returns:
        Initialized TradingStateMachine
    """
    global _fsm_instance
    _fsm_instance = TradingStateMachine(state_store=state_store, **kwargs)
    return _fsm_instance
