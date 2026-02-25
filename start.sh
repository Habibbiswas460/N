#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                                                                              ║
# ║         ██╗  ██╗██╗   ██╗██████╗ ██████╗ ██╗██████╗                          ║
# ║         ██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██║██╔══██╗                         ║
# ║         ███████║ ╚████╔╝ ██████╔╝██████╔╝██║██║  ██║                         ║
# ║         ██╔══██║  ╚██╔╝  ██╔══██╗██╔══██╗██║██║  ██║                         ║
# ║         ██║  ██║   ██║   ██████╔╝██║  ██║██║██████╔╝                         ║
# ║         ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝                          ║
# ║                                                                              ║
# ║              ADAPTIVE HYBRID TRADING SYSTEM - Control Center v3.0           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

VERSION="3.0.0"
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
cd "\$SCRIPT_DIR"

VENV_PATH="\$SCRIPT_DIR/venv"
MAIN_SCRIPT="\$SCRIPT_DIR/src/main.py"
LOG_DIR="\$SCRIPT_DIR/logs"
DATA_DIR="\$SCRIPT_DIR/data"
CONFIG_FILE="\$SCRIPT_DIR/config/settings.yaml"

# Colors
NC='\033[0m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
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

# Progress bar
progress_bar() {
    local duration=\$1
    local width=40
    for ((i=0; i<=100; i+=3)); do
        local filled=\$((i * width / 100))
        local empty=\$((width - filled))
        printf "\r  \033[0;36m[\033[1;32m%s\033[2m%s\033[0;36m]\033[0m \033[1;37m%3d%%\033[0m" \
            "\$(printf '%*s' \$filled | tr ' ' '█')" \
            "\$(printf '%*s' \$empty | tr ' ' '░')" \$i
        sleep \$(echo "scale=3; \$duration/33" | bc)
    done
    printf "\n"
}

# Pulse message
pulse_message() {
    local msg="\$1"
    for i in {1..2}; do
        printf "\r  \033[1;32m▶ %s\033[0m  " "\$msg"
        sleep 0.15
        printf "\r  \033[2m▷ %s\033[0m  " "\$msg"
        sleep 0.15
    done
    printf "\r  \033[1;32m✓ %s\033[0m  \n" "\$msg"
}

activate_venv() {
    if [ -d "\$VENV_PATH" ]; then
        source "\$VENV_PATH/bin/activate"
        return 0
    else
        echo -e "\${RED}❌ Virtual environment not found!\${NC}"
        return 1
    fi
}

get_bot_pid() {
    pgrep -f "python.*main.py" 2>/dev/null | head -1
}

is_bot_running() {
    [ -n "\$(get_bot_pid)" ]
}

get_market_status() {
    local hour=\$(date +%H)
    local minute=\$(date +%M)
    local time_num=\$((hour * 100 + minute))
    local day=\$(date +%u)
    
    if [ \$day -ge 6 ]; then echo "CLOSED"; return; fi
    if [ \$time_num -lt 915 ]; then echo "PRE-MARKET"
    elif [ \$time_num -ge 915 ] && [ \$time_num -lt 1530 ]; then echo "OPEN"
    else echo "CLOSED"; fi
}

show_header() {
    clear
    echo -e "\${BCYAN}"
    echo "  ╔══════════════════════════════════════════════════════════════════════╗"
    echo "  ║                                                                      ║"
    echo -e "  ║  \${BGREEN}█████╗ \${BCYAN}██████╗  \${BBLUE}█████╗ \${BPURPLE}██████╗ \${BRED}████████╗\${BYELLOW}██╗\${BGREEN}██╗   ██╗\${BCYAN}███████╗\${NC}\${BCYAN}  ║"
    echo -e "  ║ \${BGREEN}██╔══██╗\${BCYAN}██╔══██╗\${BBLUE}██╔══██╗\${BPURPLE}██╔══██╗\${BRED}╚══██╔══╝\${BYELLOW}██║\${BGREEN}██║   ██║\${BCYAN}██╔════╝\${NC}\${BCYAN}  ║"
    echo -e "  ║ \${BGREEN}███████║\${BCYAN}██║  ██║\${BBLUE}███████║\${BPURPLE}██████╔╝\${BRED}   ██║   \${BYELLOW}██║\${BGREEN}██║   ██║\${BCYAN}█████╗\${NC}\${BCYAN}    ║"
    echo -e "  ║ \${BGREEN}██╔══██║\${BCYAN}██║  ██║\${BBLUE}██╔══██║\${BPURPLE}██╔═══╝ \${BRED}   ██║   \${BYELLOW}██║\${BGREEN}╚██╗ ██╔╝\${BCYAN}██╔══╝\${NC}\${BCYAN}    ║"
    echo -e "  ║ \${BGREEN}██║  ██║\${BCYAN}██████╔╝\${BBLUE}██║  ██║\${BPURPLE}██║     \${BRED}   ██║   \${BYELLOW}██║\${BGREEN} ╚████╔╝ \${BCYAN}███████╗\${NC}\${BCYAN}  ║"
    echo -e "  ║ \${BGREEN}╚═╝  ╚═╝\${BCYAN}╚═════╝ \${BBLUE}╚═╝  ╚═╝\${BPURPLE}╚═╝     \${BRED}   ╚═╝   \${BYELLOW}╚═╝\${BGREEN}  ╚═══╝  \${BCYAN}╚══════╝\${NC}\${BCYAN}  ║"
    echo "  ║                                                                      ║"
    echo -e "  ║      \${BWHITE}HYBRID TRADING SYSTEM\${NC}\${BCYAN} │ \${BGREEN}Control Center v\${VERSION}\${NC}\${BCYAN}             ║"
    echo "  ╚══════════════════════════════════════════════════════════════════════╝"
    echo -e "\${NC}"
}

