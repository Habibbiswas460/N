#!/usr/bin/env python3
"""
Backtest Script for Adaptive Hybrid Strategy v1.0

Usage:
    python scripts/backtest/run_adaptive_backtest.py --days 30
    python scripts/backtest/run_adaptive_backtest.py --from 2026-01-01 --to 2026-01-31
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import random

# Add src to path
src_path = str(Path(__file__).parent.parent.parent / 'src')
sys.path.insert(0, src_path)

# Also add project root for src.* imports
project_root = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, project_root)

from src.strategy.adaptive_hybrid import AdaptiveHybridStrategy, TradeSignal, SignalType
from src.strategy.regime_detector import MarketRegime


@dataclass
class BacktestTrade:
    """Single trade result"""
    entry_time: datetime
    exit_time: datetime
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
class BacktestResult:
    """Complete backtest result"""
    start_date: datetime
    end_date: datetime
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


class AdaptiveBacktester:
    """
    Backtester for Adaptive Hybrid Strategy
    
    Simulates strategy on historical data
    """
    
    def __init__(self, config: Dict = None):
        config = config or {}
        self.lot_size = config.get('lot_size', 65)
        self.num_lots = config.get('num_lots', 4)
        self.quantity = self.lot_size * self.num_lots
        
        # Strategy config
        self.strategy_config = {
            'atr_sl_multiplier': config.get('atr_sl_multiplier', 1.0),
            'min_rr_ratio': config.get('min_rr_ratio', 2.0),
            'max_trades_per_day': config.get('max_trades_per_day', 3),
            'signal_cooldown_minutes': config.get('signal_cooldown_minutes', 5),
            'min_confidence': config.get('min_confidence', 0.7)
        }
        
    def generate_sample_data(self, start_date: datetime, days: int) -> List[Dict]:
        """Generate sample OHLCV data for testing"""
        candles = []
        base_price = 23000
        current_price = base_price
        
        for day in range(days):
            day_date = start_date + timedelta(days=day)
            
            # Skip weekends
            if day_date.weekday() >= 5:
                continue
                
            # Determine day's trend
            day_trend = random.choice(['up', 'down', 'sideways'])
            
            # Market hours: 9:15 to 15:30
            for minute in range(375):  # 375 minutes = 6.25 hours
                hour = 9 + (minute + 15) // 60
                min_of_hour = (minute + 15) % 60
                timestamp = day_date.replace(hour=hour, minute=min_of_hour, second=0)
                
                # Generate OHLCV
                if day_trend == 'up':
                    drift = random.uniform(-1, 3)
                elif day_trend == 'down':
                    drift = random.uniform(-3, 1)
                else:
                    drift = random.uniform(-2, 2)
                    
                open_price = current_price
                close_price = current_price + drift
                high = max(open_price, close_price) + random.uniform(0, 3)
                low = min(open_price, close_price) - random.uniform(0, 3)
                volume = random.randint(50000, 200000)
                
                candles.append({
                    'timestamp': timestamp,
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'close': close_price,
                    'volume': volume
                })
                
                current_price = close_price
                
                # Prevent extreme drift
                if current_price > base_price + 500:
                    current_price = base_price + 300
                elif current_price < base_price - 500:
                    current_price = base_price - 300
                    
        return candles
        
    def run(self, candles: List[Dict]) -> BacktestResult:
        """
        Run backtest on candle data
        
        Args:
            candles: List of OHLCV dictionaries
            
        Returns:
            BacktestResult with all metrics
        """
        strategy = AdaptiveHybridStrategy(self.strategy_config)
        
        trades = []
        current_trade: Optional[TradeSignal] = None
        entry_price = 0.0
        entry_time = None
        
        total_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0
        
        for candle in candles:
            timestamp = candle['timestamp']
            high = candle['high']
            low = candle['low']
            close = candle['close']
            volume = candle['volume']
            
            # If in trade, check exit conditions
            if current_trade:
                exit_reason = None
                exit_price = None
                
                if current_trade.signal == SignalType.CE_BUY:
                    # CE: SL hit if price goes below SL
                    if low <= current_trade.stop_loss:
                        exit_reason = "SL Hit"
                        exit_price = current_trade.stop_loss
                    # Target hit
                    elif high >= current_trade.target_1:
                        exit_reason = "Target Hit"
                        exit_price = current_trade.target_1
                else:
                    # PE: SL hit if price goes above SL
                    if high >= current_trade.stop_loss:
                        exit_reason = "SL Hit"
                        exit_price = current_trade.stop_loss
                    # Target hit
                    elif low <= current_trade.target_1:
                        exit_reason = "Target Hit"
                        exit_price = current_trade.target_1
                        
                if exit_reason:
                    # Calculate PnL
                    if current_trade.signal == SignalType.CE_BUY:
                        pnl_points = exit_price - entry_price
                    else:
                        pnl_points = entry_price - exit_price
                        
                    pnl_rupees = pnl_points * self.quantity
                    total_pnl += pnl_rupees
                    
                    # Track drawdown
                    if total_pnl > peak_pnl:
                        peak_pnl = total_pnl
                    drawdown = peak_pnl - total_pnl
                    if drawdown > max_drawdown:
                        max_drawdown = drawdown
                        
                    trades.append(BacktestTrade(
                        entry_time=entry_time,
                        exit_time=timestamp,
                        signal_type=current_trade.signal.value,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        sl=current_trade.stop_loss,
                        target=current_trade.target_1,
                        pnl_points=pnl_points,
                        pnl_rupees=pnl_rupees,
                        exit_reason=exit_reason,
                        regime=current_trade.regime
                    ))
                    
                    strategy.on_trade_exit(pnl_rupees)
                    current_trade = None
                    continue
                    
            # Generate signal
            signal = strategy.update(high, low, close, volume, timestamp)
            
            # Enter trade if signal
            if signal and signal.signal != SignalType.NO_SIGNAL and not current_trade:
                current_trade = signal
                entry_price = signal.entry_price
                entry_time = timestamp
                strategy.on_trade_entry(timestamp)
                
        # Calculate metrics
        winning = [t for t in trades if t.pnl_points > 0]
        losing = [t for t in trades if t.pnl_points <= 0]
        
        total_wins = sum(t.pnl_rupees for t in winning)
        total_losses = abs(sum(t.pnl_rupees for t in losing))
        
        result = BacktestResult(
            start_date=candles[0]['timestamp'] if candles else datetime.now(),
            end_date=candles[-1]['timestamp'] if candles else datetime.now(),
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=len(winning) / len(trades) * 100 if trades else 0,
            total_pnl_points=sum(t.pnl_points for t in trades),
            total_pnl_rupees=total_pnl,
            max_drawdown=max_drawdown,
            avg_win=total_wins / len(winning) if winning else 0,
            avg_loss=total_losses / len(losing) if losing else 0,
            profit_factor=total_wins / total_losses if total_losses > 0 else float('inf'),
            trades=trades
        )
        
        return result
        
    def print_result(self, result: BacktestResult):
        """Print formatted backtest results"""
        print("\n" + "="*60)
        print("  ADAPTIVE HYBRID STRATEGY - BACKTEST RESULTS")
        print("="*60)
        
        print(f"\nPeriod: {result.start_date.date()} to {result.end_date.date()}")
        print(f"Total Trading Days: {(result.end_date - result.start_date).days}")
        
        print("\n--- PERFORMANCE ---")
        print(f"Total Trades: {result.total_trades}")
        print(f"Winning Trades: {result.winning_trades}")
        print(f"Losing Trades: {result.losing_trades}")
        print(f"Win Rate: {result.win_rate:.1f}%")
        
        print("\n--- P&L ---")
        print(f"Total P&L (Points): {result.total_pnl_points:.1f}")
        print(f"Total P&L (₹): {result.total_pnl_rupees:,.0f}")
        print(f"Average Win: ₹{result.avg_win:,.0f}")
        print(f"Average Loss: ₹{result.avg_loss:,.0f}")
        print(f"Profit Factor: {result.profit_factor:.2f}")
        print(f"Max Drawdown: ₹{result.max_drawdown:,.0f}")
        
        if result.trades:
            print("\n--- RECENT TRADES ---")
            for trade in result.trades[-5:]:
                emoji = "✅" if trade.pnl_points > 0 else "❌"
                print(f"{emoji} {trade.entry_time.strftime('%m/%d %H:%M')} | "
                      f"{trade.signal_type} | {trade.regime} | "
                      f"PnL: {trade.pnl_points:+.1f}pt (₹{trade.pnl_rupees:+,.0f}) | "
                      f"{trade.exit_reason}")
                      
        print("\n" + "="*60)


def main():
    parser = argparse.ArgumentParser(description='Backtest Adaptive Hybrid Strategy')
    parser.add_argument('--days', type=int, default=30, help='Number of days to backtest')
    parser.add_argument('--lots', type=int, default=4, help='Number of lots (default: 4)')
    args = parser.parse_args()
    
    print("🚀 Starting Adaptive Hybrid Strategy Backtest...")
    
    config = {
        'lot_size': 65,
        'num_lots': args.lots,
        'atr_sl_multiplier': 1.0,
        'min_rr_ratio': 2.0,
        'max_trades_per_day': 3,
        'signal_cooldown_minutes': 5,
        'min_confidence': 0.7
    }
    
    backtester = AdaptiveBacktester(config)
    
    # Generate sample data
    print(f"Generating {args.days} days of sample data...")
    start_date = datetime(2026, 1, 1, 9, 15)
    candles = backtester.generate_sample_data(start_date, args.days)
    print(f"Generated {len(candles)} candles")
    
    # Run backtest
    print("Running backtest...")
    result = backtester.run(candles)
    
    # Print results
    backtester.print_result(result)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
