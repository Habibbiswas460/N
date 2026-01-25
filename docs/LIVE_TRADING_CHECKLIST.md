# 🚀 N-Structure Trading Bot - Live Trading Checklist

## v1.2 Stable Release

### ✅ Pre-Deployment Checklist

#### 1. Environment Setup
- [ ] Python 3.10+ installed
- [ ] Virtual environment created (`python -m venv venv`)
- [ ] Dependencies installed (`pip install -r requirements.txt`)

#### 2. Credentials (.env file)
```bash
ANGEL_API_KEY=your_api_key       # ← Angel One API key
ANGEL_CLIENT_ID=your_client_id   # ← Trading account client ID  
ANGEL_PASSWORD=your_password     # ← Trading password
ANGEL_TOTP_SECRET=your_totp      # ← TOTP secret for 2FA

# Optional - Telegram alerts
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

#### 3. Account Requirements
- [ ] **Margin**: Min ₹15,000 for 4 lots NIFTY options
- [ ] **Segment Activated**: F&O segment enabled
- [ ] **Auto-squareoff**: Verify broker's squareoff time (usually 3:20 PM)

---

### 📊 v1.2 Strategy Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Position Size** | 4 lots (260 qty) | Fixed size |
| **Initial SL** | 10 points | Fixed stop loss |
| **Max SL/Day** | 3 | Daily limit |
| **Max Risk/Trade** | ₹2,600 | 10 × 260 |
| **Max Daily Loss** | ₹7,800 | 3 × ₹2,600 |
| **Trading Window** | 09:50 - 12:30 | Entry allowed |
| **Position Manage** | Till 14:40 | TSL active |
| **Re-entries** | Max 2/day | After SL hit |

---

### 🏃 Running the Bot

#### Paper Trading (Test First!)
```bash
chmod +x scripts/start_paper.sh
./scripts/start_paper.sh
```

#### Live Trading
```bash
chmod +x scripts/start_live.sh
./scripts/start_live.sh
```

#### Manual Run
```bash
# Paper mode
python src/main.py --paper --log-level INFO

# Live mode
python src/main.py --log-level INFO
```

---

### 📈 Backtest Performance (v1.2)

**Period**: 90 days (Nov 2024 - Jan 2025)

| Metric | Value |
|--------|-------|
| Total Trades | 117 |
| Win Rate | 54.7% |
| Total P&L | ₹48,244 |
| Profit Factor | 1.39 |
| Max Drawdown | ₹6,800 |
| Avg Win | ₹1,480 |
| Avg Loss | -₹1,880 |

---

### 📁 Important Directories

```
N/
├── config/
│   └── settings.yaml      # Strategy configuration
├── data/
│   ├── logs/              # Trading logs
│   ├── cache/             # Instrument cache
│   └── state.db           # Persistence
├── logs/
│   └── paper_trading.log  # Paper trading log
└── scripts/
    ├── start_paper.sh     # Paper mode launcher
    └── start_live.sh      # Live mode launcher
```

---

### 🔔 Telegram Alerts (Optional)

1. Create bot via @BotFather
2. Get chat_id from @userinfobot
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=your_chat_id
   ```
4. Set `telegram.enabled: true` in settings.yaml

**Alert Types:**
- 📈 Entry signals
- 🔴 SL hits
- 🟢 Profit exits
- 🔄 Re-entry opportunities
- 📊 Daily summary

---

### ⚠️ Risk Warnings

1. **Never risk more than you can afford to lose**
2. **Test thoroughly in paper mode first**
3. **Monitor the bot - don't leave completely unattended**
4. **Have manual override ready (broker app)**
5. **Check position reconciliation after each session**

---

### 🆘 Troubleshooting

| Issue | Solution |
|-------|----------|
| Login failed | Check API credentials in .env |
| No options found | Instrument master outdated - delete cache |
| WebSocket disconnect | Check internet, bot auto-reconnects |
| Order rejected | Check margin, lot size, segment |
| Wrong strike selected | Premium filter may need adjustment |

---

### 📞 Emergency Commands

```bash
# Stop bot gracefully
Ctrl+C

# Force stop
kill -9 $(pgrep -f "python src/main.py")

# Check running bots
ps aux | grep "main.py"

# View live logs
tail -f data/logs/trading.log
```

---

**Version**: 1.2.0  
**Last Updated**: January 2025  
**Status**: Production Ready ✅
