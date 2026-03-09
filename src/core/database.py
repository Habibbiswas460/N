"""
Trading Database Module
SQLite-based persistence for trading data

Features:
- Daily session tracking
- Trade history with full details
- Auto-save on every trade
- Day-wise aggregation
- Recovery on restart
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from pathlib import Path
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Trade record"""
    id: Optional[int] = None
    session_id: int = 0
    trade_date: str = ""
    
    # Entry
    entry_time: str = ""
    direction: str = ""  # CE or PE
    symbol: str = "NIFTY"
    strike: int = 0
    quantity: int = 75
    entry_price: float = 0.0
    
    # Exit
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    
    # P&L
    pnl_points: Optional[float] = None
    pnl_rupees: Optional[float] = None
    
    # Context
    regime: str = ""
    regime_confidence: float = 0.0
    vwap: float = 0.0
    vwap_position: str = ""
    sl_price: float = 0.0
    
    # Status
    status: str = "OPEN"  # OPEN, CLOSED_TP, CLOSED_SL, CLOSED_TIME
    
    # Metadata
    notes: str = ""


@dataclass 
class Session:
    """Trading session (one per day)"""
    id: Optional[int] = None
    session_date: str = ""
    start_time: str = ""
    end_time: Optional[str] = None
    
    # Stats
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    
    # Status
    status: str = "ACTIVE"  # ACTIVE, COMPLETED, INTERRUPTED
    
    # Market context
    market_open: float = 0.0
    market_close: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0