show_status_bar() {
    local market=\$(get_market_status)
    local bot_status="STOPPED" bot_color="\${RED}" market_color="\${RED}"
    
    is_bot_running && { bot_status="RUNNING"; bot_color="\${BGREEN}"; }
    case "\$market" in
        "OPEN") market_color="\${BGREEN}" ;;
        "PRE-MARKET") market_color="\${BYELLOW}" ;;
    esac
    
    echo -e "  \${DIM}┌─────────────────────────────────────────────────────────────────────────┐\${NC}"
    echo -e "  \${DIM}│\${NC}  📅 \${BWHITE}\$(date '+%Y-%m-%d')\${NC}  │  🕐 \${BCYAN}\$(date '+%H:%M:%S')\${NC}  │  📈 Market: \${market_color}\${market}\${NC}  │  🤖 Bot: \${bot_color}\${bot_status}\${NC}  \${DIM}│\${NC}"
    echo -e "  \${DIM}└─────────────────────────────────────────────────────────────────────────┘\${NC}"
    echo ""
}

show_live_stats() {
    echo -e "  \${BWHITE}╭───────────────────── 📊 LIVE STATUS ─────────────────────╮\${NC}"
    
    local capital="50,000" pnl="0" trades="0"
    if [ -f "\$DATA_DIR/paper_state.json" ]; then
        capital=\$(python3 -c "import json; d=json.load(open('\$DATA_DIR/paper_state.json')); print(f\"{d.get('capital', 50000):,.0f}\")" 2>/dev/null || echo "50,000")
        pnl=\$(python3 -c "import json; d=json.load(open('\$DATA_DIR/paper_state.json')); print(f\"{d.get('daily_pnl', 0):+,.0f}\")" 2>/dev/null || echo "0")
        trades=\$(python3 -c "import json; d=json.load(open('\$DATA_DIR/paper_state.json')); print(d.get('trades_today', 0))" 2>/dev/null || echo "0")
    fi
    
    local pnl_color="\${GREEN}"
    [[ "\$pnl" == "-"* ]] && pnl_color="\${RED}"
    
    echo -e "  \${DIM}│\${NC}   💰 Capital: \${BGREEN}₹\${capital}\${NC}  │  📈 P&L: \${pnl_color}₹\${pnl}\${NC}  │  🔢 Trades: \${BCYAN}\${trades}\${NC}   \${DIM}│\${NC}"
    echo -e "  \${BWHITE}╰──────────────────────────────────────────────────────────╯\${NC}"
    echo ""
}

