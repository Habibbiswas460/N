"""
Telegram Menu System - Adaptive Hybrid Trading Bot
===================================================
Menu-based Telegram bot that does NOT load live logs.
Provides status, commands and alerts via inline keyboard menus.
"""

import os
import asyncio
import json
from datetime import datetime, date
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
import aiohttp
from loguru import logger


@dataclass
class BotState:
    """Current bot state for status reporting."""
    is_running: bool = False
    paper_mode: bool = True
    capital: float = 50000.0
    daily_pnl: float = 0.0
    trades_today: int = 0
    wins: int = 0
    losses: int = 0
    current_position: Optional[str] = None
    position_entry: float = 0.0
    position_sl: float = 0.0
    position_qty: int = 0
    sl_hits: int = 0
    last_signal: Optional[str] = None
    last_signal_time: Optional[datetime] = None


class TelegramMenu:
    """
    Telegram Menu System.
    
    Features:
    - Inline keyboard menus
    - Status commands (no live log loading)
    - Trade alerts
    - Daily summary
    """
    
    BASE_URL = "https://api.telegram.org/bot{token}"
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and bool(self.bot_token) and bool(self.chat_id)
        
        self.state = BotState()
        self._command_handlers: Dict[str, Callable] = {}
        self._last_update_id = 0
        self._polling_task = None
        
        if not self.enabled:
            logger.warning("Telegram disabled (missing token/chat_id)")
        
        # Register default commands
        self._register_commands()
    
    def _register_commands(self):
        """Register command handlers."""
        self._command_handlers = {
            '/start': self._cmd_start,
            '/menu': self._cmd_menu,
            '/status': self._cmd_status,
            '/pnl': self._cmd_pnl,
            '/position': self._cmd_position,
            '/settings': self._cmd_settings,
            '/stop': self._cmd_stop,
            '/help': self._cmd_help,
        }
    
    # ═══════════════════════════════════════════════════════════
    # API Methods
    # ═══════════════════════════════════════════════════════════
    
    async def _api_call(self, method: str, data: Dict = None) -> Optional[Dict]:
        """Make Telegram API call."""
        if not self.enabled:
            return None
        
        url = f"{self.BASE_URL.format(token=self.bot_token)}/{method}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data or {}, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Telegram API error: {await resp.text()}")
                        return None
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return None
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Dict = None,
        disable_notification: bool = False
    ) -> bool:
        """Send message with optional keyboard."""
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification
        }
        if reply_markup:
            data["reply_markup"] = reply_markup
        
        result = await self._api_call("sendMessage", data)
        return result is not None and result.get("ok", False)
    
    async def answer_callback(self, callback_id: str, text: str = None) -> bool:
        """Answer callback query."""
        data = {"callback_query_id": callback_id}
        if text:
            data["text"] = text
        result = await self._api_call("answerCallbackQuery", data)
        return result is not None
    
    async def edit_message(
        self,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Dict = None
    ) -> bool:
        """Edit existing message."""
        data = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            data["reply_markup"] = reply_markup
        
        result = await self._api_call("editMessageText", data)
        return result is not None
    
    # ═══════════════════════════════════════════════════════════
    # Keyboard Builders
    # ═══════════════════════════════════════════════════════════
    
    def _main_menu_keyboard(self) -> Dict:
        """Build main menu inline keyboard."""
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Status", "callback_data": "status"},
                    {"text": "💰 P&L", "callback_data": "pnl"}
                ],
                [
                    {"text": "📈 Position", "callback_data": "position"},
                    {"text": "⚙️ Settings", "callback_data": "settings"}
                ],
                [
                    {"text": "📋 Today Summary", "callback_data": "summary"},
                    {"text": "🔄 Refresh", "callback_data": "refresh"}
                ],
                [
                    {"text": "🛑 Stop Bot", "callback_data": "stop_confirm"}
                ]
            ]
        }
    
    def _stop_confirm_keyboard(self) -> Dict:
        """Stop confirmation keyboard."""
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Yes, Stop", "callback_data": "stop_yes"},
                    {"text": "❌ Cancel", "callback_data": "menu"}
                ]
            ]
        }
    
    def _back_keyboard(self) -> Dict:
        """Back to menu keyboard."""
        return {
            "inline_keyboard": [
                [{"text": "◀️ Back to Menu", "callback_data": "menu"}]
            ]
        }
    
    # ═══════════════════════════════════════════════════════════
    # Command Handlers
    # ═══════════════════════════════════════════════════════════
    
    async def _cmd_start(self, **kwargs):
        """Handle /start command."""
        text = """
🤖 <b>Adaptive Hybrid Trading Bot</b>

Welcome! Use the menu below to control and monitor your bot.

<b>Quick Commands:</b>
/menu - Main menu
/status - Bot status
/pnl - Today's P&L
/position - Current position
/help - Help

Bot Version: v1.2.0
"""
        await self.send_message(text.strip(), reply_markup=self._main_menu_keyboard())
    
    async def _cmd_menu(self, **kwargs):
        """Show main menu."""
        mode = "📝 PAPER" if self.state.paper_mode else "💰 LIVE"
        status = "🟢 Running" if self.state.is_running else "🔴 Stopped"
        
        text = f"""
🎛️ <b>Main Menu</b>

{status} | {mode}
━━━━━━━━━━━━━━━━━━━━━━
📅 {date.today().strftime('%d %b %Y')}
💰 P&L: ₹{self.state.daily_pnl:+,.0f}
📊 Trades: {self.state.trades_today}

Select an option:
"""
        await self.send_message(text.strip(), reply_markup=self._main_menu_keyboard())
    
    async def _cmd_status(self, **kwargs):
        """Show bot status."""
        mode = "📝 PAPER" if self.state.paper_mode else "💰 LIVE"
        status = "🟢 Running" if self.state.is_running else "🔴 Stopped"
        pos_status = "📈 In Position" if self.state.current_position else "⏸️ No Position"
        
        text = f"""
📊 <b>Bot Status</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>Status:</b> {status}
<b>Mode:</b> {mode}
<b>Position:</b> {pos_status}

<b>Capital:</b> ₹{self.state.capital:,.0f}
<b>Today P&L:</b> ₹{self.state.daily_pnl:+,.0f}
<b>Trades:</b> {self.state.trades_today} ({self.state.wins}W/{self.state.losses}L)
<b>SL Hits:</b> {self.state.sl_hits}/3

<b>Last Signal:</b> {self.state.last_signal or 'None'}
<b>Updated:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    async def _cmd_pnl(self, **kwargs):
        """Show P&L details."""
        pnl = self.state.daily_pnl
        emoji = "🟢" if pnl >= 0 else "🔴"
        win_rate = (self.state.wins / self.state.trades_today * 100) if self.state.trades_today > 0 else 0
        
        text = f"""
