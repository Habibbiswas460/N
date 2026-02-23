# N-Structure Trading Bot - Complete Project Structure

> **Project**: N-Structure Algorithmic Trading Bot v5.2  
> **Purpose**: Dual-chart (Index + Option) algorithmic trading system for NIFTY options via Angel One SmartAPI  
> **Generated**: February 12, 2026

---

## 📁 Root Directory Structure

```
N/
├── 📄 README.md                      # Project documentation & quick start guide
├── 📄 requirements.txt               # Python dependencies
├── 📄 .env                           # API credentials (not in git)
├── 📄 .env.example                   # Credential template
├── 📄 .gitignore                     # Git ignore rules
│
├── 📁 config/                        # Configuration files
├── 📁 src/                           # Main source code
├── 📁 scripts/                       # Utility scripts
├── 📁 tests/                         # Unit tests
├── 📁 data/                          # Data storage
├── 📁 logs/                          # Trading logs (daily)
└── 📁 venv/                          # Python virtual environment
```

---

## 📁 config/ - Configuration

```
config/
└── 📄 settings.yaml                  # All strategy & risk parameters (311 lines)
```

### settings.yaml Details

| Section | Description |
|---------|-------------|
| `strategy` | Name, version (v5.2.0), timeframe |
| `index` | NIFTY symbol, token, exchange |
| `option` | Underlying, expiry preference, trade direction (CE/PE/BOTH) |
| `strike_selection` | Premium range (₹85-150), scoring weights |
| `indicators` | EMA periods, N-Structure params, divergence settings |
| `entry` | Buffer points, order type, pullback entry settings |
| `exit` | Initial SL, trailing (Sniper Mode v2.0), structure TSL |
| `risk` | Position profiles, max SL/day, cooldown settings |
| `reentry` | HH breakout re-entry logic |
| `timing` | Trading hours, session management |
| `telegram` | Notification settings |
| `filters` | Volume, trend, time filters |

---

## 📁 src/ - Main Source Code

```
src/
├── 📄 __init__.py                    # Package marker
├── 📄 main.py                        # Main trading bot (1726 lines)
│
├── 📁 backtest/                      # Backtesting engine
├── 📁 broker/                        # Angel One API integration
├── 📁 core/                          # State machine & storage
├── 📁 data/                          # Market data handling
├── 📁 execution/                     # Order & SL management
├── 📁 indicators/                    # Technical indicators
├── 📁 risk/                          # Risk management
├── 📁 strategies/                    # Strategy implementations
└── 📁 utils/                         # Utilities
```

---

### 📁 src/main.py - Main Entry Point

**Lines**: 1726  
**Purpose**: Main trading bot orchestrator

**Key Features**:
- 8:30 AM: Download instrument master, initialize auth
- 9:15 AM: Connect WebSocket, select ATM strike
- 9:16+ AM: Run trading loop with FSM
- 3:30 PM: Graceful shutdown

**Class**: `TradingBot`
- Coordinates all modules
- Handles shutdown gracefully
- Manages trading loop

---

### 📁 src/backtest/ - Backtesting Engine

```
backtest/
├── 📄 __init__.py                    # Package marker
├── 📄 backtester.py                  # Main backtester (2203 lines)
└── 📄 historical_data.py             # Data fetcher (207 lines)
```

| File | Purpose |
|------|---------|
| `backtester.py` | FSM-based strategy backtester V2 with complete N-Structure implementation |
| `historical_data.py` | Angel One SmartAPI historical candle data fetcher |

---

### 📁 src/broker/ - Angel One API

```
broker/
├── 📄 __init__.py                    # Package marker
└── 📄 auth.py                        # Authentication (413 lines)
```

| File | Purpose |
|------|---------|
| `auth.py` | TOTP-based login, token management, session lifecycle via SmartAPI |

**Classes**:
- `AuthTokens`: JWT, refresh, feed token container
- `AngelOneAuth`: Login, logout, token refresh

---

### 📁 src/core/ - Core Components

```
core/
├── 📄 __init__.py                    # Package marker
├── 📄 state_machine.py               # FSM implementation (824 lines)
├── 📄 state_store.py                 # SQLite persistence (467 lines)
└── 📄 risk_manager.py                # Risk management v1.3 (476 lines)
```

| File | Purpose |
|------|---------|
| `state_machine.py` | Finite State Machine for trading with re-entry support |
| `state_store.py` | SQLite-based state persistence for crash recovery |
| `risk_manager.py` | Partial profit booking, drawdown protection, position sizing |