show_menu() {
    echo -e "  \${BGREEN}┌─────────────────────── 🚀 TRADING ────────────────────────┐\${NC}"
    if is_bot_running; then
        echo -e "  \${BGREEN}│\${NC}    \${DIM}[1] Start Live Trading\${NC}      \${BRED}[3] 🛑 Stop Bot\${NC}        \${BGREEN}│\${NC}"
        echo -e "  \${BGREEN}│\${NC}    \${DIM}[2] Start Paper Trading\${NC}                               \${BGREEN}│\${NC}"
    else
        echo -e "  \${BGREEN}│\${NC}    \${BWHITE}[1]\${NC} 🔴 \${BRED}Start Live\${NC}          \${DIM}[3] Stop Bot\${NC}            \${BGREEN}│\${NC}"
        echo -e "  \${BGREEN}│\${NC}    \${BWHITE}[2]\${NC} 📝 \${BYELLOW}Start Paper\${NC}                                    \${BGREEN}│\${NC}"
    fi
    echo -e "  \${BGREEN}└──────────────────────────────────────────────────────────┘\${NC}"
    echo ""
    
    echo -e "  \${BCYAN}┌─────────────────────── 🔬 ANALYSIS ───────────────────────┐\${NC}"
    echo -e "  \${BCYAN}│\${NC}    \${BWHITE}[4]\${NC} 📊 Dashboard         \${BWHITE}[5]\${NC} 🔬 Backtest           \${BCYAN}│\${NC}"
    echo -e "  \${BCYAN}│\${NC}    \${BWHITE}[6]\${NC} ⚙️  Optimizer          \${BWHITE}[7]\${NC} 📈 Trade History      \${BCYAN}│\${NC}"
    echo -e "  \${BCYAN}└──────────────────────────────────────────────────────────┘\${NC}"
    echo ""
    
    echo -e "  \${BBLUE}┌─────────────────────── ⚙️  SYSTEM ────────────────────────┐\${NC}"
    echo -e "  \${BBLUE}│\${NC}    \${BWHITE}[8]\${NC} 📜 View Logs          \${BWHITE}[9]\${NC} 🧪 Run Tests          \${BBLUE}│\${NC}"
    echo -e "  \${BBLUE}│\${NC}    \${BWHITE}[10]\${NC} ⚙️ Settings           \${BWHITE}[11]\${NC} 🗑️  Clear Cache       \${BBLUE}│\${NC}"
    echo -e "  \${BBLUE}└──────────────────────────────────────────────────────────┘\${NC}"
    echo ""
    
    echo -e "  \${BPURPLE}┌─────────────────────── ⚡ QUICK ──────────────────────────┐\${NC}"
    echo -e "  \${BPURPLE}│\${NC}    \${BWHITE}[r]\${NC} 🔄 Refresh    \${BWHITE}[s]\${NC} 📊 Stats    \${BWHITE}[0/q]\${NC} 🚪 Exit     \${BPURPLE}│\${NC}"
    echo -e "  \${BPURPLE}└──────────────────────────────────────────────────────────┘\${NC}"
    echo ""
}

start_live() {
    show_header
    echo -e "  \${BG_RED}\${BWHITE}  ⚠️  WARNING: LIVE TRADING WITH REAL MONEY  \${NC}"
    echo ""
    read -p "  Type 'CONFIRM' to proceed: " confirm
    [ "\$confirm" != "CONFIRM" ] && { echo -e "  \${YELLOW}❌ Cancelled\${NC}"; sleep 1; return; }
    is_bot_running && { echo -e "  \${YELLOW}⚠️  Bot already running\${NC}"; sleep 2; return; }
    
    activate_venv || return
    pulse_message "Initializing Live Trading"
    
    mkdir -p "\$LOG_DIR/\$(date +%Y-%m-%d)"
    nohup python3 "\$MAIN_SCRIPT" --polling > "\$LOG_DIR/\$(date +%Y-%m-%d)/live.log" 2>&1 &
    local PID=\$!
    progress_bar 2
    
    ps -p \$PID > /dev/null 2>&1 && echo -e "  \${BGREEN}✓ Started! PID: \$PID\${NC}" || echo -e "  \${RED}❌ Failed\${NC}"
    read -p "  Press Enter..."
}

start_paper() {
    show_header
    echo -e "  \${BYELLOW}╔═══════════════════════════════════════╗\${NC}"
    echo -e "  \${BYELLOW}║\${NC}    \${BWHITE}📝 PAPER TRADING MODE\${NC}            \${BYELLOW}║\${NC}"
    echo -e "  \${BYELLOW}╚═══════════════════════════════════════╝\${NC}"
    echo ""
    
    is_bot_running && { echo -e "  \${YELLOW}⚠️  Bot already running\${NC}"; sleep 2; return; }
    
    activate_venv || return
    pulse_message "Initializing Paper Trading"
    
    mkdir -p "\$LOG_DIR/\$(date +%Y-%m-%d)"
    nohup python3 "\$MAIN_SCRIPT" --paper --polling > "\$LOG_DIR/\$(date +%Y-%m-%d)/paper.log" 2>&1 &
    local PID=\$!
    progress_bar 2
    
    ps -p \$PID > /dev/null 2>&1 && echo -e "  \${BGREEN}✓ Started! PID: \$PID\${NC}" || echo -e "  \${RED}❌ Failed\${NC}"
    read -p "  Press Enter..."
}

