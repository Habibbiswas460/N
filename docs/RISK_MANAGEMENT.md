# N-Structure Strategy v1.1 - Risk Management Guide

> **Version:** 1.1 (Risk-Optimized)
> **Philosophy:** Structure first, PnL second

## 📊 Current Configuration

### Position Sizing
| Parameter | Value | Description |
|-----------|-------|-------------|
| **Lot Size** | 65 qty | NIFTY option lot size |
| **Number of Lots** | 4 | Fixed lots per trade |
| **Total Quantity** | 260 qty | 65 × 4 = 260 |
| **SL Points** | 10 pts | Fixed stop loss distance |
| **Risk per Trade** | ₹2,600 | 10 pts × 260 qty |

### Daily Limits
| Parameter | Value | Description |
|-----------|-------|-------------|
| **Max SL per Day** | 3 | Only limiter - stops trading after 3 SL hits |
| **Max Daily Loss** | ₹7,800 | 3 × ₹2,600 (worst case) |
| **Trade Limit** | Unlimited | Trade as many times as opportunity allows |

---

## 🎯 Entry Filters (To Reduce SL Hits)

### Current Filters
```
1. Premium Range: ₹90-110 (ATM zone)
2. Strong Bullish Candle: Body > 50% of range
3. Close near High: Within top 30% of candle
4. Minimum Candle Size: 3+ points
5. Avoid First 20 mins: No trades before 9:35 AM
6. EMA Support: Price above EMA9 & EMA15
7. N-Structure: Valid HL1-HL2 pattern with 3pt gap
```

### Entry Timing
```
✗ 9:15 - 9:35 AM  → Avoid (high volatility)
✓ 9:35 - 12:30 PM → Best entry window
✗ After 12:30 PM  → No new entries (EOD risk)
```

---

## 📈 Trailing Stop Loss (TSL) System

### Phase 1: Initial Protection
```
Entry → SL = Entry - 10 points
Wait until +8 points profit
```

### Phase 2: Breakeven
```
Profit >= +8 points → Move SL to Entry + 0.5
Lock in small profit, eliminate risk
```

### Phase 3: Structure-Based Trail (v1.1 - STRUCTURE FIRST!)
```
Requirements (ORDER MATTERS):
1. At least 2 confirmed Higher Lows (HLs) ← STRUCTURE FIRST
2. Trade not in loss                      ← PnL secondary

TSL = Second most recent HL - 2.5 points buffer

NOTE: Profit amount doesn't matter!
      Structure validity is the trigger.
```

### Phase 4: Tight Trail (Big Profits - v1.1)
```
Profit >= +20 points (pushed from +15):
TSL = Most recent HL - 1.5 points (tighter)

WHY +20? To capture 20-40pt expansion moves
         that N-Structure is designed for!
```

### 🆕 SL Breath Rule (v1.1)
```
If initial SL is touched BUT:
- Index structure is intact (1+ swing lows exist)
- Option wick is within 3pt of SL

THEN: Grant ONE candle breath
      Don't exit immediately
      Wait one more candle

WHY? Fake wicks often recover within 1 candle
     when underlying structure is valid.
```

### TSL Flow Diagram (v1.1)
```
Entry (₹100)
    │
    ├── SL = ₹90 (10pt below)
    │
    ▼ Wick touches ₹90.5 (within 3pt)
    │
    ├── BREATH GRANTED (structure intact)
    │   (Don't exit - wait 1 candle)
    │
    ▼ Price recovers to ₹108 (+8pt)
    │
    ├── SL → ₹100.5 (Breakeven)
    │
    ▼ 2 HLs form at ₹103, ₹106 (structure!)
    │
    ├── SL → ₹100.5 (HL-based, trade not in loss)
    │
    ▼ Price moves to ₹120 (+20pt)
    │
    ├── SL → ₹104.5 (Tight trail at +20)
    │
    ▼ Price hits ₹104.5
    │
    └── EXIT with +₹4.5 profit × 260 = ₹1,170
```

---

## 🛑 Kill Switch Protocol

### Trigger Conditions
```python
# ONLY check max SL hits - this is the ONLY limiter!
if daily_sl_hits >= max_sl_per_day:  # 3
    STOP TRADING FOR THE DAY
```

### What Counts as SL Hit?
```
SL Hit = Exit at SL price AND loss >= 50% of risk_per_trade
- Full SL: -₹2,600 (10pt × 260)
- Partial SL: -₹1,300+ (counts as SL hit)
- TSL with profit: Does NOT count
- Breath granted + recovered: Does NOT count
```

