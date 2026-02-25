"""
Connection Manager - Auto-reconnect on network failure

Features:
- WebSocket reconnection with exponential backoff
- Network connectivity check
- Session recovery after disconnect
- Graceful degradation
"""

import asyncio
import time
import socket
from datetime import datetime
from typing import Optional, Callable, Awaitable
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class ConnectionManager:
    """
    Manages connection with auto-reconnect
    
    Usage:
        manager = ConnectionManager(
            connect_func=my_connect,
            disconnect_func=my_disconnect
        )
        await manager.connect()
        # ... trading ...
        # On disconnect, auto-reconnect kicks in
    """
    
    def __init__(
        self,
        connect_func: Callable[[], Awaitable[bool]],
        disconnect_func: Optional[Callable[[], Awaitable[None]]] = None,
        on_reconnect: Optional[Callable[[], Awaitable[None]]] = None,
        max_retries: int = 0,  # 0 = infinite
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0
    ):
        """
        Initialize connection manager
        
        Args:
            connect_func: Async function to establish connection
            disconnect_func: Async function to clean up on disconnect
            on_reconnect: Callback after successful reconnect
            max_retries: Max reconnection attempts (0 = infinite)
            initial_delay: Initial delay between retries (seconds)
            max_delay: Maximum delay between retries
            backoff_factor: Multiplier for exponential backoff
        """
        self.connect_func = connect_func
        self.disconnect_func = disconnect_func
        self.on_reconnect = on_reconnect
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        
        self.state = ConnectionState.DISCONNECTED
        self._retry_count = 0
        self._current_delay = initial_delay
        self._last_connected: Optional[datetime] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
    @property
    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED
        
    @property
    def retry_count(self) -> int:
        return self._retry_count
        
    async def connect(self) -> bool:
        """Initial connection"""
        if self.state == ConnectionState.CONNECTED:
            return True
            
        self.state = ConnectionState.CONNECTING
        logger.info("🔌 Connecting...")
        
        try:
            success = await self.connect_func()
            if success:
                self.state = ConnectionState.CONNECTED
                self._last_connected = datetime.now()
                self._retry_count = 0
                self._current_delay = self.initial_delay
                logger.info("✅ Connected successfully")
                return True
            else:
                self.state = ConnectionState.DISCONNECTED
                logger.error("❌ Connection failed")
                return False
        except Exception as e:
            self.state = ConnectionState.DISCONNECTED
            logger.error(f"❌ Connection error: {e}")
            return False
            
    async def disconnect(self):
        """Clean disconnect"""
        self._shutdown = True
        
        if self._reconnect_task:
            self._reconnect_task.cancel()
            
        if self.disconnect_func:
            try:
                await self.disconnect_func()
            except Exception as e:
                logger.error(f"Disconnect error: {e}")
                
        self.state = ConnectionState.DISCONNECTED
        logger.info("🔌 Disconnected")
        
    async def handle_disconnect(self):
        """Handle unexpected disconnect - start reconnection"""
        if self._shutdown:
            return
            
        self.state = ConnectionState.RECONNECTING
        logger.warning("⚠️ Connection lost, starting reconnection...")
        
        # Start reconnection in background
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        
    async def _reconnect_loop(self):
        """Reconnection loop with exponential backoff"""
        while not self._shutdown:
            self._retry_count += 1
            
            # Check max retries
            if self.max_retries > 0 and self._retry_count > self.max_retries:
                logger.error(f"❌ Max retries ({self.max_retries}) exceeded")
                self.state = ConnectionState.FAILED
                return
                
            logger.info(f"🔄 Reconnection attempt {self._retry_count}...")
            
            # Check network first
            if not await self._check_network():
                logger.warning(f"📡 No network, waiting {self._current_delay:.1f}s...")
                await asyncio.sleep(self._current_delay)
                self._current_delay = min(self._current_delay * self.backoff_factor, self.max_delay)
                continue
                
            # Try to connect
            try:
                success = await self.connect_func()
                if success:
                    self.state = ConnectionState.CONNECTED
                    self._last_connected = datetime.now()
                    self._retry_count = 0
                    self._current_delay = self.initial_delay
                    
                    logger.info(f"✅ Reconnected successfully!")
                    
                    # Callback
                    if self.on_reconnect:
                        await self.on_reconnect()
                        
                    return
            except Exception as e:
                logger.error(f"Reconnection error: {e}")
                
            # Wait before next attempt
            logger.info(f"⏳ Waiting {self._current_delay:.1f}s before next attempt...")
            await asyncio.sleep(self._current_delay)
            self._current_delay = min(self._current_delay * self.backoff_factor, self.max_delay)
            
    async def _check_network(self) -> bool:
        """Check if network is available"""
        try:
            # Try to reach Google DNS
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: socket.create_connection(("8.8.8.8", 53), timeout=3)
            )
            return True
        except (socket.timeout, OSError):
            return False
            
    def get_status(self) -> dict:
        """Get connection status"""
        return {
            "state": self.state.value,
            "is_connected": self.is_connected,
            "retry_count": self._retry_count,
            "current_delay": self._current_delay,
            "last_connected": self._last_connected.isoformat() if self._last_connected else None
        }


