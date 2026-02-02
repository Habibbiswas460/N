#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# N-Structure Trading Bot v5.2 - Paper Mode
# ═══════════════════════════════════════════════════════════════

cd "$(dirname "$0")/.."

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  🎯 N-Structure Ultimate Sniper v5.2 - PAPER MODE       ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  ✓ Confirmation Candle (2 candle wait)                  ║"
echo "║  ✓ Volume Filter (1.5x breakout)                        ║"
echo "║  ✓ Gap Filter (skip >50pt gaps)                         ║"
echo "║  ✓ Sniper Mode (1 SL/day)                               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Activate venv
source venv/bin/activate

# Check .env
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found!"
    exit 1
fi

# Run paper trading
echo "🚀 Starting paper trading..."
python src/main.py --paper --polling

