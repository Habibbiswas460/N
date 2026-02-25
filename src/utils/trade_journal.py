"""
Trade Journal - Auto-logs all trades to CSV for analysis
"""
import csv
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TradeStatus(Enum):
    """Trade status"""
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"      # Take profit
    CLOSED_SL = "CLOSED_SL"      # Stop loss
    CLOSED_TIME = "CLOSED_TIME"  # Time-based exit
    CLOSED_MANUAL = "CLOSED_MANUAL"


@dataclass
class TradeRecord:
    """Single trade record for journal"""
    # Identification
    trade_id: str
    date: str
    
    # Entry
    entry_time: str
    entry_price: float
    direction: str  # "CE" or "PE"
    symbol: str
    strike: int
    quantity: int
    
    # Market context at entry
    regime: str
    regime_confidence: float
    vwap: float
    vwap_position: str
    poc: float
    entry_reason: str
    
    # Exit (filled when closed)
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    status: str = TradeStatus.OPEN.value
    
    # P&L
    pnl_points: Optional[float] = None
    pnl_rupees: Optional[float] = None
    pnl_percent: Optional[float] = None
    
    # Risk metrics
    sl_price: Optional[float] = None
    sl_points: Optional[float] = None
    initial_risk: Optional[float] = None
    risk_reward: Optional[float] = None
    
    # Duration
    holding_minutes: Optional[int] = None
    
    # Additional context
    atr_at_entry: float = 0.0
    notes: str = ""


