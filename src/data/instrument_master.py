"""
Instrument Master Module

Downloads and parses Angel One's daily instrument master file.
Provides token lookup for Index and Options.
"""

import os
import json
import requests
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from loguru import logger


class Exchange(Enum):
    """Exchange types for Angel One API."""
    NSE = "NSE"
    NFO = "NFO"
    BSE = "BSE"
    MCX = "MCX"


class OptionType(Enum):
    """Option types."""
    CALL = "CE"
    PUT = "PE"


@dataclass
class Instrument:
    """Instrument data container."""
    token: str
    symbol: str
    name: str
    exchange: str
    instrument_type: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    lot_size: int = 1
    tick_size: float = 0.05
    
    @property
    def is_option(self) -> bool:
        """Check if instrument is an option."""
        return self.instrument_type in ("OPTSTK", "OPTIDX", "CE", "PE")
    
    @property
    def is_future(self) -> bool:
        """Check if instrument is a future."""
        return self.instrument_type in ("FUTSTK", "FUTIDX")
    
    @property
    def is_index(self) -> bool:
        """Check if instrument is an index."""
        return self.instrument_type == "INDEX"
    
    @property
    def option_type(self) -> Optional[OptionType]:
        """Get option type if applicable."""
        if "CE" in self.symbol:
            return OptionType.CALL
        elif "PE" in self.symbol:
            return OptionType.PUT
        return None