**FSM States** (`state_machine.py`):
- `IDLE`: Waiting for market conditions
- `WATCHING_BREAKOUT`: Monitoring resistance breakout
- `TRACKING_PULLBACK`: Tracking pullback to EMA
- `VALIDATING_HL`: Identifying Higher Low pattern
- `CHECKING_DIVERGENCE`: Verifying Index vs Option divergence
- `ARMED`: Ready for entry trigger
- `IN_POSITION`: Managing active trade
- `PENDING_REENTRY`: Waiting for HH breakout re-entry
- `COOLDOWN`: Waiting before next setup
- `PAUSED`: Manual pause or circuit breaker
- `ERROR`: Error state

---

### 📁 src/data/ - Market Data

```
data/
├── 📄 __init__.py                    # Package marker
├── 📄 candle_builder.py              # Tick to OHLC aggregation (386 lines)
├── 📄 dynamic_strike_selector.py     # N-Structure based strike selection (415 lines)
├── 📄 instrument_master.py           # Instrument lookup (473 lines)
├── 📄 market_feed.py                 # WebSocket data streaming (499 lines)
├── 📄 market_feed_polling.py         # REST API fallback (399 lines)
├── 📄 strike_selector.py             # ATM strike selection (324 lines)
└── 📄 synchronizer.py                # Index-Option candle sync (342 lines)
```

| File | Purpose |
|------|---------|
| `candle_builder.py` | Aggregates real-time ticks into 1-minute OHLC candles |
| `dynamic_strike_selector.py` | Selects strike ONLY when N-Structure detected on INDEX |
| `instrument_master.py` | Downloads/parses Angel One's daily instrument master |
| `market_feed.py` | WebSocket-based real-time data streaming |
| `market_feed_polling.py` | REST API polling fallback for rate-limited WebSocket |
| `strike_selector.py` | ATM option strike selection with premium filtering |
| `synchronizer.py` | Ensures Index and Option candles are time-aligned |

**Key Classes**:
- `Candle`: Immutable OHLC candle data
- `CandleAggregator`: Tick to candle conversion
- `SyncedCandlePair`: Synchronized Index + Option pair
- `DynamicStrike`: Dynamically selected strike container
- `Instrument`: Instrument data container

---

### 📁 src/execution/ - Order Execution

```
execution/
├── 📄 __init__.py                    # Package marker
├── 📄 order_manager.py               # Order placement (579 lines)
└── 📄 sl_manager.py                  # Stop loss management (803 lines)
```

| File | Purpose |
|------|---------|
| `order_manager.py` | Order placement, modification, tracking via SmartAPI |
| `sl_manager.py` | Bot-side SL with N-Structure v1.1 trailing logic |

**SL Manager Trailing Strategy**:
1. **Initial SL**: Entry - 10 points
2. **Safe Mode**: At +7pt profit → SL to Entry + 1pt
3. **Trail Mode**: At +10pt → Start trailing
4. **Structure TSL**: After 2+ HLs → Trail to HL[-2] - buffer
5. **Tight Trail**: After +20pt → Tighter buffer
6. **SL Breath**: Allow 1 candle below SL if structure intact

---

### 📁 src/indicators/ - Technical Indicators

```
indicators/
├── 📄 __init__.py                    # Package marker
├── 📄 atr.py                         # Average True Range (266 lines)
├── 📄 ema.py                         # EMA indicator (344 lines)
├── 📄 filters.py                     # Volume/Trend/Time filters (413 lines)
├── 📄 market_regime.py               # Trend vs Sideways detection (327 lines)
└── 📄 n_structure.py                 # N-Structure pattern detection (1295 lines)
```

| File | Purpose |
|------|---------|
| `atr.py` | ATR for dynamic SL, volatility filtering, trailing stops |
| `ema.py` | Incremental EMA calculation (EMA 9 & 15) |
| `filters.py` | Volume, Trend, Time filters for trade quality |
| `market_regime.py` | ADX-based trending vs sideways detection |
| `n_structure.py` | Core N-Structure pattern detection (HH+HL) |

**N-Structure Pattern** (`n_structure.py`):
```
The "N" shape represents:
- Point 1: Previous High (Breakout level)
- Point 2: Higher Low 1 (First pullback)
- Point 3: New High (Momentum continuation)
- Point 4: Higher Low 2 (Current pullback - entry zone)
```

**Signal Directions**:
- `BULLISH`: Buy CE
- `BEARISH`: Buy PE
- `NEUTRAL`: No clear direction

---

### 📁 src/risk/ - Risk Management

```
risk/
├── 📄 __init__.py                    # Package marker
├── 📄 position_reconciler.py         # Bot-Broker state verification (284 lines)
└── 📄 risk_manager.py                # Production risk manager v2.0 (851 lines)
```