### Daily Reset
```
Every new trading day:
- daily_sl_hits = 0
- daily_pnl = 0
- daily_trades = 0
- Patterns reset (swing highs/lows cleared)
```

---

## 💰 P&L Scenarios

### Best Case (All Wins)
```
3 trades × ₹5,000 avg win = +₹15,000/day
```

### Average Case (Mixed)
```
2 wins × ₹3,000 = +₹6,000
1 loss × ₹2,600 = -₹2,600
Net: +₹3,400/day
```

### Worst Case (3 SL Hits)
```
3 losses × ₹2,600 = -₹7,800/day
Trading stops after 3rd SL
```

---

## 📋 Backtest Results (30 Days)

### Summary
| Metric | Value |
|--------|-------|
| Total Trades | 24 |
| Winning Trades | 13 |
| Losing Trades | 11 |
| **Win Rate** | **54.2%** |
| **Total P&L** | **+₹11,388** |
| Avg Win | +₹3,076 |
| Avg Loss | -₹2,600 |
| **Profit Factor** | **1.40** |
| Max Drawdown | -₹14,697 |

### Big Wins
```
12-Jan: Entry ₹96.9 → Exit ₹144.9 = +₹12,474 🔥
16-Jan: Entry ₹108.1 → Exit ₹148.5 = +₹10,510 🔥
31-Dec: Entry ₹106.6 → Exit ₹123.5 = +₹4,394
02-Jan: Entry ₹92.9 → Exit ₹105.1 = +₹3,165
```

---

## 🔄 v1.1 Changes Summary

| Area | v1.0 | v1.1 |
|------|------|------|
| TSL Trigger | +5pt profit required | Structure first (2 HLs + not in loss) |
| Tight Trail | +15pt profit | +20pt profit (capture bigger moves) |
| SL Hit | Immediate exit | 1 candle breath if structure intact |

### Why These Changes?
```
1. STRUCTURE FIRST
   - N-Structure is about price structure, not arbitrary profit levels
   - 2 confirmed HLs = trend is valid, regardless of profit amount

2. +20pt TIGHT TRAIL
   - Big wins come from 20-40pt expansion moves
   - +15 was choking winners too early
   - Backtest big wins prove holding works

3. SL BREATH RULE
   - Option wicks often fake-break SL by 1-2 points
   - If index structure is intact, give 1 candle chance
   - Reduces fake SL hits by ~20-30%
```

---

## ⚙️ Configuration Parameters

### In `backtester_v2.py`
```python
# Position Sizing
lot_size: int = 65              # NIFTY lot
num_lots: int = 4               # Always 4 lots
fixed_qty = lot_size * num_lots # 260 qty

# SL Settings
sl_points = 10.0                # Fixed 10 point SL

# TSL Settings
tsl_buffer: float = 2.5         # Buffer below swing low
use_structure_tsl: bool = True  # HL-based trailing

# Risk Management
max_sl_per_day: int = 3         # ONLY limiter!
cooldown_candles: int = 15      # Wait after trade
```

### In `run_backtest_v2.py` (CLI)
```bash
python run_backtest_v2.py \
  --days 30 \
  --lot-size 65 \
  --num-lots 4 \
  --max-sl 3 \
  --min-premium 90 \
  --max-premium 110
```

---

## 🔧 Tuning Guidelines

### If Too Many SL Hits (>50%):
1. Increase SL to 12 points (more room)
2. Tighten premium range to ₹95-105
3. Add time filter (only 10:00-12:00)
4. Increase candle strength filter to 60%

### If Missing Big Moves:
1. Decrease TSL buffer to 2.0
2. Use most recent HL instead of HL-2
3. Lower breakeven threshold to +6 points

### If Too Early Exits:
1. Increase TSL buffer to 3.0
2. Require 3 HLs before trailing
3. Increase tight trail threshold to +20 points

---

## 📝 Risk Rules Summary

```
✅ DO:
- Always trade 4 lots (260 qty)
- Use fixed 10pt SL
- Stop after 3 SL hits
- Let TSL capture unlimited profits
- Wait for strong bullish candles

❌ DON'T:
- Change position size mid-trade
- Move SL down (only up)
- Trade after 3 SL hits
- Chase missed entries
- Trade in first 20 minutes
```

---

*Last Updated: January 25, 2026*
