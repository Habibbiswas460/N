"""
State Store Module

SQLite-based persistence for trading state.
Enables crash recovery by saving FSM state and key levels.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

from loguru import logger


class StateStore:
    """
    SQLite-based state persistence.
    
    Stores:
    - Current FSM state
    - N-Structure levels (HL1, HL2, previous_high)
    - Active trade information
    - Daily statistics
    """
    
    def __init__(self, db_path: str = "data/state.db"):
        """
        Initialize state store.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_database()
        
    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # FSM State table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fsm_state (
                    id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data JSON
                )
            """)
            
            # N-Structure levels table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS n_structure (
                    id INTEGER PRIMARY KEY,
                    breakout_high REAL,
                    breakout_time TIMESTAMP,
                    hl1_price REAL,
                    hl1_time TIMESTAMP,
                    hl2_price REAL,
                    hl2_time TIMESTAMP,
                    recent_high REAL,
                    entry_trigger REAL,
                    divergence_confirmed INTEGER DEFAULT 0,
                    index_roc REAL,
                    option_roc REAL,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Active trade table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_trade (
                    id INTEGER PRIMARY KEY,
                    order_id TEXT,
                    symbol TEXT,
                    token TEXT,
                    entry_price REAL,
                    quantity INTEGER,
                    entry_time TIMESTAMP,
                    initial_sl REAL,
                    current_sl REAL,
                    sl_order_id TEXT,
                    trailing_active INTEGER DEFAULT 0,
                    breakeven_hit INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    exit_price REAL,
                    exit_time TIMESTAMP,
                    pnl REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Daily stats table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    consecutive_losses INTEGER DEFAULT 0,
                    signals_generated INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Trade log table (historical)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE,
                    order_id TEXT,
                    symbol TEXT,
                    token TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity INTEGER,
                    entry_time TIMESTAMP,
                    exit_time TIMESTAMP,
                    pnl REAL,
                    exit_reason TEXT,
                    n_structure_data JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            
    @contextmanager
    def _get_connection(self):
        """Get database connection context manager."""
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    # === FSM State ===
    
    def save_fsm_state(self, state: str, data: Dict[str, Any] = None) -> None:
        """
        Save current FSM state.
        
        Args:
            state: State name
            data: Additional state data
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO fsm_state (id, state, updated_at, data)
                VALUES (1, ?, ?, ?)
            """, (state, datetime.now(), json.dumps(data or {})))
            conn.commit()
            
    def get_fsm_state(self) -> Optional[Dict[str, Any]]:
        """
        Get saved FSM state.
        
        Returns:
            Dict with 'state' and 'data' keys, or None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state, data, updated_at FROM fsm_state WHERE id = 1")
            row = cursor.fetchone()
            
            if row:
                return {
                    'state': row['state'],
                    'data': json.loads(row['data']) if row['data'] else {},
                    'updated_at': row['updated_at']
                }
            return None
    
    # === N-Structure ===
    
    def save_n_structure(self, structure_data: Dict[str, Any]) -> None:
        """
        Save N-Structure data.
        
        Args:
            structure_data: Dict with structure fields
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO n_structure (
                    id, breakout_high, breakout_time, hl1_price, hl1_time,
                    hl2_price, hl2_time, recent_high, entry_trigger,
                    divergence_confirmed, index_roc, option_roc, status, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                structure_data.get('breakout_high'),
                structure_data.get('breakout_time'),
                structure_data.get('hl1_price'),
                structure_data.get('hl1_time'),
                structure_data.get('hl2_price'),
                structure_data.get('hl2_time'),
                structure_data.get('recent_high'),
                structure_data.get('entry_trigger'),
                1 if structure_data.get('divergence_confirmed') else 0,
                structure_data.get('index_roc'),
                structure_data.get('option_roc'),
                structure_data.get('status'),
                datetime.now()
            ))
            conn.commit()
            
    def get_n_structure(self) -> Optional[Dict[str, Any]]:
        """Get saved N-Structure data."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM n_structure WHERE id = 1")
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    def clear_n_structure(self) -> None:
        """Clear N-Structure data."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM n_structure WHERE id = 1")
            conn.commit()
    
    # === Active Trade ===
    
    def save_active_trade(self, trade_data: Dict[str, Any]) -> None:
        """Save active trade information."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO active_trade (
                    id, order_id, symbol, token, entry_price, quantity,
                    entry_time, initial_sl, current_sl, sl_order_id,
                    trailing_active, breakeven_hit, status
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get('order_id'),
                trade_data.get('symbol'),
                trade_data.get('token'),
                trade_data.get('entry_price'),
                trade_data.get('quantity'),
                trade_data.get('entry_time'),
                trade_data.get('initial_sl'),
                trade_data.get('current_sl'),
                trade_data.get('sl_order_id'),
                1 if trade_data.get('trailing_active') else 0,
                1 if trade_data.get('breakeven_hit') else 0,
                trade_data.get('status', 'active')
            ))
            conn.commit()
            
    def get_active_trade(self) -> Optional[Dict[str, Any]]:
        """Get active trade information."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_trade WHERE id = 1 AND status = 'active'")
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    def update_trade_sl(self, new_sl: float, sl_order_id: str = None, trailing_active: bool = False, breakeven_hit: bool = False) -> None:
        """Update trade stop loss level."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE active_trade
                SET current_sl = ?, sl_order_id = COALESCE(?, sl_order_id),
                    trailing_active = ?, breakeven_hit = ?
                WHERE id = 1 AND status = 'active'
            """, (new_sl, sl_order_id, 1 if trailing_active else 0, 1 if breakeven_hit else 0))
            conn.commit()
            
    def close_trade(self, exit_price: float, pnl: float, exit_reason: str = None) -> None:
        """Close active trade."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get current trade data
            cursor.execute("SELECT * FROM active_trade WHERE id = 1 AND status = 'active'")
            trade = cursor.fetchone()
            
            if trade:
                # Update active trade status
                cursor.execute("""
                    UPDATE active_trade
                    SET status = 'closed', exit_price = ?, exit_time = ?, pnl = ?
                    WHERE id = 1
                """, (exit_price, datetime.now(), pnl))
                
                # Add to trade log
                cursor.execute("""
                    INSERT INTO trade_log (
                        date, order_id, symbol, token, entry_price, exit_price,
                        quantity, entry_time, exit_time, pnl, exit_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().date(),
                    trade['order_id'],
                    trade['symbol'],
                    trade['token'],
                    trade['entry_price'],
                    exit_price,
                    trade['quantity'],
                    trade['entry_time'],
                    datetime.now(),
                    pnl,
                    exit_reason
                ))
                
                conn.commit()
                
    def clear_active_trade(self) -> None:
        """Clear active trade data."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_trade WHERE id = 1")
            conn.commit()
    
    # === Daily Stats ===
    
    def get_daily_stats(self, date: datetime = None) -> Dict[str, Any]:
        """Get daily statistics."""
        date = date or datetime.now()
        date_str = date.strftime("%Y-%m-%d")
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date_str,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            
            # Create default stats for today
            cursor.execute("""
                INSERT INTO daily_stats (date) VALUES (?)
            """, (date_str,))
            conn.commit()
            
            return {
                'date': date_str,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0,
                'max_drawdown': 0,
                'consecutive_losses': 0,
                'signals_generated': 0
            }
    
    def update_daily_stats(
        self,
        trade_pnl: float = None,
        signal_generated: bool = False,
        date: datetime = None
    ) -> None:
        """Update daily statistics."""
        date = date or datetime.now()
        date_str = date.strftime("%Y-%m-%d")
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Ensure row exists
            self.get_daily_stats(date)
            
            if trade_pnl is not None:
                is_win = trade_pnl > 0
                cursor.execute("""
                    UPDATE daily_stats
                    SET total_trades = total_trades + 1,
                        winning_trades = winning_trades + ?,
                        losing_trades = losing_trades + ?,
                        total_pnl = total_pnl + ?,
                        consecutive_losses = CASE WHEN ? > 0 THEN 0 ELSE consecutive_losses + 1 END,
                        updated_at = ?
                    WHERE date = ?
                """, (
                    1 if is_win else 0,
                    0 if is_win else 1,
                    trade_pnl,
                    trade_pnl,
                    datetime.now(),
                    date_str
                ))
                
            if signal_generated:
                cursor.execute("""
                    UPDATE daily_stats
                    SET signals_generated = signals_generated + 1,
                        updated_at = ?
                    WHERE date = ?
                """, (datetime.now(), date_str))
                
            conn.commit()
    
    # === Trade Log ===
    
    def get_trade_history(self, limit: int = 50, date: datetime = None) -> List[Dict[str, Any]]:
        """Get trade history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if date:
                cursor.execute("""
                    SELECT * FROM trade_log
                    WHERE date = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (date.strftime("%Y-%m-%d"), limit))
            else:
                cursor.execute("""
                    SELECT * FROM trade_log
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                
            return [dict(row) for row in cursor.fetchall()]
    
    # === Cleanup ===
    
    def reset_daily(self) -> None:
        """Reset for new trading day."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fsm_state")
            cursor.execute("DELETE FROM n_structure")
            cursor.execute("DELETE FROM active_trade")
            conn.commit()
            
        logger.info("Daily state reset complete")
        
    def close(self) -> None:
        """Close any open connections."""
        pass  # Connections are handled via context manager


# Singleton instance
_store_instance: Optional[StateStore] = None


def get_state_store(db_path: str = "data/state.db") -> StateStore:
    """Get the global state store instance."""
    global _store_instance
    if _store_instance is None:
        _store_instance = StateStore(db_path)
    return _store_instance
