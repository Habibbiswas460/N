"""
Market Feed Module

WebSocket-based real-time data streaming for Index and Options.
Handles dual-stream subscription, heartbeat, and reconnection.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Callable, Dict, List, Any, Set
from dataclasses import dataclass, field
from enum import IntEnum
from collections import deque

from loguru import logger

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except ImportError:
    SmartWebSocketV2 = None
    logger.warning("SmartApi WebSocket not available - using mock for testing")


class SubscriptionMode(IntEnum):
    """WebSocket subscription modes."""
    LTP = 1      # Last Traded Price only (51 bytes)
    QUOTE = 2    # LTP + OHLC + Volume (123 bytes)
    SNAP = 3     # Full depth data (379 bytes)


class ExchangeType(IntEnum):
    """Exchange type codes for Angel One."""
    NSE_CM = 1   # NSE Cash Market (Index)
    NSE_FO = 2   # NSE F&O (Options/Futures)
    BSE_CM = 3   # BSE Cash Market
    MCX_FO = 5   # MCX F&O


@dataclass
class TickData:
    """Container for a single tick update."""
    token: str
    exchange_type: int
    ltp: float
    timestamp: datetime
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    oi: Optional[int] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    
    @classmethod
    def from_websocket_data(cls, data: dict) -> "TickData":
        """Create TickData from WebSocket message."""
        return cls(
            token=str(data.get("token", "")),
            exchange_type=data.get("exchange_type", 0),
            ltp=float(data.get("last_traded_price", 0)) / 100,  # Divide by 100
            timestamp=datetime.now(),
            open=float(data.get("open_price_of_the_day", 0)) / 100 if data.get("open_price_of_the_day") else None,
            high=float(data.get("high_price_of_the_day", 0)) / 100 if data.get("high_price_of_the_day") else None,
            low=float(data.get("low_price_of_the_day", 0)) / 100 if data.get("low_price_of_the_day") else None,
            close=float(data.get("closed_price", 0)) / 100 if data.get("closed_price") else None,
            volume=data.get("volume_trade_for_the_day"),
            oi=data.get("open_interest"),
            best_bid=float(data.get("best_bid_price", 0)) / 100 if data.get("best_bid_price") else None,
            best_ask=float(data.get("best_ask_price", 0)) / 100 if data.get("best_ask_price") else None,
        )


# Type alias for tick callback
TickCallback = Callable[[TickData], None]


class MarketFeed:
    """
    Real-time market data feed using Angel One WebSocket.
    
    Features:
    - Dual-stream support (Index + Options)
    - Automatic heartbeat maintenance
    - Reconnection with exponential backoff
    - Tick callbacks for event-driven architecture
    """
    
    MAX_SUBSCRIPTIONS = 1000  # Angel One limit
    HEARTBEAT_INTERVAL = 30   # seconds
    
    def __init__(
        self,
        auth_token: str,
        api_key: str,
        client_code: str,
        feed_token: str,
        mode: SubscriptionMode = SubscriptionMode.QUOTE
    ):
        """
        Initialize market feed.
        
        Args:
            auth_token: JWT authentication token
            api_key: Angel One API key
            client_code: Client code/ID
            feed_token: Feed token for WebSocket
            mode: Default subscription mode
        """
        self.auth_token = auth_token
        self.api_key = api_key
        self.client_code = client_code
        self.feed_token = feed_token
        self.default_mode = mode
        
        self._ws: Optional[SmartWebSocketV2] = None
        self._is_connected: bool = False
        self._subscriptions: Dict[str, Set[str]] = {
            "NSE_CM": set(),
            "NSE_FO": set(),
        }
        
        # Callbacks
        self._tick_callbacks: List[TickCallback] = []
        self._connection_callbacks: List[Callable[[bool], None]] = []
        
        # Latest tick storage
        self._latest_ticks: Dict[str, TickData] = {}
        
        # Reconnection state
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 1.0  # Initial delay in seconds
        
        # Tick buffer for each token
        self._tick_buffers: Dict[str, deque] = {}
        self._buffer_size = 100
        
    def _create_websocket(self) -> None:
        """Create WebSocket instance with callbacks."""
        if SmartWebSocketV2 is None:
            logger.error("SmartApi WebSocket not installed")
            return
            
        self._ws = SmartWebSocketV2(
            self.auth_token,
            self.api_key,
            self.client_code,
            self.feed_token
        )
        
        # Assign callbacks
        self._ws.on_open = self._on_open
        self._ws.on_data = self._on_data
        self._ws.on_error = self._on_error
        self._ws.on_close = self._on_close
        
    def _on_open(self, wsapp) -> None:
        """Handle WebSocket connection open."""
        logger.success("WebSocket connected")
        self._is_connected = True
        self._reconnect_attempts = 0
        self._reconnect_delay = 1.0
        
        # Notify connection callbacks
        for callback in self._connection_callbacks:
            try:
                callback(True)
            except Exception as e:
                logger.error(f"Connection callback error: {e}")
        
        # Re-subscribe to all tokens
        self._resubscribe_all()
        
    def _on_data(self, wsapp, message: dict) -> None:
        """Handle incoming tick data."""
        try:
            tick = TickData.from_websocket_data(message)
            
            # Store latest tick
            self._latest_ticks[tick.token] = tick
            
            # Add to buffer
            if tick.token not in self._tick_buffers:
                self._tick_buffers[tick.token] = deque(maxlen=self._buffer_size)
            self._tick_buffers[tick.token].append(tick)
            
            # Notify tick callbacks
            for callback in self._tick_callbacks:
                try:
                    callback(tick)
                except Exception as e:
                    logger.error(f"Tick callback error: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing tick: {e}")
            
    def _on_error(self, wsapp, error: str) -> None:
        """Handle WebSocket errors."""
        logger.error(f"WebSocket error: {error}")
        
    def _on_close(self, wsapp) -> None:
        """Handle WebSocket connection close."""
        logger.warning("WebSocket disconnected")
        self._is_connected = False
        
        # Notify connection callbacks
        for callback in self._connection_callbacks:
            try:
                callback(False)
            except Exception as e:
                logger.error(f"Connection callback error: {e}")
        
        # Attempt reconnection
        self._schedule_reconnect()
        
    def _schedule_reconnect(self) -> None:
        """Schedule reconnection with exponential backoff."""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return
            
        self._reconnect_attempts += 1
        delay = self._reconnect_delay * (2 ** (self._reconnect_attempts - 1))
        delay = min(delay, 60)  # Cap at 60 seconds
        
        logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts})")
        
        # Note: In async version, use asyncio.sleep
        time.sleep(delay)
        self.connect()
        
    def _resubscribe_all(self) -> None:
        """Re-subscribe to all previously subscribed tokens."""
        for exchange, tokens in self._subscriptions.items():
            if tokens:
                exchange_type = (
                    ExchangeType.NSE_CM if exchange == "NSE_CM"
                    else ExchangeType.NSE_FO
                )
                self._do_subscribe(list(tokens), exchange_type)
                
    def _do_subscribe(
        self,
        tokens: List[str],
        exchange_type: ExchangeType,
        mode: Optional[SubscriptionMode] = None
    ) -> None:
        """Internal subscribe implementation."""
        if not self._ws or not self._is_connected:
            logger.warning("Cannot subscribe - not connected")
            return
            
        mode = mode or self.default_mode
        correlation_id = f"sub_{exchange_type}_{int(time.time())}"
        
        token_list = [
            {
                "exchangeType": exchange_type,
                "tokens": tokens
            }
        ]
        
        try:
            self._ws.subscribe(correlation_id, mode, token_list)
            logger.debug(f"Subscribed to {len(tokens)} tokens on {exchange_type.name}")
        except Exception as e:
            logger.error(f"Subscribe error: {e}")
            
    def connect(self) -> bool:
        """
        Connect to WebSocket.
        
        Returns:
            True if connection initiated successfully
        """
        try:
            self._create_websocket()
            if self._ws:
                self._ws.connect()
                return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
        return False
    
    def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception as e:
                logger.error(f"Disconnect error: {e}")
        self._is_connected = False
        
    def subscribe_index(
        self,
        token: str,
        mode: Optional[SubscriptionMode] = None
    ) -> None:
        """
        Subscribe to index data.
        
        Args:
            token: Index token (e.g., "26009" for NIFTY 50)
            mode: Subscription mode
        """
        self._subscriptions["NSE_CM"].add(token)
        
        if self._is_connected:
            self._do_subscribe([token], ExchangeType.NSE_CM, mode)
            
    def subscribe_option(
        self,
        token: str,
        mode: Optional[SubscriptionMode] = None
    ) -> None:
        """
        Subscribe to option data.
        
        Args:
            token: Option token
            mode: Subscription mode
        """
        self._subscriptions["NSE_FO"].add(token)
        
        if self._is_connected:
            self._do_subscribe([token], ExchangeType.NSE_FO, mode)
            
    def subscribe_options(
        self,
        tokens: List[str],
        mode: Optional[SubscriptionMode] = None
    ) -> None:
        """
        Subscribe to multiple options.
        
        Args:
            tokens: List of option tokens
            mode: Subscription mode
        """
        for token in tokens:
            self._subscriptions["NSE_FO"].add(token)
            
        if self._is_connected:
            self._do_subscribe(tokens, ExchangeType.NSE_FO, mode)
            
    def unsubscribe(self, token: str, exchange: str = "NSE_FO") -> None:
        """
        Unsubscribe from a token.
        
        Args:
            token: Token to unsubscribe
            exchange: Exchange (NSE_CM or NSE_FO)
        """
        if exchange in self._subscriptions:
            self._subscriptions[exchange].discard(token)
            
        if self._ws and self._is_connected:
            exchange_type = (
                ExchangeType.NSE_CM if exchange == "NSE_CM"
                else ExchangeType.NSE_FO
            )
            correlation_id = f"unsub_{int(time.time())}"
            token_list = [{"exchangeType": exchange_type, "tokens": [token]}]
            
            try:
                self._ws.unsubscribe(correlation_id, self.default_mode, token_list)
            except Exception as e:
                logger.error(f"Unsubscribe error: {e}")
                
    def add_tick_callback(self, callback: TickCallback) -> None:
        """
        Add a callback for tick updates.
        
        Args:
            callback: Function to call with TickData on each tick
        """
        self._tick_callbacks.append(callback)
        
    def remove_tick_callback(self, callback: TickCallback) -> None:
        """Remove a tick callback."""
        if callback in self._tick_callbacks:
            self._tick_callbacks.remove(callback)
            
    def add_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """
        Add a callback for connection state changes.
        
        Args:
            callback: Function called with True on connect, False on disconnect
        """
        self._connection_callbacks.append(callback)
        
    def get_ltp(self, token: str) -> Optional[float]:
        """
        Get last traded price for a token.
        
        Args:
            token: Instrument token
            
        Returns:
            LTP if available, None otherwise
        """
        tick = self._latest_ticks.get(token)
        return tick.ltp if tick else None
    
    def get_latest_tick(self, token: str) -> Optional[TickData]:
        """
        Get latest tick data for a token.
        
        Args:
            token: Instrument token
            
        Returns:
            TickData if available, None otherwise
        """
        return self._latest_ticks.get(token)
    
    def get_tick_buffer(self, token: str) -> List[TickData]:
        """
        Get buffered ticks for a token.
        
        Args:
            token: Instrument token
            
        Returns:
            List of recent ticks
        """
        buffer = self._tick_buffers.get(token)
        return list(buffer) if buffer else []
    
    def get_all_ltps(self) -> Dict[str, float]:
        """
        Get LTPs for all subscribed tokens.
        
        Returns:
            Dict of {token: ltp}
        """
        return {
            token: tick.ltp
            for token, tick in self._latest_ticks.items()
        }
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._is_connected
    
    @property
    def subscription_count(self) -> int:
        """Get total number of subscriptions."""
        return sum(len(tokens) for tokens in self._subscriptions.values())


# Factory function for creating MarketFeed from auth
def create_market_feed_from_auth(auth) -> MarketFeed:
    """
    Create MarketFeed instance from AngelOneAuth.
    
    Args:
        auth: AngelOneAuth instance
        
    Returns:
        Configured MarketFeed instance
    """
    return MarketFeed(
        auth_token=auth.jwt_token,
        api_key=auth.api_key,
        client_code=auth.client_code,
        feed_token=auth.feed_token
    )