class InstrumentMaster:
    """
    Angel One Instrument Master Manager.
    
    Downloads, caches, and provides lookup for instrument data.
    """
    
    # Angel One instrument master URL
    INSTRUMENT_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    
    def __init__(self, cache_dir: str = "data/instruments"):
        """
        Initialize instrument master.
        
        Args:
            cache_dir: Directory to cache instrument files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self._instruments: Dict[str, Instrument] = {}
        self._symbol_to_token: Dict[str, str] = {}
        self._df: Optional[pd.DataFrame] = None
        self._last_download: Optional[datetime] = None
        
    def _get_cache_path(self) -> Path:
        """Get cache file path for today."""
        today = date.today().strftime("%Y%m%d")
        return self.cache_dir / f"instruments_{today}.json"
    
    def _is_cache_valid(self) -> bool:
        """Check if today's cache exists and is valid."""
        cache_path = self._get_cache_path()
        return cache_path.exists()
    
    def download(self, force: bool = False) -> bool:
        """
        Download instrument master file.
        
        Args:
            force: Force download even if cache exists
            
        Returns:
            True if download successful, False otherwise
        """
        cache_path = self._get_cache_path()
        
        # Use cache if valid and not forcing
        if not force and self._is_cache_valid():
            logger.info(f"Using cached instrument master: {cache_path}")
            return self._load_from_cache()
        
        try:
            logger.info("Downloading instrument master from Angel One...")
            
            response = requests.get(
                self.INSTRUMENT_URL,
                timeout=60,
                headers={"User-Agent": "N-Structure-Bot/1.0"}
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Save to cache
            with open(cache_path, "w") as f:
                json.dump(data, f)
            
            self._last_download = datetime.now()
            logger.success(f"Downloaded {len(data)} instruments")
            
            return self._parse_instruments(data)
            
        except requests.RequestException as e:
            logger.error(f"Download failed: {e}")
            
            # Try to use previous cache if available
            return self._load_from_any_cache()
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            return False
    
    def _load_from_cache(self) -> bool:
        """Load instruments from today's cache."""
        cache_path = self._get_cache_path()
        
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            
            return self._parse_instruments(data)
            
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return False
    
    def _load_from_any_cache(self) -> bool:
        """Load from most recent available cache."""
        cache_files = sorted(
            self.cache_dir.glob("instruments_*.json"),
            reverse=True
        )
        
        for cache_file in cache_files:
            try:
                logger.warning(f"Falling back to cache: {cache_file}")
                with open(cache_file, "r") as f:
                    data = json.load(f)
                return self._parse_instruments(data)
            except Exception:
                continue
                
        logger.error("No valid cache files found")
        return False
    
    def _parse_instruments(self, data: List[Dict[str, Any]]) -> bool:
        """
        Parse raw instrument data into structured format.
        
        Args:
            data: List of instrument dictionaries from API
            
        Returns:
            True if parsing successful
        """
        try:
            self._instruments.clear()
            self._symbol_to_token.clear()
            
            for item in data:
                token = item.get("token", "")
                symbol = item.get("symbol", "")
                
                if not token or not symbol:
                    continue
                
                # Parse expiry date
                expiry = None
                expiry_str = item.get("expiry", "")
                if expiry_str:
                    try:
                        expiry = datetime.strptime(expiry_str, "%d%b%Y").date()
                    except ValueError:
                        pass
                
                # Parse strike price
                strike = None
                strike_val = item.get("strike", "")
                if strike_val and strike_val != "-1":
                    try:
                        # Angel One stores strike as price * 100
                        strike = float(strike_val) / 100
                    except ValueError:
                        pass
                
                instrument = Instrument(
                    token=token,
                    symbol=symbol,
                    name=item.get("name", ""),
                    exchange=item.get("exch_seg", ""),
                    instrument_type=item.get("instrumenttype", ""),
                    expiry=expiry,
                    strike=strike,
                    lot_size=int(item.get("lotsize", 1)),
                    tick_size=float(item.get("tick_size", 0.05))
                )
                
                self._instruments[token] = instrument
                
                # Build symbol lookup
                key = f"{item.get('exch_seg', '')}:{symbol}"
                self._symbol_to_token[key] = token
            
            # Build DataFrame for efficient filtering
            self._df = pd.DataFrame(data)
            
            logger.info(f"Parsed {len(self._instruments)} instruments")
            return True
            
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return False
    
    def get_by_token(self, token: str) -> Optional[Instrument]:
        """Get instrument by token."""
        return self._instruments.get(token)
    
    def get_by_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Instrument]:
        """
        Get instrument by symbol and exchange.
        
        Args:
            symbol: Trading symbol
            exchange: Exchange (NSE, NFO, BSE, MCX)
            
        Returns:
            Instrument if found, None otherwise
        """
        key = f"{exchange}:{symbol}"
        token = self._symbol_to_token.get(key)
        if token:
            return self._instruments.get(token)
        return None
    
    def get_index_token(self, index_name: str = "NIFTY") -> Optional[str]:
        """
        Get token for an index.
        
        Args:
            index_name: Index name (NIFTY, BANKNIFTY, etc.)
            
        Returns:
            Token string if found
        """
        # Correct index tokens for SmartAPI ltpData
        index_tokens = {
            "NIFTY": "99926000",
            "NIFTY 50": "99926000",
            "BANKNIFTY": "99926009",
            "NIFTY BANK": "99926009",
            "SENSEX": "99919000",
        }
        
        if index_name.upper() in index_tokens:
            return index_tokens[index_name.upper()]
        
        # Search in instruments
        for instrument in self._instruments.values():
            if instrument.name.upper() == index_name.upper() and instrument.is_index:
                return instrument.token
                
        return None
    
    def get_nifty_options(
        self,
        expiry_date: Optional[date] = None,
        option_type: Optional[OptionType] = None,
        min_strike: Optional[float] = None,
        max_strike: Optional[float] = None
    ) -> List[Instrument]:
        """
        Get NIFTY options filtered by criteria.
        
        Args:
            expiry_date: Filter by expiry date
            option_type: Filter by CE or PE
            min_strike: Minimum strike price
            max_strike: Maximum strike price
            
        Returns:
            List of matching instruments
        """
        results = []
        
        for instrument in self._instruments.values():
            # Must be NFO option
            if instrument.exchange != "NFO":
                continue
            if not instrument.is_option:
                continue
            
            # Only NIFTY (main index) - strict match
            # Symbol pattern: NIFTY<date><strike><CE/PE>
            # Exclude: BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, etc.
            name_upper = instrument.name.upper()
            symbol_upper = (instrument.symbol or "").upper()
            
            # Check symbol starts with exactly "NIFTY" followed by date
            # Valid: NIFTY27JAN2623300CE
            # Invalid: NIFTYNXT5027JAN2658500CE, BANKNIFTY...
            is_nifty = (
                symbol_upper.startswith("NIFTY") and 
                not symbol_upper.startswith("NIFTYNXT") and
                not symbol_upper.startswith("NIFTYIT") and
                "BANK" not in symbol_upper and
                "FIN" not in symbol_upper and
                "MIDCP" not in symbol_upper
            )
            
            if not is_nifty:
                continue
                
            # Apply filters
            if expiry_date and instrument.expiry != expiry_date:
                continue
            if option_type and instrument.option_type != option_type:
                continue
            if min_strike and instrument.strike and instrument.strike < min_strike:
                continue
            if max_strike and instrument.strike and instrument.strike > max_strike:
                continue
                
            results.append(instrument)
        
        # Sort by strike price
        results.sort(key=lambda x: x.strike or 0)
        return results
    
    def get_nearest_expiry(self, underlying: str = "NIFTY") -> Optional[date]:
        """
        Get the nearest expiry date for an underlying.
        
        Args:
            underlying: Underlying name (NIFTY, BANKNIFTY)
            
        Returns:
            Nearest expiry date
        """
        today = date.today()
        expiries = set()
        
        for instrument in self._instruments.values():
            if instrument.exchange != "NFO":
                continue
            
            name_upper = instrument.name.upper()
            underlying_upper = underlying.upper()
            
            # For NIFTY, exclude BANKNIFTY, FINNIFTY, MIDCPNIFTY
            if underlying_upper == "NIFTY":
                if not (name_upper.startswith("NIFTY") or " NIFTY" in name_upper):
                    continue
                if any(x in name_upper for x in ["BANK", "FIN", "MIDCP", "MIDCAP"]):
                    continue
            else:
                # For other underlyings, simple match
                if underlying_upper not in name_upper:
                    continue
                    
            if instrument.expiry and instrument.expiry >= today:
                expiries.add(instrument.expiry)
        
        if expiries:
            return min(expiries)
        return None
    
    def get_strikes_around_price(
        self,
        price: float,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        num_strikes: int = 5,
        option_type: Optional[OptionType] = None
    ) -> List[Instrument]:
        """
        Get strike prices around a given price level.
        
        Args:
            price: Current price to find strikes around
            underlying: Underlying name
            expiry: Expiry date (uses nearest if None)
            num_strikes: Number of strikes on each side
            option_type: Filter by CE or PE
            
        Returns:
            List of instruments sorted by proximity to price
        """
        if expiry is None:
            expiry = self.get_nearest_expiry(underlying)
            
        if not expiry:
            logger.warning(f"No expiry found for {underlying}")
            return []
        
        # Get all options for this expiry
        options = self.get_nifty_options(
            expiry_date=expiry,
            option_type=option_type
        )
        
        if not options:
            return []
        
        # Sort by distance from current price
        options.sort(key=lambda x: abs((x.strike or 0) - price))
        
        return options[:num_strikes * 2]
    
    @property
    def is_loaded(self) -> bool:
        """Check if instrument data is loaded."""
        return len(self._instruments) > 0
    
    @property
    def instrument_count(self) -> int:
        """Get total instrument count."""
        return len(self._instruments)


# Singleton instance
_master_instance: Optional[InstrumentMaster] = None


def get_instrument_master() -> InstrumentMaster:
    """Get the global instrument master instance."""
    global _master_instance
    if _master_instance is None:
        _master_instance = InstrumentMaster()
    return _master_instance
