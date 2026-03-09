#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║              N - ADAPTIVE HYBRID TRADING SYSTEM v3.1                         ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

VERSION="3.1.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PATH="$SCRIPT_DIR/venv"
MAIN_SCRIPT="$SCRIPT_DIR/src/main.py"
LOG_DIR="$SCRIPT_DIR/logs"
DATA_DIR="$SCRIPT_DIR/data"
CONFIG_FILE="$SCRIPT_DIR/config/settings.yaml"

# Colors
NC='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
BRED='\033[1;31m'
BGREEN='\033[1;32m'
BYELLOW='\033[1;33m'
BBLUE='\033[1;34m'
BPURPLE='\033[1;35m'
BCYAN='\033[1;36m'
BWHITE='\033[1;37m'
DIM='\033[2m'
BG_RED='\033[41m'
BG_GREEN='\033[42m'
BG_YELLOW='\033[43m'
BG_BLUE='\033[44m'

# ═══════════════════════════════════════════════════════════════════════════════
#                              UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

activate_venv() {
    [ -d "$VENV_PATH" ] && source "$VENV_PATH/bin/activate" && return 0
    echo -e "${RED}❌ venv not found!${NC}"; return 1
}

get_bot_pid() { pgrep -f "python.*main.py" 2>/dev/null | head -1; }
is_bot_running() { [ -n "$(get_bot_pid)" ]; }

get_market_status() {
    local hour=$(date +%H) minute=$(date +%M) day=$(date +%u)
    local time_num=$((hour * 100 + minute))
    [ $day -ge 6 ] && echo "🔴 WEEKEND" && return
    [ $time_num -lt 915 ] && echo "🟡 PRE-MKT" && return
    [ $time_num -ge 915 ] && [ $time_num -lt 1530 ] && echo "🟢 OPEN" && return
    echo "🔴 CLOSED"
}

# ═══════════════════════════════════════════════════════════════════════════════
#                                  HEADER
# ═══════════════════════════════════════════════════════════════════════════════

