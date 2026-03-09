"""
Logging Utilities Module - Pro Trading Style

Clean, professional logging for live trading.
Minimal noise, maximum signal.
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM LOG FORMATS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_console(record):
    """Pro-style console format with dynamic colors."""
    level = record["level"].name
    
    # Color mapping - use simple colors, avoid nested bold
    colors = {
        "DEBUG": "dim",
        "INFO": "white", 
        "SUCCESS": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "white"
    }
    color = colors.get(level, "white")
    
    # Compact time
    time_str = record["time"].strftime("%H:%M:%S")
    
    # Level symbols (cleaner than text)
    symbols = {
        "DEBUG": "·",
        "INFO": "│",
        "SUCCESS": "✓",
        "WARNING": "⚠",
        "ERROR": "✗",
        "CRITICAL": "☠"
    }
    symbol = symbols.get(level, "│")
    
    # Format based on message type
    msg = record["message"]
    
    # Special formatting for candle logs (condense them)
    if "Candle" in msg and ("Index:" in msg or "Option" in msg):
        return f"<dim>{time_str}</dim> <cyan>{symbol}</cyan> {msg}\n"
    
    # Trade execution - highlight
    if any(x in msg for x in ["TRADE EXECUTED", "ENTRY", "EXIT", "SL HIT"]):
        return f"<{color}>{time_str} {symbol}</{color}> {msg}\n"
    
    # Default format
    return f"<dim>{time_str}</dim> <{color}>{symbol}</{color}> {msg}\n"


def setup_logging(
    log_dir: str = "logs",
    level: str = "INFO",
    retention: str = "7 days"
) -> None:
    """
    Configure simple logging - one file per day in date folder.
    
    Structure:
        logs/
        └── 2026-03-09/
            └── trading.log   ← All logs (info, error, trade) in one file
    
    Args:
        log_dir: Base directory for logs
        level: Logging level
        retention: Log retention period
    """
    from datetime import date
    
    # Create today's date folder
    today = date.today().isoformat()
    log_path = Path(log_dir) / today
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Remove default handler
    logger.remove()
    
    # Console handler - clean & minimal
    logger.add(
        sys.stderr,
        level=level,
        format=_format_console,
        colorize=True
    )
    
    # Single log file per day - ALL logs in one place
    logger.add(
        log_path / "trading.log",
        level="DEBUG",  # Capture everything
        format="{time:HH:mm:ss} | {level: <7} | {message}",
        rotation="00:00",  # New file at midnight
        retention=retention,
        compression="gz"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PRO LOGGING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def log_banner(title: str, char: str = "═") -> None:
    """Print a banner line for visual separation."""
    width = 60
    logger.opt(colors=True).info(f"<cyan>{char * width}</cyan>")
    logger.opt(colors=True).info(f"<white>  {title.upper()}</white>")
    logger.opt(colors=True).info(f"<cyan>{char * width}</cyan>")


def log_trade_entry(symbol: str, price: float, qty: int, sl: float, direction: str) -> None:
    """Log trade entry in pro style."""
    emoji = "📈" if direction == "CE" else "📉"
    logger.opt(colors=True).success(
        f"\n{'═' * 50}\n"
        f"<green>{emoji} TRADE ENTRY</green>\n"
        f"{'─' * 50}\n"
        f"  Symbol    : <cyan>{symbol}</cyan>\n"
        f"  Direction : <yellow>{direction}</yellow>\n"
        f"  Entry     : ₹{price:.2f}\n"
        f"  Quantity  : {qty}\n"
        f"  Stop Loss : <red>₹{sl:.2f}</red>\n"
        f"{'═' * 50}"
    )


def log_trade_exit(symbol: str, entry: float, exit_price: float, qty: int, pnl: float, reason: str) -> None:
    """Log trade exit in pro style."""
    is_profit = pnl >= 0
    emoji = "✅" if is_profit else "❌"
    color = "green" if is_profit else "red"
    sign = "+" if is_profit else ""
    
    logger.opt(colors=True).log(
        "SUCCESS" if is_profit else "WARNING",
        f"\n{'═' * 50}\n"
        f"<{color}>{emoji} TRADE EXIT - {reason.upper()}</{color}>\n"
        f"{'─' * 50}\n"
        f"  Symbol : <cyan>{symbol}</cyan>\n"
        f"  Entry  : ₹{entry:.2f}\n"
        f"  Exit   : ₹{exit_price:.2f}\n"
        f"  Qty    : {qty}\n"
        f"  P&L    : <{color}>{sign}₹{pnl:,.0f}</{color}>\n"
        f"{'═' * 50}"
    )


def log_candle(time_str: str, index: float, option: float, opt_type: str = "CE") -> None:
    """Log candle in compact format."""
    logger.info(f"🕯 {time_str} │ Nifty: {index:,.2f} │ {opt_type}: ₹{option:.2f}")


def log_signal(signal_type: str, details: str) -> None:
    """Log trading signal."""
    colors = {
        "BULLISH": "green",
        "BEARISH": "red",
        "NEUTRAL": "yellow"
    }
    color = colors.get(signal_type.upper(), "white")
    logger.opt(colors=True).info(f"<{color}>◉ {signal_type}</{color}> {details}")


def log_risk_alert(message: str) -> None:
    """Log risk management alert."""
    logger.opt(colors=True).warning(f"<yellow>⚠ RISK</yellow> {message}")


def log_system(message: str) -> None:
    """Log system message."""
    logger.opt(colors=True).info(f"<dim>▸ {message}</dim>")


def log_startup_banner(version: str = "1.0") -> None:
    """Print startup banner."""
    logger.opt(colors=True).info(
        f"\n<cyan>{'═' * 60}</cyan>\n"
        f"<white>   N-STRUCTURE TRADING BOT v{version}</white>\n"
        f"<cyan>{'─' * 60}</cyan>\n"
        f"<dim>   Algorithmic Options Trading System</dim>\n"
        f"<cyan>{'═' * 60}</cyan>\n"
    )


class StructuredLogger:
    """
    Structured logger for specific event types.
    
    Writes JSON Lines format for easy parsing and analysis.
    """
    
    def __init__(self, log_dir: str = "data/logs"):
        """
        Initialize structured logger.
        
        Args:
            log_dir: Directory for log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self._signal_file = self.log_dir / "signals.jsonl"
        self._order_file = self.log_dir / "orders.jsonl"
        self._state_file = self.log_dir / "states.jsonl"
        self._candle_file = self.log_dir / "candles.jsonl"
    
    def _write_jsonl(self, filepath: Path, data: Dict[str, Any]) -> None:
        """Write a JSON line to file."""
        data["logged_at"] = datetime.now().isoformat()
        
        with open(filepath, "a") as f:
            f.write(json.dumps(data) + "\n")
    
    def log_signal(
        self,
        signal_type: str,
        status: str,
        index_price: float,
        option_price: Optional[float] = None,
        entry_trigger: Optional[float] = None,
        n_structure: Optional[Dict] = None,
        reason: str = ""
    ) -> None:
        """
        Log a trading signal.
        
        Args:
            signal_type: Type of signal (breakout, pullback, entry, etc.)
            status: Signal status (detected, confirmed, triggered, etc.)
            index_price: Current index price
            option_price: Current option price
            entry_trigger: Entry trigger price
            n_structure: N-Structure data
            reason: Additional reason/notes
        """
        data = {
            "event": "signal",
            "type": signal_type,
            "status": status,
            "index_price": index_price,
            "option_price": option_price,
            "entry_trigger": entry_trigger,
            "n_structure": n_structure,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        
        self._write_jsonl(self._signal_file, data)
        logger.info(f"Signal: {signal_type} | {status} | {reason}")
    
    def log_order(
        self,
        action: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        status: str = "",
        error: str = ""
    ) -> None:
        """
        Log an order event.
        
        Args:
            action: Order action (place, modify, cancel, fill)
            order_id: Order ID
            symbol: Trading symbol
            side: BUY or SELL
            quantity: Order quantity
            order_type: Order type
            price: Order price
            trigger_price: Trigger price
            status: Order status
            error: Error message if any
        """
        data = {
            "event": "order",
            "action": action,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
            "trigger_price": trigger_price,
            "status": status,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        self._write_jsonl(self._order_file, data)
        
        if error:
            logger.error(f"Order {action}: {order_id} | {side} {quantity} {symbol} | ERROR: {error}")
        else:
            logger.info(f"Order {action}: {order_id} | {side} {quantity} {symbol} | {status}")
    
    def log_state_transition(
        self,
        from_state: str,
        to_state: str,
        reason: str = "",
        context: Optional[Dict] = None
    ) -> None:
        """
        Log FSM state transition.
        
        Args:
            from_state: Previous state
            to_state: New state
            reason: Transition reason
            context: Additional context data
        """
        data = {
            "event": "state_transition",
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "context": context,
            "timestamp": datetime.now().isoformat()
        }
        
        self._write_jsonl(self._state_file, data)
        logger.info(f"State: {from_state} -> {to_state} | {reason}")
    
    def log_candle(
        self,
        token: str,
        symbol: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int = 0,
        ema_9: Optional[float] = None,
        ema_15: Optional[float] = None
    ) -> None:
        """
        Log candle data with indicators.
        
        Args:
            token: Instrument token
            symbol: Symbol name
            timestamp: Candle timestamp
            open_: Open price
            high: High price
            low: Low price
            close: Close price
            volume: Volume
            ema_9: EMA(9) value
            ema_15: EMA(15) value
        """
        data = {
            "event": "candle",
            "token": token,
            "symbol": symbol,
            "candle_time": timestamp.isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "ema_9": ema_9,
            "ema_15": ema_15
        }
        
        self._write_jsonl(self._candle_file, data)
    
    def log_trade_complete(
        self,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        entry_time: datetime,
        exit_time: datetime,
        exit_reason: str,
        n_structure: Optional[Dict] = None
    ) -> None:
        """
        Log completed trade.
        
        Args:
            entry_price: Entry price
            exit_price: Exit price
            quantity: Trade quantity
            pnl: Profit/Loss
            entry_time: Entry timestamp
            exit_time: Exit timestamp
            exit_reason: Reason for exit
            n_structure: N-Structure data for the trade
        """
        data = {
            "event": "trade_complete",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": pnl,
            "pnl_percent": (exit_price - entry_price) / entry_price * 100,
            "entry_time": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "duration_minutes": (exit_time - entry_time).total_seconds() / 60,
            "exit_reason": exit_reason,
            "n_structure": n_structure,
            "timestamp": datetime.now().isoformat()
        }
        
        self._write_jsonl(self._signal_file, data)
        
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        logger.info(
            f"Trade Complete: Entry={entry_price:.2f} Exit={exit_price:.2f} "
            f"PnL={pnl_str} | {exit_reason}"
        )
    
    def log_risk_event(
        self,
        event_type: str,
        daily_pnl: float,
        trades_today: int,
        sl_hits: int,
        can_trade: bool,
        reason: str = ""
    ) -> None:
        """
        Log risk management event.
        
        Args:
            event_type: Event type (warning, blocked, etc.)
            daily_pnl: Daily P&L
            trades_today: Trades taken today
            sl_hits: Stop-loss hits today
            can_trade: Whether trading is allowed
            reason: Additional reason
        """
        data = {
            "event": "risk",
            "type": event_type,
            "daily_pnl": daily_pnl,
            "trades_today": trades_today,
            "sl_hits": sl_hits,
            "can_trade": can_trade,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        
        self._write_jsonl(self._state_file, data)
        
        if event_type == "blocked":
            logger.error(f"Risk Event: {event_type} | {reason}")
        else:
            logger.warning(f"Risk Event: {event_type} | {reason}")


# Singleton instance
_structured_logger: Optional[StructuredLogger] = None


def get_structured_logger(log_dir: str = "data/logs") -> StructuredLogger:
    """Get the global structured logger instance."""
    global _structured_logger
    if _structured_logger is None:
        _structured_logger = StructuredLogger(log_dir)
    return _structured_logger
