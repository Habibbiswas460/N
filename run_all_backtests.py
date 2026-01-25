#!/usr/bin/env python3
"""
Multi-Period Backtest Runner

Runs backtests for multiple time periods:
- 1 year, 6 months, 3 months, 1 month, 1 week, 1 day, 1 hour
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from loguru import logger
from broker.auth import AngelOneAuth
from backtest.historical_data import HistoricalDataFetcher
from backtest.backtester_v2 import NStructureBacktesterV2, BacktestResult, print_results_v2


@dataclass
class BacktestPeriod:
    """Defines a backtest period."""
    name: str
    days: int
    hours: int = 0


# Define all periods
PERIODS = [
    BacktestPeriod("1 Year", days=365),
    BacktestPeriod("6 Months", days=182),
    BacktestPeriod("3 Months", days=90),
    BacktestPeriod("1 Month", days=30),
    BacktestPeriod("1 Week", days=7),
    BacktestPeriod("1 Day", days=1),
    BacktestPeriod("1 Hour", days=0, hours=1),
]


def run_backtest(
    fetcher: HistoricalDataFetcher,
    period: BacktestPeriod,
    end_date: datetime
) -> BacktestResult:
    """
    Run backtest for a specific period.
    
    Args:
        fetcher: Historical data fetcher
        period: Backtest period
        end_date: End date for backtest
        
    Returns:
        BacktestResult
    """
    # Calculate days
    days = period.days if period.days > 0 else 1  # Min 1 day
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Running Backtest: {period.name}")
    logger.info(f"Period: Last {days} days")
    logger.info(f"{'='*60}")
    
    # Fetch NIFTY candles
    candles = fetcher.fetch_nifty_candles(days=days)
    
    if not candles:
        logger.error(f"No candles fetched for {period.name}")
        return BacktestResult()
    
    # For 1 hour, filter to last hour only
    if period.hours > 0:
        cutoff = end_date - timedelta(hours=period.hours)
        candles = [c for c in candles if c.timestamp >= cutoff]
    
    logger.info(f"Using {len(candles)} candles")
    
    # Run backtest with v1.2 settings
    backtester = NStructureBacktesterV2(
        # Capital & Risk
        capital=100000.0,           # ₹1 lakh
        risk_per_day_pct=5.0,       # 5% max daily risk
        risk_per_trade_pct=2.5,     # 2.5% per trade
        
        # Entry
        entry_buffer=1.5,           # +1.5pt on breakout
        min_hl_gap=3.0,             # Min HL gap
        
        # Stop Loss
        initial_sl_points=10.0,     # 10pt SL
        
        # TSL
        tsl_buffer=2.5,             # Structure TSL buffer
        use_structure_tsl=True,
        
        # Risk Management - Max SL only
        max_sl_per_day=3,           # Only limiter!
        cooldown_candles=15,
        
        # Position
        lot_size=65,
        num_lots=4,                 # 260 qty
    )
    
    # Run with index candles only (uses synthetic option)
    result = backtester.run_index_only(candles)
    
    return result


def print_summary_table(results: List[tuple]):
    """Print summary table of all backtests."""
    print("\n" + "=" * 100)
    print("                        MULTI-PERIOD BACKTEST SUMMARY")
    print("=" * 100)
    print(f"{'Period':<12} | {'Trades':>7} | {'Win%':>6} | {'P&L':>12} | {'Avg Win':>10} | {'Avg Loss':>10} | {'PF':>5} | {'Max DD':>10}")
    print("-" * 100)
    
    for period_name, result in results:
        pnl_str = f"+₹{result.total_pnl:,.0f}" if result.total_pnl >= 0 else f"-₹{abs(result.total_pnl):,.0f}"
        dd_str = f"-₹{result.max_drawdown:,.0f}"
        
        print(f"{period_name:<12} | {result.total_trades:>7} | {result.win_rate:>5.1f}% | {pnl_str:>12} | "
              f"+₹{result.avg_win:>8,.0f} | -₹{result.avg_loss:>8,.0f} | {result.profit_factor:>5.2f} | {dd_str:>10}")
    
    print("=" * 100)
    
    # Total across all periods
    total_pnl = sum(r.total_pnl for _, r in results)
    total_trades = sum(r.total_trades for _, r in results)
    
    print(f"\n📊 Overall: {total_trades} trades | P&L: {'+'if total_pnl>=0 else ''}₹{total_pnl:,.0f}")


def main():
    """Main entry point."""
    print("\n" + "=" * 60)
    print("       N-STRUCTURE MULTI-PERIOD BACKTEST RUNNER")
    print("=" * 60)
    
    # Login
    logger.info("Logging in to Angel One...")
    auth = AngelOneAuth()
    if not auth.login():
        logger.error("Login failed!")
        return
    
    logger.success("Login successful!")
    
    # Create fetcher
    fetcher = HistoricalDataFetcher(auth.smart_api)
    
    # End date is today
    end_date = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Store results
    all_results = []
    
    # Run all backtests
    for period in PERIODS:
        try:
            result = run_backtest(fetcher, period, end_date)
            all_results.append((period.name, result))
            
            # Print individual result
            if result.total_trades > 0:
                print_results_v2(result)
            else:
                logger.warning(f"No trades for {period.name}")
                
        except Exception as e:
            logger.error(f"Error in {period.name} backtest: {e}")
            all_results.append((period.name, BacktestResult()))
    
    # Print summary table
    print_summary_table(all_results)
    
    # Logout
    auth.logout()
    logger.info("Done!")


if __name__ == "__main__":
    main()