show_header() {
    clear
    local market=$(get_market_status)
    local bot_status="${RED}⏹ OFF${NC}"
    is_bot_running && bot_status="${BGREEN}▶ LIVE${NC}"
    
    echo ""
    echo -e "  ${BCYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${BCYAN}║${NC}                                                                    ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}███╗${NC}   ${BCYAN}██╗${NC}    ${BWHITE}NIFTY ADAPTIVE HYBRID${NC}                          ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}████╗${NC}  ${BCYAN}██║${NC}    ${DIM}VWAP + PDH/PDL Fusion Strategy${NC}                   ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}██╔██╗${NC} ${BCYAN}██║${NC}    ${DIM}v${VERSION}${NC}                                          ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}██║╚██╗${NC}${BCYAN}██║${NC}                                                    ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}██║${NC} ${BCYAN}╚████║${NC}    ${BWHITE}$(date '+%a %d %b %Y')${NC}  │  ${BCYAN}$(date '+%H:%M')${NC}              ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}   ${BGREEN}╚═╝${NC}  ${BCYAN}╚═══╝${NC}    ${market}  │  Bot: ${bot_status}                    ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}║${NC}                                                                    ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
#                              STATS PANEL
# ═══════════════════════════════════════════════════════════════════════════════

show_stats() {
    local capital="50,000" pnl="+0" trades="0"
    
    if [ -f "$DATA_DIR/paper_state.json" ]; then
        capital=$(python3 -c "import json; d=json.load(open('$DATA_DIR/paper_state.json')); print(f\"{d.get('capital', 50000):,.0f}\")" 2>/dev/null || echo "50,000")
        pnl=$(python3 -c "import json; d=json.load(open('$DATA_DIR/paper_state.json')); print(f\"{d.get('daily_pnl', 0):+,.0f}\")" 2>/dev/null || echo "+0")
        trades=$(python3 -c "import json; d=json.load(open('$DATA_DIR/paper_state.json')); print(d.get('trades_today', 0))" 2>/dev/null || echo "0")
    fi
    
    local pnl_color="${BGREEN}"
    [[ "$pnl" == "-"* ]] && pnl_color="${BRED}"
    
    echo ""
    echo -e "  ${DIM}├──────────────────────────────────────────────────────────────────────┤${NC}"
    echo -e "  ${DIM}│${NC}  💰 ${BWHITE}₹${capital}${NC}   │   📊 ${pnl_color}₹${pnl}${NC}   │   📈 ${BCYAN}${trades}${NC} trades today              ${DIM}│${NC}"
    echo -e "  ${DIM}└──────────────────────────────────────────────────────────────────────┘${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
#                                   MENU
# ═══════════════════════════════════════════════════════════════════════════════

show_menu() {
    echo ""
    echo -e "  ${BGREEN}▸ TRADING ─────────────────────────────────────────────────────────────${NC}"
    if is_bot_running; then
        echo -e "    ${DIM}[l] Live${NC}    ${DIM}[p] Paper${NC}    ${BRED}[x] Stop Bot${NC}    ${BWHITE}[t] Today Report${NC}"
    else
        echo -e "    ${BRED}[l] Live${NC}    ${BYELLOW}[p] Paper${NC}    ${DIM}[x] Stop${NC}        ${BWHITE}[t] Today Report${NC}"
    fi
    echo ""
    
    echo -e "  ${BCYAN}▸ ANALYSIS ────────────────────────────────────────────────────────────${NC}"
    echo -e "    ${BWHITE}[b] Backtest${NC}    ${BWHITE}[c] Compare${NC}    ${BWHITE}[d] Dashboard${NC}    ${BWHITE}[o] Optimizer${NC}"
    echo ""
    
    echo -e "  ${BBLUE}▸ SYSTEM ──────────────────────────────────────────────────────────────${NC}"
    echo -e "    ${BWHITE}[v] View Logs${NC}    ${BWHITE}[e] Edit Config${NC}    ${BWHITE}[r] Run Tests${NC}    ${BWHITE}[.] Clear${NC}"
    echo ""
    
    echo -e "  ${BPURPLE}▸ QUICK ───────────────────────────────────────────────────────────────${NC}"
    echo -e "    ${BWHITE}[s] Stats${NC}    ${BWHITE}[h] History${NC}    ${BWHITE}[?] Help${NC}    ${BRED}[q] Quit${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
#                               TRADING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

start_live() {
    show_header
    echo ""
    echo -e "  ${BG_RED}${BWHITE}  ⚠️  WARNING: LIVE TRADING WITH REAL MONEY  ${NC}"
    echo ""
    read -p "  Type 'CONFIRM' to start: " confirm
    [ "$confirm" != "CONFIRM" ] && { echo -e "  ${YELLOW}Cancelled${NC}"; sleep 1; return; }
    is_bot_running && { echo -e "  ${YELLOW}Already running${NC}"; sleep 1; return; }
    
    activate_venv || return
    mkdir -p "$LOG_DIR/$(date +%Y-%m-%d)"
    
    echo -e "  ${BCYAN}Starting Live Trading...${NC}"
    nohup python3 "$MAIN_SCRIPT" --polling > "$LOG_DIR/$(date +%Y-%m-%d)/live.log" 2>&1 &
    sleep 2
    
    is_bot_running && echo -e "  ${BGREEN}✓ Live trading started (PID: $(get_bot_pid))${NC}" || echo -e "  ${RED}✗ Failed to start${NC}"
    read -p "  Press Enter..."
}

start_paper() {
    show_header
    echo ""
    echo -e "  ${BG_YELLOW}${BWHITE}  📝 PAPER TRADING MODE  ${NC}"
    echo ""
    
    is_bot_running && { echo -e "  ${YELLOW}Already running${NC}"; sleep 1; return; }
    
    activate_venv || return
    mkdir -p "$LOG_DIR/$(date +%Y-%m-%d)"
    
    echo -e "  ${BCYAN}Starting Paper Trading...${NC}"
    nohup python3 "$MAIN_SCRIPT" --paper --polling >> "$LOG_DIR/$(date +%Y-%m-%d)/paper.log" 2>&1 &
    sleep 2
    
    is_bot_running && echo -e "  ${BGREEN}✓ Paper trading started (PID: $(get_bot_pid))${NC}" || echo -e "  ${RED}✗ Failed${NC}"
    read -p "  Press Enter..."
}

stop_bot() {
    is_bot_running || { echo -e "  ${YELLOW}Not running${NC}"; sleep 1; return; }
    
    local pid=$(get_bot_pid)
    echo -e "  ${BRED}Stopping (PID: $pid)...${NC}"
    kill $pid 2>/dev/null
    sleep 2
    is_bot_running && pkill -9 -f "python.*main.py" 2>/dev/null
    echo -e "  ${BGREEN}✓ Stopped${NC}"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════════════════════
#                              ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

run_backtest() {
    show_header
    echo ""
    echo -e "  ${BCYAN}╔═══════════════════════════════════╗${NC}"
    echo -e "  ${BCYAN}║${NC}      ${BWHITE}🔬 BACKTEST${NC}                ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}╚═══════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BWHITE}Select mode:${NC}"
    echo -e "    ${BWHITE}[1]${NC} Local Data (CSV)"
    echo -e "    ${BWHITE}[2]${NC} API Data (Live fetch)"
    echo -e "    ${BWHITE}[3]${NC} Multi-breakout Compare"
    echo ""
    read -p "  Choice [2]: " mode
    mode=${mode:-2}
    
    read -p "  Days [20]: " days
    days=${days:-20}
    
    activate_venv || return
    
    case $mode in
        1)
            echo -e "  ${BCYAN}Running local backtest...${NC}"
            PYTHONPATH="$SCRIPT_DIR" python3 "$SCRIPT_DIR/scripts/backtest/run_backtest.py" --days "$days"
            ;;
        2)
            echo -e "  ${BCYAN}Fetching API data & backtesting...${NC}"
            python3 "$SCRIPT_DIR/scripts/backtest/run_adaptive_api_backtest.py" --days "$days"
            ;;
        3)
            echo -e "  ${BCYAN}Comparing Static vs Multi-breakout...${NC}"
            python3 "$SCRIPT_DIR/scripts/backtest/run_adaptive_api_backtest.py" --compare --days "$days"
            ;;
    esac
    
    read -p "  Press Enter..."
}

run_compare() {
    show_header
    activate_venv || return
    echo -e "  ${BCYAN}Running Static vs Multi-breakout comparison...${NC}"
    python3 "$SCRIPT_DIR/scripts/backtest/run_adaptive_api_backtest.py" --compare --days 20
    read -p "  Press Enter..."
}

view_dashboard() {
    show_header
    activate_venv && python3 scripts/dashboard.py 2>/dev/null || echo "  Not available"
    read -p "  Press Enter..."
}

run_optimizer() {
    show_header
    read -p "  Iterations [50]: " n
    n=${n:-50}
    activate_venv && python3 scripts/optimizer.py --max-iter "$n" 2>/dev/null || echo "  Not available"
    read -p "  Press Enter..."
}

today_report() {
    show_header
    echo ""
    echo -e "  ${BCYAN}╔═══════════════════════════════════╗${NC}"
    echo -e "  ${BCYAN}║${NC}    ${BWHITE}📊 TODAY'S REPORT${NC}            ${BCYAN}║${NC}"
    echo -e "  ${BCYAN}╚═══════════════════════════════════╝${NC}"
    echo ""
    
    local today=$(date +%Y%m%d)
    local journal="$DATA_DIR/journal/trades_${today}.csv"
    
    if [ -f "$journal" ]; then
        echo -e "  ${BWHITE}Trades:${NC}"
        echo ""
        column -t -s',' "$journal" 2>/dev/null | head -20
        echo ""
        
        # Calculate totals
        local total=$(tail -n +2 "$journal" | wc -l)
        local wins=$(tail -n +2 "$journal" | grep -c "Target" || echo 0)
        local pnl=$(tail -n +2 "$journal" | awk -F',' '{sum+=$NF} END {printf "%.0f", sum}')
        
        echo -e "  ${DIM}────────────────────────────────────${NC}"
        echo -e "  📈 Total: ${BWHITE}$total${NC}  │  ✓ Wins: ${BGREEN}$wins${NC}  │  💰 P&L: ${BCYAN}₹$pnl${NC}"
    else
        echo -e "  ${YELLOW}No trades today${NC}"
    fi
    
    read -p "  Press Enter..."
}

# ═══════════════════════════════════════════════════════════════════════════════
#                              SYSTEM FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

view_logs() {
    show_header
    echo -e "  ${BBLUE}📜 LIVE LOGS${NC} ${DIM}(Ctrl+C to exit)${NC}"
    echo ""
    local today=$(date +%Y-%m-%d)
    [ -f "$LOG_DIR/$today/paper.log" ] && { tail -f "$LOG_DIR/$today/paper.log"; return; }
    [ -f "$LOG_DIR/$today/live.log" ] && { tail -f "$LOG_DIR/$today/live.log"; return; }
    [ -f "$LOG_DIR/trading.log" ] && { tail -f "$LOG_DIR/trading.log"; return; }
    echo "  No logs found"
    read -p "  Press Enter..."
}

edit_config() {
    command -v nano &>/dev/null && { nano "$CONFIG_FILE"; return; }
    command -v vim &>/dev/null && { vim "$CONFIG_FILE"; return; }
    show_header
    cat "$CONFIG_FILE"
    read -p "  Press Enter..."
}

run_tests() {
    show_header
    echo -e "  ${BBLUE}🧪 Running Tests...${NC}"
    activate_venv && python3 -m pytest tests/ -v --tb=short 2>/dev/null || echo "  Test suite not found"
    read -p "  Press Enter..."
}

clear_cache() {
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
    find "$LOG_DIR" -type f -mtime +7 -delete 2>/dev/null
    echo -e "  ${BGREEN}✓ Cache cleared${NC}"
    sleep 1
}

quick_stats() {
    show_header
    show_stats
    echo ""
    echo -e "  ${BWHITE}Recent Logs:${NC}"
    echo ""
    local today=$(date +%Y-%m-%d)
    [ -f "$LOG_DIR/$today/paper.log" ] && tail -10 "$LOG_DIR/$today/paper.log" || echo "  No activity"
    read -p "  Press Enter..."
}

view_history() {
    show_header
    echo -e "  ${BPURPLE}📈 TRADE HISTORY${NC}"
    echo ""
    ls -la "$DATA_DIR/journal/" 2>/dev/null | tail -10 || echo "  No history"
    echo ""
    ls -la "$LOG_DIR" 2>/dev/null | tail -5
    read -p "  Press Enter..."
}

show_help() {
    show_header
    echo ""
    echo -e "  ${BWHITE}KEYBOARD SHORTCUTS${NC}"
    echo ""
    echo -e "  ${BGREEN}Trading:${NC}"
    echo -e "    ${BWHITE}l${NC} - Start Live Trading (real money)"
    echo -e "    ${BWHITE}p${NC} - Start Paper Trading (simulation)"
    echo -e "    ${BWHITE}x${NC} - Stop running bot"
    echo -e "    ${BWHITE}t${NC} - Today's trading report"
    echo ""
    echo -e "  ${BCYAN}Analysis:${NC}"
    echo -e "    ${BWHITE}b${NC} - Run backtest"
    echo -e "    ${BWHITE}c${NC} - Compare static vs multi-breakout"
    echo -e "    ${BWHITE}d${NC} - Dashboard"
    echo -e "    ${BWHITE}o${NC} - Parameter optimizer"
    echo ""
    echo -e "  ${BBLUE}System:${NC}"
    echo -e "    ${BWHITE}v${NC} - View live logs"
    echo -e "    ${BWHITE}e${NC} - Edit config"
    echo -e "    ${BWHITE}r${NC} - Run tests"
    echo -e "    ${BWHITE}.${NC} - Clear cache"
    echo ""
    echo -e "  ${BPURPLE}Quick:${NC}"
    echo -e "    ${BWHITE}s${NC} - Quick stats"
    echo -e "    ${BWHITE}h${NC} - Trade history"
    echo -e "    ${BWHITE}?${NC} - This help"
    echo -e "    ${BWHITE}q${NC} - Quit"
    echo ""
    read -p "  Press Enter..."
}

# ═══════════════════════════════════════════════════════════════════════════════
#                                 MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

main() {
    activate_venv 2>/dev/null
    
    while true; do
        show_header
        show_stats
        show_menu
        
        echo -ne "  ${BWHITE}▶${NC} "
        read -r -n1 choice
        echo ""
        
        case $choice in
            # Trading
            l|L) start_live ;;
            p|P) start_paper ;;
            x|X) stop_bot ;;
            t|T) today_report ;;
            
            # Analysis
            b|B) run_backtest ;;
            c|C) run_compare ;;
            d|D) view_dashboard ;;
            o|O) run_optimizer ;;
            
            # System
            v|V) view_logs ;;
            e|E) edit_config ;;
            r|R) run_tests ;;
            .) clear_cache ;;
            
            # Quick
            s|S) quick_stats ;;
            h|H) view_history ;;
            \?) show_help ;;
            
            # Exit
            q|Q|0) 
                echo ""
                echo -e "  ${BCYAN}Bye! 👋${NC}"
                echo ""
                exit 0 
                ;;
            
            # Refresh (any other key)
            *) ;;
        esac
    done
}

# Handle Ctrl+C
trap 'echo ""; echo -e "  ${YELLOW}Press [q] to quit${NC}"; sleep 1' INT

# Run
main "$@"
