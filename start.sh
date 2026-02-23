#!/bin/bash
#═══════════════════════════════════════════════════════════════════════════════
#  N-STRUCTURE TRADING BOT - Ultra Animated Launcher
#  Sci-Fi Terminal Experience
#═══════════════════════════════════════════════════════════════════════════════

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
BOLD='\033[1m'
DIM='\033[2m'
BLINK='\033[5m'
NC='\033[0m'

# Extended colors (256)
ORANGE='\033[38;5;208m'
PINK='\033[38;5;213m'
LIME='\033[38;5;118m'
GOLD='\033[38;5;220m'
PURPLE='\033[38;5;141m'
TEAL='\033[38;5;45m'
GRAY='\033[38;5;245m'

# Background
BG_BLACK='\033[40m'
BG_RED='\033[41m'
BG_GREEN='\033[42m'
BG_BLUE='\033[44m'

# Clear screen and hide cursor
clear
tput civis

# Restore cursor on exit
trap 'tput cnorm; echo -e "\n${NC}"; exit' INT TERM EXIT

# Get terminal dimensions
COLS=$(tput cols)
ROWS=$(tput lines)
CENTER=$((COLS / 2))

#═══════════════════════════════════════════════════════════════════════════════
# ANIMATION FUNCTIONS
#═══════════════════════════════════════════════════════════════════════════════

# Position cursor
goto() {
    echo -ne "\033[${1};${2}H"
}

# Matrix rain effect
matrix_rain() {
    local duration=$1
    local chars="ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈｽﾀﾇﾍ0123456789"
    local end=$((SECONDS + duration))
    
    while [ $SECONDS -lt $end ]; do
        local col=$((RANDOM % COLS))
        local char="${chars:RANDOM % ${#chars}:1}"
        local color=$((RANDOM % 2))
        
        goto $((RANDOM % ROWS)) $col
        if [ $color -eq 0 ]; then
            echo -ne "${GREEN}${char}${NC}"
        else
            echo -ne "${LIME}${char}${NC}"
        fi
        sleep 0.01
    done
}

# Cyber loading bar
cyber_bar() {
    local text="$1"
    local width=50
    local bar_char="█"
    local empty_char="░"
    
    echo -ne "\n"
    for i in $(seq 1 100); do
        local filled=$((i * width / 100))
        local empty=$((width - filled))
        local bar=""
        
        # Create gradient bar
        for j in $(seq 1 $filled); do
            if [ $j -lt $((width / 3)) ]; then
                bar+="${CYAN}${bar_char}"
            elif [ $j -lt $((width * 2 / 3)) ]; then
                bar+="${BLUE}${bar_char}"
            else
                bar+="${PURPLE}${bar_char}"
            fi
        done
        
        for j in $(seq 1 $empty); do
            bar+="${GRAY}${empty_char}"
        done
        
        echo -ne "\r  ${WHITE}${text} ${NC}[${bar}${NC}] ${GOLD}${i}%${NC}  "
        sleep 0.015
    done
    echo -ne " ${GREEN}✓${NC}\n"
}