class NetworkMonitor:
    """
    Background network monitoring
    
    Continuously checks connectivity and triggers reconnect
    """
    
    def __init__(
        self,
        connection_manager: ConnectionManager,
        check_interval: float = 5.0
    ):
        self.connection_manager = connection_manager
        self.check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start monitoring"""
        if self._running:
            return
            
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("📡 Network monitor started")
        
    async def stop(self):
        """Stop monitoring"""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("📡 Network monitor stopped")
        
    async def _monitor_loop(self):
        """Monitoring loop"""
        consecutive_failures = 0
        
        while self._running:
            await asyncio.sleep(self.check_interval)
            
            if not self.connection_manager.is_connected:
                continue
                
            # Check if connection is still alive
            try:
                # Simple network check
                loop = asyncio.get_event_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: socket.create_connection(("8.8.8.8", 53), timeout=2)
                    ),
                    timeout=3
                )
                consecutive_failures = 0
            except:
                consecutive_failures += 1
                logger.warning(f"⚠️ Network check failed ({consecutive_failures}/3)")
                
                if consecutive_failures >= 3:
                    logger.error("❌ Network appears down, triggering reconnect")
                    await self.connection_manager.handle_disconnect()
                    consecutive_failures = 0


# Utility functions

async def wait_for_network(timeout: float = 300) -> bool:
    """
    Wait for network to become available
    
    Args:
        timeout: Maximum wait time in seconds
        
    Returns:
        True if network available, False if timeout
    """
    start = time.time()
    check_interval = 2.0
    
    logger.info("📡 Waiting for network...")
    
    while time.time() - start < timeout:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2)
            logger.info("✅ Network available")
            return True
        except:
            elapsed = time.time() - start
            logger.debug(f"No network ({elapsed:.0f}s elapsed)")
            await asyncio.sleep(check_interval)
            
    logger.error(f"❌ Network not available after {timeout}s")
    return False


if __name__ == "__main__":
    # Test
    async def mock_connect():
        print("Connecting...")
        await asyncio.sleep(1)
        return True
        
    async def mock_disconnect():
        print("Disconnecting...")
        
    async def on_reconnect():
        print("Reconnected! Resuming...")
        
    async def test():
        manager = ConnectionManager(
            connect_func=mock_connect,
            disconnect_func=mock_disconnect,
            on_reconnect=on_reconnect
        )
        
        # Connect
        await manager.connect()
        print(f"Status: {manager.get_status()}")
        
        # Simulate disconnect
        await manager.handle_disconnect()
        
        # Wait a bit
        await asyncio.sleep(5)
        
        # Disconnect
        await manager.disconnect()
        
    asyncio.run(test())