class TradeJournal:
    """
    Trade Journal - Logs all trades to CSV
    
    Usage:
        journal = TradeJournal()
        trade_id = journal.log_entry(...)
        journal.log_exit(trade_id, ...)
        journal.get_summary()
    """
    
    def __init__(self, log_dir: str = "data/journal"):
        """
        Initialize trade journal
        
        Args:
            log_dir: Directory to store journal CSV files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Current day's trades
        self._trades: Dict[str, TradeRecord] = {}
        self._trade_counter: int = 0
        self._current_date: Optional[date] = None
        
        # Load existing trades for today if any
        self._load_today_trades()
        
    def _get_csv_path(self, trade_date: date = None) -> Path:
        """Get CSV file path for a date"""
        if trade_date is None:
            trade_date = date.today()
        return self.log_dir / f"trades_{trade_date.strftime('%Y%m%d')}.csv"
        
    def _get_headers(self) -> List[str]:
        """Get CSV headers from TradeRecord fields"""
        return list(TradeRecord.__dataclass_fields__.keys())
        
    def _load_today_trades(self):
        """Load today's trades from CSV if exists"""
        csv_path = self._get_csv_path()
        if csv_path.exists():
            try:
                with open(csv_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        trade_id = row.get('trade_id', '')
                        if trade_id:
                            # Convert numeric fields
                            for field in ['entry_price', 'exit_price', 'vwap', 'poc', 
                                         'pnl_points', 'pnl_rupees', 'pnl_percent',
                                         'sl_price', 'sl_points', 'initial_risk', 
                                         'risk_reward', 'atr_at_entry', 'regime_confidence']:
                                if row.get(field) and row[field] != '':
                                    row[field] = float(row[field])
                                else:
                                    row[field] = None if 'price' in field or 'pnl' in field else 0.0
                                    
                            for field in ['strike', 'quantity', 'holding_minutes']:
                                if row.get(field) and row[field] != '':
                                    row[field] = int(float(row[field]))
                                else:
                                    row[field] = 0
                                    
                            self._trades[trade_id] = TradeRecord(**row)
                            # Update counter
                            try:
                                num = int(trade_id.split('_')[-1])
                                self._trade_counter = max(self._trade_counter, num)
                            except:
                                pass
                                
                logger.info(f"Loaded {len(self._trades)} trades from {csv_path}")
            except Exception as e:
                logger.error(f"Error loading trades: {e}")
                
    def _save_to_csv(self):
        """Save all trades to CSV"""
        csv_path = self._get_csv_path()
        try:
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self._get_headers())
                writer.writeheader()
                for trade in self._trades.values():
                    writer.writerow(asdict(trade))
            logger.debug(f"Saved {len(self._trades)} trades to {csv_path}")
        except Exception as e:
            logger.error(f"Error saving trades: {e}")
            
    def _generate_trade_id(self) -> str:
        """Generate unique trade ID"""
        self._trade_counter += 1
        return f"{date.today().strftime('%Y%m%d')}_{self._trade_counter:03d}"
        
    def log_entry(
        self,
        direction: str,
        symbol: str,
        strike: int,
        entry_price: float,
        quantity: int,
        regime: str,
        regime_confidence: float,
        vwap: float,
        vwap_position: str,
        poc: float,
        entry_reason: str,
        sl_price: float = None,
        atr_at_entry: float = 0.0,
        notes: str = ""
    ) -> str:
        """
        Log a new trade entry
        
        Returns trade_id for tracking
        """
        trade_id = self._generate_trade_id()
        now = datetime.now()
        
        sl_points = abs(entry_price - sl_price) if sl_price else None
        initial_risk = sl_points * quantity if sl_points else None
        
        trade = TradeRecord(
            trade_id=trade_id,
            date=now.strftime('%Y-%m-%d'),
            entry_time=now.strftime('%H:%M:%S'),
            entry_price=entry_price,
            direction=direction,
            symbol=symbol,
            strike=strike,
            quantity=quantity,
            regime=regime,
            regime_confidence=regime_confidence,
            vwap=vwap,
            vwap_position=vwap_position,
            poc=poc,
            entry_reason=entry_reason,
            sl_price=sl_price,
            sl_points=sl_points,
            initial_risk=initial_risk,
            atr_at_entry=atr_at_entry,
            notes=notes
        )
        
        self._trades[trade_id] = trade
        self._save_to_csv()
        
        logger.info(f"📝 Trade entry logged: {trade_id} | {direction} {symbol} {strike} @ ₹{entry_price}")
        return trade_id
        
    def log_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        status: TradeStatus = TradeStatus.CLOSED_MANUAL
    ) -> Optional[TradeRecord]:
        """
        Log trade exit
        
        Returns updated trade record
        """
        if trade_id not in self._trades:
            logger.warning(f"Trade {trade_id} not found")
            return None
            
        trade = self._trades[trade_id]
        now = datetime.now()
        
        # Calculate P&L
        if trade.direction == "CE":
            pnl_points = exit_price - trade.entry_price
        else:  # PE
            pnl_points = exit_price - trade.entry_price
            
        pnl_rupees = pnl_points * trade.quantity
        pnl_percent = (pnl_points / trade.entry_price) * 100 if trade.entry_price else 0
        
        # Calculate holding time
        try:
            entry_dt = datetime.strptime(f"{trade.date} {trade.entry_time}", '%Y-%m-%d %H:%M:%S')
            holding_minutes = int((now - entry_dt).total_seconds() / 60)
        except:
            holding_minutes = 0
            
        # Calculate risk/reward
        risk_reward = None
        if trade.sl_points and trade.sl_points > 0:
            risk_reward = pnl_points / trade.sl_points if pnl_points > 0 else pnl_points / trade.sl_points
            
        # Update trade record
        trade.exit_time = now.strftime('%H:%M:%S')
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.status = status.value
        trade.pnl_points = pnl_points
        trade.pnl_rupees = pnl_rupees
        trade.pnl_percent = pnl_percent
        trade.holding_minutes = holding_minutes
        trade.risk_reward = risk_reward
        
        self._save_to_csv()
        
        emoji = "✅" if pnl_rupees >= 0 else "❌"
        logger.info(f"{emoji} Trade exit logged: {trade_id} @ ₹{exit_price} | P&L: ₹{pnl_rupees:,.0f}")
        
        return trade
        
    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        """Get a specific trade record"""
        return self._trades.get(trade_id)
        
    def get_open_trades(self) -> List[TradeRecord]:
        """Get all open trades"""
        return [t for t in self._trades.values() if t.status == TradeStatus.OPEN.value]
        
    def get_closed_trades(self) -> List[TradeRecord]:
        """Get all closed trades"""
        return [t for t in self._trades.values() if t.status != TradeStatus.OPEN.value]
        
    def get_summary(self) -> Dict[str, Any]:
        """Get daily summary statistics"""
        closed = self.get_closed_trades()
        
        if not closed:
            return {
                "date": date.today().strftime('%Y-%m-%d'),
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "avg_winner": 0.0,
                "avg_loser": 0.0,
                "profit_factor": 0.0,
                "avg_holding_min": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0
            }
            
        pnls = [t.pnl_rupees or 0 for t in closed]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        
        total_win = sum(winners) if winners else 0
        total_loss = abs(sum(losers)) if losers else 0
        
        return {
            "date": date.today().strftime('%Y-%m-%d'),
            "total_trades": len(closed),
            "wins": len(winners),
            "losses": len(losers),
            "win_rate": len(winners) / len(closed) * 100 if closed else 0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(closed) if closed else 0,
            "avg_winner": sum(winners) / len(winners) if winners else 0,
            "avg_loser": sum(losers) / len(losers) if losers else 0,
            "profit_factor": total_win / total_loss if total_loss > 0 else float('inf'),
            "avg_holding_min": sum(t.holding_minutes or 0 for t in closed) / len(closed) if closed else 0,
            "best_trade": max(pnls) if pnls else 0,
            "worst_trade": min(pnls) if pnls else 0
        }
        
    def print_summary(self):
        """Print formatted daily summary"""
        s = self.get_summary()
        
        print("\n" + "="*50)
        print(f"📊 TRADE JOURNAL SUMMARY - {s['date']}")
        print("="*50)
        print(f"Total Trades: {s['total_trades']}")
        print(f"Winners: {s['wins']} | Losers: {s['losses']}")
        print(f"Win Rate: {s['win_rate']:.1f}%")
        print("-"*50)
        print(f"Total P&L: ₹{s['total_pnl']:,.0f}")
        print(f"Avg P&L: ₹{s['avg_pnl']:,.0f}")
        print(f"Avg Winner: ₹{s['avg_winner']:,.0f}")
        print(f"Avg Loser: ₹{s['avg_loser']:,.0f}")
        print(f"Profit Factor: {s['profit_factor']:.2f}")
        print("-"*50)
        print(f"Avg Holding: {s['avg_holding_min']:.0f} min")
        print(f"Best Trade: ₹{s['best_trade']:,.0f}")
        print(f"Worst Trade: ₹{s['worst_trade']:,.0f}")
        print("="*50 + "\n")


# Global journal instance
_journal: Optional[TradeJournal] = None


def initialize_journal(log_dir: str = "data/journal") -> TradeJournal:
    """Initialize global journal"""
    global _journal
    _journal = TradeJournal(log_dir)
    return _journal
    

def get_journal() -> Optional[TradeJournal]:
    """Get global journal instance"""
    return _journal


if __name__ == "__main__":
    # Test the journal
    journal = TradeJournal()
    
    # Log a sample entry
    trade_id = journal.log_entry(
        direction="CE",
        symbol="NIFTY",
        strike=23000,
        entry_price=150.0,
        quantity=75,
        regime="TRENDING_UP",
        regime_confidence=0.85,
        vwap=23050.5,
        vwap_position="ABOVE",
        poc=23045.0,
        entry_reason="VWAP breakout + regime confirmation",
        sl_price=142.0,
        atr_at_entry=45.5,
        notes="Clean breakout"
    )
    
    print(f"Logged entry: {trade_id}")
    
    # Log exit
    journal.log_exit(
        trade_id=trade_id,
        exit_price=165.0,
        exit_reason="Target hit",
        status=TradeStatus.CLOSED_TP
    )
    
    # Print summary
    journal.print_summary()