| File | Purpose |
|------|---------|
| `position_reconciler.py` | Verifies bot state matches broker state |
| `risk_manager.py` | Real-time risk management for live trading |

**Risk Manager v2.0 Features**:
- Position sizing with capital validation
- Daily loss limits (absolute + SL count)
- Real-time margin check
- Time-based trading windows
- Cooldown after trades
- Re-entry tracking
- Drawdown monitoring

**Position Sizing Profiles**:

| Mode | Lots | Qty | Risk/Trade | Capital |
|------|------|-----|------------|---------|
| Conservative | 4 | 260 | ₹1,300 | ₹30K |
| Moderate | 6 | 390 | ₹1,950 | ₹50K |
| Aggressive | 8 | 520 | ₹2,600 | ₹75K |
| Ultra | 12 | 780 | ₹3,900 | ₹1L+ |

---

### 📁 src/strategies/ - Strategy Implementations

```
strategies/
├── 📄 __init__.py                    # Package marker
├── 📄 hybrid_auto_switch.py          # Auto-switch strategy (799 lines)
└── 📄 sideways_range.py              # Range trading strategy (506 lines)
```

| File | Purpose |
|------|---------|
| `hybrid_auto_switch.py` | Auto-switches between N-Structure (trending) and Range (sideways) |
| `sideways_range.py` | Mean reversion strategy for range-bound markets |

**Hybrid Strategy** (`hybrid_auto_switch.py`):
- ADX-based regime detection
- Seamless strategy switching
- Optimal entry in both market conditions

**Sideways Range Strategy** (`sideways_range.py`):
- Identifies range high (resistance) and low (support)
- CE at support bounce, PE at resistance rejection
- Quick 5-10 point profit targets

---

### 📁 src/utils/ - Utilities

```
utils/
├── 📄 __init__.py                    # Package marker
├── 📄 logger.py                      # Structured logging (363 lines)
└── 📄 telegram.py                    # Telegram notifications (500 lines)
```

| File | Purpose |
|------|---------|
| `logger.py` | Structured logging with Loguru, separate log files |
| `telegram.py` | Trade alerts, SL notifications, daily summary via Telegram |

**Log Files**:
- `trading.log`: Main application log
- `signals.jsonl`: Trading signals (JSON Lines)
- `orders.jsonl`: Order events
- `states.jsonl`: FSM state transitions

---

## 📁 scripts/ - Utility Scripts

```
scripts/
├── 📄 start_live.sh                  # Start live trading (45 lines)
├── 📄 start_paper.sh                 # Start paper trading (31 lines)
└── 📁 backtest/
    └── 📄 run_backtest.py            # Backtest runner (275 lines)
```

| Script | Purpose |
|--------|---------|
| `start_live.sh` | Live trading launcher with confirmation prompt |
| `start_paper.sh` | Paper trading launcher |
| `run_backtest.py` | Backtester with customizable parameters |

**Backtest Usage**:
```bash
python scripts/backtest/run_backtest.py --days 30 --capital 30000
```

---

## 📁 tests/ - Unit Tests

```
tests/
├── 📄 __init__.py                    # Package marker
├── 📄 test_filters.py                # Filter tests (336 lines)
├── 📄 test_risk_manager.py           # Risk manager tests
├── 📄 test_sl_manager.py             # SL manager tests
└── 📄 test_state_machine.py          # State machine tests
```

| Test File | Coverage |
|-----------|----------|
| `test_filters.py` | VolumeFilter, TrendFilter, TimeFilter, CompositeFilter |
| `test_risk_manager.py` | Position sizing, drawdown, limits |
| `test_sl_manager.py` | Trailing SL logic, phases |
| `test_state_machine.py` | FSM transitions |

**Test Status**: 98 tests passing

**Run Tests**:
```bash
python -m pytest tests/ -v
```

---

## 📁 data/ - Data Storage

```
data/
├── 📄 state.db                       # SQLite state database
├── 📁 cache/                         # Instrument cache (daily)
│   ├── instruments_20260204.json
│   ├── instruments_20260205.json
│   ├── instruments_20260206.json
│   ├── instruments_20260209.json
│   ├── instruments_20260210.json
│   ├── instruments_20260211.json
│   └── instruments_20260212.json
├── 📁 instruments/                   # Instrument master files
│   ├── .gitkeep
│   ├── instruments_20260125.json
│   ├── instruments_20260204.json
│   └── instruments_20260206.json
└── 📁 logs/                          # Trading logs
    ├── candles.jsonl                 # Candle data log
    ├── signals.jsonl                 # Trading signals
    ├── states.jsonl                  # FSM state transitions
    ├── errors.log                    # Error log (current)
    ├── errors.2026-02-04_*.log       # Archived error logs
    ├── errors.2026-02-05_*.log
    ├── errors.2026-02-09_*.log
    ├── trading.log                   # Current trading log
    └── trading.*.log.gz              # Archived logs (compressed)
```

