"""
Historical Data Fetcher for Angel One SmartAPI

Fetches candle data for backtesting.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
import time

from loguru import logger

# Angel One API limit: ~7500 candles per request
# For 1-min candles: 375 candles/day × 20 days = 7500
MAX_CANDLES_PER_REQUEST = 7500
CANDLES_PER_DAY = 375  # Market hours: 9:15 to 15:30 = 375 minutes


@dataclass
class HistoricalCandle:
    """Single candle data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class HistoricalDataFetcher:
    """
    Fetches historical OHLC data from Angel One SmartAPI.
    
    Uses getCandleData API:
    - exchange: NSE, NFO, BSE, etc.
    - symboltoken: Instrument token
    - interval: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, ONE_HOUR, ONE_DAY
    - fromdate: Start date (YYYY-MM-DD HH:MM)
    - todate: End date (YYYY-MM-DD HH:MM)
    """
    
    def __init__(self, smart_api):
        """
        Args:
            smart_api: SmartConnect instance (logged in)
        """
        self.api = smart_api
    
    def fetch_candles(
        self,
        exchange: str,
        symbol: str,
        token: str,
        interval: str,
        from_date: datetime,
        to_date: datetime
    ) -> List[HistoricalCandle]:
        """
        Fetch historical candle data with pagination for large date ranges.
        
        Angel One API limits to ~7500 candles per request.
        This method automatically paginates for longer periods.
        
        Args:
            exchange: NSE, NFO, etc.
            symbol: Trading symbol
            token: Instrument token
            interval: ONE_MINUTE, FIVE_MINUTE, etc.
            from_date: Start datetime
            to_date: End datetime
            
        Returns:
            List of HistoricalCandle objects
        """
        all_candles = []
        
        # Calculate days requested
        total_days = (to_date - from_date).days
        
        # For 1-min candles, we can fetch max ~18 trading days per request
        # (7500 candles / 375 candles per day ≈ 20 days, use 18 to be safe)
        chunk_days = 18
        
        current_from = from_date
        chunk_num = 1
        
        while current_from < to_date:
            current_to = min(current_from + timedelta(days=chunk_days), to_date)
            
            try:
                params = {
                    "exchange": exchange,
                    "symboltoken": token,
                    "interval": interval,
                    "fromdate": current_from.strftime("%Y-%m-%d %H:%M"),
                    "todate": current_to.strftime("%Y-%m-%d %H:%M")
                }
                
                logger.debug(f"Fetching chunk {chunk_num}: {current_from.date()} to {current_to.date()}")
                
                response = self.api.getCandleData(params)
                
                if response and response.get("status"):
                    data = response.get("data", [])
                    
                    for row in data:
                        # Format: [timestamp, open, high, low, close, volume]
                        candle = HistoricalCandle(
                            timestamp=datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=int(row[5])
                        )
                        all_candles.append(candle)
                    
                    logger.debug(f"Chunk {chunk_num}: Got {len(data)} candles")
                else:
                    logger.warning(f"Chunk {chunk_num} failed: {response}")
                    
            except Exception as e:
                logger.error(f"Error fetching chunk {chunk_num}: {e}")
            
            # Move to next chunk
            current_from = current_to
            chunk_num += 1
            
            # Rate limiting - avoid hitting API too fast
            if current_from < to_date:
                time.sleep(0.3)
        
        # Remove duplicates (if any overlap) and sort by timestamp
        seen = set()
        unique_candles = []
        for c in all_candles:
            key = c.timestamp
            if key not in seen:
                seen.add(key)
                unique_candles.append(c)
        
        unique_candles.sort(key=lambda x: x.timestamp)
        
        logger.info(f"Fetched {len(unique_candles)} candles for {symbol} ({total_days} days)")
        
        return unique_candles
    
    def fetch_nifty_candles(
        self,
        days: int = 30,
        interval: str = "ONE_MINUTE"
    ) -> List[HistoricalCandle]:
        """
        Fetch NIFTY index candles for backtesting.
        
        Args:
            days: Number of days of history
            interval: Candle interval
            
        Returns:
            List of candles
        """
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        
        # NIFTY 50 token
        return self.fetch_candles(
            exchange="NSE",
            symbol="Nifty 50",
            token="99926000",
            interval=interval,
            from_date=from_date,
            to_date=to_date
        )
    
    def fetch_option_candles(
        self,
        symbol: str,
        token: str,
        days: int = 30,
        interval: str = "ONE_MINUTE"
    ) -> List[HistoricalCandle]:
        """
        Fetch option candles for backtesting.
        
        Args:
            symbol: Option symbol (e.g., NIFTY27JAN2625100CE)
            token: Option token
            days: Number of days of history
            interval: Candle interval
            
        Returns:
            List of candles
        """
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        
        return self.fetch_candles(
            exchange="NFO",
            symbol=symbol,
            token=token,
            interval=interval,
            from_date=from_date,
            to_date=to_date
        )
