# 🚀 Quick Start Guide

## প্রথমবার Setup

### 1. Virtual Environment তৈরি করো
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Dependencies Install করো
```bash
pip install -r requirements.txt
```

### 3. API Credentials Setup
`config/settings.yaml` ফাইলে Angel One credentials দাও:

```yaml
broker:
  api_key: "YOUR_API_KEY"
  client_id: "YOUR_CLIENT_ID"
  password: "YOUR_PASSWORD"
  totp_secret: "YOUR_TOTP_SECRET"
```

---

## 🎮 Bot চালাও

```bash
./start.sh
```

### Shortcuts:
| Key | Action |
|-----|--------|
| `p` | 📝 Paper Trading (সিমুলেশন) |
| `l` | 🔴 Live Trading (রিয়েল মানি) |
| `x` | ⏹ Stop Bot |
| `t` | 📊 Today's Report |
| `b` | 🔬 Backtest |
| `v` | 📜 View Logs |
| `q` | 🚪 Quit |

---

## 📁 Important Folders

```
N/
├── config/settings.yaml  ← Strategy + API settings
├── data/
│   ├── journal/          ← Trade records (CSV)
│   ├── logs/             ← Structured logs
│   └── trading.db        ← SQLite database (auto-created)
├── logs/                 ← Daily log files
├── src/                  ← Source code
└── scripts/              ← Backtest scripts
```

---

## ⚡ First Run Checklist

- [ ] API credentials setup করা হয়েছে
- [ ] `./start.sh` চলছে
- [ ] প্রথমে `p` দিয়ে Paper Trading test করো
- [ ] `v` দিয়ে logs দেখো

---

## ⚠️ Important Notes

1. **প্রথমে Paper Trading করো** - Real money দিয়ে শুরু করো না
2. **Market Hours** - 9:15 AM - 3:30 PM (Indian Market)
3. **Logs Check করো** - `v` press করে দেখো কি হচ্ছে
4. **Risk Management** - Default config এ max 3 trades/day

---

## 🆘 Common Issues

### Bot start হচ্ছে না
```bash
# venv activate আছে কিনা check করো
source venv/bin/activate
```

### API Error
- Angel One credentials verify করো
- TOTP secret correct কিনা দেখো

### No Trades
- Market hours এ run করো (9:30 AM - 3:00 PM)
- Regime check করো - VOLATILE regime এ trade নেয় না

---

**Happy Trading! 🎯**
