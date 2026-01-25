"""
Position Reconciler Module

Verifies bot state matches broker state.
Detects and alerts on position mismatches.
"""

import time
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

from loguru import logger

from broker.auth import AngelOneAuth, get_auth


@dataclass
class Position:
    """Position data container."""
    symbol: str
    token: str
    exchange: str
    product_type: str
    quantity: int
    average_price: float
    ltp: float
    pnl: float
    realized_pnl: float
    unrealized_pnl: float
    
    @classmethod
    def from_api_data(cls, data: Dict[str, Any]) -> "Position":
        """Create from API position data."""
        return cls(
            symbol=data.get("tradingsymbol", ""),
            token=data.get("symboltoken", ""),
            exchange=data.get("exchange", ""),
            product_type=data.get("producttype", ""),
            quantity=int(data.get("netqty", 0)),
            average_price=float(data.get("averageprice", 0)),
            ltp=float(data.get("ltp", 0)),
            pnl=float(data.get("pnl", 0)),
            realized_pnl=float(data.get("realised", 0)),
            unrealized_pnl=float(data.get("unrealised", 0))
        )
    
    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0
    
    @property
    def is_flat(self) -> bool:
        """Check if position is flat."""
        return self.quantity == 0


@dataclass
class ReconciliationResult:
    """Result of position reconciliation."""
    is_matched: bool
    bot_position_qty: int
    broker_position_qty: int
    mismatch_reason: str = ""
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


# Callback type for mismatch alerts
MismatchCallback = Callable[[ReconciliationResult], None]


class PositionReconciler:
    """
    Reconciles bot state with broker positions.
    
    Features:
    - Periodic position polling
    - Mismatch detection
    - Alert callbacks
    - Rate limit compliance (1 req/sec for position API)
    """
    
    POLL_INTERVAL = 5.0  # Seconds between polls (respecting rate limits)
    
    def __init__(
        self,
        auth: Optional[AngelOneAuth] = None,
        poll_interval: float = 5.0,
        paper_mode: bool = False
    ):
        """
        Initialize position reconciler.
        
        Args:
            auth: Angel One authentication instance
            poll_interval: Seconds between position checks
            paper_mode: Skip actual API calls
        """
        self._auth = auth or get_auth()
        self.poll_interval = poll_interval
        self.paper_mode = paper_mode
        
        self._last_poll_time: float = 0
        self._bot_position_qty: int = 0
        self._bot_position_token: str = ""
        self._mismatch_callbacks: List[MismatchCallback] = []
        self._last_result: Optional[ReconciliationResult] = None
        
    def _get_smart_api(self):
        """Get SmartConnect instance."""
        if not self._auth.is_logged_in:
            self._auth.ensure_valid_session()
        return self._auth.smart_api
    
    def set_bot_position(self, token: str, quantity: int) -> None:
        """
        Set the bot's expected position.
        
        Args:
            token: Position token
            quantity: Expected quantity
        """
        self._bot_position_token = token
        self._bot_position_qty = quantity
        logger.debug(f"Bot position set: {token} qty={quantity}")
    
    def clear_bot_position(self) -> None:
        """Clear bot position (flat)."""
        self._bot_position_token = ""
        self._bot_position_qty = 0
    
    def get_broker_positions(self) -> List[Position]:
        """
        Get all positions from broker.
        
        Returns:
            List of Position objects
        """
        if self.paper_mode:
            return []
        
        try:
            api = self._get_smart_api()
            response = api.position()
            
            if response.get("status"):
                positions_data = response.get("data", []) or []
                return [Position.from_api_data(p) for p in positions_data]
            else:
                logger.warning(f"Get positions failed: {response.get('message')}")
                
        except Exception as e:
            logger.error(f"Get positions exception: {e}")
        
        return []
    
    def get_position_for_token(self, token: str) -> Optional[Position]:
        """
        Get position for a specific token.
        
        Args:
            token: Instrument token
            
        Returns:
            Position if found
        """
        positions = self.get_broker_positions()
        
        for pos in positions:
            if pos.token == token:
                return pos
        
        return None
    
    def reconcile(self) -> ReconciliationResult:
        """
        Reconcile bot position with broker.
        
        Returns:
            ReconciliationResult
        """
        if self.paper_mode:
            return ReconciliationResult(
                is_matched=True,
                bot_position_qty=self._bot_position_qty,
                broker_position_qty=self._bot_position_qty,
                mismatch_reason="Paper mode"
            )
        
        # Respect rate limit
        elapsed = time.time() - self._last_poll_time
        if elapsed < self.poll_interval:
            time.sleep(self.poll_interval - elapsed)
        self._last_poll_time = time.time()
        
        # Get broker position
        broker_qty = 0
        if self._bot_position_token:
            position = self.get_position_for_token(self._bot_position_token)
            if position:
                broker_qty = position.quantity
        
        # Compare
        is_matched = (self._bot_position_qty == broker_qty)
        
        result = ReconciliationResult(
            is_matched=is_matched,
            bot_position_qty=self._bot_position_qty,
            broker_position_qty=broker_qty
        )
        
        if not is_matched:
            result.mismatch_reason = (
                f"Position mismatch: Bot={self._bot_position_qty}, "
                f"Broker={broker_qty}"
            )
            logger.error(result.mismatch_reason)
            
            # Trigger callbacks
            for callback in self._mismatch_callbacks:
                try:
                    callback(result)
                except Exception as e:
                    logger.error(f"Mismatch callback error: {e}")
        
        self._last_result = result
        return result
    
    def get_net_position_value(self) -> float:
        """
        Get total P&L across all positions.
        
        Returns:
            Total P&L
        """
        positions = self.get_broker_positions()
        return sum(p.pnl for p in positions)
    
    def has_open_positions(self) -> bool:
        """
        Check if there are any open positions.
        
        Returns:
            True if open positions exist
        """
        positions = self.get_broker_positions()
        return any(not p.is_flat for p in positions)
    
    def add_mismatch_callback(self, callback: MismatchCallback) -> None:
        """Add mismatch alert callback."""
        self._mismatch_callbacks.append(callback)
    
    @property
    def last_result(self) -> Optional[ReconciliationResult]:
        """Get last reconciliation result."""
        return self._last_result
    
    @property
    def is_synced(self) -> bool:
        """Check if last reconciliation was matched."""
        return self._last_result is not None and self._last_result.is_matched


# Singleton instance
_reconciler: Optional[PositionReconciler] = None


def get_position_reconciler(paper_mode: bool = False) -> PositionReconciler:
    """Get the global position reconciler instance."""
    global _reconciler
    if _reconciler is None:
        _reconciler = PositionReconciler(paper_mode=paper_mode)
    return _reconciler
