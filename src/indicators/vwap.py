"""
VWAP (Volume Weighted Average Price) Indicator
Used for intraday trend bias detection
"""
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, time
import logging

logger = logging.getLogger(__name__)


@dataclass
class VWAPData:
    """VWAP calculation result"""
    vwap: float
    upper_band: float  # VWAP + 1 std dev
    lower_band: float  # VWAP - 1 std dev
    cumulative_volume: float
    cumulative_tp_volume: float  # typical price * volume
    price_position: str  # "ABOVE", "BELOW", "AT"
    

class VWAPIndicator:
    """
    Intraday VWAP Calculator
    
    VWAP = Cumulative(Typical Price × Volume) / Cumulative(Volume)
    Typical Price = (High + Low + Close) / 3
    
    Trading signals:
    - Price above VWAP = Bullish bias (favor CE)
    - Price below VWAP = Bearish bias (favor PE)
    - Price at VWAP = Neutral, wait for direction
    """
    
    def __init__(self, band_multiplier: float = 1.0):
        """
        Args:
            band_multiplier: Standard deviation multiplier for bands (default 1.0)
        """
        self.band_multiplier = band_multiplier
        self._reset_session()
        
    def _reset_session(self):
        """Reset for new trading session"""
        self.cumulative_volume = 0.0
        self.cumulative_tp_volume = 0.0
        self.squared_deviations = 0.0
        self.candle_count = 0
        self.last_reset_date: Optional[datetime] = None
        self._prices: List[float] = []
        self._volumes: List[float] = []
        
    def _check_session_reset(self, timestamp: datetime):
        """Reset VWAP at market open (9:15 AM)"""
        market_open = time(9, 15)
        
        if self.last_reset_date is None:
            self._reset_session()
            self.last_reset_date = timestamp
        elif timestamp.date() != self.last_reset_date.date():
            # New day - reset
            logger.info(f"VWAP: New session detected, resetting")
            self._reset_session()
            self.last_reset_date = timestamp
        elif (timestamp.time() >= market_open and 
              self.last_reset_date.time() < market_open):
            # Crossed market open time
            logger.info(f"VWAP: Market open reset")
            self._reset_session()
            self.last_reset_date = timestamp
            
    def update(self, high: float, low: float, close: float, 
               volume: float, timestamp: datetime) -> Optional[VWAPData]:
        """
        Update VWAP with new candle data
        
        Args:
            high: Candle high price
            low: Candle low price  
            close: Candle close price
            volume: Candle volume
            timestamp: Candle timestamp
            
        Returns:
            VWAPData with current VWAP and bands
        """
        self._check_session_reset(timestamp)
        
        # For polling mode without volume, use 1 as default
        if volume <= 0:
            volume = 1.0
            
        # Calculate typical price
        typical_price = (high + low + close) / 3
        
        # Update cumulative values
        self.cumulative_volume += volume
        self.cumulative_tp_volume += typical_price * volume
        self.candle_count += 1
        
        # Store for standard deviation calculation
        self._prices.append(typical_price)
        self._volumes.append(volume)
        
        # Calculate VWAP
        if self.cumulative_volume <= 0:
            return None
            
        vwap = self.cumulative_tp_volume / self.cumulative_volume
        
        # Calculate standard deviation for bands
        std_dev = self._calculate_std_dev(vwap)
        upper_band = vwap + (std_dev * self.band_multiplier)
        lower_band = vwap - (std_dev * self.band_multiplier)
        
        # Determine price position relative to VWAP
        tolerance = 0.5  # 0.5 point tolerance for "AT"
        if close > vwap + tolerance:
            position = "ABOVE"
        elif close < vwap - tolerance:
            position = "BELOW"
        else:
            position = "AT"
            
        return VWAPData(
            vwap=round(vwap, 2),
            upper_band=round(upper_band, 2),
            lower_band=round(lower_band, 2),
            cumulative_volume=self.cumulative_volume,
            cumulative_tp_volume=self.cumulative_tp_volume,
            price_position=position
        )
        
    def _calculate_std_dev(self, vwap: float) -> float:
        """Calculate volume-weighted standard deviation"""
        if self.cumulative_volume <= 0 or len(self._prices) < 2:
            return 0.0
            
        # Volume-weighted variance
        weighted_variance = 0.0
        for price, vol in zip(self._prices, self._volumes):
            weighted_variance += vol * ((price - vwap) ** 2)
            
        variance = weighted_variance / self.cumulative_volume
        return variance ** 0.5
        
    def get_current_vwap(self) -> Optional[float]:
        """Get current VWAP value"""
        if self.cumulative_volume <= 0:
            return None
        return round(self.cumulative_tp_volume / self.cumulative_volume, 2)
        
    def get_current_data(self) -> Optional[VWAPData]:
        """Get current VWAP data with bands"""
        vwap = self.get_current_vwap()
        if vwap is None:
            return None
            
        std_dev = self._calculate_std_dev(vwap)
        upper_band = vwap + std_dev * self.band_multiplier
        lower_band = vwap - std_dev * self.band_multiplier
        
        return VWAPData(
            vwap=vwap,
            upper_band=round(upper_band, 2),
            lower_band=round(lower_band, 2),
            cumulative_volume=self.cumulative_volume,
            cumulative_tp_volume=self.cumulative_tp_volume,
            price_position="NEUTRAL"
        )
        
    def get_bias(self, current_price: float) -> str:
        """
        Get trading bias based on price vs VWAP
        
        Returns:
            "BULLISH" - Price above VWAP, favor CE
            "BEARISH" - Price below VWAP, favor PE
            "NEUTRAL" - Price at VWAP, no clear bias
        """
        vwap = self.get_current_vwap()
        if vwap is None:
            return "NEUTRAL"
            
        tolerance = 2.0  # 2 point tolerance
        if current_price > vwap + tolerance:
            return "BULLISH"
        elif current_price < vwap - tolerance:
            return "BEARISH"
        return "NEUTRAL"
        
    def reset(self):
        """Force reset VWAP"""
        self._reset_session()
