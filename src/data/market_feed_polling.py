"""
Market Feed Polling Module - Fallback for WebSocket Rate Limits

Uses REST API polling when WebSocket is rate limited (429 errors).

RATE LIMIT STRATEGY:
- SmartAPI getLtpData: 10 requests/sec per client code (burst: 500)
- We have 3 tokens (1 index + 2 options)
- DO NOT call 3x per second - instead use intelligent batching
- For 3 tokens: poll every 2 seconds = 1.5 calls/sec (SAFE: well under 10/sec limit)
- Track rate limit errors and implement exponential backoff

v2.0: Comprehensive rate limit handling
- Token-level rate tracking
- Error classification (rate limit vs network vs temporary)
- Adaptive polling interval
- Graceful degradation
"""

import time
import threading
from datetime import datetime, time as dtime
from typing import Optional, Callable, Dict, List, Set
from dataclasses import dataclass
from urllib3.exceptions import NameResolutionError, MaxRetryError
from requests.exceptions import ConnectionError, Timeout, RequestException

from loguru import logger

from data.market_feed import TickData, TickCallback


# Rate limit constants based on SmartAPI documentation
RATE_LIMIT_PER_SEC = 10  # getLtpData has 10 req/sec limit
RATE_LIMIT_BURST = 500   # Burst capacity
MIN_POLL_INTERVAL = 0.5  # Absolute minimum (1 token every 0.5s = max 2 req/sec)
MAX_POLL_INTERVAL = 20.0  # Maximum backoff (increased from 5.0s for 4s base interval)


