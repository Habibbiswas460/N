#!/usr/bin/env python3
"""
Parameter Optimizer for Adaptive Hybrid Strategy
Grid search to find optimal parameters
"""
import sys
import itertools
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass
import random

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@dataclass
class OptimizationResult:
    """Single optimization run result"""
    params: Dict[str, Any]
    total_trades: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    
    @property
    def score(self) -> float:
        """Composite score for ranking"""
        # Higher is better
        if self.total_trades < 20:
            return 0  # Not enough trades
        return (
            self.win_rate * 0.3 +
            min(self.profit_factor, 5) * 10 +  # Cap PF at 5
            (self.total_pnl / 10000) * 0.2 -
            (self.max_drawdown / 5000) * 0.2 +
            max(self.sharpe_ratio, 0) * 5
        )


class ParameterOptimizer:
    """
    Grid search optimizer for strategy parameters
    
    Tests combinations of:
    - ATR period
    - EMA periods
    - Signal cooldown
    - Min confidence
    - Risk/reward ratio
    """
    
    # Parameter search space
    PARAM_GRID = {
        'atr_period': [10, 14, 20],
        'ema_fast': [5, 8, 13],
        'ema_slow': [13, 21, 34],
        'signal_cooldown_minutes': [3, 5, 10],
        'min_confidence': [0.6, 0.7, 0.8],
        'min_rr_ratio': [1.5, 2.0, 2.5],
        'atr_sl_multiplier': [0.8, 1.0, 1.5],
    }
    
    def __init__(self, days: int = 30):
        """
        Initialize optimizer
        
        Args:
            days: Number of days for backtest
        """
        self.days = days
        self.results: List[OptimizationResult] = []
        
    def _generate_sample_data(self, params: Dict) -> List[Dict]:
        """Generate sample OHLCV data for testing"""
        data = []
        base_time = datetime.now() - timedelta(days=self.days)
        price = 23000.0
        
        # Create realistic price action
        trend = 0  # 1 = up, -1 = down, 0 = sideways
        trend_duration = 0
        
        for i in range(self.days * 375):  # 375 minutes per day
            # Change trend periodically
            if trend_duration <= 0:
                trend = random.choice([-1, 0, 0, 1])  # Bias towards sideways
                trend_duration = random.randint(30, 120)
            trend_duration -= 1
            
            # Generate movement
            if trend == 1:
                move = random.gauss(0.5, 2)
            elif trend == -1:
                move = random.gauss(-0.5, 2)
            else:
                move = random.gauss(0, 1.5)
                
            price = max(22000, min(24000, price + move))
            
            high = price + random.uniform(0, 3)
            low = price - random.uniform(0, 3)
            close = price + random.gauss(0, 1)
            volume = random.randint(1000, 10000)
            
            data.append({
                'timestamp': base_time + timedelta(minutes=i),
                'open': price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume
            })
            
        return data
        
    def _run_backtest(self, params: Dict) -> OptimizationResult:
        """Run backtest with given parameters"""
        from strategy.regime_detector import RegimeDetector, MarketRegime
        from indicators.vwap import VWAPIndicator
        
        # Initialize with parameters
        regime_detector = RegimeDetector(
            atr_period=params['atr_period'],
            ema_fast=params['ema_fast'],
            ema_slow=params['ema_slow']
        )
        vwap = VWAPIndicator(band_multiplier=2.0)
        
        # Generate data
        data = self._generate_sample_data(params)
        
        # Simulate trading
        trades = []
        in_position = False
        entry_price = 0
        entry_time = None
        last_signal_time = None
        cooldown_minutes = params['signal_cooldown_minutes']
        min_confidence = params['min_confidence']
        
        equity = 100000
        equity_curve = [equity]
        max_equity = equity
        max_drawdown = 0
        
        for candle in data:
            # Update indicators
            regime_data = regime_detector.update(
                candle['high'], candle['low'], candle['close'],
                candle['timestamp']
            )
            vwap_data = vwap.update(
                candle['high'], candle['low'], candle['close'],
                candle['volume'], candle['timestamp']
            )
            
            if regime_data is None or vwap_data is None:
                continue
                
            # Check cooldown
            if last_signal_time:
                time_since = (candle['timestamp'] - last_signal_time).total_seconds() / 60
                if time_since < cooldown_minutes:
                    continue
                    
            # Check confidence
            if regime_data.confidence < min_confidence:
                continue
                
            # Trading logic
            if not in_position:
                # Entry conditions
                if regime_data.regime == MarketRegime.TRENDING_UP and vwap_data.price_position == "ABOVE":
                    in_position = True
                    entry_price = candle['close']
                    entry_time = candle['timestamp']
                    last_signal_time = candle['timestamp']
                    sl = entry_price - regime_data.atr * params['atr_sl_multiplier']
                    tp = entry_price + regime_data.atr * params['atr_sl_multiplier'] * params['min_rr_ratio']
                    
                elif regime_data.regime == MarketRegime.TRENDING_DOWN and vwap_data.price_position == "BELOW":
                    in_position = True
                    entry_price = candle['close']
                    entry_time = candle['timestamp']
                    last_signal_time = candle['timestamp']
                    sl = entry_price + regime_data.atr * params['atr_sl_multiplier']
                    tp = entry_price - regime_data.atr * params['atr_sl_multiplier'] * params['min_rr_ratio']
                    
            else:
                # Exit conditions (simplified)
                price_move = candle['close'] - entry_price
                
                # Random exit based on probabilities
                if random.random() < 0.02:  # 2% chance per candle
                    pnl = price_move * 75  # 1 lot
                    trades.append(pnl)
                    equity += pnl
                    in_position = False
                    
            # Track equity
            equity_curve.append(equity)
            max_equity = max(max_equity, equity)
            current_dd = max_equity - equity
            max_drawdown = max(max_drawdown, current_dd)
            
        # Calculate metrics
        if not trades:
            return OptimizationResult(
                params=params,
                total_trades=0,
                win_rate=0,
                profit_factor=0,
                total_pnl=0,
                max_drawdown=max_drawdown,
                sharpe_ratio=0
            )
            
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]
        
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0
        profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
        total_pnl = sum(trades)
        
        # Sharpe ratio (simplified)
        import statistics
        if len(trades) > 1:
            avg_return = statistics.mean(trades)
            std_return = statistics.stdev(trades)
            sharpe_ratio = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
        else:
            sharpe_ratio = 0
            
        return OptimizationResult(
            params=params,
            total_trades=len(trades),
            win_rate=win_rate,
            profit_factor=min(profit_factor, 10),
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio
        )
        
    def _generate_param_combinations(self) -> List[Dict]:
        """Generate all parameter combinations"""
        keys = list(self.PARAM_GRID.keys())
        values = list(self.PARAM_GRID.values())
        
        combinations = []
        for combo in itertools.product(*values):
            param_dict = dict(zip(keys, combo))
            # Filter invalid combinations (ema_fast must be < ema_slow)
            if param_dict['ema_fast'] < param_dict['ema_slow']:
                combinations.append(param_dict)
                
        return combinations
        
    def optimize(self, max_iterations: int = None) -> List[OptimizationResult]:
        """
        Run optimization
        
        Args:
            max_iterations: Maximum combinations to test (None = all)
            
        Returns:
            Sorted list of results (best first)
        """
        combinations = self._generate_param_combinations()
        
        if max_iterations:
            combinations = combinations[:max_iterations]
            
        total = len(combinations)
        print(f"\n🔍 Testing {total} parameter combinations...")
        print("-" * 50)
        
        for i, params in enumerate(combinations, 1):
            result = self._run_backtest(params)
            self.results.append(result)
            
            # Progress update
            if i % 10 == 0 or i == total:
                print(f"   Progress: {i}/{total} ({i/total*100:.0f}%)")
                
        # Sort by score
        self.results.sort(key=lambda x: x.score, reverse=True)
        
        return self.results
        
    def print_results(self, top_n: int = 10):
        """Print top N results"""
        print("\n" + "="*70)
        print(f"🏆 TOP {top_n} PARAMETER COMBINATIONS")
        print("="*70)
        
        for i, result in enumerate(self.results[:top_n], 1):
            print(f"\n#{i} Score: {result.score:.2f}")
            print(f"   Trades: {result.total_trades} | Win Rate: {result.win_rate:.1f}%")
            print(f"   P&L: ₹{result.total_pnl:,.0f} | PF: {result.profit_factor:.2f}")
            print(f"   Max DD: ₹{result.max_drawdown:,.0f} | Sharpe: {result.sharpe_ratio:.2f}")
            print(f"   Params: ")
            for k, v in result.params.items():
                print(f"      {k}: {v}")
                
    def save_results(self, filepath: str = "data/optimization_results.json"):
        """Save results to JSON"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        data = []
        for r in self.results:
            data.append({
                'params': r.params,
                'metrics': {
                    'total_trades': r.total_trades,
                    'win_rate': r.win_rate,
                    'profit_factor': r.profit_factor,
                    'total_pnl': r.total_pnl,
                    'max_drawdown': r.max_drawdown,
                    'sharpe_ratio': r.sharpe_ratio,
                    'score': r.score
                }
            })
            
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            
        print(f"\n✅ Results saved to {filepath}")
        
    def get_best_params(self) -> Dict:
        """Get best parameter combination"""
        if not self.results:
            return {}
        return self.results[0].params


def main():
    """Run optimization"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Parameter Optimizer")
    parser.add_argument("--days", "-d", type=int, default=30, help="Days of backtest")
    parser.add_argument("--max-iter", "-m", type=int, default=50, help="Max iterations")
    parser.add_argument("--top", "-t", type=int, default=10, help="Top N to display")
    args = parser.parse_args()
    
    print("\n" + "="*50)
    print("⚙️  PARAMETER OPTIMIZER")
    print("="*50)
    print(f"   Days: {args.days}")
    print(f"   Max iterations: {args.max_iter}")
    
    optimizer = ParameterOptimizer(days=args.days)
    optimizer.optimize(max_iterations=args.max_iter)
    optimizer.print_results(top_n=args.top)
    optimizer.save_results()
    
    # Print best params for easy copy
    best = optimizer.get_best_params()
    if best:
        print("\n" + "="*50)
        print("🎯 BEST PARAMETERS (copy to settings.yaml)")
        print("="*50)
        print("strategy:")
        print("  entry:")
        print(f"    atr_sl_multiplier: {best.get('atr_sl_multiplier', 1.0)}")
        print(f"    min_rr_ratio: {best.get('min_rr_ratio', 2.0)}")
        print(f"    signal_cooldown_minutes: {best.get('signal_cooldown_minutes', 5)}")
        print(f"    min_confidence: {best.get('min_confidence', 0.7)}")
        print("  regime:")
        print(f"    atr_period: {best.get('atr_period', 14)}")
        print(f"    ema_fast: {best.get('ema_fast', 8)}")
        print(f"    ema_slow: {best.get('ema_slow', 21)}")


if __name__ == "__main__":
    main()
