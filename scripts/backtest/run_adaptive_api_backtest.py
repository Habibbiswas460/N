#!/usr/bin/env python3
"""
Adaptive Hybrid Strategy Backtest with API Data

Fetches 30 days of NIFTY data from Angel One API
and runs backtest with Dynamic PDH/PDL.

Usage:
    python scripts/backtest/run_adaptive_api_backtest.py --days 30
    python scripts/backtest/run_adaptive_api_backtest.py --days 30 --dynamic-levels
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta, time
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loguru import logger
from broker.auth import AngelOneAuth
from backtest.historical_data import HistoricalDataFetcher, HistoricalCandle
from strategy.adaptive_hybrid import AdaptiveHybridStrategy, TradeSignal, SignalType


@dataclass
class BacktestTrade:
    """Single trade result"""
    date: str
    entry_time: str
    exit_time: str
    signal_type: str
    entry_price: float
    exit_price: float
    sl: float
    target: float
    pnl_points: float
    pnl_rupees: float
    exit_reason: str
    regime: str


@dataclass
class DayResult:
    """Single day result"""
    date: str
    trades: int
    wins: int
    losses: int
    pnl_points: float
    pnl_rupees: float


@dataclass
class BacktestResult:
    """Complete backtest result"""
    start_date: str
    end_date: str
    total_days: int
    trading_days: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl_points: float
    total_pnl_rupees: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    trades: List[BacktestTrade] = field(default_factory=list)
    daily_results: List[DayResult] = field(default_factory=list)


class AdaptiveApiBacktester:
    """
    Backtester for Adaptive Hybrid Strategy using real API data
    """
    
    def __init__(self, config: Dict = None):
        config = config or {}
        self.lot_size = config.get('lot_size', 65)
        self.num_lots = config.get('num_lots', 4)
        self.quantity = self.lot_size * self.num_lots
        
        # Get dynamic settings
        dynamic_levels = config.get('dynamic_levels', False)
        multi_breakout = config.get('multi_breakout', False)
        cooldown_candles = config.get('cooldown_candles', 15)  # Default 15 min cooldown
        
        # Strategy config
        self.strategy_config = {
            'atr_sl_multiplier': config.get('atr_sl_multiplier', 0.5),
            'min_rr_ratio': config.get('min_rr_ratio', 1.5),
            'max_trades_per_day': config.get('max_trades_per_day', 5),  # Allow more trades for multi-breakout
            'vwap_buffer': config.get('vwap_buffer', 5),
            'pdhl_buffer': config.get('pdhl_buffer', 5),
            'vwap_stability': config.get('vwap_stability', 3),
            'target_rr_1': config.get('target_rr_1', 1.5),
            'target_rr_2': config.get('target_rr_2', 2.5),
            # PDH/PDL settings
            'use_opening_range': config.get('use_opening_range', True),
            'dynamic_levels': dynamic_levels,
            'dynamic_lookback': config.get('dynamic_lookback', 60),
            'multi_breakout': multi_breakout,  # Allow multiple breakouts of same level
            'cooldown_candles': cooldown_candles,  # Candles to wait after exit before re-entry
        }
        
        self.entry_start = time(9, 30)
        self.entry_end = time(15, 0)
        
    def run(self, candles: List[HistoricalCandle]) -> BacktestResult:
        """
        Run backtest on candle data
        
        Args:
            candles: List of HistoricalCandle from API
            
        Returns:
            BacktestResult with all metrics
        """
        trades: List[BacktestTrade] = []
        daily_results: List[DayResult] = []
        
        # Group candles by date
        candles_by_date: Dict[str, List[HistoricalCandle]] = {}
        for candle in candles:
            date_str = candle.timestamp.strftime("%Y-%m-%d")
            if date_str not in candles_by_date:
                candles_by_date[date_str] = []
            candles_by_date[date_str].append(candle)
        
        total_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0
        
        # Process each day separately
        for date_str in sorted(candles_by_date.keys()):
            day_candles = candles_by_date[date_str]
            
            # Create fresh strategy for each day
            strategy = AdaptiveHybridStrategy(self.strategy_config)
            
            day_trades = []
            current_trade: Optional[TradeSignal] = None
            entry_price = 0.0
            entry_time = None
            
            for candle in day_candles:
                timestamp = candle.timestamp
                current_time = timestamp.time()
                
                # Skip outside trading hours
                if current_time < time(9, 15) or current_time > time(15, 30):
                    continue
                
                high = candle.high
                low = candle.low
                close = candle.close
                volume = candle.volume
                
                # If in trade, check exit conditions
                if current_trade:
                    exit_reason = None
                    exit_price = None
                    
                    if current_trade.signal == SignalType.CE_BUY:
                        # CE: SL hit if INDEX goes below SL -> option loses value
                        if low <= current_trade.stop_loss:
                            exit_reason = "SL Hit"
                            exit_price = current_trade.stop_loss
                        # Target hit - INDEX goes up
                        elif high >= current_trade.target_1:
                            exit_reason = "Target Hit"
                            exit_price = current_trade.target_1
                    else:
                        # PE: SL hit if INDEX goes above SL -> PE option loses value
                        if high >= current_trade.stop_loss:
                            exit_reason = "SL Hit"
                            exit_price = current_trade.stop_loss
                        # Target hit - INDEX goes down
                        elif low <= current_trade.target_1:
                            exit_reason = "Target Hit"
                            exit_price = current_trade.target_1
                    
                    # Time-based exit at 15:25
                    if current_time >= time(15, 25) and not exit_reason:
                        exit_reason = "Day End"
                        exit_price = close
                    
                    if exit_reason:
                        # Calculate PnL based on index movement
                        if current_trade.signal == SignalType.CE_BUY:
                            pnl_points = exit_price - entry_price
                        else:
                            pnl_points = entry_price - exit_price
                        
                        # Approximate option PnL (delta ~0.5)
                        pnl_rupees = pnl_points * self.quantity * 0.5
                        
                        trade = BacktestTrade(
                            date=date_str,
                            entry_time=entry_time.strftime("%H:%M"),
                            exit_time=timestamp.strftime("%H:%M"),
                            signal_type=current_trade.signal.value,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            sl=current_trade.stop_loss,
                            target=current_trade.target_1,
                            pnl_points=pnl_points,
                            pnl_rupees=pnl_rupees,
                            exit_reason=exit_reason,
                            regime=current_trade.regime
                        )
                        day_trades.append(trade)
                        trades.append(trade)
                        
                        total_pnl += pnl_rupees
                        peak_pnl = max(peak_pnl, total_pnl)
                        drawdown = peak_pnl - total_pnl
                        max_drawdown = max(max_drawdown, drawdown)
                        
                        # Notify strategy trade is closed
                        strategy.on_trade_exit(exit_reason == "Target Hit")
                        current_trade = None
                        
                # If not in trade and within entry window, check for signals
                if not current_trade and self.entry_start <= current_time <= self.entry_end:
                    signal = strategy.update(high, low, close, volume, timestamp)
                    
                    if signal:
                        current_trade = signal
                        entry_price = close
                        entry_time = timestamp
            
            # Day summary
            day_wins = sum(1 for t in day_trades if t.pnl_points > 0)
            day_losses = sum(1 for t in day_trades if t.pnl_points <= 0)
            day_pnl_points = sum(t.pnl_points for t in day_trades)
            day_pnl_rupees = sum(t.pnl_rupees for t in day_trades)
            
            if day_trades:
                daily_results.append(DayResult(
                    date=date_str,
                    trades=len(day_trades),
                    wins=day_wins,
                    losses=day_losses,
                    pnl_points=day_pnl_points,
                    pnl_rupees=day_pnl_rupees
                ))
        
        # Calculate metrics
        winning_trades = [t for t in trades if t.pnl_points > 0]
        losing_trades = [t for t in trades if t.pnl_points <= 0]
        
        total_wins = sum(t.pnl_rupees for t in winning_trades)
        total_losses = abs(sum(t.pnl_rupees for t in losing_trades))
        
        return BacktestResult(
            start_date=min(candles_by_date.keys()) if candles_by_date else "",
            end_date=max(candles_by_date.keys()) if candles_by_date else "",
            total_days=len(candles_by_date),
            trading_days=len(daily_results),
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=len(winning_trades) / len(trades) * 100 if trades else 0,
            total_pnl_points=sum(t.pnl_points for t in trades),
            total_pnl_rupees=sum(t.pnl_rupees for t in trades),
            max_drawdown=max_drawdown,
            avg_win=total_wins / len(winning_trades) if winning_trades else 0,
            avg_loss=total_losses / len(losing_trades) if losing_trades else 0,
            profit_factor=total_wins / total_losses if total_losses > 0 else float('inf'),
            trades=trades,
            daily_results=daily_results
        )


def print_results(result: BacktestResult, dynamic_levels: bool = False, mode: str = None):
    """Print formatted backtest results"""
    if mode is None:
        mode = "DYNAMIC PDH/PDL" if dynamic_levels else "STATIC PDH/PDL"
    
    print("\n" + "=" * 70)
    print(f"  ADAPTIVE HYBRID STRATEGY - BACKTEST RESULTS")
    print(f"  Mode: {mode}")
    print("=" * 70)
    
    print(f"\n📅 Period: {result.start_date} to {result.end_date}")
    print(f"   Total Days: {result.total_days}")
    print(f"   Trading Days: {result.trading_days}")
    
    print(f"\n📊 PERFORMANCE:")
    print(f"   Total Trades:    {result.total_trades}")
    print(f"   Winning Trades:  {result.winning_trades}")
    print(f"   Losing Trades:   {result.losing_trades}")
    print(f"   Win Rate:        {result.win_rate:.1f}%")
    
    print(f"\n💰 P&L:")
    print(f"   Total P&L (Points): {result.total_pnl_points:+.1f}")
    print(f"   Total P&L (₹):      ₹{result.total_pnl_rupees:+,.0f}")
    print(f"   Average Win:        ₹{result.avg_win:,.0f}")
    print(f"   Average Loss:       ₹{result.avg_loss:,.0f}")
    print(f"   Profit Factor:      {result.profit_factor:.2f}")
    print(f"   Max Drawdown:       ₹{result.max_drawdown:,.0f}")
    
    # Daily breakdown
    if result.daily_results:
        print(f"\n📆 DAILY BREAKDOWN:")
        print("-" * 70)
        print(f"{'Date':<12} {'Trades':>7} {'Win':>5} {'Loss':>6} {'P&L Pts':>10} {'P&L ₹':>12}")
        print("-" * 70)
        
        for day in result.daily_results:
            print(f"{day.date:<12} {day.trades:>7} {day.wins:>5} {day.losses:>6} "
                  f"{day.pnl_points:>+10.1f} ₹{day.pnl_rupees:>+11,.0f}")
        
        print("-" * 70)
    
    # Trade details
    if result.trades:
        print(f"\n📝 RECENT TRADES (Last 10):")
        print("-" * 70)
        print(f"{'Date':<12} {'Entry':>6} {'Exit':>6} {'Type':>8} {'Entry':>10} {'Exit':>10} {'P&L':>10} {'Reason':>10}")
        print("-" * 70)
        
        for trade in result.trades[-10:]:
            print(f"{trade.date:<12} {trade.entry_time:>6} {trade.exit_time:>6} "
                  f"{trade.signal_type:>8} {trade.entry_price:>10.2f} "
                  f"{trade.exit_price:>10.2f} {trade.pnl_points:>+10.1f} {trade.exit_reason:>10}")
    
    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Adaptive Hybrid Strategy Backtest (API Data)")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--dynamic-levels", action="store_true", 
                        help="Enable dynamic PDH/PDL (rolling intraday high/low)")
    parser.add_argument("--dynamic-lookback", type=int, default=60,
                        help="Dynamic level lookback candles (default: 60 = 1 hour)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--compare", action="store_true", 
                        help="Compare static vs multi-breakout mode")
    parser.add_argument("--multi-breakout", action="store_true",
                        help="Enable multi-breakout mode (allow re-entry after exit)")
    
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    level = "DEBUG" if args.debug else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:7} | {message}")
    
    print("\n" + "=" * 70)
    print("   ADAPTIVE HYBRID STRATEGY BACKTEST")
    print("   Using Real NIFTY Data from Angel One API")
    print("=" * 70)
    
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
        logger.info(f"Fetching NIFTY candles (last {args.days} days)...")
        
        to_date = datetime.now()
        from_date = to_date - timedelta(days=args.days)
        
        candles = fetcher.fetch_candles(
            exchange="NSE",
            symbol="Nifty 50",
            token="99926000",
            interval="ONE_MINUTE",
            from_date=from_date,
            to_date=to_date
        )
        
        if not candles:
            logger.error("Failed to fetch NIFTY candles")
            return
        
        logger.info(f"✅ Fetched {len(candles)} candles")
        
        if args.compare:
            # Run both static and multi-breakout for comparison
            print("\n" + "=" * 70)
            print("   COMPARISON: STATIC vs MULTI-BREAKOUT MODE")
            print("=" * 70)
            
            # Static PDH/PDL (single breakout per day)
            backtester_static = AdaptiveApiBacktester({
                'lot_size': 65,
                'num_lots': 4,
                'dynamic_levels': False,
                'multi_breakout': False,
                'use_opening_range': True,
            })
            result_static = backtester_static.run(candles)
            print_results(result_static, mode="STATIC (Single Breakout)")
            
            # Multi-breakout mode (allows re-entry after exit)
            backtester_multi = AdaptiveApiBacktester({
                'lot_size': 65,
                'num_lots': 4,
                'dynamic_levels': False,
                'multi_breakout': True,
                'max_trades_per_day': 5,
                'use_opening_range': True,
            })
            result_multi = backtester_multi.run(candles)
            print_results(result_multi, mode="MULTI-BREAKOUT (Re-entry)")
            
            # Summary comparison
            print("\n" + "=" * 70)
            print("   COMPARISON SUMMARY")
            print("=" * 70)
            print(f"\n{'Metric':<25} {'Static':>15} {'Multi-Brk':>15} {'Diff':>15}")
            print("-" * 70)
            print(f"{'Trades':<25} {result_static.total_trades:>15} {result_multi.total_trades:>15} "
                  f"{result_multi.total_trades - result_static.total_trades:>+15}")
            print(f"{'Win Rate %':<25} {result_static.win_rate:>14.1f}% {result_multi.win_rate:>14.1f}% "
                  f"{result_multi.win_rate - result_static.win_rate:>+14.1f}%")
            print(f"{'P&L Points':<25} {result_static.total_pnl_points:>+15.1f} {result_multi.total_pnl_points:>+15.1f} "
                  f"{result_multi.total_pnl_points - result_static.total_pnl_points:>+15.1f}")
            print(f"{'P&L ₹':<25} ₹{result_static.total_pnl_rupees:>+14,.0f} ₹{result_multi.total_pnl_rupees:>+14,.0f} "
                  f"₹{result_multi.total_pnl_rupees - result_static.total_pnl_rupees:>+14,.0f}")
            print(f"{'Profit Factor':<25} {result_static.profit_factor:>15.2f} {result_multi.profit_factor:>15.2f} "
                  f"{result_multi.profit_factor - result_static.profit_factor:>+15.2f}")
            print("-" * 70)
            
            if result_multi.total_pnl_rupees > result_static.total_pnl_rupees:
                print("\n✅ MULTI-BREAKOUT mode performs BETTER!")
            else:
                print("\n⚠️ STATIC mode performs better in this period.")
            
        else:
            # Single run
            backtester = AdaptiveApiBacktester({
                'lot_size': 65,
                'num_lots': 4,
                'dynamic_levels': args.dynamic_levels,
                'dynamic_lookback': args.dynamic_lookback,
                'multi_breakout': args.multi_breakout,
                'use_opening_range': True,
            })
            
            result = backtester.run(candles)
            mode = "MULTI-BREAKOUT" if args.multi_breakout else ("DYNAMIC" if args.dynamic_levels else "STATIC")
            print_results(result, dynamic_levels=args.dynamic_levels, mode=mode)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Logout from API
        try:
            auth.logout()
        except:
            pass  # Ignore logout errors


if __name__ == "__main__":
    main()