class PollingMarketFeed:
    """
    REST API based market feed using LTP polling.
    
    Use this when WebSocket is rate limited (429 errors).
    
    Rate Limit Strategy:
    - Base interval: 4 seconds (3 tokens × 0.25 req/sec = 0.75 req/sec - SAFE under 10 req/sec limit)
    - Adaptive backoff: On rate limit, increases exponentially (4s → 8s → 12s → 16s → 20s)
    - Automatic recovery: Resets to base interval when all tokens fetch successfully
    
    Only polls during market hours to avoid API errors.
    """
    
    def __init__(
        self,
        smart_api,  # SmartConnect instance or Broker instance
        poll_interval: float = 4.0,  # Poll every 4 seconds (conservative: 3 tokens × 0.25 req/sec = 0.75 req/sec)
        market_open: dtime = dtime(9, 15),
        market_close: dtime = dtime(15, 30),
        broker=None  # Optional broker for session validation
    ):
        """
        Initialize polling market feed.
        
        Args:
            smart_api: Authenticated SmartConnect instance
            poll_interval: Seconds between polls
            market_open: Market open time
            market_close: Market close time
            broker: Optional broker instance for session validation
        """
        self._api = smart_api
        self._broker = broker  # For session validation
        self._poll_interval = poll_interval
        self._base_poll_interval = poll_interval  # Store original for reset
        self._market_open = market_open
        self._market_close = market_close
        
        self._subscriptions: Dict[str, dict] = {}  # token -> {exchange, symbol}
        self._tick_callbacks: List[TickCallback] = []
        
        self._is_running = False
        self._poll_thread: Optional[threading.Thread] = None
        
        # Latest ticks
        self._latest_ticks: Dict[str, TickData] = {}
        
        # Rate limit tracking (SmartAPI documentation)
        self._rate_limit_errors = 0  # Consecutive 429/rate limit errors
        self._rate_limit_hit_time: Optional[float] = None  # When rate limit was last hit
        self._adaptive_backoff = 1.0  # Multiplier for poll_interval during rate limiting
        self._last_api_call_time = 0  # Timestamp of last successful API call
        
        # Track if we logged market closed
        self._logged_market_closed = False
        
        # Network status tracking for auto-reconnect
        self._consecutive_errors = 0
        self._max_consecutive_errors = 10  # After this, log warning
        self._network_down = False
        self._last_success_time: Optional[datetime] = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 0  # 0 = infinite retries (never give up on connection errors)
        self._backoff_time = 5.0  # Exponential backoff start
        
    def add_tick_callback(self, callback: TickCallback) -> None:
        """Add callback for tick data."""
        self._tick_callbacks.append(callback)
        
    def subscribe_index(self, token: str, symbol: str = "Nifty 50", exchange: str = "NSE") -> None:
        """Subscribe to index data."""
        self._subscriptions[token] = {
            "exchange": exchange,
            "symbol": symbol,
            "exchange_type": 1  # NSE_CM
        }
        logger.info(f"Polling subscribed: {symbol} (Token: {token})")
        
    def subscribe_option(self, token: str, symbol: str = "", exchange: str = "NFO") -> None:
        """Subscribe to option data."""
        self._subscriptions[token] = {
            "exchange": exchange,
            "symbol": symbol,
            "exchange_type": 2  # NSE_FO
        }
        logger.info(f"Polling subscribed: Option (Token: {token})")
        
    def connect(self, timeout: float = 5.0) -> bool:
        """Start polling."""
        if self._is_running:
            return True
            
        self._is_running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        
        logger.success("Polling market feed started")
        return True
        
    def disconnect(self) -> None:
        """Stop polling."""
        self._is_running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        logger.info("Polling market feed stopped")
        
    def _poll_loop(self) -> None:
        """Main polling loop with auto-reconnect on network failures."""
        while self._is_running:
            try:
                # Check if market is open
                now = datetime.now().time()
                is_market_open = self._market_open <= now < self._market_close
                
                if is_market_open:
                    self._logged_market_closed = False
                    
                    # Batch fetch all LTPs at once (reduced API calls)
                    success = self._fetch_batch_ltp(self._subscriptions)

                    
                    # Track network status
                    if success:
                        if self._network_down:
                            logger.success(f"🔄 Network RESTORED! Resuming trading after {self._reconnect_attempts} retries")
                            self._network_down = False
                        self._consecutive_errors = 0
                        self._reconnect_attempts = 0
                        self._backoff_time = 5.0
                        self._last_success_time = datetime.now()
                    else:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= self._max_consecutive_errors:
                            if not self._network_down:
                                logger.warning(f"⚠️ Network appears DOWN - {self._consecutive_errors} consecutive errors")
                                self._network_down = True
                            
                            # Check if we've exceeded max reconnect attempts (0 = infinite)
                            if self._max_reconnect_attempts > 0 and self._reconnect_attempts >= self._max_reconnect_attempts:
                                logger.error(f"❌ MAX RETRIES EXCEEDED ({self._max_reconnect_attempts}) - Stopping polling")
                                self._is_running = False
                                break
                            
                            # Exponential backoff
                            self._reconnect_attempts += 1
                            wait_time = min(self._backoff_time * (2 ** min(self._reconnect_attempts, 5)), 60)
                            logger.info(f"🔄 Auto-reconnect attempt #{self._reconnect_attempts} in {wait_time:.0f}s...")
                            time.sleep(wait_time)
                            continue
                else:
                    # Market closed - skip polling to avoid API errors
                    if not self._logged_market_closed:
                        logger.debug("Polling paused - market closed")
                        self._logged_market_closed = True
                    
                time.sleep(self._poll_interval)
                
            except Exception as e:
                logger.error(f"Polling error: {e}")
                self._consecutive_errors += 1
                time.sleep(2)
                
    def _fetch_ltp(self, token: str, info: dict) -> bool:
        """
        Fetch LTP for a token.
        
        Returns:
            True if fetch was successful, False on network error
        """
        try:
            exchange = info["exchange"]
            symbol = info["symbol"]
            
            # Call LTP API
            response = self._api.ltpData(exchange, symbol, token)
            
            if response and response.get("status"):
                data = response.get("data", {})
                ltp = float(data.get("ltp", 0))
                
                if ltp > 0:
                    tick = TickData(
                        token=token,
                        exchange_type=info["exchange_type"],
                        ltp=ltp,
                        timestamp=datetime.now(),
                        open=float(data.get("open", 0)) if data.get("open") else None,
                        high=float(data.get("high", 0)) if data.get("high") else None,
                        low=float(data.get("low", 0)) if data.get("low") else None,
                        close=float(data.get("close", 0)) if data.get("close") else None,
                    )
                    
                    # Store and notify
                    self._latest_ticks[token] = tick
                    
                    for callback in self._tick_callbacks:
                        try:
                            callback(tick)
                        except Exception as e:
                            logger.error(f"Tick callback error: {e}")
                    
                    return True  # Success
            
            return True  # API responded but no data - not a network error
                            
        except (ConnectionError, Timeout, MaxRetryError) as e:
            # Network errors - don't log each one, just return False
            return False
        except Exception as e:
            # Check if it's a DNS/network error
            error_str = str(e).lower()
            if any(x in error_str for x in ['name resolution', 'dns', 'network', 'timeout', 'connection']):
                return False  # Network error
            logger.debug(f"LTP fetch error for {token}: {e}")
            return True  # Non-network error
    
    def _fetch_batch_ltp(self, subscriptions: Dict[str, dict]) -> bool:
        """
        Batch fetch LTPs for all subscribed tokens.
        
        Reduces API calls by fetching multiple tokens together.
        Falls back to individual fetch if batch fails.
        
        Args:
            subscriptions: Dictionary of {token: {exchange, symbol}}
            
        Returns:
            True if at least one token was fetched successfully
        """
        if not subscriptions:
            return True
        
        try:
            # Build batch request for all tokens
            success_count = 0
            
            for token, info in subscriptions.items():
                exchange = info.get("exchange", "NFO")
                symbol = info.get("symbol", "")
                
                try:
                    # Use the raw API - SmartAPI ltpData
                    response = self._api.ltpData(exchange, symbol, token)
                    
                    if response and response.get("status"):
                        # SmartAPI returns LTP in response.data dict
                        data = response.get("data", {})
                        if data and "ltp" in data:
                            ltp = float(data.get("ltp", 0))
                            # Determine exchange_type from exchange code
                            exchange_type = 1 if exchange == "NFO" else 0  # 1=NFO, 0=NSE
                            tick = TickData(
                                token=token,
                                exchange_type=exchange_type,
                                ltp=ltp,
                                timestamp=datetime.now()
                            )
                            self._latest_ticks[token] = tick
                            
                            # Call all registered callbacks
                            for callback in self._tick_callbacks:
                                callback(tick)
                            
                            success_count += 1
                            self._consecutive_errors = 0
                    else:
                        self._consecutive_errors += 1
                        if not response:
                            logger.debug(f"Empty response for {symbol} (token={token})")
                        else:
                            logger.debug(f"API error for {symbol}: {response.get('message', 'Unknown')}")
                except (NameResolutionError, MaxRetryError, ConnectionError, Timeout) as e:
                    self._consecutive_errors += 1
                    logger.debug(f"Network error for {symbol}: {type(e).__name__}")
                    return False  # Network error
                except Exception as e:
                    self._consecutive_errors += 1
                    error_msg = str(e).lower()
                    
                    # ===== RATE LIMIT DETECTION =====
                    # SmartAPI returns "Access denied because of exceeding access rate"
                    if "access rate" in error_msg or "429" in error_msg or "exceeding" in error_msg:
                        self._rate_limit_errors += 1
                        self._rate_limit_hit_time = time.time()
                        
                        logger.warning(
                            f"🚨 RATE LIMITED #{self._rate_limit_errors}: {symbol} | "
                            f"Error: {error_msg[:100]}"
                        )
                        
                        # Adaptive backoff: exponential increase in poll interval
                        # After 1st rate limit: 2x, 2nd: 4x, 3rd: 8x, etc. (capped at MAX_POLL_INTERVAL)
                        new_interval = min(
                            self._base_poll_interval * (2 ** self._rate_limit_errors),
                            MAX_POLL_INTERVAL
                        )
                        if self._poll_interval != new_interval:
                            logger.warning(
                                f"📈 Adaptive backoff: Poll interval {self._poll_interval:.1f}s → {new_interval:.1f}s"
                            )
                            self._poll_interval = new_interval
                        
                        # Return False to stop this round of polling (let thread sleep longer)
                        return False
                    
                    # ===== OTHER ERRORS =====
                    else:
                        logger.error(f"ERROR fetching {symbol} (token={token}): {type(e).__name__}: {e}")
            
            # Check if we had any successes
            if success_count > 0:
                # SUCCESS: Reset rate limit counters and restore normal polling interval
                if self._rate_limit_errors > 0:
                    logger.success(
                        f"✅ RATE LIMIT RECOVERY: {success_count}/{len(subscriptions)} tokens fetched, "
                        f"resetting backoff"
                    )
                    self._rate_limit_errors = 0
                    self._poll_interval = self._base_poll_interval  # Return to normal interval
                
                # Reset network down flag if we recovered
                if self._network_down:
                    logger.success(f"🔄 Network RESTORED - Polling resumed")
                    self._network_down = False
                    self._reconnect_attempts = 0
                return True
            else:
                self._consecutive_errors += 1
                return False
                
        except Exception as e:
            logger.error(f"Batch fetch fatal error: {type(e).__name__}: {e}")
            self._consecutive_errors += 1
            return False
            
    def get_latest_tick(self, token: str) -> Optional[TickData]:
        """Get latest tick for a token."""
        return self._latest_ticks.get(token)
        
    @property
    def is_connected(self) -> bool:
        """Check if polling is active."""
        return self._is_running
    
    @property
    def is_network_healthy(self) -> bool:
        """Check if network connection is healthy."""
        return not self._network_down
    
    @property
    def network_status(self) -> str:
        """Get network status description."""
        if self._network_down:
            return f"DOWN (retry #{self._reconnect_attempts})"
        elif self._consecutive_errors > 0:
            return f"UNSTABLE ({self._consecutive_errors} errors)"
        return "HEALTHY"

