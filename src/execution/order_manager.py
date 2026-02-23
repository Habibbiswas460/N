"""
Order Manager Module

Handles order placement, modification, and tracking via Angel One SmartAPI.
Implements Stop-Limit orders with buffer logic for N-Structure entries.
"""

import time
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum

from loguru import logger

from broker.auth import AngelOneAuth, get_auth


# ═══════════════════════════════════════════════════════════════════════════════
# 💰 PAPER TRADING TRACKER - Simulates Capital & P&L for Paper Mode
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PaperPosition:
    """A paper trading position."""
    order_id: str
    symbol: str
    quantity: int
    entry_price: float
    entry_time: datetime
    current_price: float = 0.0
    
    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L."""
        if self.current_price > 0:
            return (self.current_price - self.entry_price) * self.quantity
        return 0.0


class PaperTradingTracker:
    """
    Tracks simulated capital and P&L for paper trading mode.
    
    Features:
    - Initial capital tracking
    - Position management (open/close)
    - Realized and unrealized P&L calculation
    - Daily reset with carry-forward
    - State persistence to JSON file
    """
    
    def __init__(
        self,
        initial_capital: float = 50000.0,
        state_file: Optional[str] = None
    ):
        """
        Initialize paper trading tracker.
        
        Args:
            initial_capital: Starting capital (default ₹50,000)
            state_file: Path to state file for persistence
        """
        self.initial_capital = initial_capital
        self.state_file = Path(state_file) if state_file else Path("data/paper_trading_state.json")
        
        # State
        self._balance = initial_capital
        self._positions: Dict[str, PaperPosition] = {}
        self._realized_pnl = 0.0
        self._trade_history: List[Dict[str, Any]] = []
        self._current_date = date.today()
        self._daily_pnl = 0.0
        self._daily_trades = 0
        
        # Load existing state
        self._load_state()
    
    def _load_state(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                
                saved_date = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
                
                # Check if new day - reset daily stats but keep balance
                if saved_date != date.today():
                    # Carry forward balance
                    self._balance = data.get('balance', self.initial_capital)
                    self._realized_pnl = data.get('total_realized_pnl', 0.0)
                    self._daily_pnl = 0.0
                    self._daily_trades = 0
                    self._positions = {}
                    logger.info(f"[PAPER] New day! Carry-forward balance: ₹{self._balance:,.2f}")
                else:
                    # Same day - restore full state
                    self._balance = data.get('balance', self.initial_capital)
                    self._realized_pnl = data.get('total_realized_pnl', 0.0)
                    self._daily_pnl = data.get('daily_pnl', 0.0)
                    self._daily_trades = data.get('daily_trades', 0)
                    
                    # Restore positions
                    for pos_data in data.get('positions', []):
                        pos = PaperPosition(
                            order_id=pos_data['order_id'],
                            symbol=pos_data['symbol'],
                            quantity=pos_data['quantity'],
                            entry_price=pos_data['entry_price'],
                            entry_time=datetime.fromisoformat(pos_data['entry_time']),
                            current_price=pos_data.get('current_price', 0.0)
                        )
                        self._positions[pos.order_id] = pos
                    
                logger.info(f"[PAPER] State loaded: Balance=₹{self._balance:,.2f}, Realized=₹{self._realized_pnl:,.2f}")
                
            except Exception as e:
                logger.warning(f"[PAPER] Could not load state: {e}. Starting fresh.")
                self._reset_to_initial()
        else:
            logger.info(f"[PAPER] No state file. Starting with ₹{self.initial_capital:,.2f}")
    
    def _save_state(self) -> None:
        """Save state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                'date': date.today().isoformat(),
                'balance': self._balance,
                'total_realized_pnl': self._realized_pnl,
                'daily_pnl': self._daily_pnl,
                'daily_trades': self._daily_trades,
                'positions': [
                    {
                        'order_id': pos.order_id,
                        'symbol': pos.symbol,
                        'quantity': pos.quantity,
                        'entry_price': pos.entry_price,
                        'entry_time': pos.entry_time.isoformat(),
                        'current_price': pos.current_price
                    }
                    for pos in self._positions.values()
                ]
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.error(f"[PAPER] Could not save state: {e}")
    
    def _reset_to_initial(self) -> None:
        """Reset to initial state."""
        self._balance = self.initial_capital
        self._positions = {}
        self._realized_pnl = 0.0
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._current_date = date.today()
    
    def open_position(
        self,
        order_id: str,
        symbol: str,
        quantity: int,
        entry_price: float
    ) -> bool:
        """
        Open a new paper position.
        
        Args:
            order_id: Order ID
            symbol: Trading symbol
            quantity: Position size
            entry_price: Entry price
            
        Returns:
            True if position opened successfully
        """
        # Check if we have enough margin (simplified: entry_price * quantity)
        margin_required = entry_price * quantity * 0.15  # 15% margin estimate
        
        if margin_required > self._balance:
            logger.warning(f"[PAPER] Insufficient margin! Required: ₹{margin_required:,.2f}, Available: ₹{self._balance:,.2f}")
            return False
        
        position = PaperPosition(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=datetime.now(),
            current_price=entry_price
        )
        
        self._positions[order_id] = position
        self._daily_trades += 1
        
        logger.info(f"[PAPER] Position opened: {symbol} x{quantity} @ ₹{entry_price:.2f}")
        self._save_state()
        return True
    
    def close_position(
        self,
        order_id: str,
        exit_price: float
    ) -> Optional[float]:
        """
        Close a paper position and realize P&L.
        
        Args:
            order_id: Order ID to close
            exit_price: Exit price
            
        Returns:
            Realized P&L or None if position not found
        """
        if order_id not in self._positions:
            logger.warning(f"[PAPER] Position {order_id} not found")
            return None
        
        position = self._positions.pop(order_id)
        pnl = (exit_price - position.entry_price) * position.quantity
        
        self._realized_pnl += pnl
        self._daily_pnl += pnl
        self._balance += pnl
        
        logger.info(
            f"[PAPER] Position closed: {position.symbol} @ ₹{exit_price:.2f} | "
            f"P&L: ₹{pnl:+,.2f} | Balance: ₹{self._balance:,.2f}"
        )
        
        self._save_state()
        return pnl
    
    def update_position_price(self, order_id: str, current_price: float) -> None:
        """Update current price for unrealized P&L calculation."""
        if order_id in self._positions:
            self._positions[order_id].current_price = current_price
    
    @property
    def balance(self) -> float:
        """Current balance."""
        return self._balance
    
    @property
    def available_margin(self) -> float:
        """Available margin (balance - margin used)."""
        margin_used = sum(
            pos.entry_price * pos.quantity * 0.15
            for pos in self._positions.values()
        )
        return self._balance - margin_used
    
    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(pos.unrealized_pnl for pos in self._positions.values())
    
    @property
    def realized_pnl(self) -> float:
        """Total realized P&L."""
        return self._realized_pnl
    
    @property
    def daily_pnl(self) -> float:
        """Today's P&L."""
        return self._daily_pnl
    
    @property
    def total_pnl(self) -> float:
        """Total P&L (realized + unrealized)."""
        return self._realized_pnl + self.unrealized_pnl
    
    @property
    def daily_trades(self) -> int:
        """Number of trades today."""
        return self._daily_trades
    
    @property
    def has_open_position(self) -> bool:
        """Check if any position is open."""
        return len(self._positions) > 0
    
    def get_position(self, order_id: str) -> Optional[PaperPosition]:
        """Get position by order ID."""
        return self._positions.get(order_id)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get paper trading summary."""
        return {
            'balance': self._balance,
            'initial_capital': self.initial_capital,
            'realized_pnl': self._realized_pnl,
            'unrealized_pnl': self.unrealized_pnl,
            'total_pnl': self.total_pnl,
            'daily_pnl': self._daily_pnl,
            'daily_trades': self._daily_trades,
            'return_pct': ((self._balance - self.initial_capital) / self.initial_capital) * 100,
            'open_positions': len(self._positions)
        }
    
    def log_summary(self) -> None:
        """Log paper trading summary."""
        summary = self.get_summary()
        logger.info(
            f"\n{'═'*50}\n"
            f"💰 PAPER TRADING SUMMARY\n"
            f"{'─'*50}\n"
            f"  Initial Capital: ₹{summary['initial_capital']:,.2f}\n"
            f"  Current Balance: ₹{summary['balance']:,.2f}\n"
            f"  Total Return:    {summary['return_pct']:+.2f}%\n"
            f"{'─'*50}\n"
            f"  Realized P&L:    ₹{summary['realized_pnl']:+,.2f}\n"
            f"  Unrealized P&L:  ₹{summary['unrealized_pnl']:+,.2f}\n"
            f"  Today's P&L:     ₹{summary['daily_pnl']:+,.2f}\n"
            f"  Today's Trades:  {summary['daily_trades']}\n"
            f"{'═'*50}"
        )


class OrderStatus(Enum):
    """Order status values."""
    PENDING = "pending"
    OPEN = "open"
    TRIGGER_PENDING = "trigger pending"
    COMPLETE = "complete"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    MODIFIED = "modified"
    UNKNOWN = "unknown"


class OrderType(Enum):
    """Order types."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOPLOSS_LIMIT = "STOPLOSS_LIMIT"
    STOPLOSS_MARKET = "STOPLOSS_MARKET"


