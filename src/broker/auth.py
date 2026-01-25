"""
Angel One SmartAPI Authentication Module

Handles TOTP-based login, token management, and session lifecycle.
"""

import os
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect
from loguru import logger

# Load environment variables from .env file
load_dotenv()


@dataclass
class AuthTokens:
    """Container for authentication tokens."""
    jwt_token: str
    refresh_token: str
    feed_token: str
    created_at: datetime = field(default_factory=datetime.now)
    
    def is_expired(self, buffer_minutes: int = 30) -> bool:
        """Check if tokens need refresh (conservative 30-min buffer before midnight)."""
        now = datetime.now()
        # Tokens expire at midnight
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        time_to_expiry = midnight - now
        return time_to_expiry < timedelta(minutes=buffer_minutes)


class AngelOneAuth:
    """
    Angel One SmartAPI Authentication Manager.
    
    Handles:
    - TOTP-based login
    - Automatic token refresh
    - Session logout at EOD
    - Connection state management
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None
    ):
        """
        Initialize authentication manager.
        
        Args:
            api_key: Angel One API key (or from ANGEL_API_KEY env var)
            client_id: Client ID (or from ANGEL_CLIENT_ID env var)
            password: Account password (or from ANGEL_PASSWORD env var)
            totp_secret: TOTP secret key (or from ANGEL_TOTP_SECRET env var)
        """
        self.api_key = api_key or os.getenv("ANGEL_API_KEY")
        self.client_id = client_id or os.getenv("ANGEL_CLIENT_ID")
        self.password = password or os.getenv("ANGEL_PASSWORD")
        self.totp_secret = totp_secret or os.getenv("ANGEL_TOTP_SECRET")
        
        self._validate_credentials()
        
        self._smart_api: Optional[SmartConnect] = None
        self._tokens: Optional[AuthTokens] = None
        self._is_logged_in: bool = False
        self._login_time: Optional[datetime] = None
        
    def _validate_credentials(self) -> None:
        """Validate that all required credentials are present."""
        missing = []
        if not self.api_key:
            missing.append("ANGEL_API_KEY")
        if not self.client_id:
            missing.append("ANGEL_CLIENT_ID")
        if not self.password:
            missing.append("ANGEL_PASSWORD")
        if not self.totp_secret:
            missing.append("ANGEL_TOTP_SECRET")
            
        if missing:
            raise ValueError(
                f"Missing required credentials: {', '.join(missing)}. "
                "Set them as environment variables or pass to constructor."
            )
    
    def _generate_totp(self) -> str:
        """Generate current TOTP code."""
        totp = pyotp.TOTP(self.totp_secret)
        return totp.now()
    
    def login(self, max_retries: int = 3, retry_delay: float = 2.0) -> bool:
        """
        Login to Angel One SmartAPI.
        
        Args:
            max_retries: Maximum login attempts
            retry_delay: Delay between retries in seconds
            
        Returns:
            True if login successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Login attempt {attempt + 1}/{max_retries}")
                
                # Create new SmartConnect instance
                self._smart_api = SmartConnect(api_key=self.api_key)
                
                # Generate fresh TOTP
                totp_code = self._generate_totp()
                
                # Perform login
                login_response = self._smart_api.generateSession(
                    clientCode=self.client_id,
                    password=self.password,
                    totp=totp_code
                )
                
                if login_response.get("status"):
                    data = login_response.get("data", {})
                    
                    self._tokens = AuthTokens(
                        jwt_token=data.get("jwtToken", ""),
                        refresh_token=data.get("refreshToken", ""),
                        feed_token=self._smart_api.getfeedToken()
                    )
                    
                    self._is_logged_in = True
                    self._login_time = datetime.now()
                    
                    logger.success(
                        f"Login successful for client {self.client_id} "
                        f"at {self._login_time.strftime('%H:%M:%S')}"
                    )
                    return True
                else:
                    error_msg = login_response.get("message", "Unknown error")
                    logger.warning(f"Login failed: {error_msg}")
                    
            except Exception as e:
                logger.error(f"Login exception: {type(e).__name__}: {e}")
                
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                
        logger.error("All login attempts failed")
        return False
    
    def refresh_token(self) -> bool:
        """
        Refresh authentication tokens.
        
        Returns:
            True if refresh successful, False otherwise
        """
        if not self._smart_api or not self._tokens:
            logger.warning("Cannot refresh - not logged in")
            return False
            
        try:
            refresh_response = self._smart_api.generateToken(
                refreshToken=self._tokens.refresh_token
            )
            
            if refresh_response.get("status"):
                data = refresh_response.get("data", {})
                
                self._tokens = AuthTokens(
                    jwt_token=data.get("jwtToken", self._tokens.jwt_token),
                    refresh_token=data.get("refreshToken", self._tokens.refresh_token),
                    feed_token=self._smart_api.getfeedToken()
                )
                
                logger.info("Token refresh successful")
                return True
            else:
                error_msg = refresh_response.get("message", "Unknown error")
                logger.warning(f"Token refresh failed: {error_msg}")
                
                # If refresh fails, try full re-login
                logger.info("Attempting full re-login...")
                return self.login()
                
        except Exception as e:
            logger.error(f"Token refresh exception: {e}")
            return self.login()
    
    def ensure_valid_session(self) -> bool:
        """
        Ensure we have a valid session, refreshing if needed.
        
        Returns:
            True if session is valid, False otherwise
        """
        if not self._is_logged_in:
            return self.login()
            
        if self._tokens and self._tokens.is_expired():
            logger.info("Tokens expiring soon, refreshing...")
            return self.refresh_token()
            
        return True
    
    def logout(self) -> bool:
        """
        Logout from Angel One SmartAPI.
        
        Returns:
            True if logout successful, False otherwise
        """
        if not self._smart_api or not self._is_logged_in:
            logger.info("Not logged in, nothing to logout")
            return True
            
        try:
            logout_response = self._smart_api.terminateSession(self.client_id)
            
            if logout_response.get("status"):
                logger.success("Logout successful")
            else:
                logger.warning(f"Logout response: {logout_response.get('message')}")
                
        except Exception as e:
            logger.error(f"Logout exception: {e}")
            
        finally:
            # Always clear local state
            self._is_logged_in = False
            self._tokens = None
            self._smart_api = None
            
        return True
    
    @property
    def smart_api(self) -> Optional[SmartConnect]:
        """Get the SmartConnect instance for API calls."""
        return self._smart_api
    
    @property
    def feed_token(self) -> Optional[str]:
        """Get the feed token for WebSocket connections."""
        return self._tokens.feed_token if self._tokens else None
    
    @property
    def jwt_token(self) -> Optional[str]:
        """Get the JWT token for REST API calls."""
        return self._tokens.jwt_token if self._tokens else None
    
    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        return self._is_logged_in
    
    @property
    def client_code(self) -> str:
        """Get the client code/ID."""
        return self.client_id
    
    def get_profile(self) -> Optional[dict]:
        """
        Get user profile information.
        
        Returns:
            Profile dict if successful, None otherwise
        """
        if not self.ensure_valid_session():
            return None
            
        try:
            profile = self._smart_api.getProfile(self._tokens.refresh_token)
            if profile.get("status"):
                return profile.get("data")
            else:
                logger.warning(f"Get profile failed: {profile.get('message')}")
        except Exception as e:
            logger.error(f"Get profile exception: {e}")
            
        return None
    
    def get_ltp(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        """
        Get Last Traded Price for a symbol.
        
        Args:
            exchange: Exchange (NSE, NFO, etc.)
            symbol: Trading symbol
            token: Instrument token
            
        Returns:
            LTP if successful, None otherwise
        """
        if not self.ensure_valid_session():
            return None
            
        try:
            data = self._smart_api.ltpData(exchange, symbol, token)
            logger.debug(f"Raw LTP response: {data}")
            if data.get("status"):
                ltp_data = data.get("data", {})
                ltp = float(ltp_data.get("ltp", 0))
                logger.debug(f"Extracted LTP: {ltp}")
                return ltp
            else:
                logger.warning(f"LTP fetch failed: {data.get('message')}")
        except Exception as e:
            logger.error(f"LTP fetch exception: {e}")
            
        return None
    
    def get_multiple_ltp(self, symbols: list) -> dict:
        """
        Get LTP for multiple symbols.
        
        Args:
            symbols: List of dicts with exchange, symbol, token
            
        Returns:
            Dict of {token: ltp}
        """
        if not self.ensure_valid_session():
            return {}
            
        result = {}
        
        # Angel One API allows batch LTP - use it
        try:
            # Format: {"exchange": "NSE", "tradingsymbol": "SBIN-EQ", "symboltoken": "3045"}
            exchange_tokens = {}
            for s in symbols:
                exchange = s.get("exchange", "NFO")
                token = s.get("token")
                if exchange not in exchange_tokens:
                    exchange_tokens[exchange] = []
                exchange_tokens[exchange].append(token)
            
            # Call API for each exchange
            for exchange, tokens in exchange_tokens.items():
                data = self._smart_api.getMarketData(
                    mode="LTP",
                    exchangeTokens={exchange: tokens}
                )
                
                if data.get("status"):
                    fetched = data.get("data", {}).get("fetched", [])
                    for item in fetched:
                        tok = item.get("symbolToken")
                        ltp = item.get("ltp")
                        if tok and ltp:
                            result[tok] = float(ltp)
                else:
                    logger.warning(f"Batch LTP failed for {exchange}: {data.get('message')}")
                    
        except Exception as e:
            logger.error(f"Batch LTP exception: {e}")
            
        return result


# Singleton instance for global access
_auth_instance: Optional[AngelOneAuth] = None


def get_auth() -> AngelOneAuth:
    """
    Get the global authentication instance.
    
    Returns:
        AngelOneAuth singleton instance
    """
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = AngelOneAuth()
    return _auth_instance


def initialize_auth(
    api_key: Optional[str] = None,
    client_id: Optional[str] = None,
    password: Optional[str] = None,
    totp_secret: Optional[str] = None
) -> AngelOneAuth:
    """
    Initialize the global authentication instance with credentials.
    
    Args:
        api_key: Angel One API key
        client_id: Client ID
        password: Account password
        totp_secret: TOTP secret key
        
    Returns:
        Initialized AngelOneAuth instance
    """
    global _auth_instance
    _auth_instance = AngelOneAuth(
        api_key=api_key,
        client_id=client_id,
        password=password,
        totp_secret=totp_secret
    )
    return _auth_instance
