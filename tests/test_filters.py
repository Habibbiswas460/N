"""
Tests for Strategy Filters Module

Tests VolumeFilter, TrendFilter, TimeFilter, and CompositeFilter.
"""

import pytest
from datetime import time

import sys
sys.path.insert(0, '/home/lora/projects/N/src')

from indicators.filters import (
    VolumeFilter,
    VolumeAnalysis,
    TrendFilter,
    TrendAnalysis,
    TimeFilter,
    TimeAnalysis,
    CompositeFilter
)


class TestVolumeFilter:
    """Tests for VolumeFilter."""
    
    def test_initial_state_allows_trades(self):
        """Volume filter should allow trades when insufficient data."""
        vf = VolumeFilter(lookback_periods=20, min_volume_ratio=0.8)
        result = vf.update(1000)
        
        assert result.is_sufficient is True
        assert "Insufficient" in result.message
    
    def test_normal_volume_passes(self):
        """Normal volume should pass filter."""
        vf = VolumeFilter(lookback_periods=5, min_volume_ratio=0.8)
        
        # Build history
        for _ in range(5):
            vf.update(1000)
        
        # Test with 90% of average
        result = vf.update(900)
        assert result.is_sufficient is True
        assert result.volume_ratio == pytest.approx(0.9, rel=0.1)
    
    def test_low_volume_fails(self):
        """Low volume should fail filter."""
        vf = VolumeFilter(lookback_periods=5, min_volume_ratio=0.8)
        
        # Build history
        for _ in range(5):
            vf.update(1000)
        
        # Test with 50% of average
        result = vf.update(500)
        assert result.is_sufficient is False
        assert "Low volume" in result.message
    
    def test_high_volume_detected(self):
        """High volume should be properly identified."""
        vf = VolumeFilter(lookback_periods=5, min_volume_ratio=0.8, high_volume_bonus=1.5)
        
        # Build history
        for _ in range(5):
            vf.update(1000)
        
        # Test with 200% of average
        result = vf.update(2000)
        assert result.is_sufficient is True
        assert "High volume" in result.message
    
    def test_reset_clears_history(self):
        """Reset should clear volume history."""
        vf = VolumeFilter()
        
        for _ in range(10):
            vf.update(1000)
        
        vf.reset()
        result = vf.update(500)
        
        assert "Insufficient" in result.message


class TestTrendFilter:
    """Tests for TrendFilter."""
    
    def test_initial_state(self):
        """Initial analysis should show sideways."""
        tf = TrendFilter()
        result = tf.analyze(100.0, 100.0, 99.0)
        
        assert result.trend_direction == "SIDEWAYS"
        assert result.is_favorable is True
    
    def test_uptrend_detection(self):
        """Uptrend should be detected with rising prices."""
        tf = TrendFilter(trend_strength_threshold=0.002)
        
        # Build rising price history
        for i in range(15):
            price = 100 + i * 0.5  # 0.5% per candle
            tf.analyze(price, price + 1, price - 1)
        
        # Final analysis
        result = tf.analyze(108.0, 108.5, 107.0)
        
        assert result.trend_direction == "UP"
        assert result.is_favorable is True
    
    def test_downtrend_detection(self):
        """Downtrend should be detected with falling prices."""
        tf = TrendFilter(trend_strength_threshold=0.002)
        
        # Build falling price history
        for i in range(15):
            price = 100 - i * 0.5
            tf.analyze(price, price + 1, price - 1)
        
        result = tf.analyze(92.0, 91.5, 92.5)
        
        assert result.trend_direction == "DOWN"
    
    def test_ema_alignment(self):
        """EMA alignment should be correctly detected."""
        tf = TrendFilter()
        
        # Fast > Slow = bullish alignment
        result = tf.analyze(100.0, 101.0, 99.0)
        assert result.ema_alignment is True
        
        # Fast < Slow = bearish alignment
        result = tf.analyze(100.0, 99.0, 101.0)
        assert result.ema_alignment is False
    
    def test_reset_clears_history(self):
        """Reset should clear price history."""
        tf = TrendFilter()
        
        for i in range(20):
            tf.analyze(100 + i, 100 + i + 1, 100 + i - 1)
        
        tf.reset()
        result = tf.analyze(100.0, 100.0, 99.0)
        
        assert "Insufficient" in result.message