---

## 📁 logs/ - Daily Application Logs

```
logs/
├── 2026-02-01/
│   └── app.log
├── 2026-02-02/
│   └── app.log
├── 2026-02-04/
│   └── app.log
├── 2026-02-05/
│   └── app.log
├── 2026-02-06/
│   └── app.log
├── 2026-02-09/
│   └── app.log
├── 2026-02-10/
│   └── app.log
├── 2026-02-11/
│   └── app.log
└── 2026-02-12/
    └── app.log
```

---

## 📄 Root Configuration Files

### requirements.txt - Python Dependencies

```
smartapi-python>=1.5.5     # Angel One SmartAPI
pyotp>=2.9.0               # TOTP authentication
logzero>=1.7.0             # Required by smartapi
pandas>=2.0.0              # Data processing
numpy>=1.24.0              # Numerical operations
pydantic>=2.0.0            # Data validation
pydantic-settings>=2.0.0   # Settings management
PyYAML>=6.0.0              # YAML config
aiosqlite>=0.19.0          # Async SQLite
asyncio-throttle>=1.0.2    # Rate limiting
loguru>=0.7.0              # Logging
schedule>=1.2.0            # Task scheduling
requests>=2.31.0           # HTTP client
websocket-client>=1.6.0    # WebSocket
aiohttp>=3.9.0             # Async HTTP
python-dotenv>=1.0.0       # Environment variables
```

### .env.example - Credential Template

```dotenv
ANGEL_API_KEY=your_api_key_here
ANGEL_CLIENT_ID=your_client_id_here
ANGEL_PASSWORD=your_password_here
ANGEL_TOTP_SECRET=your_totp_secret_here
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### .gitignore - Ignore Rules

**Categories**:
- 🔒 Credentials & Secrets: `.env`, `*.pem`, `*.key`
- 🐍 Python: `__pycache__/`, `*.pyc`, `build/`, `dist/`
- 📦 Virtual Environment: `venv/`, `.venv/`
- 💻 IDE & Editor: `.idea/`, `.vscode/`
- 📊 Data & Logs: `logs/`, `*.log`, `data/cache/`, `*.db`
- 🧪 Testing: `.pytest_cache/`, `.coverage`
- 💾 OS Files: `.DS_Store`, `Thumbs.db`

---

## 🎯 Strategy Overview

### N-Structure Pattern

```
INDEX Chart           OPTION Chart
    ▲                      ▲
   /│\  HH (Higher High)  /│\  Entry Point
  / │ \                  / │ \
 /  │  \  ← Pullback    /  │  \
HL2 HL1  ← Confirmation ────────────→ BUY!
```

### v5.2 Features

| Feature | Description |
|---------|-------------|
| Confirmation Candle | Wait 2 candles after pattern - avoid early entries |
| Volume Filter | Breakout volume must be 1.5x average |
| Gap Filter | Skip first signal on large gap days (>50pt) |
| Sniper Mode | 1 SL/day = Day Over (capital protection) |
| Structure TSL | Trail to swing lows, not just candle lows |
| Dynamic Strike | Select strike AFTER N-Structure confirmed |

---

## 🚀 Quick Start Commands

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Paper Trading
python src/main.py --paper --polling

# Live Trading
python src/main.py --polling

# Run Tests
python -m pytest tests/ -v

# Backtest
python scripts/backtest/run_backtest.py --days 30
```

---

## 📊 File Statistics Summary

| Directory | Files | Total Lines |
|-----------|-------|-------------|
| src/ | 24 | ~12,000+ |
| src/main.py | 1 | 1,726 |
| src/backtest/ | 2 | 2,410 |
| src/broker/ | 1 | 413 |
| src/core/ | 3 | 1,767 |
| src/data/ | 7 | 2,838 |
| src/execution/ | 2 | 1,382 |
| src/indicators/ | 5 | 2,645 |
| src/risk/ | 2 | 1,135 |
| src/strategies/ | 2 | 1,305 |
| src/utils/ | 2 | 863 |
| tests/ | 4 | ~1,000+ |
| scripts/ | 3 | 351 |
| config/ | 1 | 311 |

---

> **Note**: This is a private project. Not for redistribution.  
> **Last Updated**: February 12, 2026
