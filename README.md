# N-Structure Algorithmic Trading Bot v5.3

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

## 🔥 Features (v5.3)

| Feature | Description |
|---------|-------------|
| **Confirmation Candle** | Wait 2 candles after pattern - avoid early entries |
| **Volume Filter** | Breakout volume must be 1.5x average |
| **Gap Filter** | Skip first signal on large gap days (>50pt) |
| **Sniper Mode** | 1 SL/day = Day Over (capital protection) |
| **Structure TSL** | Trail to swing lows, not just candle lows |
| **Dynamic Strike** | Select strike AFTER N-Structure confirmed |
| **Network Retry** | Auto-wait on connection errors (infinite retry) |
| **8pt SL** | Safer stop loss (was 5pt) |

---

## 📊 Position Sizing Profiles

| Mode | Lots | Qty | Risk/Trade | SL Points | Capital |
|------|------|-----|------------|-----------|---------|
| 🟢 **Conservative** | 4 | 260 | ₹2,080 | 8pt | ₹30K |
| 🟡 Moderate | 6 | 390 | ₹3,120 | 8pt | ₹50K |
| 🔴 Aggressive | 8 | 520 | ₹4,160 | 8pt | ₹75K |
| 🔥 Ultra | 12 | 780 | ₹6,240 | 8pt | ₹1L+ |

**Current: Conservative (4 lots, 8pt SL)**

---

## 🏗️ Project Structure

```
N/
├── config/settings.yaml        # All strategy parameters
├── src/                        # Source code (40 Python files)
│   ├── main.py                 # Main trading bot
│   ├── broker/auth.py          # Angel One API + retry
│   ├── core/                   # FSM, State Store, Risk
│   ├── data/                   # Market feed, Candles, Strikes
│   ├── execution/              # Order & SL management
│   ├── indicators/             # N-Structure, EMA, Filters
│   ├── risk/                   # Risk Manager v2.0
│   └── utils/                  # Logger, Telegram
├── scripts/backtest/           # Backtest scripts
├── tests/                      # 98 unit tests
├── data/                       # Cache, State DB
├── logs/                       # Daily logs
├── start.sh                    # Animated launcher
└── requirements.txt            # 38 packages
```

---

## 🚀 Quick Start

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env

# Run Paper Trading
./start.sh

# Run Live Trading
python src/main.py --polling
```

---

## ⚙️ Key Configuration (v5.3)

```yaml
exit:
  initial_sl_points: 8.0           # Safer SL

risk:
  position_mode: "conservative"    # 4 lots
  max_sl_per_day: 1                # SNIPER MODE
  sl_points: 8.0

timing:
  trading_start: "09:30"           # After first 15 mins
  no_new_trades_after: "14:30"
```

---

## 🧪 Run Tests

```bash
python -m pytest tests/ -v  # 98 passing
```

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| **v5.3.0** | Feb 23, 2026 | 8pt SL, 4 lots, 9:30 start, cleanup |
| v5.2.0 | Feb 2026 | Confirmation Candle, Risk Manager v2.0 |
| v5.1.0 | Feb 2026 | Volume Filter, Gap Filter |
| v5.0.0 | Jan 2026 | Pullback Entry, Dual Direction |

---

## ⚠️ Disclaimer

This software is for educational purposes only. Trading involves significant risk.

---

**Made with ❤️ for algorithmic trading**