class TradingDatabase:
    """
    SQLite database for trading data
    
    Usage:
        db = TradingDatabase()
        session_id = db.start_session()
        trade_id = db.add_trade(...)
        db.close_trade(trade_id, exit_price, reason)
        summary = db.get_session_summary(session_id)
    """
    
    def __init__(self, db_path: str = "data/trading.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_database()
        logger.info(f"📊 Database initialized: {self.db_path}")
        
    def _init_database(self):
        """Create tables if not exist"""
        with self._get_connection() as conn:
            # Sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    total_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'ACTIVE',
                    market_open REAL DEFAULT 0.0,
                    market_close REAL DEFAULT 0.0,
                    day_high REAL DEFAULT 0.0,
                    day_low REAL DEFAULT 0.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Trades table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    trade_date TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    symbol TEXT DEFAULT 'NIFTY',
                    strike INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 75,
                    entry_price REAL NOT NULL,
                    exit_time TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    pnl_points REAL,
                    pnl_rupees REAL,
                    regime TEXT,
                    regime_confidence REAL,
                    vwap REAL,
                    vwap_position TEXT,
                    sl_price REAL,
                    status TEXT DEFAULT 'OPEN',
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """)
            
            # Daily stats view
            conn.execute("""
                CREATE VIEW IF NOT EXISTS daily_stats AS
                SELECT 
                    trade_date,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_rupees < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(COALESCE(pnl_rupees, 0)) as total_pnl,
                    AVG(CASE WHEN pnl_rupees > 0 THEN pnl_rupees END) as avg_win,
                    AVG(CASE WHEN pnl_rupees < 0 THEN pnl_rupees END) as avg_loss,
                    MAX(pnl_rupees) as best_trade,
                    MIN(pnl_rupees) as worst_trade
                FROM trades
                WHERE status != 'OPEN'
                GROUP BY trade_date
            """)
            
            conn.commit()
            
    @contextmanager
    def _get_connection(self):
        """Get database connection with auto-commit"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
            
    # ========== SESSION MANAGEMENT ==========
    
    def start_session(self, market_open: float = 0.0) -> int:
        """Start new trading session, returns session_id"""
        today = date.today().isoformat()
        now = datetime.now().strftime("%H:%M:%S")
        
        # Check if session exists for today
        existing = self.get_today_session()
        if existing:
            logger.info(f"📅 Resuming existing session #{existing['id']}")
            return existing['id']
        
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO sessions (session_date, start_time, market_open)
                VALUES (?, ?, ?)
            """, (today, now, market_open))
            conn.commit()
            session_id = cursor.lastrowid
            
        logger.info(f"📅 Started new session #{session_id} for {today}")
        return session_id
        
    def end_session(self, session_id: int, market_close: float = 0.0):
        """End trading session"""
        now = datetime.now().strftime("%H:%M:%S")
        
        with self._get_connection() as conn:
            # Get session stats
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_rupees < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(COALESCE(pnl_rupees, 0)) as pnl
                FROM trades WHERE session_id = ? AND status != 'OPEN'
            """, (session_id,)).fetchone()
            
            conn.execute("""
                UPDATE sessions 
                SET end_time = ?, 
                    status = 'COMPLETED',
                    market_close = ?,
                    total_trades = ?,
                    wins = ?,
                    losses = ?,
                    total_pnl = ?
                WHERE id = ?
            """, (now, market_close, stats['total'], stats['wins'], 
                  stats['losses'], stats['pnl'], session_id))
            conn.commit()
            
        logger.info(f"📅 Session #{session_id} ended - {stats['total']} trades, ₹{stats['pnl']:,.0f}")
        
    def get_today_session(self) -> Optional[Dict]:
        """Get today's session if exists"""
        today = date.today().isoformat()
        
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM sessions WHERE session_date = ?
            """, (today,)).fetchone()
            
        return dict(row) if row else None
        
    def get_last_session(self) -> Optional[Dict]:
        """Get most recent session"""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM sessions ORDER BY id DESC LIMIT 1
            """).fetchone()
            
        return dict(row) if row else None
        
    # ========== TRADE MANAGEMENT ==========
    
    def add_trade(
        self,
        session_id: int,
        direction: str,
        strike: int,
        entry_price: float,
        quantity: int = 75,
        symbol: str = "NIFTY",
        regime: str = "",
        regime_confidence: float = 0.0,
        vwap: float = 0.0,
        vwap_position: str = "",
        sl_price: float = 0.0,
        notes: str = ""
    ) -> int:
        """Add new trade entry, returns trade_id"""
        today = date.today().isoformat()
        now = datetime.now().strftime("%H:%M:%S")
        
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO trades (
                    session_id, trade_date, entry_time, direction, symbol,
                    strike, quantity, entry_price, regime, regime_confidence,
                    vwap, vwap_position, sl_price, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, today, now, direction, symbol, strike, quantity,
                  entry_price, regime, regime_confidence, vwap, vwap_position,
                  sl_price, notes))
            conn.commit()
            trade_id = cursor.lastrowid
            
        logger.info(f"📝 Trade #{trade_id} added: {direction} {symbol} {strike} @ ₹{entry_price}")
        return trade_id
        
    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str,
        status: str = "CLOSED_TP"
    ) -> Dict:
        """Close trade and calculate P&L"""
        now = datetime.now().strftime("%H:%M:%S")
        
        with self._get_connection() as conn:
            # Get trade details
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            
            if not trade:
                raise ValueError(f"Trade #{trade_id} not found")
                
            # Calculate P&L
            pnl_points = exit_price - trade['entry_price']
            pnl_rupees = pnl_points * trade['quantity']
            
            # Update trade
            conn.execute("""
                UPDATE trades SET
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    pnl_points = ?,
                    pnl_rupees = ?,
                    status = ?
                WHERE id = ?
            """, (now, exit_price, exit_reason, pnl_points, pnl_rupees, status, trade_id))
            conn.commit()
            
            # Get updated trade
            updated = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            
        emoji = "✅" if pnl_rupees >= 0 else "❌"
        logger.info(f"{emoji} Trade #{trade_id} closed @ ₹{exit_price} | P&L: ₹{pnl_rupees:,.0f}")
        
        return dict(updated)
        
    def get_trade(self, trade_id: int) -> Optional[Dict]:
        """Get trade by ID"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
        return dict(row) if row else None
        
    def get_open_trades(self, session_id: int = None) -> List[Dict]:
        """Get all open trades"""
        with self._get_connection() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE session_id = ? AND status = 'OPEN'",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE status = 'OPEN'"
                ).fetchall()
        return [dict(r) for r in rows]
        
    def get_session_trades(self, session_id: int) -> List[Dict]:
        """Get all trades for a session"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
        return [dict(r) for r in rows]
        
    # ========== STATISTICS ==========
    
    def get_session_summary(self, session_id: int) -> Dict:
        """Get session summary statistics"""
        with self._get_connection() as conn:
            # Session info
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            
            if not session:
                return {}
                
            # Trade stats
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN status != 'OPEN' AND pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN status != 'OPEN' AND pnl_rupees < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) as open_trades,
                    SUM(COALESCE(pnl_rupees, 0)) as total_pnl,
                    AVG(CASE WHEN pnl_rupees > 0 THEN pnl_rupees END) as avg_win,
                    AVG(CASE WHEN pnl_rupees < 0 THEN pnl_rupees END) as avg_loss,
                    MAX(pnl_rupees) as best_trade,
                    MIN(pnl_rupees) as worst_trade
                FROM trades WHERE session_id = ?
            """, (session_id,)).fetchone()
            
        return {
            "session": dict(session),
            "stats": {
                "total_trades": stats['total_trades'] or 0,
                "wins": stats['wins'] or 0,
                "losses": stats['losses'] or 0,
                "open_trades": stats['open_trades'] or 0,
                "total_pnl": stats['total_pnl'] or 0.0,
                "win_rate": (stats['wins'] / (stats['wins'] + stats['losses']) * 100) 
                           if (stats['wins'] or 0) + (stats['losses'] or 0) > 0 else 0,
                "avg_win": stats['avg_win'] or 0,
                "avg_loss": stats['avg_loss'] or 0,
                "best_trade": stats['best_trade'] or 0,
                "worst_trade": stats['worst_trade'] or 0
            }
        }
        
    def get_daily_stats(self, days: int = 30) -> List[Dict]:
        """Get daily stats for past N days"""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM daily_stats 
                ORDER BY trade_date DESC 
                LIMIT ?
            """, (days,)).fetchall()
        return [dict(r) for r in rows]
        
    def get_all_time_stats(self) -> Dict:
        """Get all-time trading statistics"""
        with self._get_connection() as conn:
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as total_wins,
                    SUM(CASE WHEN pnl_rupees < 0 THEN 1 ELSE 0 END) as total_losses,
                    SUM(COALESCE(pnl_rupees, 0)) as total_pnl,
                    AVG(CASE WHEN pnl_rupees > 0 THEN pnl_rupees END) as avg_win,
                    AVG(CASE WHEN pnl_rupees < 0 THEN pnl_rupees END) as avg_loss,
                    MAX(pnl_rupees) as best_trade,
                    MIN(pnl_rupees) as worst_trade,
                    COUNT(DISTINCT trade_date) as trading_days
                FROM trades WHERE status != 'OPEN'
            """).fetchone()
            
        total = (stats['total_wins'] or 0) + (stats['total_losses'] or 0)
        
        return {
            "total_trades": stats['total_trades'] or 0,
            "total_wins": stats['total_wins'] or 0,
            "total_losses": stats['total_losses'] or 0,
            "win_rate": (stats['total_wins'] / total * 100) if total > 0 else 0,
            "total_pnl": stats['total_pnl'] or 0,
            "avg_win": stats['avg_win'] or 0,
            "avg_loss": stats['avg_loss'] or 0,
            "best_trade": stats['best_trade'] or 0,
            "worst_trade": stats['worst_trade'] or 0,
            "trading_days": stats['trading_days'] or 0,
            "avg_daily_pnl": (stats['total_pnl'] / stats['trading_days']) 
                            if stats['trading_days'] else 0
        }
        
    def get_previous_session_pnl(self) -> float:
        """Get previous day's P&L (for display on restart)"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT SUM(COALESCE(pnl_rupees, 0)) as pnl
                FROM trades WHERE trade_date = ?
            """, (yesterday,)).fetchone()
            
        return row['pnl'] if row and row['pnl'] else 0.0
        
    # ========== RECOVERY ==========
    
    def recover_interrupted_session(self) -> Optional[int]:
        """Check for interrupted session and recover"""
        with self._get_connection() as conn:
            # Find interrupted session from today
            today = date.today().isoformat()
            session = conn.execute("""
                SELECT * FROM sessions 
                WHERE session_date = ? AND status = 'ACTIVE'
            """, (today,)).fetchone()
            
            if session:
                logger.warning(f"⚠️ Found interrupted session #{session['id']}, recovering...")
                return session['id']
                
        return None
    
    def get_today_stats(self) -> Dict[str, Any]:
        """Get today's trading stats for RiskManager/SLManager."""
        today = date.today().isoformat()
        
        with self._get_connection() as conn:
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN pnl_rupees < 0 THEN 1 ELSE 0 END) as losing_trades,
                    SUM(CASE WHEN status = 'CLOSED_SL' THEN 1 ELSE 0 END) as sl_hits,
                    SUM(COALESCE(pnl_rupees, 0)) as total_pnl,
                    COUNT(CASE WHEN notes LIKE '%reentry%' THEN 1 END) as reentries
                FROM trades WHERE trade_date = ?
            """, (today,)).fetchone()
            
        return {
            'total_trades': stats['total_trades'] or 0,
            'winning_trades': stats['winning_trades'] or 0,
            'losing_trades': stats['losing_trades'] or 0,
            'sl_hits': stats['sl_hits'] or 0,
            'total_pnl': stats['total_pnl'] or 0.0,
            'reentries': stats['reentries'] or 0,
            'sl_count': stats['sl_hits'] or 0,
            'trade_count': stats['total_trades'] or 0,
            'pnl': stats['total_pnl'] or 0.0
        }
    
    def update_today_stats(self, **kwargs) -> None:
        """Placeholder for backward compatibility - stats auto-update via trades."""
        pass


class DatabaseStateStore:
    """StateStore implementation backed by TradingDatabase."""
    
    def __init__(self, db: Optional[TradingDatabase] = None):
        self._db = db
    
    @property
    def db(self) -> TradingDatabase:
        if self._db is None:
            self._db = get_database()
        return self._db
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get today's stats from database."""
        return self.db.get_today_stats()
    
    def update_daily_stats(self, **kwargs) -> None:
        """Stats auto-update when trades are recorded."""
        pass
    
    def update_trade_sl(self, **kwargs) -> None:
        """Update SL for a trade - handled by SLManager directly."""
        pass


