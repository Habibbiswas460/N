# VWAP + PDH/PDL Fusion Trading System

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Angel One](https://img.shields.io/badge/Angel_One-SmartAPI-FF6B35?style=for-the-badge)](https://smartapi.angelbroking.com/)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)]()

**Automated NIFTY Options Trading Bot with Multi-Breakout Strategy**

[Features](#-features) • [Strategy](#-strategy-logic) • [Setup](#-installation) • [Configuration](#️-configuration) • [Architecture](#-architecture)

</div>

---

## 📋 Overview

A production-ready algorithmic trading system for NIFTY index options that combines **Previous Day High/Low (PDH/PDL) breakout** with **VWAP confirmation** for high-probability trade setups. Built with Python and integrated with Angel One SmartAPI for real-time market data and order execution.

### Why This Strategy?

| Traditional Breakout | VWAP + PDH/PDL Fusion |
|---------------------|----------------------|
| Many false breakouts | VWAP filters noise |
| Single entry per level | Multi-breakout allows re-entry |
| Fixed stop loss | ATR-based dynamic SL |
| No cooldown | 15-candle cooldown reduces overtrading |

---

## ✨ Features

### Trading Engine
- **Real-time Market Feed** - 1-minute candles via REST API polling
- **Dynamic Strike Selection** - ATM option with ₹85-150 premium range
- **Order Management** - Market orders with bracket order support
- **Stop Loss Manager** - Trailing SL with partial profit booking

### Risk Management
- **Daily Loss Limit** - Max 2% capital loss per day
- **Position Sizing** - Fixed 4 lots (260 qty) conservative mode
- **Trade Cooldown** - 15-minute wait after each trade
- **Entry Window** - 09:30 to 15:00 only

### Monitoring & Alerts
- **Telegram Integration** - Real-time trade alerts and daily reports
- **SQLite Database** - Persistent trade history and analytics
- **Structured Logging** - Daily log files with rotation

### Developer Tools
- **Control Center CLI** - Single-key shortcuts for all operations
- **Historical Backtesting** - Test strategy on past data via API
- **Paper Trading Mode** - Simulate trades without real money

---

## 🎯 Strategy Logic

### Entry Conditions

```
┌─────────────────────────────────────────────────────────────┐
│                    CE (Call) Entry                          │
├─────────────────────────────────────────────────────────────┤
│  1. Price breaks above PDH (Previous Day High)              │
│  2. Price > VWAP (bullish bias confirmation)                │
│  3. Within entry window (09:30 - 15:00)                     │
│  4. No active position                                      │
│  5. Cooldown elapsed (15 candles since last trade)          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    PE (Put) Entry                           │
├─────────────────────────────────────────────────────────────┤
│  1. Price breaks below PDL (Previous Day Low)               │
│  2. Price < VWAP (bearish bias confirmation)                │
│  3. Within entry window (09:30 - 15:00)                     │
│  4. No active position                                      │
│  5. Cooldown elapsed (15 candles since last trade)          │
└─────────────────────────────────────────────────────────────┘
```

### Exit Conditions

| Exit Type | Condition |
|-----------|-----------|
| **Stop Loss** | ATR × 0.5 (~10-12 points) below entry |
| **Target 1** | 1.5× Risk (exit 50% position) |
| **Target 2** | 2.5× Risk (exit remaining) |
| **Time Exit** | 15:15 forced square-off |

### Multi-Breakout Mode

Unlike traditional breakout systems that allow only one trade per level:

```
Traditional:  PDH break → Trade → Exit → DONE for the day
Multi-Break:  PDH break → Trade → Exit → Wait 15 min → PDH break again → Trade
```

This captures multiple momentum waves in trending markets.

---

## 📊 Backtest Results

### Multi-Breakout vs Static Mode (30 Days)

| Metric | Multi-Breakout | Static |
|--------|---------------|--------|
| Total Trades | 134 | 14 |
| Win Rate | 44.0% | 35.7% |
| Profit Factor | 1.21 | 0.68 |
| Net P&L | **+₹6,199** | -₹947 |
| Max Drawdown | ₹8,450 | ₹3,200 |
| Avg Trade | ₹46 | -₹68 |

### Key Insights
- More trades = more opportunities to profit from volatility
- Cooldown prevents revenge trading after losses
- VWAP filter reduces false breakout entries by ~40%

---

## 🚀 Installation

### Prerequisites
- Python 3.12+
- Angel One Trading Account with SmartAPI access
- TOTP Secret for authentication

### Setup

```bash
# Clone repository
git clone https://github.com/Habibbiswas460/N.git
cd N

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

### Environment Variables

```env
# .env file
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PASSWORD=your_pin
ANGEL_TOTP_SECRET=your_totp_secret

# Optional: Telegram alerts
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## 🎮 Usage

### Control Center

```bash
./start.sh
```

| Key | Action | Description |
|-----|--------|-------------|
| `l` | Live Trading | Real money, real orders |
| `p` | Paper Trading | Simulated trades |
| `x` | Stop | Stop running bot |
| `t` | Today's Report | P&L summary |
| `b` | Backtest | Run historical test |
| `c` | Config | Edit settings.yaml |
| `g` | Logs | View today's logs |
| `q` | Quit | Exit control center |

### Direct Commands

```bash
# Paper trading (recommended for testing)
python src/main.py --paper --polling

# Live trading
python src/main.py --polling

# Backtest last 30 days
PYTHONPATH=. python scripts/backtest/run_adaptive_api_backtest.py --days 30

# Run tests
pytest tests/ -v
```

---

## ⚙️ Configuration

### config/settings.yaml
entry:
  multi_breakout: true      # Allow re-entry
  cooldown_candles: 15      # Wait after exit
  min_rr_ratio: 1.5         # Risk-Reward

trading_hours:
  entry_start: "09:30"
  entry_end: "15:00"
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                 │
│                    (Entry Point & Orchestrator)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│    broker/      │  │     data/       │  │   strategy/     │
│  Angel One API  │  │  Market Feed    │  │ Adaptive Hybrid │
│  Authentication │  │  Candle Builder │  │  Signal Logic   │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   execution/    │  │   indicators/   │  │     risk/       │
│ Order Manager   │  │  VWAP, ATR, EMA │  │ Risk Manager    │
│   SL Manager    │  │ Market Structure│  │ Position Sizing │
└─────────────────┘  └─────────────────┘  └─────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                    ┌─────────────────┐
                    │     utils/      │
                    │ Logger, Telegram│
                    │  Trade Journal  │
                    └─────────────────┘
```

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `broker/` | Angel One SmartAPI authentication & session management |
| `data/` | Market feed polling, candle aggregation, strike selection |
| `indicators/` | VWAP, ATR, EMA, PDH/PDL market structure |
| `strategy/` | Trade signal generation, entry/exit logic |
| `execution/` | Order placement, stop loss management |
| `risk/` | Position sizing, daily loss limits, cooldowns |
| `utils/` | Logging, Telegram alerts, trade journaling |
| `core/` | SQLite database for state persistence |

---

## 📁 Project Structure

```
N/
├── config/
│   └── settings.yaml          # All strategy parameters
├── src/
│   ├── main.py                # Application entry point
│   ├── broker/
│   │   └── auth.py            # Angel One authentication
│   ├── core/
│   │   └── database.py        # SQLite state persistence
│   ├── data/
│   │   ├── market_feed.py         # WebSocket feed (future)
│   │   ├── market_feed_polling.py # REST API polling
│   │   ├── candle_builder.py      # OHLC aggregation
│   │   ├── instrument_master.py   # Option chain data
│   │   └── dynamic_strike_selector.py
│   ├── execution/
│   │   ├── order_manager.py   # Order placement
│   │   └── sl_manager.py      # Stop loss tracking
│   ├── indicators/
│   │   ├── vwap.py            # Volume Weighted Avg Price
│   │   ├── atr.py             # Average True Range
│   │   ├── ema.py             # Exponential Moving Average
│   │   ├── market_structure.py # PDH/PDL levels
│   │   └── volume_profile.py  # POC, VAH, VAL
│   ├── strategy/
│   │   ├── adaptive_hybrid.py # Main strategy logic
│   │   └── regime_detector.py # Market regime detection
│   ├── risk/
│   │   ├── risk_manager.py    # Position & capital management
│   │   └── position_reconciler.py
│   └── utils/
│       ├── logger.py          # Loguru configuration
│       ├── telegram.py        # Alert notifications
│       └── trade_journal.py   # CSV trade logging
├── scripts/
│   └── backtest/
│       └── run_adaptive_api_backtest.py
├── tests/
│   ├── test_indicators.py
│   ├── test_risk_manager.py
│   ├── test_sl_manager.py
│   └── test_strategy.py
├── data/
│   ├── cache/                 # Temporary data
│   ├── instruments/           # Option chain cache
│   └── journal/               # Trade logs
├── logs/
│   └── YYYY-MM-DD/
│       └── trading.log        # Daily log file
├── .env                       # Credentials (git-ignored)
├── .env.example               # Template for .env
├── requirements.txt           # Python dependencies
├── start.sh                   # Control center CLI
├── QUICKSTART.md              # Setup guide (Bengali)
└── README.md                  # This file
```

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_strategy.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Test Coverage

| Module | Tests |
|--------|-------|
| Indicators | VWAP calculation, ATR, EMA |
| Risk Manager | Position sizing, loss limits |
| SL Manager | Trailing stop, partial exits |
| Strategy | Signal generation, cooldowns |

---

## 📈 Performance Tips

1. **Paper Trade First** - Always test with `--paper` flag before live
2. **Check Logs** - Review `logs/YYYY-MM-DD/trading.log` for issues
3. **Avoid Volatile Sessions** - Disable trading on high-impact news days
4. **Monitor Slippage** - Real orders may have 2-5 point slippage

---

## ⚠️ Disclaimer

This software is for **educational purposes only**. Trading in financial markets involves substantial risk of loss. Past performance (including backtest results) is not indicative of future results. 

**Use at your own risk. The author is not responsible for any financial losses.**

---

## 📝 License

Private repository. All rights reserved.

---

<div align="center">

**Built with Python 🐍 | Powered by Angel One SmartAPI**

[⬆ Back to Top](#vwap--pdhpdl-fusion-trading-system)

</div>
