#!/usr/bin/env python3
"""
N-Structure Strategy Backtester

Run: python run_backtest.py --days 30
     python run_backtest.py --days 30 --index-only  (recommended - dynamic strike)
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from loguru import logger
from broker.auth import AngelOneAuth
from data.instrument_master import InstrumentMaster, OptionType
from backtest.historical_data import HistoricalDataFetcher
from backtest.backtester import NStructureBacktester, print_results


def main():
    parser = argparse.ArgumentParser(description="N-Structure Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--index-only", action="store_true", 
                        help="Use index-only backtest with dynamic strike selection (recommended)")
    parser.add_argument("--min-premium", type=float, default=90.0,
                        help="Min entry premium (default: 90)")
    parser.add_argument("--max-premium", type=float, default=110.0,
                        help="Max entry premium (default: 110)")
    parser.add_argument("--delta", type=float, default=0.5,
                        help="Option delta (default: 0.5 for ATM)")
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    level = "DEBUG" if args.debug else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:7} | {message}")
    
    print("\n" + "=" * 60)
    if args.index_only:
        print("   N-STRUCTURE BACKTEST - DYNAMIC STRIKE (₹90-110)")
    else:
        print("       N-STRUCTURE BACKTEST - 30 DAYS HISTORICAL DATA")
    print("=" * 60 + "\n")
    
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
        
        # Create backtester with OPTIMIZED V3 parameters
        # Higher win rate: 1 trade/day, wider SL, larger target
        backtester = NStructureBacktester(
            initial_sl_points=12.0,     # 12 point SL (more room)
            target_points=30.0,         # 30 point target (2.5:1 R:R)
            be_trigger_points=10.0,     # Move to BE at 10pt profit
            trail_distance=5.0,
            lot_size=25,
            max_trades_per_day=1,       # Only 1 quality trade/day
            loss_cooldown_candles=30    # Wait 30 min after loss
        )
        
        # INDEX-ONLY MODE: Dynamic strike selection (recommended)
        if args.index_only:
            logger.info("\n🎯 INDEX-ONLY MODE: Dynamic strike selection")
            logger.info(f"Entry premium: ₹{args.min_premium}-₹{args.max_premium} | Delta: {args.delta}")
            
            result = backtester.run_index_only(
                index_candles,
                entry_premium_range=(args.min_premium, args.max_premium),
                delta=args.delta
            )
            
            print_results(result)
            return
        
        # LEGACY MODE: Use actual option candles (single strike)
        logger.info("\n📊 LEGACY MODE: Using single option strike")
        
        # For backtesting, we need to pick an option
        # In real backtest, you'd fetch actual option candles
        
        # Get current ATM for reference
        nifty_ltp = auth.get_ltp("NSE", "Nifty 50", "99926000")
        atm = round(nifty_ltp / 50) * 50
        
        logger.info(f"Current NIFTY: ₹{nifty_ltp:.2f} | ATM: {atm}")
        
        # Load instruments to find an option
        im = InstrumentMaster()
        im.download()
        
        expiry = im.get_nearest_expiry("NIFTY")
        logger.info(f"Using expiry: {expiry}")
        
        # Get ATM CE option
        options = im.get_nifty_options(expiry_date=expiry, option_type=OptionType.CALL)
        atm_option = None
        for opt in options:
            if abs(opt.strike - atm) <= 50:
                atm_option = opt
                break
        
        if not atm_option:
            logger.error("Could not find ATM option")
            return
        
        logger.info(f"Using option: {atm_option.symbol} (Strike: {atm_option.strike})")
        
        # Fetch option candles
        logger.info(f"Fetching {args.days} days of option candles...")
        
        option_candles = fetcher.fetch_candles(
            exchange="NFO",
            symbol=atm_option.symbol,
            token=atm_option.token,
            interval="ONE_MINUTE",
            from_date=from_date,
            to_date=to_date
        )
        
        if not option_candles:
            logger.warning("No option candles - option might be new")
            logger.info("Creating synthetic option data from NIFTY...")
            
            # Create synthetic option candles (for demo)
            # Option price roughly follows: Premium + Delta * NIFTY_move
            base_premium = 100.0
            delta = 0.5  # ATM delta
            
            from backtest.historical_data import HistoricalCandle
            
            option_candles = []
            base_nifty = index_candles[0].close if index_candles else nifty_ltp
            
            for idx_c in index_candles:
                nifty_change = idx_c.close - base_nifty
                opt_price = base_premium + (delta * nifty_change)
                opt_price = max(opt_price, 5)  # Min price
                
                # Add some noise for realism
                import random
                noise = random.uniform(-1, 1)
                
                opt_candle = HistoricalCandle(
                    timestamp=idx_c.timestamp,
                    open=opt_price + noise,
                    high=opt_price + abs(noise) + 1,
                    low=opt_price - abs(noise) - 1,
                    close=opt_price,
                    volume=int(idx_c.volume * 0.1)
                )
                option_candles.append(opt_candle)
        
        logger.info(f"Got {len(option_candles)} option candles")
        
        # Run backtest with OPTIMIZED V2 parameters
        logger.info("\nRunning backtest with OPTIMIZED V2 parameters...")
        logger.info("Strategy: 10pt SL | 25pt Target | 2.5:1 R:R")
        
        result = backtester.run(index_candles, option_candles)
        
        # Print results
        print_results(result)
        
    finally:
        auth.logout()
        logger.info("Logged out")


if __name__ == "__main__":
    main()