💰 <b>P&L Summary</b>
━━━━━━━━━━━━━━━━━━━━━━
{emoji} <b>Today:</b> ₹{pnl:+,.0f}

<b>Stats:</b>
• Trades: {self.state.trades_today}
• Wins: {self.state.wins}
• Losses: {self.state.losses}
• Win Rate: {win_rate:.0f}%

<b>Capital:</b> ₹{self.state.capital:,.0f}
<b>ROI:</b> {(pnl / self.state.capital * 100):+.2f}%

📅 {date.today().strftime('%d %b %Y')}
"""
        await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    async def _cmd_position(self, **kwargs):
        """Show current position."""
        if self.state.current_position:
            unrealized = 0  # Would need LTP to calculate
            text = f"""
📈 <b>Current Position</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>Symbol:</b> {self.state.current_position}
<b>Entry:</b> ₹{self.state.position_entry:.2f}
<b>SL:</b> ₹{self.state.position_sl:.2f}
<b>Qty:</b> {self.state.position_qty}

<b>Risk:</b> ₹{abs(self.state.position_entry - self.state.position_sl) * self.state.position_qty:,.0f}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        else:
            text = """
⏸️ <b>No Active Position</b>
━━━━━━━━━━━━━━━━━━━━━━
Waiting for signal...

The bot will automatically enter when conditions are met.
"""
        await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    async def _cmd_settings(self, **kwargs):
        """Show current settings."""
        text = f"""
⚙️ <b>Settings</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>Mode:</b> {'Paper' if self.state.paper_mode else 'Live'}
<b>Capital:</b> ₹{self.state.capital:,.0f}
<b>Position Size:</b> {self.state.position_qty} qty

<b>Risk Management:</b>
• Max SL/Day: 3
• Re-entries: 2
• Risk/Trade: ₹1,300

<i>Edit settings.yaml to change</i>
"""
        await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    async def _cmd_stop(self, **kwargs):
        """Handle stop command."""
        text = """
🛑 <b>Stop Bot?</b>
━━━━━━━━━━━━━━━━━━━━━━
Are you sure you want to stop the trading bot?

⚠️ Any open positions will remain open.
"""
        await self.send_message(text.strip(), reply_markup=self._stop_confirm_keyboard())
    
    async def _cmd_help(self, **kwargs):
        """Show help."""
        text = """
❓ <b>Help</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>Commands:</b>
/start - Start bot interface
/menu - Main menu
/status - Bot status
/pnl - Today's P&L
/position - Current position
/settings - View settings
/stop - Stop bot
/help - This help

<b>Menu Buttons:</b>
Use the inline buttons for quick navigation.

<b>Alerts:</b>
The bot will automatically send:
• Entry alerts
• Exit alerts
• SL hit notifications
• Daily summary

<i>Need help? Contact support.</i>
"""
        await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    # ═══════════════════════════════════════════════════════════
    # Callback Handler
    # ═══════════════════════════════════════════════════════════
    
    async def handle_callback(self, callback_id: str, data: str, message_id: int):
        """Handle callback query from inline button."""
        await self.answer_callback(callback_id)
        
        handlers = {
            "menu": self._cmd_menu,
            "status": self._cmd_status,
            "pnl": self._cmd_pnl,
            "position": self._cmd_position,
            "settings": self._cmd_settings,
            "summary": self._send_daily_summary,
            "refresh": self._cmd_status,
            "stop_confirm": self._cmd_stop,
            "stop_yes": self._handle_stop_yes,
        }
        
        handler = handlers.get(data)
        if handler:
            await handler()
    
    async def _handle_stop_yes(self):
        """Handle confirmed stop."""
        self.state.is_running = False
        await self.send_message("🛑 <b>Bot stopping...</b>\n\nSending shutdown signal.")
        # The main bot should check state.is_running
    
    # ═══════════════════════════════════════════════════════════
    # Polling (Optional - for receiving commands)
    # ═══════════════════════════════════════════════════════════
    
    async def start_polling(self):
        """Start polling for updates (optional)."""
        if not self.enabled:
            return
        
        logger.info("Telegram polling started")
        self._polling_task = asyncio.create_task(self._poll_updates())
    
    async def stop_polling(self):
        """Stop polling."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
    
    async def _poll_updates(self):
        """Poll for updates."""
        while True:
            try:
                result = await self._api_call("getUpdates", {
                    "offset": self._last_update_id + 1,
                    "timeout": 30
                })
                
                if result and result.get("ok"):
                    for update in result.get("result", []):
                        self._last_update_id = update["update_id"]
                        await self._process_update(update)
                
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)
    
    async def _process_update(self, update: Dict):
        """Process single update."""
        # Handle commands
        if "message" in update:
            text = update["message"].get("text", "")
            if text.startswith("/"):
                cmd = text.split()[0].lower()
                handler = self._command_handlers.get(cmd)
                if handler:
                    await handler()
        
        # Handle callbacks
        elif "callback_query" in update:
            cb = update["callback_query"]
            await self.handle_callback(
                cb["id"],
                cb.get("data", ""),
                cb.get("message", {}).get("message_id", 0)
            )
    
    # ═══════════════════════════════════════════════════════════
    # Trade Alerts
    # ═══════════════════════════════════════════════════════════
    
    async def send_entry_alert(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        direction: str = "CE",
        is_reentry: bool = False
    ) -> bool:
        """Send trade entry notification."""
        self.state.current_position = symbol
        self.state.position_entry = entry_price
        self.state.position_sl = sl_price
        self.state.position_qty = quantity
        
        if is_reentry:
            trade_type = "🔄 RE-ENTRY"
        elif direction == "PE":
            trade_type = "🔴 PE ENTRY"
        else:
            trade_type = "🟢 CE ENTRY"
        
        risk = abs(entry_price - sl_price) * quantity
        
        text = f"""
{trade_type} EXECUTED

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction}
<b>Entry:</b> ₹{entry_price:.2f}
<b>SL:</b> ₹{sl_price:.2f}
<b>Qty:</b> {quantity}
<b>Risk:</b> ₹{risk:,.0f}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_exit_alert(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        exit_reason: str
    ) -> bool:
        """Send trade exit notification."""
        self.state.current_position = None
        self.state.daily_pnl += pnl
        self.state.trades_today += 1
        
        if pnl > 0:
            self.state.wins += 1
            emoji = "✅"
        else:
            self.state.losses += 1
            emoji = "❌"
        
        text = f"""
{emoji} EXIT - {exit_reason.upper()}

<b>Symbol:</b> {symbol}
<b>Entry:</b> ₹{entry_price:.2f}
<b>Exit:</b> ₹{exit_price:.2f}
<b>P&L:</b> <b>₹{pnl:+,.0f}</b>

<b>Today:</b> {self.state.trades_today} trades | ₹{self.state.daily_pnl:+,.0f}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_sl_hit_alert(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        sl_hits_today: int,
        max_sl: int,
        can_reenter: bool = True
    ) -> bool:
        """Send SL hit notification."""
        self.state.sl_hits = sl_hits_today
        pnl = (sl_price - entry_price) * quantity
        
        reentry_msg = "🔄 Watching for re-entry..." if can_reenter else "⛔ Max SL reached"
        
        text = f"""
