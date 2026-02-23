# N-Structure Algorithmic Trading Bot v5.2

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production_Ready-green.svg)]()
[![Tests: 98 Passing](https://img.shields.io/badge/Tests-98_Passing-success.svg)]()

A dual-chart (Index + Option) algorithmic trading system implementing the **N-Structure Ultimate Sniper** strategy for NIFTY options via Angel One SmartAPI.

> ⚠️ **Private Repository** - For personal use only. Not for redistribution.

---

## 🎯 Strategy Overview

```
INDEX Chart           OPTION Chart
    ▲                      ▲
   /│\  HH (Higher High)  /│\  Entry Point
  / │ \                  / │ \
 /  │  \  ← Pullback    /  │  \
HL2 HL1  ← Confirmation ────────────→ BUY!
```

**N-Structure Pattern Detection:**
1. **HH+HL Formation** - Higher High followed by Higher Lows
2. **Confirmation Candle** - Wait 2 candles after pattern (v5.2)
3. **Volume Breakout** - 1.5x average volume required
4. **Gap Filter** - Skip signals on 50+ point gap days

---

## 🔥 Features (v5.2)

| Feature | Description |
|---------|-------------|
| **Confirmation Candle** | Wait 2 candles after pattern - avoid early entries |
| **Volume Filter** | Breakout volume must be 1.5x average |
| **Gap Filter** | Skip first signal on large gap days (>50pt) |
| **Sniper Mode** | 1 SL/day = Day Over (capital protection) |
| **Structure TSL** | Trail to swing lows, not just candle lows |
| **Dynamic Strike** | Select strike AFTER N-Structure confirmed |

---

## 📊 Position Sizing Profiles

| Mode | Lots | Qty | Risk/Trade | Daily Loss | Capital |
|------|------|-----|------------|------------|---------|
| 🟢 Conservative | 4 | 260 | ₹1,300 | ₹1,300 | ₹30K |
| 🟡 **Moderate** | 6 | 390 | ₹1,950 | ₹1,950 | ₹50K |
| 🔴 Aggressive | 8 | 520 | ₹2,600 | ₹2,600 | ₹75K |
| 🔥 Ultra | 12 | 780 | ₹3,900 | ₹3,900 | ₹1L+ |

**Change in `config/settings.yaml`:**
```yaml
risk:
  position_mode: "moderate"  # conservative | moderate | aggressive | ultra
```

---

## 🏗️ Project Structure

```
N/
├── 📁 config/
│   └── settings.yaml           # All strategy parameters
│
├── 📁 src/                     # Source code
│   ├── main.py                 # Main trading bot
│   ├── backtest/               # Backtesting engine
│   ├── broker/                 # Angel One API
│   ├── core/                   # State machine & storage
│   ├── data/                   # Market data handling
│   ├── execution/              # Order & SL management
│   ├── indicators/             # EMA, N-Structure, Filters
│   ├── risk/                   # Risk Manager v2.0
│   ├── strategies/             # Strategy implementations
│   └── utils/                  # Logging, Telegram
│
├── 📁 scripts/
│   ├── backtest/               # All backtest scripts
│   ├── paper_trade.py          # Paper trading script
│   ├── start_live.sh           # Start live trading
│   └── start_paper.sh          # Start paper trading
│
├── 📁 tests/                   # 98 unit tests
│   ├── test_filters.py
│   ├── test_risk_manager.py
│   ├── test_sl_manager.py
│   └── test_state_machine.py
│
├── 📁 docs/
│   ├── LIVE_TRADING_CHECKLIST.md
│   ├── RISK_MANAGEMENT.md
│   ├── archive/                # Old session docs
│   └── research/               # Strategy research
│
├── 📁 data/
│   ├── cache/                  # Instrument cache
│   └── instruments/            # Daily instrument files
│
├── 📁 logs/                    # Daily trading logs
│
├── .env                        # API credentials (not in git)
├── .env.example                # Credential template
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

---

## 🚀 Quick Start

### 1. Setup Environment
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Credentials
```bash
# Copy example and fill in your Angel One API credentials
cp .env.example .env
nano .env
```

### 3. Configure Strategy
```bash
# Edit config/settings.yaml
# Set position_mode, trading times, etc.
nano config/settings.yaml
```

### 4. Run Paper Trading
```bash
python src/main.py --paper --polling
```

### 5. Run Live Trading (after testing)
```bash
python src/main.py --polling
```

---

## ⚙️ Key Configuration

```yaml
# config/settings.yaml

strategy:
  version: "5.2.0"

indicators:
  n_structure:
    confirmation_candles: 2        # Wait 2 candles
    require_direction_candle: true # Must be directional
    volume_confirmation_enabled: true
    gap_filter_enabled: true

risk:
  position_mode: "moderate"
  max_sl_per_day: 1               # SNIPER MODE
  sl_points: 5.0

timing:
  trading_start: "09:50"
  no_new_trades_after: "14:30"    # Extended for testing
  manage_till: "14:40"
```

---

## 🧪 Run Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_risk_manager.py -v
```

---

## 📈 Risk Management v2.0

**Protection Layers (checked in order):**
1. 🛑 **Emergency Halt** - Manual override
2. ⏰ **Time Window** - No trades before 9:50 or after 14:30
3. 🎯 **SNIPER MODE** - Max 1 SL hit per day
4. 💰 **Daily Loss Limit** - Absolute ₹ limit
5. 📉 **Capital Protection** - Max 5% loss
6. 📊 **Max Trades** - Safety cap (10/day)
7. ⏳ **Cooldown** - After each trade

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| v5.2.0 | Feb 2026 | Confirmation Candle, Risk Manager v2.0 |
| v5.1.0 | Feb 2026 | Volume Filter, Gap Filter |
| v5.0.0 | Jan 2026 | Pullback Entry, Dual Direction |
| v1.2.0 | Jan 2026 | HH Breakout Re-entry |
| v1.1.0 | Jan 2026 | Structure-based TSL |
| v1.0.0 | Jan 2026 | Initial release |

---

## ⚠️ Disclaimer

This software is for educational purposes only. Trading involves significant risk of loss. Past performance does not guarantee future results. Use at your own risk.

---

**Made with ❤️ for algorithmic trading**
