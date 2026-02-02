"""
Dynamic Strike Selector Module

Selects optimal strike ONLY when N-Structure is detected on INDEX.
Strategy:
1. Watch INDEX candles for N-Structure (HH + HL pattern)
2. When N-Structure confirmed, find strikes in 85-110 premium range
3. Select strike with best movement potential (based on delta/gamma)
4. Return selected strike for entry

This replaces upfront strike selection with on-demand selection.
"""

from datetime import date, datetime
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from data.instrument_master import (
    InstrumentMaster,
    Instrument,
    OptionType,
    get_instrument_master
)
from indicators.n_structure import NStructure, SignalDirection


@dataclass
class DynamicStrike:
    """Container for dynamically selected strike."""
    instrument: Instrument
    token: str
    symbol: str
    strike: float
    expiry: date
    option_type: OptionType
    premium: float
    index_price: float
    selection_time: datetime
    movement_score: float = 0.0  # Score based on expected movement
    
    def __str__(self) -> str:
        type_str = "CE" if self.option_type == OptionType.CALL else "PE"
        return (
            f"{type_str} Strike {int(self.strike)} | "
            f"Premium: ₹{self.premium:.2f} | "
            f"Score: {self.movement_score:.2f}"
        )


class DynamicStrikeSelector:
    """
    Dynamic Strike Selector - Selects strike AFTER N-Structure detection.
    
    Selection Logic (triggered when N-Structure forms on INDEX):
    1. Get all strikes within ±5 of ATM
    2. Filter by premium range (₹85-₹110)
    3. Score each strike by movement potential
    4. Select highest scoring strike
    
    Movement Scoring:
    - Closer to ATM = Higher delta = Better movement = Higher score
    - Premium in sweet spot (95-100) = Best balance = Bonus score
    - Recent momentum matching direction = Extra score
    """
    
    def __init__(
        self,
        instrument_master: Optional[InstrumentMaster] = None,
        premium_min: float = 85.0,
        premium_max: float = 110.0,
        premium_sweet_min: float = 90.0,
        premium_sweet_max: float = 100.0,
        strike_range: int = 5  # Check ±5 strikes around ATM
    ):
        """
        Initialize dynamic strike selector.
        
        Args:
            instrument_master: Instrument master instance
            premium_min: Minimum acceptable premium (₹85)
            premium_max: Maximum acceptable premium (₹110)
            premium_sweet_min: Sweet spot minimum (₹90)
            premium_sweet_max: Sweet spot maximum (₹100)
            strike_range: Number of strikes to check on each side
        """
        self._master = instrument_master or get_instrument_master()
        self.premium_min = premium_min
        self.premium_max = premium_max
        self.premium_sweet_min = premium_sweet_min
        self.premium_sweet_max = premium_sweet_max
        self.strike_range = strike_range
        
        self._selected_ce: Optional[DynamicStrike] = None
        self._selected_pe: Optional[DynamicStrike] = None
        self._last_index_price: float = 0.0
        
        logger.info(
            f"DynamicStrikeSelector initialized | "
            f"Premium Range: ₹{premium_min}-₹{premium_max} | "
            f"Sweet Spot: ₹{premium_sweet_min}-₹{premium_sweet_max}"
        )
    
    def get_atm_strike(self, index_price: float, underlying: str = "NIFTY") -> float:
        """Calculate ATM strike price."""
        interval = 100 if "BANK" in underlying.upper() else 50
        return round(index_price / interval) * interval
    
    def _calculate_movement_score(
        self,
        strike: float,
        premium: float,
        atm_strike: float,
        option_type: OptionType
    ) -> float:
        """
        Calculate movement potential score for a strike.
        
        Scoring Factors:
        1. Distance from ATM (closer = higher delta = better)
        2. Premium in sweet spot (bonus points)
        3. Type alignment (CE for bullish ATM, PE for bearish)
        
        Returns:
            Score from 0-100 (higher is better)
        """
        score = 50.0  # Base score
        
        # Factor 1: Distance from ATM (max 30 points)
        # ATM gets full points, OTM/ITM get less
        distance = abs(strike - atm_strike)
        distance_score = max(0, 30 - (distance / 50) * 10)  # -10 per 50 points
        score += distance_score
        
        # Factor 2: Premium in sweet spot (max 20 points)
        if self.premium_sweet_min <= premium <= self.premium_sweet_max:
            # Perfect sweet spot
            score += 20
        elif self.premium_min <= premium <= self.premium_max:
            # Within range but not sweet spot
            # Closer to sweet spot = more points
            if premium < self.premium_sweet_min:
                score += 10 * (premium - self.premium_min) / (self.premium_sweet_min - self.premium_min)
            else:
                score += 10 * (self.premium_max - premium) / (self.premium_max - self.premium_sweet_max)
        
        return score
    
    def select_strike_on_signal(
        self,
        n_structure: NStructure,
        index_price: float,
        premium_fetcher,  # Callable to get premium for a token
        underlying: str = "NIFTY",
        expiry: Optional[date] = None
    ) -> Optional[DynamicStrike]:
        """
        Select optimal strike when N-Structure signal is detected.
        
        This is the main method - called ONLY when INDEX shows valid N-Structure.
        
        Args:
            n_structure: Detected N-Structure pattern
            index_price: Current index price
            premium_fetcher: Function(token, symbol, exchange) -> premium
            underlying: Underlying name
            expiry: Target expiry (uses nearest if None)
            
        Returns:
            DynamicStrike if found, None otherwise
        """
        # Determine option type based on signal direction
        if n_structure.direction == SignalDirection.BULLISH:
            option_type = OptionType.CALL
            type_str = "CE"
        elif n_structure.direction == SignalDirection.BEARISH:
            option_type = OptionType.PUT
            type_str = "PE"
        else:
            logger.warning("N-Structure has no clear direction, skipping strike selection")
            return None
        
        logger.info(
            f"🎯 N-Structure {type_str} signal detected! "
            f"Selecting optimal strike in ₹{self.premium_min}-₹{self.premium_max} range..."
        )
        
        # Get expiry
        if expiry is None:
            expiry = self._master.get_nearest_expiry(underlying)
        
        if not expiry:
            logger.error(f"No expiry found for {underlying}")
            return None
        
        # Calculate ATM
        atm_strike = self.get_atm_strike(index_price, underlying)
        logger.debug(f"Index: {index_price:.2f} | ATM Strike: {atm_strike}")
        
        # Get candidate strikes around ATM
        candidates = self._master.get_strikes_around_price(
            price=index_price,
            underlying=underlying,
            expiry=expiry,
            num_strikes=self.strike_range,
            option_type=option_type
        )
        
        if not candidates:
            logger.warning(f"No {type_str} strikes found for {underlying} {expiry}")
            return None
        
        # Score each candidate
        scored_strikes: List[Tuple[Instrument, float, float]] = []  # (instrument, premium, score)
        
        for inst in candidates:
            # Fetch premium
            try:
                premium = premium_fetcher(inst.token, inst.symbol, "NFO")
                if premium is None or premium <= 0:
                    logger.debug(f"Skip {inst.symbol} - no premium data")
                    continue
            except Exception as e:
                logger.debug(f"Error fetching premium for {inst.symbol}: {e}")
                continue
            
            # Check premium range
            if not (self.premium_min <= premium <= self.premium_max):
                logger.debug(
                    f"Skip {inst.symbol} @ ₹{premium:.2f} - "
                    f"outside ₹{self.premium_min}-₹{self.premium_max}"
                )
                continue
            
            # Calculate score
            score = self._calculate_movement_score(
                strike=inst.strike or 0,
                premium=premium,
                atm_strike=atm_strike,
                option_type=option_type
            )
            
            scored_strikes.append((inst, premium, score))
            logger.debug(
                f"Candidate: {inst.symbol} | Strike: {inst.strike} | "
                f"Premium: ₹{premium:.2f} | Score: {score:.1f}"
            )
        
        if not scored_strikes:
            logger.warning(
                f"❌ No {type_str} strikes in ₹{self.premium_min}-₹{self.premium_max} range"
            )
            return None
        
        # Sort by score (highest first)
        scored_strikes.sort(key=lambda x: x[2], reverse=True)
        
        # Select best strike
        best_inst, best_premium, best_score = scored_strikes[0]
        
        selection = DynamicStrike(
            instrument=best_inst,
            token=best_inst.token,
            symbol=best_inst.symbol,
            strike=best_inst.strike or 0,
            expiry=expiry,
            option_type=option_type,
            premium=best_premium,
            index_price=index_price,
            selection_time=datetime.now(),
            movement_score=best_score
        )
        
        # Cache selection
        if option_type == OptionType.CALL:
            self._selected_ce = selection
        else:
            self._selected_pe = selection
        
        self._last_index_price = index_price
        
        logger.success(
            f"✅ SELECTED: {selection.symbol} | "
            f"Strike: {int(selection.strike)} | "
            f"Premium: ₹{selection.premium:.2f} | "
            f"Score: {selection.movement_score:.1f}/100"
        )
        
        return selection
    
    def get_all_candidates_in_range(
        self,
        index_price: float,
        premium_fetcher,
        option_type: OptionType,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None
    ) -> List[Dict]:
        """
        Get all strikes within premium range with their scores.
        Useful for logging/debugging strike selection.
        
        Returns:
            List of dicts with strike info
        """
        if expiry is None:
            expiry = self._master.get_nearest_expiry(underlying)
        
        if not expiry:
            return []
        
        atm_strike = self.get_atm_strike(index_price, underlying)
        
        candidates = self._master.get_strikes_around_price(
            price=index_price,
            underlying=underlying,
            expiry=expiry,
            num_strikes=self.strike_range,
            option_type=option_type
        )
        
        results = []
        for inst in candidates:
            try:
                premium = premium_fetcher(inst.token, inst.symbol, "NFO")
                if premium is None or premium <= 0:
                    continue
                
                in_range = self.premium_min <= premium <= self.premium_max
                score = self._calculate_movement_score(
                    strike=inst.strike or 0,
                    premium=premium,
                    atm_strike=atm_strike,
                    option_type=option_type
                ) if in_range else 0
                
                results.append({
                    'symbol': inst.symbol,
                    'token': inst.token,
                    'strike': inst.strike,
                    'premium': premium,
                    'in_range': in_range,
                    'score': score
                })
            except Exception:
                continue
        
        return sorted(results, key=lambda x: x['score'], reverse=True)
    
    @property
    def selected_ce(self) -> Optional[DynamicStrike]:
        """Get currently selected CE strike."""
        return self._selected_ce
    
    @property
    def selected_pe(self) -> Optional[DynamicStrike]:
        """Get currently selected PE strike."""
        return self._selected_pe
    
    def clear_selection(self, option_type: Optional[OptionType] = None) -> None:
        """Clear cached selection(s)."""
        if option_type is None or option_type == OptionType.CALL:
            self._selected_ce = None
        if option_type is None or option_type == OptionType.PUT:
            self._selected_pe = None
        logger.info("Strike selection cleared")


# Singleton instance
_dynamic_selector: Optional[DynamicStrikeSelector] = None


def get_dynamic_strike_selector() -> DynamicStrikeSelector:
    """Get the global dynamic strike selector instance."""
    global _dynamic_selector
    if _dynamic_selector is None:
        _dynamic_selector = DynamicStrikeSelector()
    return _dynamic_selector


def initialize_dynamic_strike_selector(
    premium_min: float = 85.0,
    premium_max: float = 110.0,
    **kwargs
) -> DynamicStrikeSelector:
    """
    Initialize the global dynamic strike selector.
    
    Args:
        premium_min: Minimum acceptable premium (default ₹85)
        premium_max: Maximum acceptable premium (default ₹110)
        **kwargs: Additional parameters
        
    Returns:
        Initialized DynamicStrikeSelector instance
    """
    global _dynamic_selector
    _dynamic_selector = DynamicStrikeSelector(
        premium_min=premium_min,
        premium_max=premium_max,
        **kwargs
    )
    return _dynamic_selector
