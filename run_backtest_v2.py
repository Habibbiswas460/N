#!/usr/bin/env python3
"""
N-Structure Strategy Backtester V2

Implements the complete Strategic Architecture with:
- FSM-based state management
- Capital-based risk management (₹30,000 capital)
- Risk per trade: 2% = ₹600
- Risk per day: 5% = ₹1,500
- Structure-based Trailing SL (HL-based, UNLIMITED profit!)
- NIFTY lot size: 75 qty

Run: python run_backtest_v2.py --days 30
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from loguru import logger
from broker.auth import AngelOneAuth
from backtest.historical_data import HistoricalDataFetcher
from backtest.backtester_v2 import NStructureBacktesterV2, print_results_v2


def main():
    parser = argparse.ArgumentParser(description="N-Structure V2 Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    # Capital & Risk Parameters
    parser.add_argument("--capital", type=float, default=30000.0,
                        help="Trading capital (default: ₹30,000)")
    parser.add_argument("--risk-day", type=float, default=5.0,
                        help="Daily risk %% (default: 5%%)")
    parser.add_argument("--risk-trade", type=float, default=2.0,
                        help="Per trade risk %% (default: 2%%)")
    parser.add_argument("--lot-size", type=int, default=65,
                        help="NIFTY lot size (default: 65)")
    parser.add_argument("--num-lots", type=int, default=4,
                        help="Number of lots to trade (default: 4)")
    
    # Strategy Parameters
    parser.add_argument("--entry-buffer", type=float, default=1.5,
                        help="Entry buffer points (default: 1.5)")
    parser.add_argument("--min-hl-gap", type=float, default=3.0,
                        help="Min gap between HL1 and HL2 (default: 3.0)")
    parser.add_argument("--max-sl", type=int, default=3,
                        help="Max SL hits per day - ONLY limiter! (default: 3)")
    
    # Premium Range
    parser.add_argument("--min-premium", type=float, default=90.0,
                        help="Min entry premium (default: 90)")
    parser.add_argument("--max-premium", type=float, default=110.0,
                        help="Max entry premium (default: 110)")
    parser.add_argument("--delta", type=float, default=0.5,
                        help="Option delta (default: 0.5)")
    
    args = parser.parse_args()
    
    # Calculate fixed quantity and risk
    fixed_qty = args.lot_size * args.num_lots  # 65 × 4 = 260
    sl_points = 10.0  # Fixed SL
    risk_per_trade = sl_points * fixed_qty  # 10 × 260 = ₹2,600
    
    # Configure logging
    logger.remove()
    level = "DEBUG" if args.debug else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:7} | {message}")
    
    print("\\n" + "=" * 65)
    print("   N-STRUCTURE V2 - UNLIMITED TSL BACKTEST")
    print("=" * 65)
    print(f"\\n💰 Fixed Position Size:")
    print(f"   Lot Size:        {args.lot_size} qty")
    print(f"   Number of Lots:  {args.num_lots}")
    print(f"   Total Quantity:  {fixed_qty} qty")
    print(f"   SL Points:       {sl_points:.0f} pts")
    print(f"   Risk/Trade:      ₹{risk_per_trade:,.0f}")
    print(f"\\n📋 Strategy:")
    print(f"   Entry Buffer:    +{args.entry_buffer} points")
    print(f"   Min HL Gap:      {args.min_hl_gap} points")
    print(f"   Max SL/Day:      {args.max_sl} (ONLY limiter - unlimited trades!)")
    print(f"   Premium Range:   ₹{args.min_premium}-₹{args.max_premium}")
    print(f"   TSL Mode:        HL-Based (UNLIMITED profit!)")
    print()
    
    # Login
    logger.info("Logging in to Angel One...")
    auth = AngelOneAuth()
    if not auth.login():
        logger.error("Login failed!")
        return
    
    try:
        # Initialize historical data fetcher
        fetcher = HistoricalDataFetcher(auth._smart_api)
        
        # Fetch NIFTY index candles
        logger.info(f"Fetching {args.days} days of NIFTY candles...")
        
        to_date = datetime.now()
        from_date = to_date - timedelta(days=args.days)
        
        index_candles = fetcher.fetch_candles(
            exchange="NSE",
            symbol="Nifty 50",
            token="99926000",
            interval="ONE_MINUTE",
            from_date=from_date,
            to_date=to_date
        )
        
        if not index_candles:
            logger.error("Failed to fetch NIFTY candles")
            return
        
        logger.info(f"Got {len(index_candles)} NIFTY candles")
        
        # Create V2 backtester with FIXED 4 lots (260 qty)
        backtester = NStructureBacktesterV2(
            # Capital & Risk (for daily limits)
            capital=args.capital,
            risk_per_day_pct=args.risk_day,
            risk_per_trade_pct=args.risk_trade,
            
            # Entry
            entry_buffer=args.entry_buffer,
            min_hl_gap=args.min_hl_gap,
            
            # TSL
            tsl_buffer=2.5,
            use_structure_tsl=True,
            
            # Risk - Max SL per day is the ONLY limiter!
            max_sl_per_day=args.max_sl,
            cooldown_candles=15,
            
            # FIXED position size: 4 lots × 65 = 260 qty
            lot_size=args.lot_size,
            num_lots=args.num_lots
        )
        
        logger.info("\n🎯 Running N-Structure V2 Backtest (UNLIMITED TSL)...")
        
        result = backtester.run_index_only(
            index_candles,
            entry_premium_range=(args.min_premium, args.max_premium),
            delta=args.delta
        )
        
        # Print results
        print_results_v2(result)
        
    finally:
        auth.logout()
        logger.info("Logged out")


if __name__ == "__main__":
    main()
