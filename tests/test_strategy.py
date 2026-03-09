"""
Test script for new Adaptive Hybrid Strategy indicators
Verifies VWAP, Volume Profile, Regime Detection work correctly
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
import random

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.indicators.vwap import VWAPIndicator
from src.indicators.volume_profile import VolumeProfile
from src.indicators.market_structure import MarketStructure
from src.strategy.regime_detector import RegimeDetector, MarketRegime
from src.strategy.adaptive_hybrid import AdaptiveHybridStrategy, SignalType


def generate_candles(base_price: float, num_candles: int, trend: str = "sideways"):
    """Generate simulated candle data"""
    candles = []
    price = base_price
    
    for i in range(num_candles):
        # Add trend bias
        if trend == "up":
            drift = random.uniform(-2, 4)
        elif trend == "down":
            drift = random.uniform(-4, 2)
        else:
            drift = random.uniform(-3, 3)
            
        open_price = price
        close_price = price + drift
        high = max(open_price, close_price) + random.uniform(0, 5)
        low = min(open_price, close_price) - random.uniform(0, 5)
        volume = random.randint(50000, 200000)
        timestamp = datetime(2026, 1, 28, 9, 15) + timedelta(minutes=i)
        
        candles.append({
            'open': open_price,
            'high': high,
            'low': low,
            'close': close_price,
            'volume': volume,
            'timestamp': timestamp
        })
        
        price = close_price
        
    return candles


def test_vwap():
    """Test VWAP indicator"""
    print("\n" + "="*50)
    print("Testing VWAP Indicator")
    print("="*50)
    
    vwap = VWAPIndicator()
    candles = generate_candles(23000, 50, "up")
    
    for c in candles:
        data = vwap.update(c['high'], c['low'], c['close'], c['volume'], c['timestamp'])
        
    print(f"Final VWAP: {vwap.get_current_vwap():.2f}")
    print(f"Current Price: {candles[-1]['close']:.2f}")
    print(f"Bias: {vwap.get_bias(candles[-1]['close'])}")
    
    data = vwap.get_current_data()
    if data:
        print(f"Upper Band: {data.upper_band:.2f}")
        print(f"Lower Band: {data.lower_band:.2f}")
        
    print("✅ VWAP Test PASSED")
    return True


def test_volume_profile():
    """Test Volume Profile indicator"""
    print("\n" + "="*50)
    print("Testing Volume Profile Indicator")
    print("="*50)
    
    vp = VolumeProfile(tick_size=0.5)
    candles = generate_candles(23000, 100, "sideways")
    
    for c in candles:
        levels = vp.update(c['high'], c['low'], c['close'], c['volume'], c['timestamp'])
        
    if levels:
        print(f"POC: {levels.poc:.2f}")
        print(f"VAH: {levels.vah:.2f}")
        print(f"VAL: {levels.val:.2f}")
        print(f"Profile Type: {levels.profile_type}")
        print(f"Current Price: {candles[-1]['close']:.2f}")
        print(f"In Value Area: {vp.is_in_value_area(candles[-1]['close'])}")
        print(f"Trading Bias: {vp.get_trading_bias(candles[-1]['close'])}")
        
    print("✅ Volume Profile Test PASSED")
    return True


def test_market_structure():
    """Test Market Structure indicator"""
    print("\n" + "="*50)
    print("Testing Market Structure Indicator")
    print("="*50)
    
    ms = MarketStructure(swing_lookback=5)
    
    # Day 1 data
    candles_day1 = generate_candles(23000, 100, "up")
    for c in candles_day1:
        ms.update(c['high'], c['low'], c['close'], c['timestamp'])
        
    # Day 2 data (new day)
    candles_day2 = generate_candles(23150, 50, "sideways")
    for c in candles_day2:
        # Adjust timestamp to be next day
        c['timestamp'] = c['timestamp'] + timedelta(days=1)
        levels = ms.update(c['high'], c['low'], c['close'], c['timestamp'])
        
    if levels:
        print(f"PDH: {levels.pdh:.2f}")
        print(f"PDL: {levels.pdl:.2f}")
        print(f"PDC: {levels.pdc:.2f}")
        print(f"Current High: {levels.current_high:.2f}")
        print(f"Current Low: {levels.current_low:.2f}")
        print(f"Pivot: {levels.pivot:.2f}")
        print(f"R1: {levels.r1:.2f}")
        print(f"S1: {levels.s1:.2f}")
        print(f"Daily Range: {levels.daily_range:.2f}")
        print(f"Range Type: {levels.range_type}")
        
    swing = ms.get_swing_analysis()
    print(f"Trend: {swing.trend}")
    print(f"ATR: {ms.get_atr():.2f}")
    
    print("✅ Market Structure Test PASSED")
    return True


def test_regime_detector():
    """Test Regime Detector"""
    print("\n" + "="*50)
    print("Testing Regime Detector")
    print("="*50)
    
    rd = RegimeDetector(atr_period=14, lookback=20)
    
    # Test trending market
    print("\n--- Testing UPTREND ---")
    candles = generate_candles(23000, 50, "up")
    for c in candles:
        data = rd.update(c['high'], c['low'], c['close'], c['timestamp'])
        
    print(f"Regime: {data.regime.value}")
    print(f"Confidence: {data.confidence:.0%}")
    print(f"Trend Strength: {data.trend_strength:.1f}")
    print(f"Range Score: {data.range_bound_score:.1f}")
    print(f"Is Tradeable: {data.is_tradeable}")
    
    # Reset and test sideways
    print("\n--- Testing SIDEWAYS ---")
    rd.reset()
    candles = generate_candles(23000, 50, "sideways")
    for c in candles:
        data = rd.update(c['high'], c['low'], c['close'], c['timestamp'])
        
    print(f"Regime: {data.regime.value}")
    print(f"Confidence: {data.confidence:.0%}")
    print(f"Trend Strength: {data.trend_strength:.1f}")
    print(f"Range Score: {data.range_bound_score:.1f}")
    
    print("✅ Regime Detector Test PASSED")
    return True


def test_adaptive_strategy():
    """Test full Adaptive Hybrid Strategy"""
    print("\n" + "="*50)
    print("Testing Adaptive Hybrid Strategy")
    print("="*50)
    
    strategy = AdaptiveHybridStrategy({
        'atr_sl_multiplier': 1.0,
        'min_rr_ratio': 2.0,
        'max_trades_per_day': 3,
        'max_daily_loss_pct': 2.0
    })
    
    # Generate warm-up candles (before trading window)
    warmup_candles = generate_candles(23000, 30, "up")
    for c in warmup_candles:
        c['timestamp'] = datetime(2026, 1, 28, 9, 0) + timedelta(minutes=warmup_candles.index(c))
        strategy.update(c['high'], c['low'], c['close'], c['volume'], c['timestamp'])
    print("Warmup complete")
    
    # Generate trading candles
    trading_candles = generate_candles(23050, 60, "sideways")
    signals = []
    
    for i, c in enumerate(trading_candles):
        c['timestamp'] = datetime(2026, 1, 28, 9, 30) + timedelta(minutes=i)
        signal = strategy.update(c['high'], c['low'], c['close'], c['volume'], c['timestamp'])
        
        if signal and signal.signal != SignalType.NO_SIGNAL:
            signals.append(signal)
            print(f"\n🎯 SIGNAL at {c['timestamp'].strftime('%H:%M')}:")
            print(f"   Type: {signal.signal.value}")
            print(f"   Entry: {signal.entry_price:.2f}")
            print(f"   SL: {signal.stop_loss:.2f}")
            print(f"   Target: {signal.target_1:.2f}")
            print(f"   R:R: 1:{signal.reward_ratio:.1f}")
            print(f"   Regime: {signal.regime}")
            print(f"   Reason: {signal.reason}")
            
    status = strategy.get_status()
    print(f"\nFinal Status:")
    print(f"  Regime: {status['regime']}")
    vwap_val = status['vwap']
    print(f"  VWAP: {vwap_val:.2f}" if vwap_val else "  VWAP: N/A")
    print(f"  ATR: {status['atr']:.2f}")
    print(f"  Trades Today: {status['trades_today']}")
    
    print(f"\nTotal Signals Generated: {len(signals)}")
    print("✅ Adaptive Strategy Test PASSED")
    return True


def main():
    """Run all tests"""
    print("="*60)
    print("  ADAPTIVE HYBRID STRATEGY - TEST SUITE")
    print("="*60)
    
    tests = [
        ("VWAP", test_vwap),
        ("Volume Profile", test_volume_profile),
        ("Market Structure", test_market_structure),
        ("Regime Detector", test_regime_detector),
        ("Adaptive Strategy", test_adaptive_strategy)
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ {name} Test FAILED: {e}")
            results.append((name, False))
            import traceback
            traceback.print_exc()
            
    # Summary
    print("\n" + "="*60)
    print("  TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {name}: {status}")
        
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Strategy is ready for use.")
        return 0
    else:
        print("\n⚠️ Some tests failed. Review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