🛑 SL HIT ({sl_hits_today}/{max_sl})

<b>Symbol:</b> {symbol}
<b>Entry:</b> ₹{entry_price:.2f}
<b>SL Exit:</b> ₹{sl_price:.2f}
<b>Loss:</b> <b>₹{pnl:,.0f}</b>

{reentry_msg}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_max_sl_reached(self, sl_hits: int, daily_pnl: float) -> bool:
        """Send max SL reached alert."""
        text = f"""
⛔ MAX SL REACHED - TRADING STOPPED

<b>SL Hits:</b> {sl_hits}
<b>Daily P&L:</b> ₹{daily_pnl:,.0f}

No more trades today.

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def _send_daily_summary(self, **kwargs) -> bool:
        """Send daily trading summary."""
        win_rate = (self.state.wins / self.state.trades_today * 100) if self.state.trades_today > 0 else 0
        emoji = "🟢" if self.state.daily_pnl >= 0 else "🔴"
        
        text = f"""
📊 DAILY SUMMARY - {date.today().strftime('%d %b %Y')}

{emoji} <b>P&L:</b> ₹{self.state.daily_pnl:+,.0f}

<b>Trades:</b> {self.state.trades_today}
<b>Wins:</b> {self.state.wins} | <b>Losses:</b> {self.state.losses}
<b>Win Rate:</b> {win_rate:.0f}%

