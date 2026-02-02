#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# N-Structure Trading Bot v5.2 - LIVE MODE
# ⚠️  WARNING: REAL MONEY TRADING!
# ═══════════════════════════════════════════════════════════════

cd "$(dirname "$0")/.."

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ⚠️  WARNING: LIVE TRADING MODE ⚠️                       ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  This will place REAL orders with REAL money!           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Strategy: N-Structure Ultimate Sniper v5.2"
echo ""
echo "Risk Settings (from config):"
grep -E "position_mode|max_sl_per_day|sl_points" config/settings.yaml | head -5
echo ""
echo "Time Settings:"
grep -E "trading_start|no_new_trades_after" config/settings.yaml | head -3
echo ""

# Confirmation
read -p "Type 'LIVE' to confirm: " confirm
if [ "$confirm" != "LIVE" ]; then
    echo "❌ Cancelled."
    exit 0
fi

# Activate venv
source venv/bin/activate

# Check .env
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found!"
    exit 1
fi

# Run live trading
echo ""
echo "🚀 Starting LIVE trading..."
python src/main.py --polling
