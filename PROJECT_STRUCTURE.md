# N-Structure Trading Bot - Project Structure v5.3

## 📁 Directory Layout

```
N/
├── config/
│   └── settings.yaml           # All strategy parameters (335 lines)
│
├── src/                        # Source code
│   ├── __init__.py
│   ├── main.py                 # Main bot orchestrator (1896 lines)
│   │
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── backtester.py       # N-Structure V2 backtester (2203 lines)
│   │   └── historical_data.py  # Historical data fetching
│   │
│   ├── broker/
│   │   ├── __init__.py
│   │   └── auth.py             # Angel One API auth + retry (463 lines)
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state_machine.py    # FSM v1.2 - 11 states (836 lines)
│   │   ├── state_store.py      # SQLite persistence (555 lines)
│   │   └── risk_manager.py     # Partial profits/drawdown (476 lines)
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── candle_builder.py   # Tick→OHLC aggregation (386 lines)
│   │   ├── instrument_master.py # Downloads instrument file
│   │   ├── market_feed.py      # WebSocket feed (backup)
│   │   ├── market_feed_polling.py # REST polling (primary)
│   │   ├── strike_selector.py  # ATM strike selection
│   │   ├── dynamic_strike_selector.py # On-demand selection (415 lines)
│   │   └── synchronizer.py     # Index+Option sync (342 lines)
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_manager.py    # Paper/Live orders (979 lines)
│   │   └── sl_manager.py       # Sniper TSL v2.0 (805 lines)
│   │
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── n_structure.py      # Dual direction detector (1295 lines)
│   │   ├── ema.py              # Incremental EMA (344 lines)
│   │   ├── atr.py              # ATR calculation (266 lines)
│   │   ├── filters.py          # Volume/Trend/Time (413 lines)
│   │   └── market_regime.py    # ADX-based detection (327 lines)
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── risk_manager.py     # v2.0 Sniper Mode (851 lines)
│   │   └── position_reconciler.py # Broker position sync (284 lines)
│   │
│   ├── strategies/
│   │   └── __init__.py         # (N-Structure only)
│   │
│   └── utils/
│       ├── __init__.py         # IST timezone utilities
│       ├── logger.py           # Loguru structured logging (363 lines)
│       └── telegram.py         # Async notifications (500 lines)
│
├── scripts/
│   └── backtest/
│       └── run_backtest.py     # CLI backtester
│
├── tests/                      # 98 unit tests
│   ├── __init__.py
│   ├── test_filters.py         # Filter tests (336 lines)
│   ├── test_risk_manager.py    # Risk v2.0 tests (528 lines)
│   ├── test_sl_manager.py      # SL Manager tests (564 lines)
│   └── test_state_machine.py   # FSM tests (367 lines)
│
├── data/
│   ├── cache/                  # Daily instrument cache (auto-cleaned)
│   ├── instruments/            # Empty (uses cache)
│   ├── logs/                   # Reserved
│   └── state.db                # SQLite state database
│
├── logs/                       # Daily trading logs by date
│   └── 2026-02-XX/
│       └── app.log
│
├── docs/
│   ├── LIVE_TRADING_CHECKLIST.md
│   └── RISK_MANAGEMENT.md
│
├── .env                        # API credentials (not in git)
├── .env.example                # Credential template
├── .gitignore
├── requirements.txt            # 38 Python packages
├── start.sh                    # Animated launcher
├── README.md                   # Project overview
├── PROJECT_STRUCTURE.md        # This file
└── TRADING_LOG_ANALYSIS.md     # Trading analysis
```

---

## 🔧 Core Components

### State Machine (FSM v1.2)

```
IDLE → WATCHING_BREAKOUT → TRACKING_PULLBACK → VALIDATING_HL
                                                    ↓
COOLDOWN ← IN_POSITION ← ARMED ← CHECKING_DIVERGENCE
    ↓           ↓
PENDING_REENTRY → (back to ARMED)
```

**11 States:**
- `IDLE` - Waiting for pattern
- `WATCHING_BREAKOUT` - HH detected, watching for pullback
- `TRACKING_PULLBACK` - Following pullback
- `VALIDATING_HL` - Confirming HL1/HL2
- `CHECKING_DIVERGENCE` - Optional divergence filter
- `READY_FOR_ENTRY` - Confirmation candle wait
- `ARMED` - Ready to trigger entry
- `IN_POSITION` - Trade active
- `PENDING_REENTRY` - After SL, watching for re-entry
- `COOLDOWN` - Post-trade cooldown
- `PAUSED` / `ERROR` - Manual halt or error state

### Risk Manager v2.0

**Protection Layers:**
1. Emergency Halt
2. Time Filter (09:30 - 14:30)
3. Sniper Mode (1 SL/day)
4. Daily Loss Limit
5. Capital Protection (5%)
6. Max Trades (10/day)
7. Cooldown (15-30 candles)

### SL Manager v2.0 (Sniper Mode)

**Phases:**
1. **Initial** - 8pt fixed SL
2. **Safe Mode** - At +7pt → SL = Entry + 1pt
3. **Trail Mode** - At +10pt → TSL = High - 5pt
4. **Tight Trail** - At +20pt → TSL = High - 1.5pt
5. **Structure TSL** - Uses swing lows when available

---

## 📊 Current Settings (v5.3)

| Setting | Value |
|---------|-------|
| **Position Mode** | Conservative |
| **Lots** | 4 (260 qty) |
| **SL Points** | 8 |
| **Risk/Trade** | ₹2,080 |
| **Max SL/Day** | 1 |
| **Trading Start** | 09:30 |
| **No New After** | 14:30 |
| **Premium Range** | ₹85-₹150 |

---

## 📦 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| smartapi-python | 1.5.5 | Angel One API |
| pandas | 3.0.1 | Data analysis |
| loguru | 0.7.3 | Logging |
| pydantic | 2.12.5 | Settings validation |
| aiohttp | 3.13.3 | Async HTTP |
| pytest | 9.0.2 | Testing |
| PyYAML | 6.0.3 | Config parsing |
| pyotp | 2.9.0 | TOTP generation |

---

## 🧪 Test Coverage

| Module | Tests | Status |
|--------|-------|--------|
| Filters | 25 | ✅ |
| Risk Manager | 30 | ✅ |
| SL Manager | 22 | ✅ |
| State Machine | 21 | ✅ |
| **Total** | **98** | **✅ All Passing** |

---

## 📝 File Statistics

| Category | Files | Lines |
|----------|-------|-------|
| Source (src/) | 28 | ~12,000 |
| Tests | 4 | ~1,800 |
| Config | 1 | 335 |
| Scripts | 1 | ~200 |
| **Total** | **34** | **~14,300** |

---

*Last Updated: February 23, 2026*
