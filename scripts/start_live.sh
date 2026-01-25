#!/bin/bash
# N-Structure Trading Bot - LIVE Trading Launcher
# v1.2 - Stable Release
#
# ⚠️  WARNING: This will place REAL orders with REAL money!
# ⚠️  Always test with paper mode first!

set -e

# Change to project root
cd "$(dirname "$0")/.."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

clear
echo -e "${RED}=================================================${NC}"
echo -e "${RED}  ⚠️  WARNING: LIVE TRADING MODE ⚠️${NC}"
echo -e "${RED}=================================================${NC}"
echo ""
echo -e "${YELLOW}This will place REAL orders with REAL money!${NC}"
echo ""
echo "Configuration Summary:"
echo "  - Strategy: N-Structure Momentum Breakout v1.2"
echo "  - Position Size: 4 lots (260 qty)"
echo "  - Max Risk/Trade: ₹2,600 (10pt SL)"
echo "  - Max Daily Loss: ₹7,800 (3 SL max)"
echo "  - Trading Window: 09:50 - 12:30 IST"
echo ""

# Require confirmation
read -p "Type 'START LIVE' to confirm: " confirmation
if [ "$confirmation" != "START LIVE" ]; then
    echo -e "${YELLOW}Cancelled.${NC}"
    exit 0
fi

echo ""

# Activate venv
source venv/bin/activate

# Check .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    exit 1
fi

# Verify required env vars
source .env
if [ -z "$ANGEL_API_KEY" ] || [ "$ANGEL_API_KEY" = "your_api_key_here" ]; then
    echo -e "${RED}Error: ANGEL_API_KEY not configured in .env${NC}"
    exit 1
fi

# Create data directories
mkdir -p data/logs data/cache

# Pre-flight checks
echo -e "${YELLOW}Running pre-flight checks...${NC}"

# Check Python
python_version=$(python --version 2>&1)
echo -e "  ✓ Python: $python_version"

# Check key dependencies
python -c "import smartapi" 2>/dev/null && echo -e "  ✓ SmartAPI installed" || {
    echo -e "${RED}  ✗ SmartAPI not found${NC}"
    exit 1
}

# Check market hours (IST)
current_hour=$(TZ='Asia/Kolkata' date +%H)
current_min=$(TZ='Asia/Kolkata' date +%M)
current_time="${current_hour}:${current_min}"
day_of_week=$(TZ='Asia/Kolkata' date +%u)

# Weekend check
if [ "$day_of_week" -gt 5 ]; then
    echo -e "${RED}  ✗ Today is weekend - Markets closed${NC}"
    exit 1
fi

# Time check
if [ "$current_hour" -lt 9 ] || [ "$current_hour" -gt 15 ]; then
    echo -e "${RED}  ✗ Outside market hours (IST: ${current_time})${NC}"
    echo -e "    Market: 09:15 - 15:30 IST"
    read -p "Continue anyway? (y/n): " continue_anyway
    if [ "$continue_anyway" != "y" ]; then
        exit 0
    fi
else
    echo -e "  ✓ Market hours OK (IST: ${current_time})"
fi

# Final confirmation
echo ""
echo -e "${BLUE}Final Checklist:${NC}"
echo "  [ ] Sufficient margin in account?"
echo "  [ ] Paper trading validated?"
echo "  [ ] Internet connection stable?"
echo "  [ ] No other auto-trading active?"
echo ""
read -p "All checks passed? (y/n): " final_confirm
if [ "$final_confirm" != "y" ]; then
    echo -e "${YELLOW}Cancelled.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Starting LIVE Trading Bot...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

# Start bot in LIVE mode (no --paper flag)
python src/main.py --log-level INFO