def get_state_store() -> DatabaseStateStore:
    """Get database-backed state store."""
    return DatabaseStateStore()


# Global instance
_db: Optional[TradingDatabase] = None


def get_database() -> TradingDatabase:
    """Get global database instance"""
    global _db
    if _db is None:
        _db = TradingDatabase()
    return _db


def initialize_database(db_path: str = "data/trading.db") -> TradingDatabase:
    """Initialize global database"""
    global _db
    _db = TradingDatabase(db_path)
    return _db


if __name__ == "__main__":
    # Test database
    db = TradingDatabase()
    
    # Start session
    session_id = db.start_session(market_open=23000.0)
    print(f"Session ID: {session_id}")
    
    # Add trade
    trade_id = db.add_trade(
        session_id=session_id,
        direction="CE",
        strike=23000,
        entry_price=150.0,
        regime="TRENDING_UP",
        regime_confidence=0.85,
        vwap=23050.0,
        vwap_position="ABOVE",
        sl_price=142.0
    )
    print(f"Trade ID: {trade_id}")
    
    # Close trade
    db.close_trade(trade_id, exit_price=165.0, exit_reason="Target", status="CLOSED_TP")
    
    # Get summary
    summary = db.get_session_summary(session_id)
    print(f"Summary: {json.dumps(summary, indent=2)}")
    
    # End session
    db.end_session(session_id, market_close=23100.0)
    
    print("\n✅ Database test complete!")
