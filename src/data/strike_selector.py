"""
Strike Selector Module

Selects optimal ATM option strike with premium filtering (₹90-₹110).
Handles dynamic re-evaluation based on index movement.
"""

from datetime import date, datetime
from typing import Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from data.instrument_master import (
    InstrumentMaster,
    Instrument,
    OptionType,
    get_instrument_master
)


@dataclass
class SelectedStrike:
    """Container for selected strike information."""
    instrument: Instrument
    token: str
    symbol: str
    strike: float
    expiry: date
    option_type: OptionType
    ltp: float  # Last traded price at selection time
    index_price_at_selection: float
    selection_time: datetime
    
    def __str__(self) -> str:
        return (
            f"{self.symbol} | Strike: {self.strike} | "
            f"LTP: ₹{self.ltp:.2f} | Index: {self.index_price_at_selection:.2f}"
        )


class StrikeSelector:
    """
    ATM Strike Selector with Premium Filtering.
    
    Selection Logic:
    1. Find ATM strike based on current index price
    2. Check ±3 strikes around ATM
    3. Filter by premium range (₹90-₹110)
    4. Select the strike closest to ATM within premium range
    
    Re-evaluation Triggers:
    - Every 15 minutes
    - Index moves ₹50 from selection price
    """
    
    def __init__(
        self,
        instrument_master: Optional[InstrumentMaster] = None,
        premium_min: float = 90.0,
        premium_max: float = 110.0,
        strike_range: int = 3,
        reeval_interval_minutes: int = 15,
        reeval_index_move: float = 50.0,
        default_option_type: OptionType = OptionType.CALL
    ):
        """
        Initialize strike selector.
        
        Args:
            instrument_master: Instrument master instance
            premium_min: Minimum acceptable premium
            premium_max: Maximum acceptable premium
            strike_range: Number of strikes to check on each side
            reeval_interval_minutes: Re-evaluate after these many minutes
            reeval_index_move: Re-evaluate if index moves this much
            default_option_type: Default to CE or PE
        """
        self._master = instrument_master or get_instrument_master()
        self.premium_min = premium_min
        self.premium_max = premium_max
        self.strike_range = strike_range
        self.reeval_interval_minutes = reeval_interval_minutes
        self.reeval_index_move = reeval_index_move
        self.default_option_type = default_option_type
        
        self._current_selection: Optional[SelectedStrike] = None
        self._last_evaluation_time: Optional[datetime] = None
        
    def get_atm_strike(self, index_price: float, underlying: str = "NIFTY") -> float:
        """
        Calculate ATM strike price.
        
        Args:
            index_price: Current index price
            underlying: Underlying name
            
        Returns:
            ATM strike price
        """
        # NIFTY strikes are in intervals of 50
        # BANKNIFTY strikes are in intervals of 100
        if "BANK" in underlying.upper():
            interval = 100
        else:
            interval = 50
            
        return round(index_price / interval) * interval
    
    def select_strike(
        self,
        index_price: float,
        option_premiums: dict[str, float],
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        option_type: Optional[OptionType] = None
    ) -> Optional[SelectedStrike]:
        """
        Select optimal strike based on premium filter.
        
        Args:
            index_price: Current index price
            option_premiums: Dict of {token: ltp} for option premiums
            underlying: Underlying name
            expiry: Target expiry (uses nearest if None)
            option_type: CE or PE (uses default if None)
            
        Returns:
            SelectedStrike if found, None otherwise
        """
        option_type = option_type or self.default_option_type
        
        if expiry is None:
            expiry = self._master.get_nearest_expiry(underlying)
            
        if not expiry:
            logger.error(f"No expiry found for {underlying}")
            return None
        
        # Calculate ATM strike
        atm_strike = self.get_atm_strike(index_price, underlying)
        logger.debug(f"ATM strike for {index_price:.2f}: {atm_strike}")
        
        # Get strikes around ATM
        candidates = self._master.get_strikes_around_price(
            price=index_price,
            underlying=underlying,
            expiry=expiry,
            num_strikes=self.strike_range,
            option_type=option_type
        )
        
        if not candidates:
            logger.warning(f"No option strikes found for {underlying} {expiry}")
            return None
        
        # Filter by premium range
        valid_strikes = []
        for inst in candidates:
            premium = option_premiums.get(inst.token)
            if premium is None:
                logger.debug(f"No premium for {inst.symbol} (token: {inst.token})")
                continue
                
            if self.premium_min <= premium <= self.premium_max:
                distance_from_atm = abs((inst.strike or 0) - atm_strike)
                valid_strikes.append((inst, premium, distance_from_atm))
                logger.debug(
                    f"Valid: {inst.symbol} | Strike: {inst.strike} | "
                    f"Premium: ₹{premium:.2f} | Distance: {distance_from_atm}"
                )
            else:
                logger.debug(
                    f"Filtered: {inst.symbol} | Premium: ₹{premium:.2f} "
                    f"(outside ₹{self.premium_min}-₹{self.premium_max})"
                )
        
        if not valid_strikes:
            logger.warning(
                f"No strikes within premium range ₹{self.premium_min}-₹{self.premium_max}"
            )
            return None
        
        # Select closest to ATM
        valid_strikes.sort(key=lambda x: x[2])  # Sort by distance from ATM
        best_instrument, best_premium, _ = valid_strikes[0]
        
        selection = SelectedStrike(
            instrument=best_instrument,
            token=best_instrument.token,
            symbol=best_instrument.symbol,
            strike=best_instrument.strike or 0,
            expiry=expiry,
            option_type=option_type,
            ltp=best_premium,
            index_price_at_selection=index_price,
            selection_time=datetime.now()
        )
        
        self._current_selection = selection
        self._last_evaluation_time = datetime.now()
        
        logger.success(f"Selected strike: {selection}")
        return selection
    
    def should_reevaluate(self, current_index_price: float) -> Tuple[bool, str]:
        """
        Check if strike should be re-evaluated.
        
        Args:
            current_index_price: Current index price
            
        Returns:
            Tuple of (should_reeval, reason)
        """
        if self._current_selection is None:
            return True, "No current selection"
        
        now = datetime.now()
        
        # Check time-based re-evaluation
        if self._last_evaluation_time:
            elapsed = (now - self._last_evaluation_time).total_seconds() / 60
            if elapsed >= self.reeval_interval_minutes:
                return True, f"Time elapsed: {elapsed:.1f} minutes"
        
        # Check price-based re-evaluation
        price_move = abs(
            current_index_price - self._current_selection.index_price_at_selection
        )
        if price_move >= self.reeval_index_move:
            return True, f"Index moved: {price_move:.2f} points"
        
        return False, "No re-evaluation needed"
    
    def get_strikes_for_premium_fetch(
        self,
        index_price: float,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        option_type: Optional[OptionType] = None
    ) -> List[Instrument]:
        """
        Get list of strikes that need premium data fetched.
        
        Args:
            index_price: Current index price
            underlying: Underlying name
            expiry: Target expiry
            option_type: CE or PE
            
        Returns:
            List of instruments to fetch premiums for
        """
        option_type = option_type or self.default_option_type
        
        if expiry is None:
            expiry = self._master.get_nearest_expiry(underlying)
            
        if not expiry:
            return []
        
        return self._master.get_strikes_around_price(
            price=index_price,
            underlying=underlying,
            expiry=expiry,
            num_strikes=self.strike_range,
            option_type=option_type
        )
    
    @property
    def current_selection(self) -> Optional[SelectedStrike]:
        """Get current selected strike."""
        return self._current_selection
    
    @property
    def current_token(self) -> Optional[str]:
        """Get current selected token."""
        return self._current_selection.token if self._current_selection else None
    
    def clear_selection(self) -> None:
        """Clear current selection."""
        self._current_selection = None
        self._last_evaluation_time = None
        logger.info("Strike selection cleared")


# Singleton instance
_selector_instance: Optional[StrikeSelector] = None


def get_strike_selector() -> StrikeSelector:
    """Get the global strike selector instance."""
    global _selector_instance
    if _selector_instance is None:
        _selector_instance = StrikeSelector()
    return _selector_instance


def initialize_strike_selector(
    premium_min: float = 90.0,
    premium_max: float = 110.0,
    **kwargs
) -> StrikeSelector:
    """
    Initialize the global strike selector with custom settings.
    
    Args:
        premium_min: Minimum acceptable premium
        premium_max: Maximum acceptable premium
        **kwargs: Additional StrikeSelector parameters
        
    Returns:
        Initialized StrikeSelector instance
    """
    global _selector_instance
    _selector_instance = StrikeSelector(
        premium_min=premium_min,
        premium_max=premium_max,
        **kwargs
    )
    return _selector_instance
