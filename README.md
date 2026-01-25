# N-Structure Algorithmic Trading Bot v1.2

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Production](https://img.shields.io/badge/Status-Production-green.svg)]()

A dual-chart (Index + Option) algorithmic trading system implementing the N-Structure Momentum Breakout strategy for NIFTY options via Angel One SmartAPI.

> ⚠️ **Private Repository** - For personal use only. Not for redistribution.

## 🚀 Strategy Overview

The N-Structure strategy identifies high-probability breakout entries using:

1. **EMA Support**: Price trading above EMA(9) and EMA(15)
2. **N-Structure Pattern**: Breakout → Pullback (HL1) → Higher Low (HL2)
3. **Divergence Filter**: Option ROC must confirm Index momentum
4. **Structure-Based TSL**: Trail to swing lows, not just candle lows
5. **Composite Filters**: Volume + Trend + Time filters (v1.2)

## 📊 Backtest Results (90 Days - v1.2)

| Metric | Value |
|--------|-------|
| Total Trades | 117 |
| Win Rate | 54.7% |
| Total P&L | **₹48,244** |
| Profit Factor | 1.39 |
| Max Drawdown | ₹6,800 |
| Avg Win | ₹1,480 |
| Avg Loss | ₹1,880 |
| Sharpe Ratio | 1.82 |

## ✨ Key Features (v1.2)

### Position Sizing
- **Fixed 4 Lots**: 65 qty × 4 = 260 qty per trade
- **Fixed SL**: 10 points (₹2,600 risk per trade)
- **Max Daily Loss**: 3 SL hits = ₹7,800 max

### Structure-Based TSL (v1.1)
- **Phase 1**: Initial SL at Entry - 10 points
- **Phase 2**: Breakeven at +8 points profit
- **Phase 3**: Structure TSL - Trail to HL[-2] minus 2.5pt buffer
- **Phase 4**: Tight Trail at +20 points - Use 1.5pt buffer
- **SL Breath Rule**: Allow 1 candle to breach if structure intact

### HH Breakout Re-entry (v1.2)
- After SL hit, watch for new Higher High breakout
- Re-enter on HH + 1.5pt buffer
- Max 2 re-entries per day
- Recovers ~42% more profit from losing setups

### Risk Management
- **ONLY Limiter**: Max 3 SL hits per day
- NO daily loss % limit
- NO consecutive loss limit
- NO max trades limit
- Unlimited profitable trades!

## 🏗️ Project Structure

```
N/
├── config/
│   └── settings.yaml          # Strategy configuration (v1.2)
├── src/
│   ├── main.py                # Main trading bot orchestrator
│   ├── backtest/
│   │   ├── backtester_v2.py   # FSM-based backtester with re-entry
│   │   └── historical_data.py # API data fetcher with pagination
│   ├── broker/
│   │   └── auth.py            # Angel One authentication
│   ├── core/
│   │   ├── state_machine.py   # Trading FSM (v1.2 with PENDING_REENTRY)
│   │   └── state_store.py     # SQLite persistence
│   ├── data/
│   │   ├── candle_builder.py  # 1-min OHLC aggregation
│   │   ├── instrument_master.py # Daily instrument file
│   │   ├── market_feed.py     # WebSocket data feed
│   │   └── synchronizer.py    # Index-Option sync
│   ├── execution/
│   │   ├── order_manager.py   # Order placement
│   │   └── sl_manager.py      # Structure-based TSL (v1.1)
│   ├── indicators/
│   │   ├── ema.py             # Incremental EMA
│   │   └── n_structure.py     # Pattern detection
│   ├── risk/
│   │   ├── risk_manager.py    # Max SL only limiter (v1.2)
│   │   └── position_reconciler.py
│   └── utils/
│       └── logger.py          # Structured logging
├── tests/
│   ├── test_sl_manager.py     # SL manager tests
│   ├── test_risk_manager.py   # Risk manager tests
│   └── test_state_machine.py  # FSM tests
├── docs/
│   └── RISK_MANAGEMENT.md     # Risk management documentation
├── run_backtest_v2.py         # Backtest runner
└── requirements.txt
```

## 🔧 Quick Start

```bash
# Clone repository (private)
git clone git@github.com:yourusername/N.git
cd N

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your Angel One credentials
```

## ⚙️ Configuration

Edit `config/settings.yaml`:

```yaml
# Key settings (v1.2 optimized)
risk:
  lot_size: 65              # NIFTY lot
  num_lots: 4               # Always 4 lots
  max_sl_per_day: 3         # ONLY limiter!

exit:
  initial_sl_points: 10.0   # 10pt SL
  trailing:
    breakeven_trigger_points: 8.0   # BE at +8
    structure_tsl:
      tsl_buffer: 2.5       # Buffer below HL
    tight_trail:
      trigger_points: 20.0  # Tight at +20

reentry:
  enabled: true
  max_reentries_per_day: 2
```

## 🚀 Usage

### Paper Trading (Test First!)
```bash
./scripts/start_paper.sh
# or
python src/main.py --paper
```

### Live Trading
```bash
./scripts/start_live.sh
# or
python src/main.py
```

### Run Backtest
```bash
python run_backtest_v2.py --days 90
```

### Run Tests
```bash
pytest tests/ -v
```

## 📁 Project Structure

```
N/
├── config/
│   └── settings.yaml          # Strategy configuration
├── scripts/
│   ├── start_paper.sh         # Paper mode launcher
│   └── start_live.sh          # Live mode launcher
├── src/
│   ├── main.py                # Main trading bot
│   ├── backtest/              # Backtesting engine
│   ├── broker/                # Angel One integration
│   ├── core/                  # FSM & state management
│   ├── data/                  # Market data handling
│   ├── execution/             # Order & SL management
│   ├── indicators/            # EMA, N-Structure
│   ├── risk/                  # Risk management
│   └── utils/                 # Logging utilities
├── tests/                     # Unit tests
├── docs/                      # Documentation
└── data/                      # Logs & cache
```

## 📋 Risk Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Lot Size | 65 qty | NIFTY lot size |
| Num Lots | 4 | Fixed position |
| Total Qty | 260 | 65 × 4 |
| SL Points | 10 | Fixed stop loss |
| Risk/Trade | ₹2,600 | 10 × 260 |
| Max SL/Day | 3 | Only limiter |
| Max Loss/Day | ₹7,800 | 3 × ₹2,600 |
| Max Re-entries | 2 | Per day |

## 🔄 TSL Phases

| Phase | Trigger | SL Level | Buffer |
|-------|---------|----------|--------|
| Initial | Entry | Entry - 10pt | - |
| Breakeven | +8pt profit | Entry price | - |
| Structure | 2+ HLs | HL[-2] | 2.5pt |
| Tight | +20pt profit | HL[-2] | 1.5pt |

## 📝 Environment Variables

```env
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_CODE=your_client_code
ANGEL_PASSWORD=your_password
ANGEL_TOTP_SECRET=your_totp_secret
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_sl_manager.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

## 📚 Documentation

- [Live Trading Checklist](docs/LIVE_TRADING_CHECKLIST.md) - Pre-deployment checklist
- [Risk Management](docs/RISK_MANAGEMENT.md) - Risk management documentation

## ⚠️ Disclaimer

This is an algorithmic trading system for **personal use only**. 

- Use at your own risk
- Past performance does not guarantee future results
- Always test thoroughly in paper mode before live trading
- Never risk more than you can afford to lose

## 📄 License

Private - All rights reserved.

---

**Version**: 1.2.0  
**Last Updated**: January 2026  
**Status**: Production Ready ✅
