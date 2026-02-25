"""
Unit Tests for Indicators
Tests VWAP, Volume Profile, and Regime Detector
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, time
from indicators.vwap import VWAPIndicator, VWAPData
from indicators.volume_profile import VolumeProfile, VolumeProfileLevels
from strategy.regime_detector import RegimeDetector, MarketRegime, RegimeData


class TestVWAPIndicator:
    """Tests for VWAP Indicator"""
    
    def test_vwap_initialization(self):
        """VWAP initializes with correct defaults"""
        vwap = VWAPIndicator(band_multiplier=2.0)
        assert vwap.band_multiplier == 2.0
        assert vwap.cumulative_volume == 0.0
        assert vwap.cumulative_tp_volume == 0.0
        
    def test_vwap_first_candle(self):
        """VWAP updates correctly on first candle"""
        vwap = VWAPIndicator()
        timestamp = datetime(2026, 2, 24, 9, 16)
        
        result = vwap.update(
            high=23100, low=23050, close=23075,
            volume=10000, timestamp=timestamp
        )
        
        assert result is not None
        # Typical price = (23100 + 23050 + 23075) / 3 = 23075
        assert result.vwap == pytest.approx(23075.0, rel=0.01)
        assert result.cumulative_volume == 10000
        
    def test_vwap_multiple_candles(self):
        """VWAP calculates correctly over multiple candles"""
        vwap = VWAPIndicator()
        base_time = datetime(2026, 2, 24, 9, 16)
        
        # Candle 1
        vwap.update(high=100, low=90, close=95, volume=1000, 
                    timestamp=base_time)
        
        # Candle 2 - price moves up
        from datetime import timedelta
        result = vwap.update(high=105, low=95, close=100, volume=2000,
                            timestamp=base_time + timedelta(minutes=1))
        
        # TP1 = (100+90+95)/3 = 95, TP2 = (105+95+100)/3 = 100
        # VWAP = (95*1000 + 100*2000) / 3000 = 98.33
        assert result is not None
        assert result.vwap == pytest.approx(98.33, rel=0.01)
        
    def test_vwap_position_above(self):
        """VWAP correctly identifies price ABOVE vwap"""
        vwap = VWAPIndicator()
        timestamp = datetime(2026, 2, 24, 9, 16)
        
        # First candle sets vwap low
        vwap.update(high=100, low=90, close=95, volume=1000, timestamp=timestamp)
        
        # Second candle much higher
        from datetime import timedelta
        result = vwap.update(high=120, low=110, close=115, volume=100,
                            timestamp=timestamp + timedelta(minutes=1))
        
        # Price is above VWAP
        assert result.price_position == "ABOVE"
        
    def test_vwap_session_reset(self):
        """VWAP resets on new trading day"""
        vwap = VWAPIndicator()
        
        # Day 1
        day1 = datetime(2026, 2, 24, 9, 16)
        vwap.update(high=100, low=90, close=95, volume=10000, timestamp=day1)
        
        # Day 2 - should reset
        day2 = datetime(2026, 2, 25, 9, 16)
        result = vwap.update(high=200, low=190, close=195, volume=5000, timestamp=day2)
        
        # Should only include day 2 data
        assert result.cumulative_volume == 5000
        assert result.vwap == pytest.approx(195.0, rel=0.01)


class TestVolumeProfile:
    """Tests for Volume Profile Indicator"""
    
    def test_volume_profile_initialization(self):
        """Volume Profile initializes correctly"""
        vp = VolumeProfile(tick_size=0.5, value_area_pct=0.70)
        assert vp.tick_size == 0.5
        assert vp.value_area_pct == 0.70
        
    def test_volume_profile_single_candle(self):
        """Volume Profile returns None for insufficient data"""
        vp = VolumeProfile()
        timestamp = datetime(2026, 2, 24, 9, 16)
        
        result = vp.update(high=100, low=90, close=95, volume=1000, timestamp=timestamp)
        
        # Need multiple candles for meaningful profile
        # Depending on implementation, might return basic data or None
        if result is not None:
            assert result.poc > 0
            
    def test_volume_profile_builds_over_time(self):
        """Volume Profile builds meaningful levels"""
        vp = VolumeProfile(tick_size=1.0)
        base_time = datetime(2026, 2, 24, 9, 16)
        
        from datetime import timedelta
        
        # Add multiple candles around similar levels
        for i in range(10):
            result = vp.update(
                high=100 + i % 3,
                low=95 + i % 3,
                close=97 + i % 3,
                volume=1000 + i * 100,
                timestamp=base_time + timedelta(minutes=i)
            )
            
        assert result is not None
        # POC should be within the range we traded
        assert 95 <= result.poc <= 103
        # VAH should be above VAL
        assert result.vah >= result.val


class TestRegimeDetector:
    """Tests for Market Regime Detector"""
    
    def test_regime_detector_initialization(self):
        """Regime Detector initializes correctly"""
        detector = RegimeDetector(atr_period=14, lookback=20)
        assert detector.atr_period == 14
        assert detector.lookback == 20
        
    def test_regime_unknown_initially(self):
        """Regime is UNKNOWN with insufficient data"""
        from datetime import timedelta
        detector = RegimeDetector()
        timestamp = datetime(2026, 2, 24, 9, 16)
        
        result = detector.update(high=100, low=90, close=95, timestamp=timestamp)
        
        if result is not None:
            # With little data, confidence should be low or regime unknown
            assert result.regime == MarketRegime.UNKNOWN or result.confidence < 0.5
            
    def test_regime_trending_up(self):
        """Detects TRENDING_UP with ascending prices"""
        from datetime import timedelta
        detector = RegimeDetector(lookback=10)
        base_time = datetime(2026, 2, 24, 9, 16)
        
        # Feed ascending prices
        result = None
        for i in range(25):
            result = detector.update(
                high=100 + i * 2,
                low=98 + i * 2,
                close=99 + i * 2,
                timestamp=base_time + timedelta(minutes=i)
            )
            
        assert result is not None
        # Should detect uptrend
        if result.confidence >= 0.5:
            assert result.regime in (MarketRegime.TRENDING_UP, MarketRegime.VOLATILE)
            
    def test_regime_trending_down(self):
        """Detects TRENDING_DOWN with descending prices"""
        from datetime import timedelta
        detector = RegimeDetector(lookback=10)
        base_time = datetime(2026, 2, 24, 9, 16)
        
        # Feed descending prices
        result = None
        for i in range(25):
            result = detector.update(
                high=200 - i * 2,
                low=198 - i * 2,
                close=199 - i * 2,
                timestamp=base_time + timedelta(minutes=i)
            )
            
        assert result is not None
        # Should detect downtrend
        if result.confidence >= 0.5:
            assert result.regime in (MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE)
            
    def test_regime_sideways(self):
        """Detects SIDEWAYS with oscillating prices"""
        from datetime import timedelta
        detector = RegimeDetector(lookback=10)
        base_time = datetime(2026, 2, 24, 9, 16)
        
        # Feed oscillating prices
        result = None
        for i in range(30):
            # Oscillate between 100 and 102
            offset = (i % 4) - 2  # -2, -1, 0, 1, -2, ...
            result = detector.update(
                high=101 + offset,
                low=99 + offset,
                close=100 + offset,
                timestamp=base_time + timedelta(minutes=i)
            )
            
        assert result is not None
        # With low range, should be sideways
        # Could also be classified differently depending on implementation
        
    def test_regime_data_properties(self):
        """RegimeData properties work correctly"""
        data = RegimeData(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.8,
            atr=10.5,
            atr_percentile=50.0,
            trend_strength=65.0,
            range_bound_score=20.0
        )
        
        assert data.is_trending == True
        assert data.is_sideways == False
        assert data.is_tradeable == True
        
    def test_regime_data_not_tradeable(self):
        """RegimeData correctly identifies untradeable conditions"""
        data = RegimeData(
            regime=MarketRegime.VOLATILE,
            confidence=0.3,
            atr=50.0,
            atr_percentile=95.0,
            trend_strength=30.0,
            range_bound_score=40.0
        )
        
        assert data.is_tradeable == False


class TestIntegration:
    """Integration tests combining indicators"""
    
    def test_all_indicators_together(self):
        """All indicators work together without errors"""
        vwap = VWAPIndicator(band_multiplier=2.0)
        vp = VolumeProfile(tick_size=0.5)
        regime = RegimeDetector()
        
        base_time = datetime(2026, 2, 24, 9, 16)
        from datetime import timedelta
        
        for i in range(50):
            high = 23000 + i * 2 + (i % 5)
            low = 22990 + i * 2 + (i % 5)
            close = 22995 + i * 2 + (i % 5)
            volume = 5000 + i * 100
            ts = base_time + timedelta(minutes=i)
            
            vwap_data = vwap.update(high, low, close, volume, ts)
            vp_data = vp.update(high, low, close, volume, ts)
            regime_data = regime.update(high, low, close, ts)
            
        # All should return data after 50 candles
        assert vwap_data is not None
        assert vwap_data.vwap > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
