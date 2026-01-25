"""
Order Manager Module

Handles order placement, modification, and tracking via Angel One SmartAPI.
Implements Stop-Limit orders with buffer logic for N-Structure entries.
"""

import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from broker.auth import AngelOneAuth, get_auth


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
    def from_api_response(cls, response: Dict[str, Any]) -> "OrderResponse":
        """Create from API response."""
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
        paper_mode: bool = False
    ):
        """
        Initialize order manager.
        
        Args:
            auth: Angel One authentication instance
            paper_mode: If True, simulate orders without placing
        """
        self._auth = auth or get_auth()
        self.paper_mode = paper_mode
        
        self._last_order_time: float = 0
        self._orders: Dict[str, Order] = {}
        self._paper_order_counter = 0
        
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
        
        return OrderResponse(
            success=True,
            order_id=order_id,
            message="Paper order simulated"
        )
    
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