<b>SL Hits:</b> {self.state.sl_hits}/3
<b>Capital:</b> ₹{self.state.capital:,.0f}
"""
        return await self.send_message(text.strip(), reply_markup=self._back_keyboard())
    
    async def send_daily_summary(
        self,
        trades_today: int,
        wins: int,
        losses: int,
        daily_pnl: float,
        sl_hits: int,
        reentries: int = 0
    ) -> bool:
        """Send daily summary with provided data."""
        self.state.trades_today = trades_today
        self.state.wins = wins
        self.state.losses = losses
        self.state.daily_pnl = daily_pnl
        self.state.sl_hits = sl_hits
        return await self._send_daily_summary()
    
    # ═══════════════════════════════════════════════════════════
    # System Alerts
    # ═══════════════════════════════════════════════════════════
    
    async def send_bot_started(self, paper_mode: bool = False) -> bool:
        """Send bot started notification."""
        self.state.is_running = True
        self.state.paper_mode = paper_mode
        
        mode = "📝 PAPER MODE" if paper_mode else "💰 LIVE MODE"
        
        text = f"""
🤖 <b>Bot Started</b>

<b>Mode:</b> {mode}
<b>Version:</b> v1.2.0

Ready to trade! 🚀

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip(), reply_markup=self._main_menu_keyboard())
    
    async def send_bot_stopped(self, reason: str = "Normal shutdown") -> bool:
        """Send bot stopped notification."""
        self.state.is_running = False
        
        text = f"""
🔴 <b>Bot Stopped</b>

<b>Reason:</b> {reason}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_error_alert(
        self,
        error_type: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Send error alert."""
        context_str = ""
        if context:
            context_str = "\n".join([f"• {k}: {v}" for k, v in context.items()])
        
        text = f"""
🚨 ERROR ALERT

<b>Type:</b> {error_type}
<b>Message:</b> {error_message}

{context_str}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_signal_alert(self, signal_type: str, confidence: float, details: str = "") -> bool:
        """Send signal detection alert."""
        self.state.last_signal = signal_type
        self.state.last_signal_time = datetime.now()
        
        emoji = "📈" if "CE" in signal_type.upper() else "📉"
        
        text = f"""
{emoji} SIGNAL DETECTED

