"""
Telegram Notifier Module

Sends trading notifications via Telegram bot:
- Trade entry/exit alerts
- SL hit notifications
- Re-entry notifications
- Daily summary
- Error alerts
"""

import os
import asyncio
from datetime import datetime, date
from typing import Optional, Dict, Any
from dataclasses import dataclass
import aiohttp
from loguru import logger


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str
    chat_id: str
    enabled: bool = True
    silent_hours: tuple = (22, 8)  # Silent between 10pm-8am


class TelegramNotifier:
    """
    Telegram notification sender.
    
    Usage:
        notifier = TelegramNotifier(bot_token="...", chat_id="...")
        await notifier.send_entry_alert(...)
        await notifier.send_sl_alert(...)
        await notifier.send_daily_summary(...)
    """
    
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True
    ):
        """
        Initialize Telegram notifier.
        
        Args:
            bot_token: Telegram bot token (or from TELEGRAM_BOT_TOKEN env)
            chat_id: Telegram chat ID (or from TELEGRAM_CHAT_ID env)
            enabled: Whether notifications are enabled
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and bool(self.bot_token) and bool(self.chat_id)
        
        if not self.enabled:
            logger.warning("Telegram notifications disabled (missing token or chat_id)")
    
    async def _send_message(
        self, 
        text: str, 
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ) -> bool:
        """
        Send message via Telegram API.
        
        Args:
            text: Message text (HTML formatted)
            parse_mode: Parse mode (HTML or Markdown)
            disable_notification: Send silently
            
        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False
        
        url = self.BASE_URL.format(token=self.bot_token)
        
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 200:
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"Telegram API error: {error}")
                        return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False
    
    def _is_silent_hour(self) -> bool:
        """Check if current hour is in silent period."""
        hour = datetime.now().hour
        silent_start, silent_end = 22, 8
        
        if silent_start > silent_end:
            return hour >= silent_start or hour < silent_end
        return silent_start <= hour < silent_end
    
    # === Trade Alerts ===
    
    async def send_entry_alert(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        is_reentry: bool = False
    ) -> bool:
        """
        Send trade entry notification.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            sl_price: Stop loss price
            quantity: Position quantity
            is_reentry: Whether this is a re-entry
        """
        trade_type = "🔄 RE-ENTRY" if is_reentry else "🟢 ENTRY"
        
        text = f"""
{trade_type} EXECUTED

<b>Symbol:</b> {symbol}
<b>Entry:</b> ₹{entry_price:.2f}
<b>SL:</b> ₹{sl_price:.2f}
<b>Qty:</b> {quantity}
<b>Risk:</b> ₹{(entry_price - sl_price) * quantity:,.0f}
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        return await self._send_message(text.strip())
    
    async def send_exit_alert(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        exit_reason: str
    ) -> bool:
        """
        Send trade exit notification.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            exit_price: Exit price
            quantity: Position quantity
            pnl: Profit/Loss in rupees
            exit_reason: Reason for exit
        """
        emoji = "✅" if pnl > 0 else "❌"
        pnl_sign = "+" if pnl > 0 else ""
        pnl_points = (exit_price - entry_price)
        
        text = f"""
{emoji} EXIT - {exit_reason.upper()}

<b>Symbol:</b> {symbol}
<b>Entry:</b> ₹{entry_price:.2f}
<b>Exit:</b> ₹{exit_price:.2f}
<b>P&L:</b> <b>{pnl_sign}₹{pnl:,.0f}</b> ({pnl_points:+.1f}pt)
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        return await self._send_message(text.strip())
    
    async def send_sl_hit_alert(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        quantity: int,
        sl_hits_today: int,
        max_sl: int,
        can_reenter: bool
    ) -> bool:
        """
        Send SL hit notification.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            sl_price: SL exit price
            quantity: Position quantity
            sl_hits_today: Total SL hits today
            max_sl: Maximum SL allowed
            can_reenter: Whether re-entry is possible
        """
        pnl = (sl_price - entry_price) * quantity
        
        reentry_status = "🔄 Watching for re-entry..." if can_reenter else "⛔ Re-entry not available"
        
        text = f"""
🛑 SL HIT ({sl_hits_today}/{max_sl})

<b>Symbol:</b> {symbol}
<b>Entry:</b> ₹{entry_price:.2f}
<b>SL Exit:</b> ₹{sl_price:.2f}
<b>Loss:</b> <b>₹{pnl:,.0f}</b>
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

{reentry_status}
"""
        return await self._send_message(text.strip())
    
    async def send_reentry_opportunity(
        self,
        symbol: str,
        hh_price: float,
        trigger_price: float,
        reentry_count: int
    ) -> bool:
        """
        Send re-entry opportunity notification.
        
        Args:
            symbol: Trading symbol
            hh_price: Higher High price
            trigger_price: Entry trigger price
            reentry_count: Re-entry attempt number
        """
        text = f"""
🔄 RE-ENTRY ARMED (#{reentry_count + 1})

<b>Symbol:</b> {symbol}
<b>HH Detected:</b> ₹{hh_price:.2f}
<b>Trigger:</b> ₹{trigger_price:.2f}
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        return await self._send_message(text.strip())
    
    # === Risk Alerts ===
    
    async def send_max_sl_reached(
        self,
        sl_hits: int,
        daily_pnl: float
    ) -> bool:
        """
        Send max SL reached alert.
        
        Args:
            sl_hits: Total SL hits
            daily_pnl: Daily P&L
        """
        text = f"""