stop_bot() {
    show_header
    is_bot_running || { echo -e "  \${YELLOW}ℹ️  Bot not running\${NC}"; sleep 1; return; }
    
    local pid=\$(get_bot_pid)
    echo -e "  \${BRED}Stopping bot (PID: \$pid)...\${NC}"
    kill \$pid 2>/dev/null; sleep 2
    is_bot_running && pkill -9 -f "python.*main.py" 2>/dev/null
    echo -e "  \${BGREEN}✓ Stopped\${NC}"
    sleep 1
}

view_dashboard() {
    show_header
    echo -e "  \${BCYAN}📊 DASHBOARD\${NC}"
    activate_venv && python3 scripts/dashboard.py 2>/dev/null || echo "  Dashboard not available"
    read -p "  Press Enter..."
}

run_backtest() {
    show_header
    echo -e "  \${BCYAN}🔬 BACKTEST\${NC}"
    read -p "  Days [30]: " days; days=\${days:-30}
    activate_venv || return
    pulse_message "Running \$days day backtest"
    PYTHONPATH="\$SCRIPT_DIR" python3 run_backtest.py --days "\$days" 2>/dev/null || echo "  Backtest not available"
    read -p "  Press Enter..."
}

run_optimizer() {
    show_header
    echo -e "  \${BPURPLE}⚙️  OPTIMIZER\${NC}"
    read -p "  Iterations [50]: " n; n=\${n:-50}
    activate_venv && python3 scripts/optimizer.py --max-iter "\$n" 2>/dev/null || echo "  Not available"
    read -p "  Press Enter..."
}

view_history() {
    show_header
    echo -e "  \${BPURPLE}📈 TRADE HISTORY\${NC}"
    ls -la "\$LOG_DIR" 2>/dev/null | tail -10 || echo "  No history"
    read -p "  Press Enter..."
}

view_logs() {
    show_header
    echo -e "  \${BBLUE}📜 LIVE LOGS\${NC} (Ctrl+C to exit)"
    local today=\$(date +%Y-%m-%d)
    [ -f "\$LOG_DIR/\$today/paper.log" ] && tail -f "\$LOG_DIR/\$today/paper.log" && return
    [ -f "\$LOG_DIR/\$today/live.log" ] && tail -f "\$LOG_DIR/\$today/live.log" && return
    [ -f "\$LOG_DIR/trading.log" ] && tail -f "\$LOG_DIR/trading.log" && return
    echo "  No logs"; read -p "  Press Enter..."
}

run_tests() {
    show_header
    echo -e "  \${BBLUE}🧪 TESTS\${NC}"
    activate_venv && python3 -m pytest tests/ -v --tb=short 2>/dev/null || echo "  No tests"
    read -p "  Press Enter..."
}

edit_settings() {
    command -v nano &>/dev/null && nano "\$CONFIG_FILE" && return
    command -v vim &>/dev/null && vim "\$CONFIG_FILE" && return
    show_header; cat "\$CONFIG_FILE"; read -p "  Press Enter..."
}

clear_cache() {
    show_header
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
    find "\$LOG_DIR" -type f -mtime +7 -delete 2>/dev/null
    echo -e "  \${BGREEN}✓ Cache cleared!\${NC}"
    sleep 1
}

quick_stats() {
    show_header
    show_status_bar
    show_live_stats
    local today=\$(date +%Y-%m-%d)
    echo -e "  \${BWHITE}Recent Activity:\${NC}"
    [ -f "\$LOG_DIR/\$today/paper.log" ] && tail -5 "\$LOG_DIR/\$today/paper.log" || echo "  No recent activity"
    read -p "  Press Enter..."
}

main() {
    activate_venv 2>/dev/null
    while true; do
        show_header
        show_status_bar
        show_live_stats
        show_menu
        echo -ne "  \${BWHITE}▶ Select: \${NC}"
        read -r choice
        case \$choice in
            1) start_live ;; 2) start_paper ;; 3) stop_bot ;;
            4) view_dashboard ;; 5) run_backtest ;; 6) run_optimizer ;; 7) view_history ;;
            8) view_logs ;; 9) run_tests ;; 10) edit_settings ;; 11) clear_cache ;;
            r|R) continue ;; s|S) quick_stats ;;
            0|q|Q) echo -e "\n  \${BCYAN}Goodbye! 👋\${NC}\n"; exit 0 ;;
            *) echo -e "  \${RED}Invalid\${NC}"; sleep 0.5 ;;
        esac
    done
}

trap 'echo ""; echo -e "  \${YELLOW}Use [0] or [q] to exit\${NC}"; sleep 1' INT
main
