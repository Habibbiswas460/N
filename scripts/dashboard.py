"""
Performance Dashboard - Real-time trading stats display
"""
import os
import sys
import csv
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from utils.trade_journal import TradeJournal
except ImportError:
    TradeJournal = None


class PerformanceDashboard:
    """
    Real-time performance dashboard
    
    Displays:
    - Today's P&L and stats
    - Weekly/Monthly performance
    - Regime breakdown
    - Win rate by direction
    """
    
    def __init__(self, journal_dir: str = "data/journal"):
        self.journal_dir = Path(journal_dir)
        
    def _load_trades(self, start_date: date, end_date: date) -> List[Dict]:
        """Load trades from CSV files in date range"""
        trades = []
        current = start_date
        
        while current <= end_date:
            csv_path = self.journal_dir / f"trades_{current.strftime('%Y%m%d')}.csv"
            if csv_path.exists():
                try:
                    with open(csv_path, 'r') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if row.get('status', '') != 'OPEN':
                                trades.append(row)
                except Exception as e:
                    pass
            current += timedelta(days=1)
            
        return trades
        
    def _parse_float(self, value: str) -> float:
        """Safely parse float from string"""
        try:
            return float(value) if value else 0.0
        except:
            return 0.0
            
    def _calculate_stats(self, trades: List[Dict]) -> Dict[str, Any]:
        """Calculate statistics from trades"""
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "profit_factor": 0.0,
                "avg_winner": 0.0,
                "avg_loser": 0.0,
                "max_win": 0.0,
                "max_loss": 0.0,
                "avg_holding": 0.0,
                "ce_wins": 0,
                "ce_losses": 0,
                "pe_wins": 0,
                "pe_losses": 0
            }
            
        pnls = [self._parse_float(t.get('pnl_rupees', 0)) for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        
        total_win = sum(winners) if winners else 0
        total_loss = abs(sum(losers)) if losers else 0
        
        # Direction breakdown
        ce_trades = [t for t in trades if t.get('direction', '') == 'CE']
        pe_trades = [t for t in trades if t.get('direction', '') == 'PE']
        
        ce_wins = len([t for t in ce_trades if self._parse_float(t.get('pnl_rupees', 0)) > 0])
        pe_wins = len([t for t in pe_trades if self._parse_float(t.get('pnl_rupees', 0)) > 0])
        
        return {
            "total_trades": len(trades),
            "wins": len(winners),
            "losses": len(losers),
            "win_rate": len(winners) / len(trades) * 100 if trades else 0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(trades) if trades else 0,
            "profit_factor": total_win / total_loss if total_loss > 0 else float('inf'),
            "avg_winner": sum(winners) / len(winners) if winners else 0,
            "avg_loser": sum(losers) / len(losers) if losers else 0,
            "max_win": max(pnls) if pnls else 0,
            "max_loss": min(pnls) if pnls else 0,
            "avg_holding": sum(int(float(t.get('holding_minutes', 0) or 0)) for t in trades) / len(trades) if trades else 0,
            "ce_wins": ce_wins,
            "ce_losses": len(ce_trades) - ce_wins,
            "pe_wins": pe_wins,
            "pe_losses": len(pe_trades) - pe_wins
        }
        
    def _regime_breakdown(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Break down performance by regime"""
        by_regime = defaultdict(list)
        
        for t in trades:
            regime = t.get('regime', 'UNKNOWN')
            pnl = self._parse_float(t.get('pnl_rupees', 0))
            by_regime[regime].append(pnl)
            
        result = {}
        for regime, pnls in by_regime.items():
            wins = len([p for p in pnls if p > 0])
            result[regime] = {
                "trades": len(pnls),
                "win_rate": wins / len(pnls) * 100 if pnls else 0,
                "total_pnl": sum(pnls)
            }
            
        return result
        
    def _clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')
        
    def _format_pnl(self, pnl: float) -> str:
        """Format P&L with color indicator"""
        if pnl >= 0:
            return f"₹{pnl:,.0f} 🟢"
        else:
            return f"₹{pnl:,.0f} 🔴"
            
    def display(self, clear: bool = True):
        """Display the dashboard"""
        if clear:
            self._clear_screen()
            
        now = datetime.now()
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        
        # Load data for different periods
        today_trades = self._load_trades(today, today)
        week_trades = self._load_trades(week_start, today)
        month_trades = self._load_trades(month_start, today)
        
        # Calculate stats
        today_stats = self._calculate_stats(today_trades)
        week_stats = self._calculate_stats(week_trades)
        month_stats = self._calculate_stats(month_trades)
        
        # Regime breakdown (this month)
        regime_stats = self._regime_breakdown(month_trades)
        
        # Header
        print("\n" + "╔" + "═"*60 + "╗")
        print(f"║{'📊 PERFORMANCE DASHBOARD':^60}║")
        print(f"║{now.strftime('%Y-%m-%d %H:%M:%S'):^60}║")
        print("╠" + "═"*60 + "╣")
        
        # Today's Stats
        print(f"║{'📅 TODAY':^60}║")
        print("╠" + "─"*60 + "╣")
        print(f"║ Trades: {today_stats['total_trades']:>3} │ W/L: {today_stats['wins']}/{today_stats['losses']} │ Win Rate: {today_stats['win_rate']:>5.1f}%{' '*14}║")
        print(f"║ P&L: {self._format_pnl(today_stats['total_pnl']):>15} │ Avg: ₹{today_stats['avg_pnl']:>8,.0f}{' '*18}║")
        print(f"║ CE: {today_stats['ce_wins']}W/{today_stats['ce_losses']}L │ PE: {today_stats['pe_wins']}W/{today_stats['pe_losses']}L{' '*30}║")
        
        # Weekly Stats
        print("╠" + "─"*60 + "╣")
        print(f"║{'📆 THIS WEEK':^60}║")
        print("╠" + "─"*60 + "╣")
        print(f"║ Trades: {week_stats['total_trades']:>3} │ W/L: {week_stats['wins']}/{week_stats['losses']} │ Win Rate: {week_stats['win_rate']:>5.1f}%{' '*14}║")
        print(f"║ P&L: {self._format_pnl(week_stats['total_pnl']):>15} │ PF: {week_stats['profit_factor']:>5.2f}{' '*22}║")
        
        # Monthly Stats
        print("╠" + "─"*60 + "╣")
        print(f"║{'📅 THIS MONTH':^60}║")
        print("╠" + "─"*60 + "╣")
        print(f"║ Trades: {month_stats['total_trades']:>3} │ W/L: {month_stats['wins']}/{month_stats['losses']} │ Win Rate: {month_stats['win_rate']:>5.1f}%{' '*14}║")
        print(f"║ P&L: {self._format_pnl(month_stats['total_pnl']):>15} │ PF: {month_stats['profit_factor']:>5.2f}{' '*22}║")
        print(f"║ Best: ₹{month_stats['max_win']:>8,.0f} │ Worst: ₹{month_stats['max_loss']:>8,.0f}{' '*17}║")
        
        # Regime Breakdown
        if regime_stats:
            print("╠" + "─"*60 + "╣")
            print(f"║{'🎯 BY REGIME (This Month)':^60}║")
            print("╠" + "─"*60 + "╣")
            for regime, stats in sorted(regime_stats.items()):
                line = f"║ {regime:<15} │ {stats['trades']:>2}T │ {stats['win_rate']:>5.1f}% │ {self._format_pnl(stats['total_pnl']):>12}"
                print(f"{line:<60}║")
                
        # Footer
        print("╚" + "═"*60 + "╝\n")
        
    def get_stats_dict(self) -> Dict[str, Any]:
        """Get stats as dictionary (for programmatic use)"""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        
        return {
            "today": self._calculate_stats(self._load_trades(today, today)),
            "week": self._calculate_stats(self._load_trades(week_start, today)),
            "month": self._calculate_stats(self._load_trades(month_start, today)),
            "regime_breakdown": self._regime_breakdown(self._load_trades(month_start, today))
        }


def main():
    """Run dashboard"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Performance Dashboard")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode (refresh every 30s)")
    parser.add_argument("--journal-dir", "-d", default="data/journal", help="Journal directory")
    args = parser.parse_args()
    
    dashboard = PerformanceDashboard(args.journal_dir)
    
    if args.watch:
        import time
        try:
            while True:
                dashboard.display()
                print("(Press Ctrl+C to exit, refreshing in 30s...)")
                time.sleep(30)
        except KeyboardInterrupt:
            print("\nExiting dashboard.")
    else:
        dashboard.display(clear=False)


if __name__ == "__main__":
    main()
