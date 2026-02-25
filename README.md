# Adaptive Hybrid Trading System v3.0

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production_Ready-green.svg)]()
[![Control Center](https://img.shields.io/badge/Control_Center-v3.0-purple.svg)]()

NIFTY options trading bot using **Adaptive Hybrid Strategy** - a regime-based approach combining VWAP, Volume Profile, and Market Structure analysis via Angel One SmartAPI.

> ⚠️ **Private Repository** - For personal use only.

---

## 🎮 Control Center v3.0

```bash
./start.sh
```

**Features:**
- 🎨 **Ultra Professional UI** - Animated, colorful terminal interface
- 📊 **Live Status Dashboard** - Real-time capital, P&L, trades
- 🚀 **One-Click Trading** - Start/Stop Live or Paper trading
- 🔬 **Built-in Backtest** - Run strategy backtests
- 📱 **Telegram Menu** - Control bot via Telegram commands
- 📜 **Live Logs** - Real-time log viewing

---

## 🎯 Strategy Overview

```
Market Regime Detection (1m + 5m Confirmation)
           ↓
┌─────────────────────────────────────────────────┐
│  TRENDING_UP    → CE Entry (VWAP + Volume)      │
│  TRENDING_DOWN  → PE Entry (VWAP + Volume)      │
│  RANGING        → Support/Resistance plays      │
│  VOLATILE       → Wide stop, quick profit       │
│  UNKNOWN        → No trade                      │
└─────────────────────────────────────────────────┘
```

**Core Components:**
1. **VWAP** - Dynamic support/resistance with standard deviation bands
2. **Volume Profile** - POC, VAH, VAL for key levels
3. **Market Regime** - ADX, ATR, Bollinger for regime detection
4. **5-Min Confirmation** - Multi-timeframe regime agreement
5. **Signal Cooldown** - 5 minutes between signals

---

## 🔥 Features

| Feature | Description |
|---------|-------------|
| **Regime Detection** | 5 market states: Trending Up/Down, Ranging, Volatile, Unknown |
| **Multi-Timeframe** | 1-min + 5-min regime confirmation required |
| **Signal Cooldown** | 5 min minimum between signals (reduce noise) |
| **VWAP Bands** | Entry near VWAP with 2σ bands for S/R |
| **Volume Profile** | POC, VAH, VAL levels for precision entries |
| **Confidence Score** | Signal strength (0.5-0.9), min 70% to trade |
| **Sniper Mode** | 1 SL per day = Day Over |
| **Dynamic Strike** | Select ATM strike after signal confirmation |

---

## 📊 Backtest Results (30 Days)

| Metric | Value |
|--------|-------|
| **Win Rate** | 67.9% |
| **Profit Factor** | 4.47 |
| **Total P&L** | ₹64,179 |
| **Total Trades** | 56 |
| **Max Drawdown** | ₹4,166 |
| **Avg Win** | ₹2,101 |
| **Avg Loss** | ₹1,111 |

---

## 🏗️ Project Structure

```
N/
├── config/settings.yaml        # Strategy parameters
├── src/
│   ├── main.py                 # Trading bot entry
│   ├── broker/auth.py          # Angel One API
│   ├── core/risk_manager.py    # Capital protection
│   ├── data/                   # Market feed, Candles, Strikes
│   ├── execution/              # Order & SL management
│   ├── indicators/             # VWAP, Volume Profile, EMA, ATR
│   ├── risk/                   # Position reconciler
│   ├── strategies/             # Adaptive Hybrid Strategy
│   └── utils/                  # Logger, Telegram
├── scripts/backtest/           # Backtest scripts
├── tests/                      # Unit tests
├── data/                       # Cache
└── logs/                       # Daily logs
```

---

## 🚀 Quick Start

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env

# Run Paper Trading
python src/main.py --paper

# Run Backtest
PYTHONPATH=. python scripts/backtest/run_adaptive_backtest.py --days 30

# Run Live
python src/main.py
```

---

## ⚙️ Key Configuration

```yaml
strategy:
  type: "adaptive_hybrid"
  signal_cooldown_minutes: 5
  min_confidence: 0.7

exit:
  initial_sl_points: 8.0
  trailing_activation_points: 15.0

risk:
  position_mode: "conservative"    # 4 lots
  max_sl_per_day: 1                # SNIPER MODE

timing:
  trading_start: "09:20"
  no_new_trades_after: "14:30"
```

---

## 🧪 Run Tests

```bash
python test_strategy.py  # 5 passing
```

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| **v1.0.0** | Jan 2026 | Adaptive Hybrid Strategy with VWAP, Volume Profile, Regime Detection |

---

## ⚠️ Disclaimer

This software is for educational purposes only. Trading involves significant risk.

---

**Made with ❤️ for algorithmic trading**