# Spinning loader
spin() {
    local text="$1"
    local duration=$2
    local spinners=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    local end=$((SECONDS + duration))
    local i=0
    
    while [ $SECONDS -lt $end ]; do
        echo -ne "\r  ${CYAN}${spinners[i]}${NC} ${text}"
        i=$(((i + 1) % ${#spinners[@]}))
        sleep 0.08
    done
    echo -ne "\r  ${GREEN}✓${NC} ${text}\n"
}

# Pulse text animation
pulse_text() {
    local text="$1"
    local colors=("$RED" "$ORANGE" "$YELLOW" "$GREEN" "$CYAN" "$BLUE" "$PURPLE")
    
    for _ in {1..3}; do
        for color in "${colors[@]}"; do
            echo -ne "\r  ${color}${BOLD}${text}${NC}   "
            sleep 0.05
        done
    done
    echo -ne "\r  ${GREEN}${BOLD}${text}${NC}   \n"
}

# Typewriter effect
typewriter() {
    local text="$1"
    local delay=${2:-0.03}
    
    for ((i=0; i<${#text}; i++)); do
        echo -n "${text:$i:1}"
        sleep $delay
    done
}

# Glitch text
glitch() {
    local text="$1"
    local glitch_chars="!@#$%^&*()_+-=[]{}|;':\",./<>?"
    
    for _ in {1..5}; do
        local glitched=""
        for ((i=0; i<${#text}; i++)); do
            if [ $((RANDOM % 4)) -eq 0 ]; then
                glitched+="${glitch_chars:RANDOM % ${#glitch_chars}:1}"
            else
                glitched+="${text:$i:1}"
            fi
        done
        echo -ne "\r  ${RED}${glitched}${NC}"
        sleep 0.05
    done
    echo -ne "\r  ${WHITE}${text}${NC}\n"
}

#═══════════════════════════════════════════════════════════════════════════════
# MAIN ANIMATION SEQUENCE
#═══════════════════════════════════════════════════════════════════════════════

# Matrix intro
matrix_rain 1
clear

# ASCII Art Banner with animation
echo ""
sleep 0.1

# Draw banner character by character
banner_lines=(
"${CYAN}    ███╗   ██╗${BLUE}    ███████╗████████╗██████╗ ██╗   ██╗ ██████╗████████╗${NC}"
"${CYAN}    ████╗  ██║${BLUE}    ██╔════╝╚══██╔══╝██╔══██╗██║   ██║██╔════╝╚══██╔══╝${NC}"
"${CYAN}    ██╔██╗ ██║${BLUE}    ███████╗   ██║   ██████╔╝██║   ██║██║        ██║   ${NC}"
"${CYAN}    ██║╚██╗██║${BLUE}    ╚════██║   ██║   ██╔══██╗██║   ██║██║        ██║   ${NC}"
"${CYAN}    ██║ ╚████║${BLUE}    ███████║   ██║   ██║  ██║╚██████╔╝╚██████╗   ██║   ${NC}"
"${CYAN}    ╚═╝  ╚═══╝${BLUE}    ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝   ╚═╝   ${NC}"
)

for line in "${banner_lines[@]}"; do
    echo -e "$line"
    sleep 0.08
done

echo ""
echo -e "${GRAY}    ─────────────────────────────────────────────────────────────────${NC}"
echo ""

# Animated subtitle
echo -ne "    "
typewriter "⚡ ALGORITHMIC OPTIONS TRADING SYSTEM v3.0 ⚡" 0.02
echo ""
echo ""

sleep 0.3

# System check animations
echo -e "  ${GOLD}┌──────────────────────────────────────────────────────────────────┐${NC}"
echo -e "  ${GOLD}│${NC}                    ${WHITE}${BOLD}SYSTEM INITIALIZATION${NC}                        ${GOLD}│${NC}"
echo -e "  ${GOLD}└──────────────────────────────────────────────────────────────────┘${NC}"
echo ""

spin "Initializing quantum trading core..." 1
spin "Loading neural pattern recognition..." 1
spin "Calibrating market sensors..." 1
spin "Establishing secure connection..." 1

echo ""

# Mode selection with fancy display
echo -e "  ${PURPLE}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "  ${PURPLE}║${NC}  ${BLINK}▶${NC}  ${WHITE}${BOLD}SELECT TRADING MODE${NC}                                          ${PURPLE}║${NC}"
echo -e "  ${PURPLE}╠══════════════════════════════════════════════════════════════════╣${NC}"
echo -e "  ${PURPLE}║${NC}                                                                  ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}    ${CYAN}[1]${NC} ${WHITE}📝 PAPER TRADING${NC}  ${DIM}- Safe simulation mode${NC}                ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}                                                                  ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}    ${RED}[2]${NC} ${WHITE}🔴 LIVE TRADING${NC}   ${DIM}- Real money (use caution!)${NC}           ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}                                                                  ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}    ${YELLOW}[3]${NC} ${WHITE}🧪 BACKTEST${NC}       ${DIM}- Test on historical data${NC}             ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}                                                                  ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}    ${GRAY}[q]${NC} ${WHITE}❌ EXIT${NC}                                                    ${PURPLE}║${NC}"
echo -e "  ${PURPLE}║${NC}                                                                  ${PURPLE}║${NC}"
echo -e "  ${PURPLE}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Blinking prompt
echo -ne "  ${CYAN}${BOLD}⟫${NC} "
read -n1 choice
echo ""
echo ""

case $choice in
    1)
        glitch "PAPER TRADING MODE SELECTED"
        echo ""
        cyber_bar "Loading Paper Trading Environment"
        
        echo ""
        echo -e "  ${GREEN}┌────────────────────────────────────────────────────────────────┐${NC}"
        echo -e "  ${GREEN}│${NC}  ${LIME}▶${NC} ${WHITE}Starting Paper Trading Bot...${NC}                               ${GREEN}│${NC}"
        echo -e "  ${GREEN}│${NC}  ${DIM}  Mode: Simulation | Capital: ₹50,000 | Risk: ₹1,950/trade${NC}  ${GREEN}│${NC}"
        echo -e "  ${GREEN}└────────────────────────────────────────────────────────────────┘${NC}"
        echo ""
        
        sleep 1
        tput cnorm
        cd "$(dirname "$0")"
        source venv/bin/activate 2>/dev/null || true
        exec python3 src/main.py --paper --polling
        ;;
    2)
        glitch "⚠️  LIVE TRADING MODE - REAL MONEY ⚠️"
        echo ""
        echo -e "  ${RED}${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "  ${RED}${BOLD}║${NC}  ${BLINK}⚠️${NC}  ${WHITE}WARNING: THIS WILL TRADE WITH REAL MONEY!${NC}               ${RED}${BOLD}║${NC}"
        echo -e "  ${RED}${BOLD}║${NC}      ${DIM}Ensure you understand the risks involved.${NC}              ${RED}${BOLD}║${NC}"
        echo -e "  ${RED}${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -ne "  ${YELLOW}Continue? (y/N): ${NC}"
        read -n1 confirm
        echo ""
        
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            cyber_bar "Activating Live Trading Systems"
            echo ""
            tput cnorm
            cd "$(dirname "$0")"
            source venv/bin/activate 2>/dev/null || true
            exec python3 src/main.py --polling
        else
            echo -e "  ${GREEN}✓${NC} Cancelled. Stay safe!"
        fi
        ;;
    3)
        glitch "BACKTEST MODE SELECTED"
        echo ""
        echo -ne "  ${CYAN}Enter date range (YYYY-MM-DD to YYYY-MM-DD): ${NC}"
        read daterange
        cyber_bar "Loading Historical Data"
        echo ""
        tput cnorm
        cd "$(dirname "$0")"
        source venv/bin/activate 2>/dev/null || true
        exec python3 scripts/backtest/run_backtest.py
        ;;
    q|Q)
        echo ""
        echo -e "  ${CYAN}Shutting down...${NC}"
        for i in {5..1}; do
            echo -ne "\r  ${GRAY}Goodbye in ${WHITE}$i${GRAY}...${NC}  "
            sleep 0.3
        done
        echo -e "\n\n  ${GREEN}💫 May your trades be profitable! 💫${NC}\n"
        exit 0
        ;;
    *)
        echo -e "  ${RED}Invalid option. Exiting...${NC}"
        exit 1
        ;;
esac