⛔ MAX SL REACHED - TRADING STOPPED

<b>SL Hits Today:</b> {sl_hits}
<b>Daily P&L:</b> ₹{daily_pnl:,.0f}
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

No more trades will be executed today.
"""
        return await self._send_message(text.strip())
    
    async def send_daily_summary(
        self,
        trades_today: int,
        wins: int,
        losses: int,
        daily_pnl: float,
        sl_hits: int,
        reentries: int
    ) -> bool:
        """
        Send daily trading summary.
        
        Args:
            trades_today: Total trades
            wins: Winning trades
            losses: Losing trades
            daily_pnl: Total P&L
            sl_hits: Total SL hits
            reentries: Re-entries used
        """
        win_rate = (wins / trades_today * 100) if trades_today > 0 else 0
        emoji = "🟢" if daily_pnl >= 0 else "🔴"
        pnl_sign = "+" if daily_pnl >= 0 else ""
        
        text = f"""
📊 DAILY SUMMARY - {date.today().strftime('%d %b %Y')}

{emoji} <b>P&L:</b> {pnl_sign}₹{daily_pnl:,.0f}

<b>Total Trades:</b> {trades_today}
<b>Wins:</b> {wins} | <b>Losses:</b> {losses}
<b>Win Rate:</b> {win_rate:.0f}%

<b>SL Hits:</b> {sl_hits}/3
<b>Re-entries:</b> {reentries}/2

See you tomorrow! 👋
"""
        return await self._send_message(text.strip())
    
    # === System Alerts ===
    
    async def send_error_alert(
        self,
        error_type: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send error alert.
        
        Args:
            error_type: Type of error
            error_message: Error message
            context: Additional context
        """
        context_str = ""
        if context:
            context_str = "\n".join([f"<b>{k}:</b> {v}" for k, v in context.items()])
        
        text = f"""
🚨 ERROR ALERT

<b>Type:</b> {error_type}
<b>Message:</b> {error_message}
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

{context_str}
"""
        return await self._send_message(text.strip())
    
    async def send_bot_started(self, paper_mode: bool = False) -> bool:
        """Send bot started notification."""
        mode = "📝 PAPER MODE" if paper_mode else "💰 LIVE MODE"
        
        text = f"""
🤖 N-Structure Bot Started

<b>Mode:</b> {mode}
<b>Version:</b> 1.2.0
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

Ready to trade! 🚀
"""
        return await self._send_message(text.strip())
    
    async def send_bot_stopped(self, reason: str = "Normal shutdown") -> bool:
        """Send bot stopped notification."""
        text = f"""
🔴 Bot Stopped

<b>Reason:</b> {reason}
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
"""
        return await self._send_message(text.strip())
    
    async def send_tsl_update(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        current_price: float,
        phase: str
    ) -> bool:
        """
        Send TSL update notification (optional, can be verbose).
        
        Args:
            symbol: Trading symbol
            old_sl: Previous SL level
            new_sl: New SL level
            current_price: Current price
            phase: TSL phase (breakeven, structure, tight)
        """
        locked_profit = new_sl - old_sl if new_sl > old_sl else 0
        
        text = f"""
📈 TSL Update ({phase.upper()})

<b>Symbol:</b> {symbol}
<b>Old SL:</b> ₹{old_sl:.2f}
<b>New SL:</b> ₹{new_sl:.2f}
<b>Current:</b> ₹{current_price:.2f}
<b>Locked:</b> +₹{locked_profit:.2f}
"""
        # Send silently to not spam
        return await self._send_message(text.strip(), disable_notification=True)


# Singleton instance
_notifier: Optional[TelegramNotifier] = None


def get_telegram_notifier(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None
) -> TelegramNotifier:
    """Get the global Telegram notifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)
    return _notifier


def initialize_telegram(
    bot_token: str,
    chat_id: str,
    enabled: bool = True
) -> TelegramNotifier:
    """
    Initialize Telegram notifier with custom settings.
    
    Args:
        bot_token: Telegram bot token
        chat_id: Telegram chat ID
        enabled: Whether to enable notifications
        
    Returns:
        Initialized TelegramNotifier
    """
    global _notifier
    _notifier = TelegramNotifier(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=enabled
    )
    return _notifier


# Synchronous wrappers for non-async code
def send_entry_sync(
    symbol: str,
    entry_price: float,
    sl_price: float,
    quantity: int,
    is_reentry: bool = False
) -> None:
    """Synchronous entry alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(
        notifier.send_entry_alert(symbol, entry_price, sl_price, quantity, is_reentry)
    )


def send_exit_sync(
    symbol: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    pnl: float,
    exit_reason: str
) -> None:
    """Synchronous exit alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(
        notifier.send_exit_alert(symbol, entry_price, exit_price, quantity, pnl, exit_reason)
    )


def send_sl_hit_sync(
    symbol: str,
    entry_price: float,
    sl_price: float,
    quantity: int,
    sl_hits_today: int,
    max_sl: int,
    can_reenter: bool
) -> None:
    """Synchronous SL hit alert."""
    notifier = get_telegram_notifier()
    asyncio.create_task(
        notifier.send_sl_hit_alert(
            symbol, entry_price, sl_price, quantity,
            sl_hits_today, max_sl, can_reenter
        )
    )
