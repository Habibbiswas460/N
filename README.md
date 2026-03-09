# VWAP + PDH/PDL Fusion Trading Bot

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Angel One](https://img.shields.io/badge/Broker-Angel_One-orange.svg)]()

NIFTY options trading bot using **VWAP + PDH/PDL Fusion** strategy with multi-breakout mode via Angel One SmartAPI.

---

## 🎮 Control Center

```bash
./start.sh
```

| Key | Action |
|-----|--------|
| `l` | Live Trading |
| `p` | Paper Trading |
| `x` | Stop Bot |
| `t` | Today's Report |
| `b` | Run Backtest |
| `c` | Config |
| `g` | Logs |

---

## 🎯 Strategy

```
PDH/PDL Breakout + VWAP Confirmation
           ↓
┌─────────────────────────────────────┐
│  Price > PDH + VWAP bias UP → CE    │
│  Price < PDL + VWAP bias DOWN → PE  │
│  Multi-breakout: re-entry allowed   │
│  15 candle cooldown after exit      │
└─────────────────────────────────────┘
```

**Key Features:**
- PDH/PDL breakout with VWAP confirmation
- Multi-breakout mode (re-entry on same level after cooldown)
- 15-candle cooldown between trades
- Entry window: 09:30 - 15:00
- 1.5:1 Risk-Reward minimum

---

## 📊 Backtest (Multi-breakout vs Static)

| Mode | Trades | Win Rate | P&L |
|------|--------|----------|-----|
| **Multi-breakout** | 134 | 44% | +₹6,199 |
| Static | 14 | 36% | -₹947 |

---

## 🏗️ Project Structure

```
N/
├── config/settings.yaml       # Strategy config
├── src/
│   ├── main.py               # Entry point
│   ├── broker/               # Angel One API
│   ├── data/                 # Market feed
│   ├── execution/            # Order management
│   ├── indicators/           # VWAP, ATR, EMA
│   ├── strategy/             # Adaptive Hybrid
│   ├── risk/                 # Risk management
│   └── utils/                # Logger, Telegram
├── scripts/backtest/         # Backtesting
├── tests/                    # Unit tests
├── logs/YYYY-MM-DD/          # Daily logs
└── start.sh                  # Control center
```

---

## 🚀 Quick Start

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add Angel One API credentials

# Run
./start.sh
```

See [QUICKSTART.md](QUICKSTART.md) for detailed setup guide.

---

## ⚙️ Key Configuration

```yaml
# config/settings.yaml
entry:
  multi_breakout: true      # Allow re-entry
  cooldown_candles: 15      # Wait after exit
  min_rr_ratio: 1.5         # Risk-Reward

trading_hours:
  entry_start: "09:30"
  entry_end: "15:00"
```

---

## 🧪 Tests

```bash
pytest tests/
```

---

**Angel One SmartAPI | NIFTY Options | Python 3.12+**
