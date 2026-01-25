"""
Synchronizer Module

Ensures Index and Option candles are time-aligned before divergence analysis.
Emits synchronized candle pairs for downstream processing.
"""

from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, List, Tuple
from dataclasses import dataclass
from collections import defaultdict, deque

from loguru import logger

from data.candle_builder import Candle, CandleAggregator


@dataclass
class SyncedCandlePair:
    """
    Synchronized Index + Option candle pair.
    
    Only emitted when both candles have the same timestamp.
    """
    timestamp: datetime
    index_candle: Candle
    option_candle: Candle
    sync_delay_ms: float = 0.0  # Time between the two candles arriving
    
    @property
    def index_close(self) -> float:
        """Get index close price."""
        return self.index_candle.close
    
    @property
    def option_close(self) -> float:
        """Get option close price."""
        return self.option_candle.close
    
    def __str__(self) -> str:
        return (
            f"SyncedPair @ {self.timestamp.strftime('%H:%M')} | "
            f"Index: {self.index_close:.2f} | Option: {self.option_close:.2f}"
        )


# Type alias for sync callback
SyncCallback = Callable[[SyncedCandlePair], None]


class CandleSynchronizer:
    """
    Synchronizes candles from multiple instruments.
    
    Logic:
    1. Receives candles from Index and Option aggregators
    2. Buffers candles until both are available for same timestamp
    3. Emits SyncedCandlePair when matched
    4. Handles late arrivals within tolerance window
    
    Time Alignment:
    - Uses candle open timestamp as the sync key
    - Both candles must have identical timestamps to pair
    """
    
    def __init__(
        self,
        index_token: str,
        option_token: str,
        sync_tolerance_ms: float = 500.0,
        max_buffer_size: int = 10
    ):
        """
        Initialize synchronizer.
        
        Args:
            index_token: Index instrument token
            option_token: Option instrument token
            sync_tolerance_ms: Max time to wait for matching candle
            max_buffer_size: Max unpaired candles to buffer
        """
        self.index_token = index_token
        self.option_token = option_token
        self.sync_tolerance_ms = sync_tolerance_ms
        self.max_buffer_size = max_buffer_size
        
        # Pending candles waiting for match
        self._index_pending: Dict[datetime, Tuple[Candle, datetime]] = {}
        self._option_pending: Dict[datetime, Tuple[Candle, datetime]] = {}
        
        # Synced pairs history - use deque for O(1) append and automatic size limiting
        self._history_size = 100
        self._synced_history: deque = deque(maxlen=self._history_size)
        
        # Callbacks
        self._callbacks: List[SyncCallback] = []
        
        # Stats
        self._pairs_emitted = 0
        self._index_candles_received = 0
        self._option_candles_received = 0
        self._missed_syncs = 0
        
    def _try_pair(self, candle_time: datetime) -> Optional[SyncedCandlePair]:
        """
        Try to create a synced pair for a given timestamp.
        
        Args:
            candle_time: Candle timestamp to match
            
        Returns:
            SyncedCandlePair if both candles available, None otherwise
        """
        index_entry = self._index_pending.get(candle_time)
        option_entry = self._option_pending.get(candle_time)
        
        if index_entry and option_entry:
            index_candle, index_arrival = index_entry
            option_candle, option_arrival = option_entry
            
            # Calculate sync delay
            sync_delay_ms = abs((index_arrival - option_arrival).total_seconds() * 1000)
            
            pair = SyncedCandlePair(
                timestamp=candle_time,
                index_candle=index_candle,
                option_candle=option_candle,
                sync_delay_ms=sync_delay_ms
            )
            
            # Remove from pending
            del self._index_pending[candle_time]
            del self._option_pending[candle_time]
            
            # Add to history - deque automatically maintains maxlen
            self._synced_history.append(pair)
            
            self._pairs_emitted += 1
            
            logger.debug(f"Synced pair: {pair}")
            return pair
            
        return None
    
    def _cleanup_stale(self) -> None:
        """Remove stale pending candles that exceeded tolerance."""
        now = datetime.now()
        tolerance = timedelta(milliseconds=self.sync_tolerance_ms * 10)  # 10x buffer
        
        # Clean index pending
        stale_times = [
            t for t, (_, arrival) in self._index_pending.items()
            if now - arrival > tolerance
        ]
        for t in stale_times:
            del self._index_pending[t]
            self._missed_syncs += 1
            logger.warning(f"Dropped stale index candle: {t}")
            
        # Clean option pending
        stale_times = [
            t for t, (_, arrival) in self._option_pending.items()
            if now - arrival > tolerance
        ]
        for t in stale_times:
            del self._option_pending[t]
            self._missed_syncs += 1
            logger.warning(f"Dropped stale option candle: {t}")
            
        # Enforce max buffer size
        while len(self._index_pending) > self.max_buffer_size:
            oldest = min(self._index_pending.keys())
            del self._index_pending[oldest]
            
        while len(self._option_pending) > self.max_buffer_size:
            oldest = min(self._option_pending.keys())
            del self._option_pending[oldest]
    
    def on_candle(self, candle: Candle) -> Optional[SyncedCandlePair]:
        """
        Process a completed candle.
        
        Args:
            candle: Completed candle from aggregator
            
        Returns:
            SyncedCandlePair if pair completed, None otherwise
        """
        now = datetime.now()
        candle_time = candle.timestamp
        
        pair = None
        
        if candle.token == self.index_token:
            self._index_candles_received += 1
            self._index_pending[candle_time] = (candle, now)
            pair = self._try_pair(candle_time)
            
        elif candle.token == self.option_token:
            self._option_candles_received += 1
            self._option_pending[candle_time] = (candle, now)
            pair = self._try_pair(candle_time)
        
        # Cleanup periodically
        if (self._index_candles_received + self._option_candles_received) % 10 == 0:
            self._cleanup_stale()
        
        # Notify callbacks
        if pair:
            for callback in self._callbacks:
                try:
                    callback(pair)
                except Exception as e:
                    logger.error(f"Sync callback error: {e}")
        
        return pair
    
    def update_option_token(self, new_token: str) -> None:
        """
        Update the option token (when strike changes).
        
        Args:
            new_token: New option token to sync
        """
        old_token = self.option_token
        self.option_token = new_token
        self._option_pending.clear()
        logger.info(f"Option token updated: {old_token} -> {new_token}")
    
    def add_callback(self, callback: SyncCallback) -> None:
        """
        Add callback for synced pairs.
        
        Args:
            callback: Function to call with SyncedCandlePair
        """
        self._callbacks.append(callback)
        
    def remove_callback(self, callback: SyncCallback) -> None:
        """Remove a sync callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def get_synced_history(self, count: Optional[int] = None) -> List[SyncedCandlePair]:
        """
        Get history of synced pairs.
        
        Args:
            count: Number of pairs to return (None for all)
            
        Returns:
            List of synced pairs (oldest first)
        """
        if count:
            return self._synced_history[-count:]
        return self._synced_history.copy()
    
    def get_latest_pair(self) -> Optional[SyncedCandlePair]:
        """Get most recent synced pair."""
        return self._synced_history[-1] if self._synced_history else None
    
    def clear(self) -> None:
        """Clear all pending and history data."""
        self._index_pending.clear()
        self._option_pending.clear()
        self._synced_history.clear()
        
    @property
    def stats(self) -> Dict:
        """Get synchronization statistics."""
        return {
            "pairs_emitted": self._pairs_emitted,
            "index_candles_received": self._index_candles_received,
            "option_candles_received": self._option_candles_received,
            "missed_syncs": self._missed_syncs,
            "pending_index": len(self._index_pending),
            "pending_option": len(self._option_pending),
            "sync_rate": (
                self._pairs_emitted / max(self._index_candles_received, 1) * 100
            )
        }


class MultiTokenSynchronizer:
    """
    Synchronizes candles across multiple token pairs.
    
    Useful when monitoring multiple option strikes simultaneously.
    """
    
    def __init__(self, index_token: str):
        """
        Initialize multi-token synchronizer.
        
        Args:
            index_token: Index token to sync against
        """
        self.index_token = index_token
        self._synchronizers: Dict[str, CandleSynchronizer] = {}
        
    def add_option(self, option_token: str) -> CandleSynchronizer:
        """
        Add an option token to synchronize.
        
        Args:
            option_token: Option token to add
            
        Returns:
            CandleSynchronizer for this pair
        """
        if option_token not in self._synchronizers:
            self._synchronizers[option_token] = CandleSynchronizer(
                index_token=self.index_token,
                option_token=option_token
            )
        return self._synchronizers[option_token]
    
    def remove_option(self, option_token: str) -> None:
        """Remove an option token."""
        self._synchronizers.pop(option_token, None)
    
    def on_candle(self, candle: Candle) -> List[SyncedCandlePair]:
        """
        Process a candle across all synchronizers.
        
        Args:
            candle: Completed candle
            
        Returns:
            List of any synced pairs created
        """
        pairs = []
        for sync in self._synchronizers.values():
            pair = sync.on_candle(candle)
            if pair:
                pairs.append(pair)
        return pairs
    
    def get_synchronizer(self, option_token: str) -> Optional[CandleSynchronizer]:
        """Get synchronizer for a specific option."""
        return self._synchronizers.get(option_token)