class OrderVariety(Enum):
    """Order variety."""
    NORMAL = "NORMAL"
    STOPLOSS = "STOPLOSS"
    AMO = "AMO"


class ProductType(Enum):
    """Product type."""
    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    CARRYFORWARD = "CARRYFORWARD"


class TransactionType(Enum):
    """Transaction type."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderRequest:
    """Order request parameters."""
    symbol: str
    token: str
    exchange: str
    transaction_type: TransactionType
    quantity: int
    order_type: OrderType = OrderType.MARKET
    price: float = 0.0
    trigger_price: float = 0.0
    variety: OrderVariety = OrderVariety.NORMAL
    product_type: ProductType = ProductType.INTRADAY
    
    def to_api_params(self) -> Dict[str, Any]:
        """Convert to Angel One API parameters."""
        params = {
            "variety": self.variety.value,
            "tradingsymbol": self.symbol,
            "symboltoken": self.token,
            "transactiontype": self.transaction_type.value,
            "exchange": self.exchange,
            "ordertype": self.order_type.value,
            "producttype": self.product_type.value,
            "quantity": str(self.quantity),
            "duration": "DAY",
        }
        
        if self.price > 0:
            params["price"] = str(round(self.price, 2))
        else:
            params["price"] = "0"
            
        if self.trigger_price > 0:
            params["triggerprice"] = str(round(self.trigger_price, 2))
        else:
            params["triggerprice"] = "0"
            
        return params


@dataclass
class OrderResponse:
    """Order response from API."""
    success: bool
    order_id: str = ""
    message: str = ""
    error_code: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_api_response(cls, response) -> "OrderResponse":
        """Create from API response.
        
        Handles both dict and string responses for robustness.
        """
        # Handle non-dict responses (error strings, None, etc.)
        if not isinstance(response, dict):
            return cls(
                success=False,
                message=f"Invalid API response: {response}",
                error_code="INVALID_RESPONSE",
                raw_response={"error": str(response)}
            )
        
        if response.get("status"):
            return cls(
                success=True,
                order_id=response.get("data", {}).get("orderid", ""),
                message=response.get("message", "Order placed successfully"),
                raw_response=response
            )
        else:
            return cls(
                success=False,
                message=response.get("message", "Order failed"),
                error_code=response.get("errorcode", ""),
                raw_response=response
            )


@dataclass
class Order:
    """Order information."""
    order_id: str
    symbol: str
    token: str
    exchange: str
    transaction_type: TransactionType
    quantity: int
    price: float
    trigger_price: float
    status: OrderStatus
    order_type: OrderType
    variety: OrderVariety
    product_type: ProductType
    filled_quantity: int = 0
    average_price: float = 0.0
    placed_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @classmethod
    def from_api_order(cls, data: Dict[str, Any]) -> "Order":
        """Create from API order data."""
        status_map = {
            "open": OrderStatus.OPEN,
            "complete": OrderStatus.COMPLETE,
            "rejected": OrderStatus.REJECTED,
            "cancelled": OrderStatus.CANCELLED,
            "trigger pending": OrderStatus.TRIGGER_PENDING,
            "pending": OrderStatus.PENDING,
        }
        
        return cls(
            order_id=data.get("orderid", ""),
            symbol=data.get("tradingsymbol", ""),
            token=data.get("symboltoken", ""),
            exchange=data.get("exchange", ""),
            transaction_type=TransactionType(data.get("transactiontype", "BUY")),
            quantity=int(data.get("quantity", 0)),
            price=float(data.get("price", 0)),
            trigger_price=float(data.get("triggerprice", 0)),
            status=status_map.get(data.get("status", "").lower(), OrderStatus.UNKNOWN),
            order_type=OrderType(data.get("ordertype", "MARKET")),
            variety=OrderVariety(data.get("variety", "NORMAL")),
            product_type=ProductType(data.get("producttype", "INTRADAY")),
            filled_quantity=int(data.get("filledshares", 0)),
            average_price=float(data.get("averageprice", 0)),
        )


class OrderManager:
    """
    Order management for N-Structure trading.
    
    Features:
    - Stop-Limit order placement with buffer
    - Order modification and cancellation
    - Order status tracking
    - Rate limit compliance (9 orders/sec)
    """
    
    # Rate limit: 9 orders per second
    RATE_LIMIT_PER_SEC = 9
    MIN_ORDER_INTERVAL = 1.0 / RATE_LIMIT_PER_SEC
    
    def __init__(
        self,
        auth: Optional[AngelOneAuth] = None,
        paper_mode: bool = False,
        paper_capital: float = 50000.0
    ):
        """
        Initialize order manager.
        
        Args:
            auth: Angel One authentication instance
            paper_mode: If True, simulate orders without placing
            paper_capital: Initial capital for paper trading (default ₹50,000)
        """
        self._auth = auth or get_auth()
        self.paper_mode = paper_mode
        
        self._last_order_time: float = 0
        self._orders: Dict[str, Order] = {}
        self._paper_order_counter = 0
        
        # Initialize paper trading tracker if in paper mode
        self._paper_tracker: Optional[PaperTradingTracker] = None
        if paper_mode:
            self._paper_tracker = PaperTradingTracker(
                initial_capital=paper_capital,
                state_file="data/paper_trading_state.json"
            )
            logger.info(f"[PAPER] Capital tracker initialized: ₹{paper_capital:,.2f}")
    
    @property
    def paper_tracker(self) -> Optional[PaperTradingTracker]:
        """Get paper trading tracker (None if not in paper mode)."""
        return self._paper_tracker
        
    def _respect_rate_limit(self) -> None:
        """Wait if needed to respect rate limit."""
        elapsed = time.time() - self._last_order_time
        if elapsed < self.MIN_ORDER_INTERVAL:
            time.sleep(self.MIN_ORDER_INTERVAL - elapsed)
        self._last_order_time = time.time()
    
    def _get_smart_api(self):
        """Get SmartConnect instance."""
        if not self._auth.is_logged_in:
            self._auth.ensure_valid_session()
        return self._auth.smart_api
    
    def _round_to_tick(self, price: float, tick_size: float = 0.05) -> float:
        """Round price to nearest tick size."""
        return round(price / tick_size) * tick_size
    
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """
        Place an order.
        
        Args:
            request: Order request parameters
            
        Returns:
            OrderResponse with result
        """
        if self.paper_mode:
            return self._simulate_order(request)
        
        self._respect_rate_limit()
        
        try:
            api = self._get_smart_api()
            params = request.to_api_params()
            
            logger.info(f"Placing order: {params}")
            response = api.placeOrder(params)
            
            result = OrderResponse.from_api_response(response)
            
            if result.success:
                logger.success(f"Order placed: {result.order_id}")
            else:
                logger.error(f"Order failed: {result.message} ({result.error_code})")
                
            return result
            
        except Exception as e:
            logger.error(f"Order exception: {e}")
            return OrderResponse(
                success=False,
                message=str(e)
            )
    
    def _simulate_order(self, request: OrderRequest) -> OrderResponse:
        """Simulate order for paper trading."""
        self._paper_order_counter += 1
        order_id = f"PAPER_{self._paper_order_counter:06d}"
        
        logger.info(f"[PAPER] Order simulated: {order_id}")
        logger.info(f"[PAPER] {request.transaction_type.value} {request.quantity} {request.symbol}")
        logger.info(f"[PAPER] Type: {request.order_type.value}, Trigger: {request.trigger_price}, Price: {request.price}")
        
        # Log paper balance if tracker exists
        if self._paper_tracker:
            summary = self._paper_tracker.get_summary()
            logger.info(
                f"[PAPER] Balance: ₹{summary['balance']:,.2f} | "
                f"Daily P&L: ₹{summary['daily_pnl']:+,.2f} | "
                f"Trades: {summary['daily_trades']}"
            )
        
        return OrderResponse(
            success=True,
            order_id=order_id,
            message="Paper order simulated"
        )
    
    def paper_open_position(
        self,
        order_id: str,
        symbol: str,
        quantity: int,
        entry_price: float
    ) -> bool:
        """
        Open a paper trading position.
        
        Args:
            order_id: Order ID from simulated order
            symbol: Trading symbol
            quantity: Position size
            entry_price: Entry price
            
        Returns:
            True if position opened successfully
        """
        if not self._paper_tracker:
            logger.warning("[PAPER] Tracker not initialized")
            return False
        return self._paper_tracker.open_position(order_id, symbol, quantity, entry_price)
    
    def paper_close_position(
        self,
        order_id: str,
        exit_price: float
    ) -> Optional[float]:
        """
        Close a paper trading position.
        
        Args:
            order_id: Order ID to close
            exit_price: Exit price
            
        Returns:
            Realized P&L or None if position not found
        """
        if not self._paper_tracker:
            logger.warning("[PAPER] Tracker not initialized")
            return None
        return self._paper_tracker.close_position(order_id, exit_price)
    
    def paper_update_price(self, order_id: str, current_price: float) -> None:
        """Update current price for unrealized P&L."""
        if self._paper_tracker:
            self._paper_tracker.update_position_price(order_id, current_price)
    
    def log_paper_summary(self) -> None:
        """Log paper trading summary."""
        if self._paper_tracker:
            self._paper_tracker.log_summary()
    
    def place_entry_order(
        self,
        symbol: str,
        token: str,
        exchange: str,
        quantity: int,
        trigger_price: float,
        limit_price: float,
        product_type: ProductType = ProductType.INTRADAY
    ) -> OrderResponse:
        """
        Place entry order with stop-limit.
        
        For N-Structure: trigger = high + 1.5, limit = high + 2.0
        
        Args:
            symbol: Trading symbol
            token: Instrument token
            exchange: Exchange (NFO)
            quantity: Order quantity
            trigger_price: Trigger price (high + buffer)
            limit_price: Limit price (slightly higher than trigger)
            product_type: Product type
            
        Returns:
            OrderResponse
        """
        request = OrderRequest(
            symbol=symbol,
            token=token,
            exchange=exchange,
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            order_type=OrderType.STOPLOSS_LIMIT,
            price=self._round_to_tick(limit_price),
            trigger_price=self._round_to_tick(trigger_price),
            variety=OrderVariety.STOPLOSS,
            product_type=product_type
        )
        
        return self.place_order(request)
    
    def place_sl_order(
        self,
        symbol: str,
        token: str,
        exchange: str,
        quantity: int,
        trigger_price: float,
        limit_price: Optional[float] = None,
        product_type: ProductType = ProductType.INTRADAY
    ) -> OrderResponse:
        """
        Place stop loss order.
        
        Args:
            symbol: Trading symbol
            token: Instrument token
            exchange: Exchange
            quantity: Order quantity
            trigger_price: SL trigger price
            limit_price: SL limit price (uses trigger - 0.5 if None)
            product_type: Product type
            
        Returns:
            OrderResponse
        """
        if limit_price is None:
            limit_price = trigger_price - 0.5  # Slight buffer below trigger
            
        request = OrderRequest(
            symbol=symbol,
            token=token,
            exchange=exchange,
            transaction_type=TransactionType.SELL,
            quantity=quantity,
            order_type=OrderType.STOPLOSS_LIMIT,
            price=self._round_to_tick(limit_price),
            trigger_price=self._round_to_tick(trigger_price),
            variety=OrderVariety.STOPLOSS,
            product_type=product_type
        )
        
        return self.place_order(request)
    
    def place_market_exit(
        self,
        symbol: str,
        token: str,
        exchange: str,
        quantity: int,
        product_type: ProductType = ProductType.INTRADAY
    ) -> OrderResponse:
        """
        Place market exit order.
        
        Args:
            symbol: Trading symbol
            token: Instrument token
            exchange: Exchange
            quantity: Order quantity
            product_type: Product type
            
        Returns:
            OrderResponse
        """
        request = OrderRequest(
            symbol=symbol,
            token=token,
            exchange=exchange,
            transaction_type=TransactionType.SELL,
            quantity=quantity,
            order_type=OrderType.MARKET,
            variety=OrderVariety.NORMAL,
            product_type=product_type
        )
        
        return self.place_order(request)
    
    def modify_order(
        self,
        order_id: str,
        new_trigger_price: Optional[float] = None,
        new_price: Optional[float] = None,
        new_quantity: Optional[int] = None
    ) -> OrderResponse:
        """
        Modify an existing order.
        
        Args:
            order_id: Order ID to modify
            new_trigger_price: New trigger price
            new_price: New limit price
            new_quantity: New quantity
            
        Returns:
            OrderResponse
        """
        if self.paper_mode:
            logger.info(f"[PAPER] Order modified: {order_id}")
            return OrderResponse(success=True, order_id=order_id, message="Paper modify")
        
        self._respect_rate_limit()
        
        try:
            api = self._get_smart_api()
            
            # Get existing order details
            order = self.get_order(order_id)
            if not order:
                return OrderResponse(success=False, message="Order not found")
            
            params = {
                "variety": order.variety.value,
                "orderid": order_id,
                "ordertype": order.order_type.value,
                "producttype": order.product_type.value,
                "duration": "DAY",
                "quantity": str(new_quantity or order.quantity),
                "tradingsymbol": order.symbol,
                "symboltoken": order.token,
                "exchange": order.exchange,
            }
            
            if new_price is not None:
                params["price"] = str(self._round_to_tick(new_price))
            else:
                params["price"] = str(order.price)
                
            if new_trigger_price is not None:
                params["triggerprice"] = str(self._round_to_tick(new_trigger_price))
            else:
                params["triggerprice"] = str(order.trigger_price)
            
            logger.info(f"Modifying order {order_id}: trigger={new_trigger_price}, price={new_price}")
            response = api.modifyOrder(params)
            
            return OrderResponse.from_api_response(response)
            
        except Exception as e:
            logger.error(f"Modify order exception: {e}")
            return OrderResponse(success=False, message=str(e))
    
    def cancel_order(self, order_id: str, variety: str = "STOPLOSS") -> OrderResponse:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID to cancel
            variety: Order variety
            
        Returns:
            OrderResponse
        """
        if self.paper_mode:
            logger.info(f"[PAPER] Order cancelled: {order_id}")
            return OrderResponse(success=True, order_id=order_id, message="Paper cancel")
        
        self._respect_rate_limit()
        
        try:
            api = self._get_smart_api()
            
            response = api.cancelOrder(order_id, variety)
            result = OrderResponse.from_api_response(response)
            
            if result.success:
                logger.info(f"Order cancelled: {order_id}")
            else:
                logger.warning(f"Cancel failed: {result.message}")
                
            return result
            
        except Exception as e:
            logger.error(f"Cancel order exception: {e}")
            return OrderResponse(success=False, message=str(e))
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """
        Get order details.
        
        Args:
            order_id: Order ID
            
        Returns:
            Order if found
        """
        if self.paper_mode:
            return None
            
        try:
            api = self._get_smart_api()
            order_book = api.orderBook()
            
            if order_book.get("status"):
                orders = order_book.get("data", []) or []
                for order_data in orders:
                    if order_data.get("orderid") == order_id:
                        return Order.from_api_order(order_data)
                        
        except Exception as e:
            logger.error(f"Get order exception: {e}")
            
        return None
    
    def get_order_status(self, order_id: str) -> OrderStatus:
        """
        Get order status.
        
        Args:
            order_id: Order ID
            
        Returns:
            OrderStatus
        """
        order = self.get_order(order_id)
        return order.status if order else OrderStatus.UNKNOWN
    
    def get_all_orders(self) -> List[Order]:
        """Get all orders for today."""
        if self.paper_mode:
            return []
            
        try:
            api = self._get_smart_api()
            order_book = api.orderBook()
            
            if order_book.get("status"):
                orders_data = order_book.get("data", []) or []
                return [Order.from_api_order(o) for o in orders_data]
                
        except Exception as e:
            logger.error(f"Get orders exception: {e}")
            
        return []
    
    def is_order_filled(self, order_id: str) -> bool:
        """Check if order is filled."""
        status = self.get_order_status(order_id)
        return status == OrderStatus.COMPLETE
    
    def is_order_pending(self, order_id: str) -> bool:
        """Check if order is pending/trigger pending."""
        status = self.get_order_status(order_id)
        return status in [OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING, OrderStatus.PENDING]


# Singleton instance
_order_manager: Optional[OrderManager] = None


def get_order_manager(paper_mode: bool = False) -> OrderManager:
    """Get the global order manager instance."""
    global _order_manager
    if _order_manager is None:
        _order_manager = OrderManager(paper_mode=paper_mode)
    return _order_manager
