"""
N-Structure Backtester

Runs the trading strategy on historical data.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque

from loguru import logger

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.historical_data import HistoricalCandle, HistoricalDataFetcher
from indicators.ema import EMASet


@dataclass
class Trade:
    """Single trade record."""
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    sl_price: float = 0.0
    quantity: int = 25  # NIFTY lot size
    pnl: float = 0.0
    exit_reason: str = ""
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class BacktestResult:
    """Backtest results summary."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    trades: List[Trade] = field(default_factory=list)


class NStructureBacktester:
    """
    Backtester for N-Structure strategy.
    
    Simulates trading on historical data.
    """
    
    def __init__(
        self,
        initial_sl_points: float = 10.0,  # Wider SL - let trades breathe
        target_points: float = 25.0,      # 2.5:1 R:R target
        be_trigger_points: float = 8.0,   # Move to BE at 8pt
        trail_distance: float = 5.0,
        lot_size: int = 25,
        max_trades_per_day: int = 2,      # Only 2 quality trades/day
        loss_cooldown_candles: int = 20   # Wait 20 min after loss
    ):
        """
        Initialize backtester - OPTIMIZED FOR BETTER R:R.
        
        Args:
            initial_sl_points: Initial stop loss in points
            target_points: Target profit in points
            be_trigger_points: Points to move SL to breakeven
            trail_distance: Trailing stop distance
            lot_size: NIFTY lot size
            max_trades_per_day: Max trades per day
            loss_cooldown_candles: Candles to wait after a loss
        """
        self.initial_sl_points = initial_sl_points
        self.target_points = target_points
        self.be_trigger_points = be_trigger_points
        self.trail_distance = trail_distance
        self.lot_size = lot_size
        self.max_trades_per_day = max_trades_per_day
        self.loss_cooldown_candles = loss_cooldown_candles
        
        # EMAs
        self.index_emas = EMASet(periods=[9, 15])
        self.option_emas = EMASet(periods=[9, 15])
        
        # State
        self.current_trade: Optional[Trade] = None
        self.trades: List[Trade] = []
        self.daily_trades: int = 0
        self.current_date: Optional[datetime] = None
        self.equity_curve: List[float] = [0.0]
        self._last_loss_idx: int = 0
        
        # Candle windows for N detection
        self.index_candles: deque = deque(maxlen=20)
        self.option_candles: deque = deque(maxlen=20)
        
        # Track swing lows for N-structure
        self.recent_lows: List[float] = []
    
    def reset(self):
        """Reset backtester state."""
        self.index_emas = EMASet(periods=[9, 15])
        self.option_emas = EMASet(periods=[9, 15])
        self.current_trade = None
        self.trades = []
        self.daily_trades = 0
        self.current_date = None
        self.equity_curve = [0.0]
        self._last_loss_idx = 0
        self.index_candles.clear()
        self.option_candles.clear()
        self.recent_lows = []
    
    def _is_trading_hours(self, ts: datetime) -> bool:
        """Check if within trading hours (9:16 AM - 3:25 PM)."""
        t = ts.time()
        return time(9, 16) <= t <= time(15, 25)
    
    def _check_day_change(self, ts: datetime):
        """Reset daily counters on new day."""
        if self.current_date is None or ts.date() != self.current_date:
            self.current_date = ts.date()
            self.daily_trades = 0
            self.recent_lows = []  # Reset swing tracking
            self._last_loss_idx = 0  # Reset loss cooldown
    
    def _update_sl(self, current_price: float, entry_price: float):
        """
        Update stop loss (trailing/breakeven) - OPTIMIZED V3.
        
        Strategy: Let winners run to target, quick BE for safety.
        - BE at 10 points (lock cost quickly)
        - Trail loosely at 15+ points (let it run)
        - Tighter trail only at 25+ points
        """
        if not self.current_trade:
            return
        
        profit = current_price - entry_price
        
        # Move to breakeven after 10 points profit
        if profit >= 10.0:
            new_sl = entry_price + 1.0  # Lock in 1 point at BE
            if new_sl > self.current_trade.sl_price:
                self.current_trade.sl_price = new_sl
        
        # Trail stop loosely after 15 points - let it run to target
        if profit >= 15.0:
            trail_sl = entry_price + 5.0  # Lock 5 points
            if trail_sl > self.current_trade.sl_price:
                self.current_trade.sl_price = trail_sl
        
        # Trail after 20 points - medium trail
        if profit >= 20.0:
            trail_sl = current_price - 8.0  # 8 point trail (loose)
            if trail_sl > self.current_trade.sl_price:
                self.current_trade.sl_price = trail_sl
        
        # Tighter trail after 25 points (near target)
        if profit >= 25.0:
            trail_sl = current_price - 5.0  # 5 point trail
            if trail_sl > self.current_trade.sl_price:
                self.current_trade.sl_price = trail_sl
    
    def _check_sl_hit(self, candle: HistoricalCandle) -> bool:
        """Check if stop loss was hit."""
        if not self.current_trade:
            return False
        
        return candle.low <= self.current_trade.sl_price
    
    def _check_target_hit(self, candle: HistoricalCandle, target_points: float = 15.0) -> bool:
        """Check if target profit is reached."""
        if not self.current_trade:
            return False
        
        target_price = self.current_trade.entry_price + target_points
        return candle.high >= target_price
    
    def _enter_trade(self, ts: datetime, price: float):
        """Enter a new trade."""
        self.current_trade = Trade(
            entry_time=ts,
            entry_price=price,
            sl_price=price - self.initial_sl_points,
            quantity=self.lot_size
        )
        self.daily_trades += 1
        logger.info(f"ENTRY @ ₹{price:.2f} | SL: ₹{self.current_trade.sl_price:.2f} | {ts}")
    
    def _exit_trade(self, ts: datetime, price: float, reason: str):
        """Exit current trade."""
        if not self.current_trade:
            return
        
        self.current_trade.exit_time = ts
        self.current_trade.exit_price = price
        self.current_trade.exit_reason = reason
        self.current_trade.pnl = (price - self.current_trade.entry_price) * self.lot_size
        
        self.trades.append(self.current_trade)
        
        # Track loss for cooldown
        if self.current_trade.pnl < 0:
            self._last_loss_idx = len(self.option_candles)
        
        # Update equity curve
        self.equity_curve.append(self.equity_curve[-1] + self.current_trade.pnl)
        
        pnl_str = f"+₹{self.current_trade.pnl:.0f}" if self.current_trade.pnl > 0 else f"-₹{abs(self.current_trade.pnl):.0f}"
        logger.info(f"EXIT @ ₹{price:.2f} | {reason} | PnL: {pnl_str} | {ts}")
        
        self.current_trade = None
    
    def run(
        self,
        index_candles: List[HistoricalCandle],
        option_candles: List[HistoricalCandle]
    ) -> BacktestResult:
        """
        Run backtest on historical data (legacy method with option candles).
        
        Args:
            index_candles: NIFTY index 1-min candles
            option_candles: Option 1-min candles
            
        Returns:
            BacktestResult with all stats
        """
        self.reset()
        
        # Create timestamp-indexed dict for option candles
        option_dict = {c.timestamp: c for c in option_candles}
        
        logger.info(f"Starting backtest with {len(index_candles)} index candles")
        
        for idx_candle in index_candles:
            ts = idx_candle.timestamp
            
            # Check day change
            self._check_day_change(ts)
            
            # Skip non-trading hours
            if not self._is_trading_hours(ts):
                continue
            
            # Get matching option candle
            opt_candle = option_dict.get(ts)
            if not opt_candle:
                continue
            
            # Update EMAs
            self.index_emas.update(idx_candle.close)
            self.option_emas.update(opt_candle.close)
            
            # Store candles for N detection
            self.index_candles.append(idx_candle)
            self.option_candles.append(opt_candle)
            
            # Get EMA values
            idx_ema9 = self.index_emas.get_value(9)
            idx_ema15 = self.index_emas.get_value(15)
            opt_ema9 = self.option_emas.get_value(9)
            
            if not all([idx_ema9, idx_ema15, opt_ema9]):
                continue
            
            # Check for open position
            if self.current_trade:
                # Update trailing SL
                self._update_sl(opt_candle.close, self.current_trade.entry_price)
                
                # Check Target hit (use configured target_points)
                if self._check_target_hit(opt_candle, target_points=self.target_points):
                    target_price = self.current_trade.entry_price + self.target_points
                    self._exit_trade(ts, target_price, "Target Hit")
                    continue
                
                # Check SL hit (this includes trailing SL)
                if self._check_sl_hit(opt_candle):
                    # Exit at SL price (could be BE or trailing SL)
                    self._exit_trade(ts, self.current_trade.sl_price, "SL Hit")
                    continue
                
                # Check EOD exit (3:25 PM)
                if ts.time() >= time(15, 25):
                    self._exit_trade(ts, opt_candle.close, "EOD Exit")
                    continue
            
            else:
                # Check for entry signal
                if self.daily_trades >= self.max_trades_per_day:
                    continue
                
                # Need at least 10 candles for pattern detection
                if len(self.option_candles) < 10:
                    continue
                
                # Simple N-Structure Detection:
                # 1. Index EMA9 > EMA15 (uptrend)
                # 2. Option pullback to EMA (within 2%)
                # 3. Option close above EMA9 (breakout)
                # 4. Higher low pattern (HL1 -> HL2)
                
                entry_signal = self._check_entry_signal(
                    idx_candle, opt_candle,
                    idx_ema9, idx_ema15, opt_ema9
                )
                
                if entry_signal:
                    self._enter_trade(ts, opt_candle.close)
        
        # Close any open trade at end
        if self.current_trade and option_candles:
            last_candle = option_candles[-1]
            self._exit_trade(last_candle.timestamp, last_candle.close, "Backtest End")
        
        # Calculate results
        return self._calculate_results()
    
    def run_index_only(
        self,
        index_candles: List[HistoricalCandle],
        entry_premium_range: Tuple[float, float] = (90.0, 110.0),
        delta: float = 0.5
    ) -> BacktestResult:
        """
        Run backtest using ONLY index data with dynamic strike selection.
        
        At each signal, simulates picking a CE option with premium in 
        the specified range (₹90-110). Option price moves with delta.
        
        This is MORE REALISTIC than using a single option for 30 days!
        
        Args:
            index_candles: NIFTY index 1-min candles
            entry_premium_range: (min, max) premium range for strike selection
            delta: Option delta (0.5 for ATM)
            
        Returns:
            BacktestResult with all stats
        """
        self.reset()
        
        min_premium, max_premium = entry_premium_range
        target_premium = (min_premium + max_premium) / 2  # ₹100 default
        
        logger.info(f"Starting INDEX-ONLY backtest with {len(index_candles)} candles")
        logger.info(f"Entry premium range: ₹{min_premium}-₹{max_premium} | Delta: {delta}")
        
        # Track current trade's option state
        trade_entry_nifty: Optional[float] = None
        trade_entry_premium: Optional[float] = None
        
        for idx_candle in index_candles:
            ts = idx_candle.timestamp
            
            # Check day change
            self._check_day_change(ts)
            
            # Skip non-trading hours
            if not self._is_trading_hours(ts):
                continue
            
            # Update index EMAs
            self.index_emas.update(idx_candle.close)
            
            # Store candles
            self.index_candles.append(idx_candle)
            
            # Get EMA values
            idx_ema9 = self.index_emas.get_value(9)
            idx_ema15 = self.index_emas.get_value(15)
            
            if not all([idx_ema9, idx_ema15]):
                continue
            
            # Check for open position
            if self.current_trade and trade_entry_nifty is not None:
                # Calculate current option price based on NIFTY movement
                nifty_change = idx_candle.close - trade_entry_nifty
                current_premium = trade_entry_premium + (delta * nifty_change)
                current_premium = max(current_premium, 1.0)  # Min ₹1
                
                # Create synthetic option candle for SL/Target check
                # Simulate intrabar volatility
                high_premium = current_premium + abs(nifty_change * 0.1)
                low_premium = current_premium - abs(nifty_change * 0.1)
                low_premium = max(low_premium, 0.5)
                
                # Update trailing SL
                self._update_sl(current_premium, self.current_trade.entry_price)
                
                # Check Target hit
                target_price = self.current_trade.entry_price + self.target_points
                if high_premium >= target_price:
                    self._exit_trade(ts, target_price, "Target Hit")
                    trade_entry_nifty = None
                    trade_entry_premium = None
                    continue
                
                # Check SL hit
                if low_premium <= self.current_trade.sl_price:
                    self._exit_trade(ts, self.current_trade.sl_price, "SL Hit")
                    trade_entry_nifty = None
                    trade_entry_premium = None
                    continue
                
                # Check EOD exit (3:25 PM)
                if ts.time() >= time(15, 25):
                    self._exit_trade(ts, current_premium, "EOD Exit")
                    trade_entry_nifty = None
                    trade_entry_premium = None
                    continue
            
            else:
                # Check for entry signal
                if self.daily_trades >= self.max_trades_per_day:
                    continue
                
                # Need at least 10 candles for pattern detection
                if len(self.index_candles) < 10:
                    continue
                
                # Check entry signal using INDEX data only
                entry_signal = self._check_index_entry_signal(
                    idx_candle, idx_ema9, idx_ema15
                )
                
                if entry_signal:
                    # Pick a fresh option with premium in ₹90-110 range
                    # In reality, this would be ATM or slightly OTM CE
                    import random
                    entry_premium = random.uniform(min_premium, max_premium)
                    
                    # Record entry state
                    trade_entry_nifty = idx_candle.close
                    trade_entry_premium = entry_premium
                    
                    self._enter_trade(ts, entry_premium)
        
        # Close any open trade at end
        if self.current_trade and trade_entry_nifty is not None:
            nifty_change = index_candles[-1].close - trade_entry_nifty
            final_premium = trade_entry_premium + (delta * nifty_change)
            final_premium = max(final_premium, 1.0)
            self._exit_trade(index_candles[-1].timestamp, final_premium, "Backtest End")
        
        # Calculate results
        return self._calculate_results()
    
    def _check_index_entry_signal(
        self,
        idx_candle: HistoricalCandle,
        idx_ema9: float,
        idx_ema15: float
    ) -> bool:
        """
        Check for entry signal using ONLY INDEX data - OPTIMIZED V3 FINAL.
        
        BALANCED Entry Filters for Good Win Rate + Profit:
        1. Time filter - 9:50 AM - 2:40 PM
        2. Cooldown after loss (20 candles)
        3. Uptrend (EMA9 > EMA15)
        4. Price near EMA (within 0.2%)
        5. Strong bullish candle (body > 45%)
        6. Higher low pattern
        7. Momentum: close in upper 60% of range
        """
        ts = idx_candle.timestamp
        
        # TIME FILTER: 9:50 AM - 2:40 PM
        if ts.time() < time(9, 50) or ts.time() > time(14, 40):
            return False
        
        # COOLDOWN: Wait 20 candles after a loss
        candles_since_loss = len(self.index_candles) - self._last_loss_idx
        if self._last_loss_idx > 0 and candles_since_loss < 20:
            return False
        
        # 1. UPTREND: EMA9 > EMA15
        if idx_ema9 <= idx_ema15:
            return False
        
        # 2. INDEX NEAR EMA: Within 0.2% of EMA9
        ema_distance = abs(idx_candle.close - idx_ema9) / idx_ema9
        if ema_distance > 0.002:  # 0.2%
            return False
        
        # 3. INDEX ABOVE EMA (breakout confirmed)
        if idx_candle.close <= idx_ema9:
            return False
        
        # 4. STRONG BULLISH CANDLE (body > 45% of range)
        candle_body = idx_candle.close - idx_candle.open
        candle_range = idx_candle.high - idx_candle.low
        if candle_body <= 0:  # Must be bullish
            return False
        if candle_range > 0 and (candle_body / candle_range) < 0.45:
            return False
        
        # 5. MOMENTUM: Close should be in upper 60% of candle range
        if candle_range > 0:
            close_position = (idx_candle.close - idx_candle.low) / candle_range
            if close_position < 0.60:
                return False
        
        # 6. HIGHER LOW PATTERN on Index
        self.recent_lows.append(idx_candle.low)
        if len(self.recent_lows) > 12:
            self.recent_lows = self.recent_lows[-12:]
        
        if len(self.recent_lows) < 5:
            return False
        
        # Find swing lows (local minima)
        swing_lows = []
        for i in range(1, len(self.recent_lows) - 1):
            if (self.recent_lows[i] < self.recent_lows[i-1] and
                self.recent_lows[i] <= self.recent_lows[i+1]):
                swing_lows.append(self.recent_lows[i])
        
        # Need 2 swing lows with HL pattern
        if len(swing_lows) >= 2:
            hl1 = swing_lows[-2]
            hl2 = swing_lows[-1]
            if hl2 > hl1:
                return True
        
        return False
    
    def _check_entry_signal(
        self,
        idx_candle: HistoricalCandle,
        opt_candle: HistoricalCandle,
        idx_ema9: float,
        idx_ema15: float,
        opt_ema9: float
    ) -> bool:
        """
        Check for N-Structure entry signal - OPTIMIZED V2 (BALANCED).
        
        Entry Filters:
        1. Time filter - 9:45 AM - 2:45 PM
        2. Cooldown after loss (20 candles)
        3. Uptrend (EMA9 > EMA15)
        4. Option near EMA (within 2.5%)
        5. Option above EMA
        6. Bullish candle with good body
        7. Higher low pattern
        """
        ts = opt_candle.timestamp
        
        # TIME FILTER: 9:45 AM - 2:45 PM
        if ts.time() < time(9, 45) or ts.time() > time(14, 45):
            return False
        
        # COOLDOWN: Wait 20 candles after a loss
        candles_since_loss = len(self.option_candles) - self._last_loss_idx
        if self._last_loss_idx > 0 and candles_since_loss < self.loss_cooldown_candles:
            return False
        
        # 1. UPTREND: EMA9 > EMA15
        if idx_ema9 <= idx_ema15:
            return False
        
        # 2. OPTION NEAR EMA: Within 2.5%
        ema_distance = abs(opt_candle.close - opt_ema9) / opt_ema9
        if ema_distance > 0.025:
            return False
        
        # 3. OPTION ABOVE EMA (breakout)
        if opt_candle.close <= opt_ema9:
            return False
        
        # 4. BULLISH CANDLE (body > 40% of range)
        candle_body = opt_candle.close - opt_candle.open
        candle_range = opt_candle.high - opt_candle.low
        if candle_body <= 0:  # Must be bullish
            return False
        if candle_range > 0 and (candle_body / candle_range) < 0.4:
            return False  # Reject doji/weak candles
        
        # 5. HIGHER LOW PATTERN
        self.recent_lows.append(opt_candle.low)
        if len(self.recent_lows) > 12:
            self.recent_lows = self.recent_lows[-12:]
        
        if len(self.recent_lows) < 5:
            return False
        
        # Find swing lows (local minima)
        swing_lows = []
        for i in range(1, len(self.recent_lows) - 1):
            if (self.recent_lows[i] < self.recent_lows[i-1] and
                self.recent_lows[i] <= self.recent_lows[i+1]):
                swing_lows.append(self.recent_lows[i])
        
        # Need 2 swing lows with HL pattern
        if len(swing_lows) >= 2:
            hl1 = swing_lows[-2]
            hl2 = swing_lows[-1]
            
            # HL2 must be higher than HL1
            if hl2 > hl1:
                return True
        
        return False
    
    def _calculate_results(self) -> BacktestResult:
        """Calculate backtest statistics."""
        result = BacktestResult(trades=self.trades)
        
        if not self.trades:
            return result
        
        result.total_trades = len(self.trades)
        
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.total_pnl = sum(t.pnl for t in self.trades)
        
        if result.total_trades > 0:
            result.win_rate = (result.winning_trades / result.total_trades) * 100
        
        if wins:
            result.avg_win = sum(t.pnl for t in wins) / len(wins)
        
        if losses:
            result.avg_loss = abs(sum(t.pnl for t in losses) / len(losses))
        
        # Profit factor
        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Max drawdown
        peak = 0
        max_dd = 0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd
        
        return result