class TestTimeFilter:
    """Tests for TimeFilter."""
    
    def test_pre_market_closed(self):
        """Before 9:15 should be CLOSED."""
        tf = TimeFilter()
        result = tf.analyze(time(9, 0))
        
        assert result.market_phase == "CLOSED"
        assert result.is_optimal is False
    
    def test_opening_volatility(self):
        """9:15-9:45 should be OPENING."""
        tf = TimeFilter()
        result = tf.analyze(time(9, 30))
        
        assert result.market_phase == "OPENING"
        assert result.is_optimal is False
        assert "Opening volatility" in result.message
    
    def test_prime_trading_window(self):
        """10:00 should be ACTIVE and optimal."""
        tf = TimeFilter(optimal_start=time(9, 50), optimal_end=time(11, 30))
        result = tf.analyze(time(10, 0))
        
        assert result.market_phase == "ACTIVE"
        assert result.is_optimal is True
        assert "Prime trading" in result.message
    
    def test_midday_trading(self):
        """12:00 should be MIDDAY."""
        tf = TimeFilter(no_new_trades_after=time(12, 30))
        result = tf.analyze(time(12, 0))
        
        assert result.market_phase == "MIDDAY"
        assert result.is_optimal is True
    
    def test_after_cutoff(self):
        """After 12:30 should not be optimal."""
        tf = TimeFilter(no_new_trades_after=time(12, 30))
        result = tf.analyze(time(13, 0))
        
        assert result.is_optimal is False
        # Message can be "Avoid new trades" or "Position management only" depending on time
        assert "Position management" in result.message or "Avoid" in result.message
    
    def test_closing_phase(self):
        """14:00 should be CLOSING."""
        tf = TimeFilter()
        result = tf.analyze(time(14, 0))
        
        assert result.market_phase == "CLOSING"
        assert result.is_optimal is False


class TestCompositeFilter:
    """Tests for CompositeFilter."""
    
    def test_all_filters_pass(self):
        """When all conditions are met, should pass."""
        cf = CompositeFilter(
            enable_volume_filter=True,
            enable_trend_filter=True,
            enable_time_filter=True,
            volume_lookback=5,
            min_volume_ratio=0.8
        )
        
        # Build volume history
        for _ in range(5):
            cf.check_all(volume=1000)
        
        # Check all filters with good conditions
        passed, messages = cf.check_all(
            volume=1000,
            price=100.0,
            ema_fast=101.0,
            ema_slow=99.0,
            current_time=time(10, 30)
        )
        
        assert passed is True
        assert len(messages) == 3
    
    def test_volume_filter_fails(self):
        """Low volume should fail composite."""
        cf = CompositeFilter(
            enable_volume_filter=True,
            enable_trend_filter=False,
            enable_time_filter=False,
            volume_lookback=5,
            min_volume_ratio=0.8
        )
        
        # Build volume history
        for _ in range(5):
            cf.check_all(volume=1000)
        
        # Check with low volume
        passed, messages = cf.check_all(volume=500)
        
        assert passed is False
        assert any("Low volume" in m for m in messages)
    
    def test_time_filter_fails(self):
        """Bad timing should fail composite."""
        cf = CompositeFilter(
            enable_volume_filter=False,
            enable_trend_filter=False,
            enable_time_filter=True
        )
        
        passed, messages = cf.check_all(current_time=time(9, 30))
        
        assert passed is False
        assert any("Opening" in m or "volatility" in m.lower() for m in messages)
    
    def test_disabled_filters_ignored(self):
        """Disabled filters should not affect result."""
        cf = CompositeFilter(
            enable_volume_filter=False,
            enable_trend_filter=False,
            enable_time_filter=False
        )
        
        passed, messages = cf.check_all(
            volume=100,  # Low volume
            current_time=time(9, 30)  # Bad timing
        )
        
        assert passed is True
        assert len(messages) == 0
    
    def test_reset_clears_all(self):
        """Reset should clear all filter histories."""
        cf = CompositeFilter(
            enable_volume_filter=True,
            enable_trend_filter=True
        )
        
        # Build history
        for _ in range(10):
            cf.check_all(
                volume=1000,
                price=100.0,
                ema_fast=100.0,
                ema_slow=99.0
            )
        
        cf.reset()
        
        # After reset, should behave like fresh start
        passed, messages = cf.check_all(volume=500)
        assert passed is True  # Insufficient history allows


class TestFilterEdgeCases:
    """Edge case tests for filters."""
    
    def test_volume_zero_division(self):
        """Volume filter should handle zero average."""
        vf = VolumeFilter()
        result = vf.update(0)
        assert result.is_sufficient is True
    
    def test_trend_single_price(self):
        """Trend filter should handle single price."""
        tf = TrendFilter()
        result = tf.analyze(100.0, 100.0, 99.0)
        assert result.trend_direction == "SIDEWAYS"
    
    def test_time_boundary_conditions(self):
        """Test exact boundary times."""
        tf = TimeFilter(optimal_start=time(9, 50))
        
        # Exactly at 9:50
        result = tf.analyze(time(9, 50))
        assert result.is_optimal is True
        
        # One minute before
        result = tf.analyze(time(9, 49))
        assert result.is_optimal is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
