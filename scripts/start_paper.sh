#!/bin/bash
# N-Structure Trading Bot - Paper Mode Launcher
# v1.2 - Stable Release

set -e

# Change to project root
cd "$(dirname "$0")/.."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}  N-Structure Trading Bot v1.2 - Paper Mode${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""

# Check if venv exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: venv not found. Run: python -m venv venv${NC}"
    exit 1
fi

# Activate venv
source venv/bin/activate

# Check .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Create .env from .env.example and add your credentials."
    exit 1
fi

# Verify required env vars
source .env
if [ -z "$ANGEL_API_KEY" ] || [ "$ANGEL_API_KEY" = "your_api_key_here" ]; then
    echo -e "${RED}Error: ANGEL_API_KEY not configured in .env${NC}"
    exit 1
fi

# Create data directories if needed
mkdir -p data/logs data/cache

# Pre-flight checks
echo -e "${YELLOW}Running pre-flight checks...${NC}"

# Check Python
python_version=$(python --version 2>&1)
echo -e "  ✓ Python: $python_version"

# Check key dependencies
python -c "import smartapi" 2>/dev/null && echo -e "  ✓ SmartAPI installed" || {
    echo -e "${RED}  ✗ SmartAPI not found. Run: pip install smartapi-python${NC}"
    exit 1
}

# Check market hours (IST)
current_hour=$(TZ='Asia/Kolkata' date +%H)
current_min=$(TZ='Asia/Kolkata' date +%M)
current_time="${current_hour}:${current_min}"

if [ "$current_hour" -lt 9 ] || [ "$current_hour" -gt 15 ]; then
    echo -e "${YELLOW}  ⚠ Outside market hours (IST: ${current_time})${NC}"
    echo -e "    Market: 09:15 - 15:30 IST"
else
    echo -e "  ✓ Market hours OK (IST: ${current_time})"
fi

echo ""
echo -e "${GREEN}Starting Paper Trading Bot...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

# Start bot in paper mode
python src/main.py --paper --log-level INFO