def print_results(result: BacktestResult):
    """Print backtest results."""
    print("\n" + "=" * 60)
    print("           N-STRUCTURE BACKTEST RESULTS")
    print("=" * 60)
    
    print(f"\n📊 Trade Statistics:")
    print(f"   Total Trades:    {result.total_trades}")
    print(f"   Winning Trades:  {result.winning_trades}")
    print(f"   Losing Trades:   {result.losing_trades}")
    print(f"   Win Rate:        {result.win_rate:.1f}%")
    
    print(f"\n💰 P&L Analysis:")
    pnl_str = f"+₹{result.total_pnl:,.0f}" if result.total_pnl > 0 else f"-₹{abs(result.total_pnl):,.0f}"
    print(f"   Total P&L:       {pnl_str}")
    print(f"   Avg Win:         +₹{result.avg_win:,.0f}")
    print(f"   Avg Loss:        -₹{result.avg_loss:,.0f}")
    print(f"   Profit Factor:   {result.profit_factor:.2f}")
    print(f"   Max Drawdown:    -₹{result.max_drawdown:,.0f}")
    
    if result.trades:
        print(f"\n📝 Trade Log:")
        print("-" * 60)
        for i, t in enumerate(result.trades[:20], 1):  # Show first 20
            pnl = f"+₹{t.pnl:.0f}" if t.pnl > 0 else f"-₹{abs(t.pnl):.0f}"
            print(f"   {i:2}. {t.entry_time.strftime('%d-%b %H:%M')} | "
                  f"Entry: ₹{t.entry_price:.1f} | "
                  f"Exit: ₹{t.exit_price:.1f} | "
                  f"{t.exit_reason:10} | {pnl}")
        
        if len(result.trades) > 20:
            print(f"   ... and {len(result.trades) - 20} more trades")
    
    print("\n" + "=" * 60)