<b>Type:</b> {signal_type}
<b>Confidence:</b> {confidence:.0%}
{f'<b>Details:</b> {details}' if details else ''}

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        return await self.send_message(text.strip())
    
    async def send_tsl_update(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        current_price: float,
        phase: str
    ) -> bool:
        """Send TSL update (silent)."""
        text = f"""
📈 TSL Update ({phase.upper()})

<b>Symbol:</b> {symbol}
<b>Old SL:</b> ₹{old_sl:.2f}
<b>New SL:</b> ₹{new_sl:.2f}
<b>Locked:</b> +₹{new_sl - old_sl:.2f}
"""
        return await self.send_message(text.strip(), disable_notification=True)
    
    # ═══════════════════════════════════════════════════════════
    # State Management
    # ═══════════════════════════════════════════════════════════
    
    def update_state(
        self,
        capital: float = None,
        daily_pnl: float = None,
        trades_today: int = None,
        wins: int = None,
        losses: int = None,
        sl_hits: int = None,
        is_running: bool = None,
        paper_mode: bool = None
    ):
        """Update bot state for status reporting."""
        if capital is not None:
            self.state.capital = capital
        if daily_pnl is not None:
            self.state.daily_pnl = daily_pnl
        if trades_today is not None:
            self.state.trades_today = trades_today
        if wins is not None:
            self.state.wins = wins
        if losses is not None:
            self.state.losses = losses
        if sl_hits is not None:
            self.state.sl_hits = sl_hits
        if is_running is not None:
            self.state.is_running = is_running
        if paper_mode is not None:
            self.state.paper_mode = paper_mode
    
    def should_stop(self) -> bool:
        """Check if stop was requested via Telegram."""
        return not self.state.is_running


# ═══════════════════════════════════════════════════════════════════
# Backward Compatibility - TelegramNotifier alias
# ═══════════════════════════════════════════════════════════════════

class TelegramNotifier(TelegramMenu):
    """Alias for backward compatibility."""
    
    async def _send_message(self, text: str, parse_mode: str = "HTML", disable_notification: bool = False) -> bool:
        """Backward compatible send method."""
        return await self.send_message(text, parse_mode, disable_notification=disable_notification)


# ═══════════════════════════════════════════════════════════════════
# Singleton & Factory Functions
# ═══════════════════════════════════════════════════════════════════

_notifier: Optional[TelegramMenu] = None


def get_telegram_notifier(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None
) -> TelegramMenu:
    """Get the global Telegram instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramMenu(bot_token=bot_token, chat_id=chat_id)
    return _notifier


def initialize_telegram(
    bot_token: str,
    chat_id: str,
    enabled: bool = True
) -> TelegramNotifier:
    """Initialize Telegram with custom settings."""
    global _notifier
    _notifier = TelegramNotifier(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=enabled
    )
    return _notifier


# Synchronous wrappers
def send_entry_sync(symbol: str, entry_price: float, sl_price: float, quantity: int, is_reentry: bool = False) -> None:
    """Sync entry alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(notifier.send_entry_alert(symbol, entry_price, sl_price, quantity, is_reentry=is_reentry))


def send_exit_sync(symbol: str, entry_price: float, exit_price: float, quantity: int, pnl: float, exit_reason: str) -> None:
    """Sync exit alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(notifier.send_exit_alert(symbol, entry_price, exit_price, quantity, pnl, exit_reason))


def send_sl_hit_sync(symbol: str, entry_price: float, sl_price: float, quantity: int, sl_hits_today: int, max_sl: int, can_reenter: bool) -> None:
    """Sync SL hit alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(notifier.send_sl_hit_alert(symbol, entry_price, sl_price, quantity, sl_hits_today, max_sl, can_reenter))
