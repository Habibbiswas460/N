#!/usr/bin/env python3
"""
N-Structure Strategy Backtester

Implements the complete Strategic Architecture with:
- FSM-based state management
- Capital-based risk management (₹30,000 capital)
- Risk per trade: 2% = ₹600
- Risk per day: 5% = ₹1,500
- Structure-based Trailing SL (HL-based, UNLIMITED profit!)
- NIFTY lot size: 75 qty

Run: python run_backtest.py --days 30
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loguru import logger
from broker.auth import AngelOneAuth
from backtest.historical_data import HistoricalDataFetcher
from backtest.backtester import NStructureBacktesterV2, print_results_v2


def main():
    parser = argparse.ArgumentParser(description="N-Structure V2 Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument("--start-date", type=str, default=None, 
                        help="Start date (YYYY-MM-DD), e.g. 2024-01-01")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date (YYYY-MM-DD), e.g. 2025-01-01")
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
    
    # Strategy Parameters (v1.7 Bulletproof)
    parser.add_argument("--entry-buffer", type=float, default=2.5,
                        help="Entry buffer points (default: 2.5 - v1.7 Bulletproof)")
    parser.add_argument("--min-hl-gap", type=float, default=3.2,
                        help="Min gap between HL1 and HL2 (default: 3.0)")
    parser.add_argument("--max-sl", type=int, default=1,
                        help="Max SL hits per day - One Bullet Rule! (default: 1)")
    parser.add_argument("--interval", type=str, default="ONE_MINUTE",
                        help="Candle interval: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE (default: ONE_MINUTE)")
    
    # Premium Range
    parser.add_argument("--min-premium", type=float, default=90.0,
                        help="Min entry premium (default: 90)")
    parser.add_argument("--max-premium", type=float, default=110.0,
                        help="Max entry premium (default: 110)")
    parser.add_argument("--delta", type=float, default=0.5,
                        help="Option delta (default: 0.5)")
    
    # ===== v3.0 SNIPER FILTERS (RECOMMENDED: Only Option C) =====
    # Option B: Pullback Entry - DISABLED (too strict, misses opportunities)
    parser.add_argument("--enable-pullback", action="store_true",
                        help="Enable pullback entry - NOT RECOMMENDED")
    parser.add_argument("--pullback-buffer", type=float, default=3.0,
                        help="Pullback buffer points (default: 3.0)")
    parser.add_argument("--max-pullback-candles", type=int, default=15,
                        help="Max candles to wait for pullback (default: 15)")
    # Option C: Strong Momentum - ENABLED (best improvement!)
    parser.add_argument("--min-body-ratio", type=float, default=0.65,
                        help="Min body ratio for strong momentum (default: 0.65) ✅ RECOMMENDED")
    # Option E: Confirmation Candle - DISABLED (reduces trades too much)
    parser.add_argument("--enable-confirmation", action="store_true",
                        help="Enable confirmation candle - NOT RECOMMENDED")
    parser.add_argument("--confirmation-candles", type=int, default=1,
                        help="Number of confirmation candles to wait (default: 1)")
    
    # ===== v5.0 SL REDUCTION FILTERS =====
    parser.add_argument("--no-new-after", type=str, default="1230",
                        help="No new trades after this time HHMM (default: 1230)")
    parser.add_argument("--ce-only", action="store_true",
                        help="CE only mode - skip PE trades (100%% PE losses!)")
    parser.add_argument("--require-ema-trend", action="store_true",
                        help="Require EMA9>EMA15 for CE, EMA9<EMA15 for PE")
    
    # ===== v6.0 SIDEWAYS MARKET PROTECTION =====
    parser.add_argument("--enable-adx-filter", action="store_true",
                        help="Skip trades when ADX < 18 (sideways market protection)")
    parser.add_argument("--adx-trending", type=float, default=22.0,
                        help="ADX threshold for trending market (default: 22)")
    parser.add_argument("--adx-sideways", type=float, default=15.0,
                        help="ADX threshold for sideways market (default: 15)")
    
    # ===== COMPOUND SYSTEM =====
    parser.add_argument("--compound", action="store_true",
                        help="Enable auto-compounding (capital grows with profits)")
    parser.add_argument("--compound-risk", type=float, default=2.0,
                        help="Risk %% per trade for compounding (default: 2%%)")
    parser.add_argument("--min-lots", type=int, default=1,
                        help="Minimum lots to trade (default: 1)")
    parser.add_argument("--max-lots", type=int, default=20,
                        help="Maximum lots to cap position size (default: 20)")
    
    args = parser.parse_args()
    
    # ============================================
    # FIXED Position Size: 4 lots × 65 = 260 qty
    # ============================================
    sl_points = 5.0  # Fixed SL (v1.8: Tight SL)
    fixed_qty = args.lot_size * args.num_lots  # 65 × 4 = 260
    risk_per_trade = sl_points * fixed_qty  # 5 × 260 = ₹1,300
    
    # Configure logging
    logger.remove()
    level = "DEBUG" if args.debug else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:7} | {message}")
    
    print("\\n" + "=" * 65)
    print("   N-STRUCTURE v1.8 - 5pt SL + UNLIMITED TSL")
    print("=" * 65)
    print(f"\\n💰 FIXED Position Size:")
    print(f"   Capital:         ₹{args.capital:,.0f}")
    print(f"   Lot Size:        {args.lot_size} qty")
    print(f"   Lots:            {args.num_lots}")
    print(f"   Total Quantity:  {fixed_qty} qty")
    print(f"   SL Points:       {sl_points:.0f} pts")
    print(f"   Risk/Trade:      ₹{risk_per_trade:,.0f}")
    print(f"\\n📋 Strategy:")
    print(f"   Entry Buffer:    +{args.entry_buffer} points")
    print(f"   Min HL Gap:      {args.min_hl_gap} points")
    print(f"   Max SL/Day:      {args.max_sl} (ONLY limiter - unlimited trades!)")
    print(f"   Premium Range:   ₹{args.min_premium}-₹{args.max_premium}")
    print(f"   TSL Mode:        HL-Based (UNLIMITED profit!)")
    
    # Compound System Info
    if args.compound:
        print(f"\\n💰 COMPOUND MODE ENABLED:")
        print(f"   Starting Capital: ₹{args.capital:,.0f}")
        print(f"   Risk Per Trade:   {args.compound_risk}%")
        print(f"   Min Lots:         {args.min_lots}")
        print(f"   Max Lots:         {args.max_lots}")
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
        logger.info(f"Fetching NIFTY candles ({args.interval})...")
        
        # Use custom date range if provided
        if args.start_date and args.end_date:
            from_date = datetime.strptime(args.start_date, "%Y-%m-%d")
            to_date = datetime.strptime(args.end_date, "%Y-%m-%d")
            logger.info(f"📅 Date Range: {args.start_date} to {args.end_date}")
        else:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=args.days)
            logger.info(f"📅 Last {args.days} days")
        
        index_candles = fetcher.fetch_candles(
            exchange="NSE",
            symbol="Nifty 50",
            token="99926000",
            interval=args.interval,
            from_date=from_date,
            to_date=to_date
        )
        
        if not index_candles:
            logger.error("Failed to fetch NIFTY candles")
            return
        
        logger.info(f"Got {len(index_candles)} NIFTY candles")
        
        # Create V2 backtester with FIXED 4 lots (260 qty)
        # v1.2 Stable: All v1.3 optimizations DISABLED for consistent results
        backtester = NStructureBacktesterV2(
            # Capital & Risk (for daily limits)
            capital=args.capital,
            risk_per_day_pct=args.risk_day,
            risk_per_trade_pct=args.risk_trade,
            
            # Entry
            entry_buffer=args.entry_buffer,
            min_hl_gap=args.min_hl_gap,
            
            # TSL - Structure-based (v1.2)
            tsl_buffer=2.5,
            use_structure_tsl=True,
            
            # Risk - Max SL per day is the ONLY limiter!
            max_sl_per_day=args.max_sl,
            cooldown_candles=15,
            
            # FIXED position size: 4 lots × 65 = 260 qty
            lot_size=args.lot_size,
            num_lots=args.num_lots,
            
            # Entry filters enabled for stricter 1-minute backtest
            enable_volume_filter=False,  # REMOVED - No volume data in backtest
            enable_trend_filter=True,
            enable_atr_sl=False,
            enable_partial_profits=False,
            enable_volatility_filter=False,
            enable_drawdown_protection=True,
            enable_atr_tsl=False,
            
            # v1.9 Breakout Confirmation - DISABLED (v1.8 better)
            enable_breakout_confirmation=False,
            breakout_buffer=5.0,
            confirmation_target=10.0,
            
            # ===== v3.0 SNIPER FILTERS =====
            # Option B: Pullback Entry
            enable_pullback_entry=args.enable_pullback,
            pullback_buffer=args.pullback_buffer,
            max_pullback_candles=args.max_pullback_candles,
            # Option C: Strong Momentum (65% body ratio)
            min_body_ratio=args.min_body_ratio,
            # Option E: Confirmation Candle
            enable_confirmation_candle=args.enable_confirmation,
            confirmation_candle_count=args.confirmation_candles,
            
            # v5.0 SL REDUCTION FILTERS
            no_new_trades_after=args.no_new_after,
            ce_only_mode=args.ce_only,
            require_ema_trend=args.require_ema_trend,
            
            # v6.0 SIDEWAYS MARKET PROTECTION
            enable_adx_filter=args.enable_adx_filter,
            adx_trending_threshold=args.adx_trending,
            adx_sideways_threshold=args.adx_sideways,
            
            # ===== COMPOUND SYSTEM =====
            enable_compound=args.compound,
            compound_risk_pct=args.compound_risk,
            min_lots=args.min_lots,
            max_lots=args.max_lots,
        )
        
        logger.info("\n🎯 Running N-Structure v1.8 Backtest (5pt SL, Unlimited TSL)...")
        
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
