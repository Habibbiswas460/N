"""
N-Structure Algorithmic Trading Bot - Main Entry Point

Orchestrates:
- 8:30 AM: Download instrument master, initialize auth
- 9:15 AM: Connect WebSocket, select ATM strike
- 9:16+ AM: Run trading loop with FSM
- 3:30 PM: Graceful shutdown
"""

import asyncio
import signal
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from broker.auth import AngelOneAuth
from data.instrument_master import InstrumentMaster, OptionType
from data.market_feed import MarketFeed, SubscriptionMode
from data.candle_builder import CandleAggregator, Candle
from data.synchronizer import CandleSynchronizer, SyncedCandlePair
from indicators.ema import EMASet
from indicators.n_structure import NStructureDetector, NStructure
from indicators.filters import CompositeFilter, VolumeAnalysis, TrendAnalysis
from core.state_store import StateStore
from core.state_machine import TradingStateMachine, TradingState
from execution.order_manager import OrderManager, OrderRequest, OrderType, TransactionType, ProductType
from execution.sl_manager import StopLossManager, SLStatus, initialize_sl_manager
from risk.risk_manager import RiskManager, RiskLimits, RiskEvent, initialize_risk_manager
from risk.position_reconciler import PositionReconciler
from utils.logger import setup_logging, get_structured_logger
from utils.telegram import TelegramNotifier, initialize_telegram

class TradingBot:
    """
    Main trading bot orchestrator.
    
    Coordinates all modules and runs the trading loop.
    """
    
    def __init__(self, config_path: str = "config/settings.yaml"):
        """
        Initialize the trading bot.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config: dict = {}
        self._shutdown_flag = False
        self._running = False
        
        # Core components (initialized in setup)
        self.auth: Optional[AngelOneAuth] = None
        self.instrument_master: Optional[InstrumentMaster] = None
        self.market_feed: Optional[MarketFeed] = None
        self.state_store: Optional[StateStore] = None
        self.fsm: Optional[TradingStateMachine] = None
        self.order_manager: Optional[OrderManager] = None
        self.sl_manager: Optional[StopLossManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.reconciler: Optional[PositionReconciler] = None
        self.telegram: Optional[TelegramNotifier] = None
        
        # Data processing
        self.index_candle_builder: Optional[CandleAggregator] = None
        self.option_candle_builder: Optional[CandleAggregator] = None
        self.synchronizer: Optional[CandleSynchronizer] = None
        
        # Indicators
        self.index_emas: Optional[EMASet] = None
        self.option_emas: Optional[EMASet] = None
        self.n_detector: Optional[NStructureDetector] = None
        self.composite_filter: Optional[CompositeFilter] = None
        
        # State
        self.current_index_token: Optional[str] = None
        self.current_option_token: Optional[str] = None
        self.current_option_symbol: Optional[str] = None
        self.paper_mode: bool = False
        
        # Structured logger
        self.slog = get_structured_logger()
    
    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        with open(self.config_path, "r") as f:
            self.config = yaml.safe_load(f)
        
        logger.info(f"Configuration loaded from {self.config_path}")
    
    async def setup(self, paper_mode: bool = False) -> None:
        """
        Initialize all components.
        
        Args:
            paper_mode: If True, don't place real orders
        """
        self.paper_mode = paper_mode
        
        # Load config
        self._load_config()
        
        # Initialize auth
        self.auth = AngelOneAuth()
        if not self.auth.login():
            raise RuntimeError("Failed to authenticate with Angel One")
        
        # Initialize instrument master
        self.instrument_master = InstrumentMaster(
            cache_dir=self.config.get("paths", {}).get("cache_dir", "data/cache")
        )
        
        # Initialize state store (SQLite) - initializes synchronously
        self.state_store = StateStore(
            db_path=self.config.get("paths", {}).get("state_db", "data/state.db")
        )
        
        # Initialize FSM - restores state in constructor
        self.fsm = TradingStateMachine(self.state_store)
        
        # Initialize risk manager (v1.2 - max SL only)
        risk_config = self.config.get("risk", {})
        self.risk_manager = initialize_risk_manager(
            lot_size=risk_config.get("lot_size", 65),
            num_lots=risk_config.get("num_lots", 4),
            sl_points=risk_config.get("sl_points", 10.0),
            max_sl_per_day=risk_config.get("max_sl_per_day", 3),
            max_reentries_per_day=risk_config.get("max_reentries_per_day", 2),
            cooldown_candles_normal=risk_config.get("cooldown_candles", 15),
            cooldown_candles_after_sl=risk_config.get("cooldown_after_sl", 30),
            capital=risk_config.get("paper_capital", 100000.0)
        )
        self.risk_manager.add_event_callback(self._on_risk_event)
        
        # Initialize order manager
        self.order_manager = OrderManager(
            auth=self.auth,
            paper_mode=paper_mode
        )
        
        # Initialize SL manager (v1.2 - structure-based TSL)
        exit_config = self.config.get("exit", {})
        trailing_config = exit_config.get("trailing", {})
        tsl_config = trailing_config.get("structure_tsl", {})
        tight_config = trailing_config.get("tight_trail", {})
        breath_config = exit_config.get("sl_breath", {})
        
        self.sl_manager = initialize_sl_manager(
            initial_sl_points=exit_config.get("initial_sl_points", 10.0),
            breakeven_trigger_points=trailing_config.get("breakeven_trigger_points", 8.0),
            tsl_buffer=tsl_config.get("tsl_buffer", 2.5),
            tight_trigger_points=tight_config.get("trigger_points", 20.0),
            tight_buffer=tight_config.get("buffer", 1.5),
            enable_breath_rule=breath_config.get("enabled", True)
        )
        
        # Initialize position reconciler
        self.reconciler = PositionReconciler(
            auth=self.auth,
            poll_interval=5.0,
            paper_mode=paper_mode
        )
        self.reconciler.add_mismatch_callback(self._on_position_mismatch)
        
        # Initialize N-Structure detector
        n_config = self.config.get("n_structure", {})
        self.n_detector = NStructureDetector(
            entry_buffer=n_config.get("buffer", 1.5),
            index_sideways_threshold=n_config.get("divergence_threshold", 0.3) / 100,  # Convert to decimal
            option_strength_threshold=n_config.get("divergence_threshold", 0.3) / 100
        )
        
        # Initialize Composite Filter (v1.3 - volume + trend + time)
        filter_config = self.config.get("filters", {})
        self.composite_filter = CompositeFilter(
            enable_volume_filter=filter_config.get("enable_volume", True),
            enable_trend_filter=filter_config.get("enable_trend", True),
            enable_time_filter=filter_config.get("enable_time", True),
            volume_lookback=filter_config.get("volume_lookback", 20),
            min_volume_ratio=filter_config.get("min_volume_ratio", 0.8)
        )
        logger.info(
            f"Composite Filter initialized | Volume: {filter_config.get('enable_volume', True)} | "
            f"Trend: {filter_config.get('enable_trend', True)} | Time: {filter_config.get('enable_time', True)}"
        )
        
        # Initialize Telegram notifier (v1.2)
        telegram_config = self.config.get("telegram", {})
        self.telegram = initialize_telegram(
            bot_token=telegram_config.get("bot_token", ""),
            chat_id=telegram_config.get("chat_id", ""),
            enabled=telegram_config.get("enabled", False)
        )
        
        logger.info(f"Bot initialized | Paper Mode: {paper_mode}")
    
    async def _fast_strike_selection(self, expiry, atm_strike) -> tuple:
        """
        Fast strike selection using binary search.
        
        Instead of scanning all strikes, uses binary search to find
        the strike with premium in ₹90-110 range quickly.
        
        Args:
            expiry: Option expiry date
            atm_strike: ATM strike price
            
        Returns:
            Tuple of (selected_option, premium)
        """
        strike_config = self.config.get("strike_selection", {})
        premium_min = strike_config.get("min_premium", 90.0)
        premium_max = strike_config.get("max_premium", 110.0)
        target_premium = (premium_min + premium_max) / 2  # ₹100 target
        
        # Get all CE options
        all_ce_options = self.instrument_master.get_nifty_options(
            expiry_date=expiry,
            option_type=OptionType.CALL
        )
        
        if not all_ce_options:
            raise RuntimeError("No NIFTY CE options found")
        
        # Sort by strike (higher strike = lower premium for CE)
        all_ce_options.sort(key=lambda x: x.strike)
        
        # Filter to strikes near ATM (±300 points) for faster search
        nearby_options = [o for o in all_ce_options if abs(o.strike - atm_strike) <= 300]
        if not nearby_options:
            nearby_options = all_ce_options  # Fallback to all
        
        logger.info(f"Fast strike selection (target: ₹{target_premium:.0f}, {len(nearby_options)} strikes near ATM)...")
        
        # Binary search on nearby options
        left, right = 0, len(nearby_options) - 1
        best_option = None
        best_premium = 0.0
        best_diff = float('inf')
        
        # Binary search with ~3-4 API calls
        iterations = 0
        while left <= right and iterations < 6:
            iterations += 1
            mid = (left + right) // 2
            opt = nearby_options[mid]
            
            premium = self.auth.get_ltp("NFO", opt.symbol, opt.token)
            if not premium:
                # Skip if no price
                right = mid - 1
                continue
            
            diff = abs(premium - target_premium)
            logger.info(f"  #{iterations} {int(opt.strike)} | ₹{premium:.2f} | diff: {diff:.1f}")
            
            # Track best match
            if premium_min <= premium <= premium_max:
                if diff < best_diff:
                    best_option = opt
                    best_premium = premium
                    best_diff = diff
                # Found in range, but continue to find closer to target
            
            # Binary search direction
            if premium > target_premium:
                # Premium too high, need higher strike (lower premium)
                left = mid + 1
            else:
                # Premium too low, need lower strike (higher premium)
                right = mid - 1
        
        # If no exact match, check neighbors
        if not best_option and iterations > 0:
            mid = (left + right) // 2
            for offset in [-1, 0, 1, 2]:
                idx = mid + offset
                if 0 <= idx < len(nearby_options):
                    opt = nearby_options[idx]
                    premium = self.auth.get_ltp("NFO", opt.symbol, opt.token)
                    if premium and premium_min <= premium <= premium_max:
                        best_option = opt
                        best_premium = premium
                        break
        
        if not best_option:
            raise RuntimeError(f"No strike found in ₹{premium_min}-₹{premium_max} range")
        
        logger.info(f"✓ Found in {iterations} API calls")
        return best_option, best_premium
    
    async def _setup_market_data(self) -> None:
        """Set up market data subscriptions after strike selection."""
        # Download instrument master if not loaded
        if not self.instrument_master.is_loaded:
            self.instrument_master.download()
        
        # NIFTY index token (hardcoded - correct for SmartAPI)
        self.current_index_token = "99926000"
        
        # Get LIVE NIFTY price using SmartAPI ltpData
        logger.info("Fetching live NIFTY price...")
        nifty_ltp = self.auth.get_ltp(
            exchange="NSE",
            symbol="Nifty 50",
            token=self.current_index_token
        )
        
        if not nifty_ltp:
            raise RuntimeError("Could not fetch live NIFTY price")
        
        # Calculate ATM strike (NIFTY has 50-point gap)
        atm_strike = round(nifty_ltp / 50) * 50
        logger.info(f"✓ NIFTY: ₹{nifty_ltp:.2f} | ATM: {atm_strike}")
        
        # Get nearest weekly expiry
        expiry = self.instrument_master.get_nearest_expiry("NIFTY")
        if not expiry:
            raise RuntimeError("Could not find nearest expiry")
        
        logger.info(f"Expiry: {expiry.strftime('%d%b%y').upper()}")
        
        # Fast strike selection using binary search
        selected_option, selected_premium = await self._fast_strike_selection(
            expiry=expiry,
            atm_strike=atm_strike
        )
        
        self.current_option_token = selected_option.token
        self.current_option_symbol = selected_option.symbol
        
        logger.success(
            f"✓ Selected: {self.current_option_symbol} "
            f"(Token: {self.current_option_token}, Strike: {int(selected_option.strike)}, "
            f"Premium: ₹{selected_premium:.2f})"
        )
        
        # Initialize candle builders
        self.index_candle_builder = CandleAggregator(
            timeframe_seconds=60
        )
        
        self.option_candle_builder = CandleAggregator(
            timeframe_seconds=60
        )
        
        # Initialize synchronizer with both tokens
        self.synchronizer = CandleSynchronizer(
            index_token=self.current_index_token,
            option_token=self.current_option_token
        )
        self.synchronizer.add_callback(self._on_synced_candles_sync)
        
        # Initialize EMAs
        ema_config = self.config.get("indicators", {}).get("ema", {})
        ema_periods = ema_config.get("periods", [9, 15])
        self.index_emas = EMASet(periods=ema_periods)
        self.option_emas = EMASet(periods=ema_periods)
        
        # Set up candle callbacks - route completed candles to synchronizer
        self.index_candle_builder.add_callback(self.synchronizer.on_candle)
        self.option_candle_builder.add_callback(self.synchronizer.on_candle)
        
        # Initialize market feed
        self.market_feed = MarketFeed(
            auth_token=self.auth.jwt_token,
            api_key=self.auth.api_key,
            client_code=self.auth.client_code,
            feed_token=self.auth.feed_token,
            mode=SubscriptionMode.QUOTE
        )
        self.market_feed.add_tick_callback(self._on_tick)
        
        # Connect and subscribe to both tokens
        self.market_feed.connect()
        self.market_feed.subscribe_index(self.current_index_token)
        self.market_feed.subscribe_option(self.current_option_token)
    
    def _on_tick(self, tick) -> None:
        """
        Handle incoming tick data.
        
        Routes tick to appropriate candle aggregator.
        """
        token = str(tick.token)
        
        if token == self.current_index_token:
            self.index_candle_builder.process_tick(tick)
        elif token == self.current_option_token:
            self.option_candle_builder.process_tick(tick)
    
    def _on_synced_candles_sync(self, pair: SyncedCandlePair) -> None:
        """
        Synchronous callback for synced candles.
        
        Wraps the async handler.
        """
        # Schedule the async handler to run
        asyncio.create_task(self._on_synced_candles(pair))
    
    async def _on_synced_candles(self, pair: SyncedCandlePair) -> None:
        """
        Process synchronized candle pair.
        
        This is the main trading logic entry point (v1.2 with re-entry).
        """
        try:
            # Update EMAs
            self.index_emas.update(pair.index_candle.close)
            self.option_emas.update(pair.option_candle.close)
            
            index_ema_9 = self.index_emas.get_value(9)
            index_ema_15 = self.index_emas.get_value(15)
            option_ema_9 = self.option_emas.get_value(9)
            
            # Log candles
            self.slog.log_candle(
                token=self.current_index_token,
                symbol="INDEX",
                timestamp=pair.timestamp,
                open_=pair.index_candle.open,
                high=pair.index_candle.high,
                low=pair.index_candle.low,
                close=pair.index_candle.close,
                ema_9=index_ema_9,
                ema_15=index_ema_15
            )
            
            self.slog.log_candle(
                token=self.current_option_token,
                symbol=self.current_option_symbol,
                timestamp=pair.timestamp,
                open_=pair.option_candle.open,
                high=pair.option_candle.high,
                low=pair.option_candle.low,
                close=pair.option_candle.close,
                ema_9=option_ema_9
            )
            
            # Create Candle object for FSM
            index_candle = Candle(
                timestamp=pair.timestamp,
                open=pair.index_candle.open,
                high=pair.index_candle.high,
                low=pair.index_candle.low,
                close=pair.index_candle.close,
                volume=pair.index_candle.volume
            )
            
            option_candle = Candle(
                timestamp=pair.timestamp,
                open=pair.option_candle.open,
                high=pair.option_candle.high,
                low=pair.option_candle.low,
                close=pair.option_candle.close,
                volume=pair.option_candle.volume
            )
            
            # Tick cooldown counter
            self.risk_manager.tick_cooldown()
            
            # Update composite filter with current data
            current_time = pair.timestamp.time() if pair.timestamp else datetime.now().time()
            filter_passed, filter_messages = self.composite_filter.check_all(
                volume=option_candle.volume,
                price=pair.index_candle.close,
                ema_fast=index_ema_9,
                ema_slow=index_ema_15,
                current_time=current_time
            )
            
            # Check risk before processing
            can_trade, reason = self.risk_manager.can_enter_trade()
            if not can_trade:
                logger.debug(f"Cannot trade: {reason}")
                # But still process if in position or pending re-entry
                if self.fsm.state not in [TradingState.IN_POSITION, TradingState.PENDING_REENTRY]:
                    return
            
            # Process through N-Structure detector
            n_structure = self.n_detector.process_candle(
                index_candle=index_candle,
                option_candle=option_candle,
                ema_9=index_ema_9,
                ema_15=index_ema_15
            )
            
            previous_state = self.fsm.state
            
            # Handle PENDING_REENTRY state (v1.2)
            if self.fsm.state == TradingState.PENDING_REENTRY:
                await self._handle_reentry_opportunity(option_candle)
            
            # Process FSM for other states
            elif self.fsm.state != TradingState.IN_POSITION:
                await self.fsm.process_candle(
                    index_candle=index_candle,
                    option_candle=option_candle,
                    ema_9=index_ema_9,
                    ema_15=index_ema_15,
                    n_structure=n_structure
                )
            
            # Log state transition
            if self.fsm.state != previous_state:
                self.slog.log_state_transition(
                    from_state=previous_state.value,
                    to_state=self.fsm.state.value,
                    reason=f"N-Structure: {n_structure is not None}",
                    context={
                        "index_close": pair.index_candle.close,
                        "option_close": pair.option_candle.close,
                        "ema_9": index_ema_9,
                        "ema_15": index_ema_15
                    }
                )
            
            # Handle entry signal (with filter check)
            if self.fsm.state == TradingState.ARMED and self.fsm.pending_entry:
                # Check if filters pass before entry
                if not filter_passed:
                    logger.info(f"Entry blocked by filters: {' | '.join(filter_messages)}")
                    # Stay in ARMED state, will retry next candle
                else:
                    is_reentry = self.fsm.is_reentry_trade
                    logger.info(f"Filters passed: {' | '.join(filter_messages)}")
                    await self._execute_entry(n_structure, option_candle, is_reentry=is_reentry)
            
            # Update SL if in position
            if self.fsm.state == TradingState.IN_POSITION:
                sl_status, sl_reason = self.sl_manager.update_on_tick(
                    current_price=option_candle.close,
                    candle=option_candle
                )
                
                # Check if SL triggered
                if sl_status == SLStatus.TRIGGERED:
                    await self._handle_sl_exit(option_candle.close, sl_reason)
        
        except Exception as e:
            logger.error(f"Error processing candles: {e}", exc_info=True)
    
    async def _handle_reentry_opportunity(self, option_candle: Candle) -> None:
        """
        Check for HH breakout re-entry opportunity (v1.2).
        
        Args:
            option_candle: Current option candle
        """
        # Track high after SL
        self.fsm.update_high_after_sl(option_candle.high)
        
        # Check if this candle breaks above previous high (HH)
        sl_exit_price = self.fsm.context.sl_exit_price
        last_high = self.fsm.context.last_high_after_sl
        
        # New HH detected
        if option_candle.high > last_high:
            # Check if HH is significantly above SL exit (min 2 points gap)
            min_gap = self.config.get("reentry", {}).get("min_hh_gap_points", 2.0)
            
            if option_candle.high > sl_exit_price + min_gap:
                # Check candle quality (relaxed for re-entry)
                candle_range = option_candle.high - option_candle.low
                candle_body = abs(option_candle.close - option_candle.open)
                body_pct = (candle_body / candle_range * 100) if candle_range > 0 else 0
                
                min_body_pct = self.config.get("reentry", {}).get("min_candle_body_pct", 40)
                min_range = self.config.get("reentry", {}).get("min_candle_range", 2.0)
                
                if body_pct >= min_body_pct and candle_range >= min_range:
                    # HH Breakout detected - arm for re-entry
                    entry_buffer = self.config.get("entry", {}).get("buffer_points", 1.5)
                    entry_trigger = option_candle.high + entry_buffer
                    
                    logger.info(
                        f"HH Breakout for re-entry! HH={option_candle.high:.2f}, "
                        f"SL Exit was {sl_exit_price:.2f}, Trigger={entry_trigger:.2f}"
                    )
                    
                    self.fsm.on_reentry_hh_detected(
                        hh_price=option_candle.high,
                        entry_trigger=entry_trigger
                    )
    
    async def _handle_sl_exit(self, exit_price: float, reason: str) -> None:
        """
        Handle SL exit with re-entry consideration (v1.2).
        
        Args:
            exit_price: Exit price
            reason: SL trigger reason
        """
        # Calculate P&L
        entry_price = self.fsm.context.entry_price
        quantity = self.risk_manager.get_position_size()
        pnl_points = exit_price - entry_price
        pnl = pnl_points * quantity
        
        # Record trade with risk manager
        is_reentry = self.fsm.is_reentry_trade
        self.risk_manager.record_trade(
            pnl=pnl,
            pnl_points=pnl_points,
            exit_reason=reason,
            entry_price=entry_price,
            exit_price=exit_price,
            is_reentry=is_reentry
        )
        
        # Log
        logger.warning(
            f"SL Exit: {exit_price:.2f} | PnL: ₹{pnl:.0f} ({pnl_points:+.1f}pt) | "
            f"Reason: {reason} | Re-entry: {is_reentry}"
        )
        
        # Check if re-entry is allowed
        can_reenter, reentry_reason = self.risk_manager.can_reenter()
        
        # Send Telegram SL alert
        if self.telegram:
            await self.telegram.send_sl_hit_alert(
                symbol=self.current_option_symbol,
                entry_price=entry_price,
                sl_price=exit_price,
                quantity=quantity,
                sl_hits_today=self.risk_manager.sl_hits_today,
                max_sl=self.risk_manager.limits.max_sl_per_day,
                can_reenter=can_reenter
            )
        
        # Transition FSM
        self.fsm.on_sl_hit(
            exit_price=exit_price,
            can_reenter=can_reenter,
            max_reentries=self.risk_manager.limits.max_reentries_per_day
        )
        
        # Clear reconciler position
        self.reconciler.clear_bot_position()
        
        # Reset SL manager
        self.sl_manager.reset()
    
    async def _execute_entry(
        self,
        n_structure: Optional[NStructure],
        option_candle: Candle,
        is_reentry: bool = False
    ) -> None:
        """
        Execute entry order (v1.2 with re-entry support).
        
        Args:
            n_structure: N-Structure data (may be None for re-entry)
            option_candle: Current option candle
            is_reentry: Whether this is a re-entry trade
        """
        # Get entry trigger
        if is_reentry:
            entry_trigger = self.fsm.context.reentry_hh_trigger
        elif n_structure:
            entry_config = self.config.get("entry", {})
            buffer = entry_config.get("buffer_points", 1.5)
            entry_trigger = n_structure.breakout_high + buffer
        else:
            return
        
        # Get fixed quantity from risk manager
        quantity = self.risk_manager.get_position_size()
        
        order_request = OrderRequest(
            symbol=self.current_option_symbol,
            token=self.current_option_token,
            exchange="NFO",
            transaction_type=TransactionType.BUY,
            quantity=quantity,
            order_type=OrderType.STOPLOSS_LIMIT,
            price=entry_trigger + 1.0,  # Limit price above trigger
            trigger_price=entry_trigger,
            product_type=ProductType.INTRADAY
        )
        
        # Log signal
        trade_type = "RE-ENTRY" if is_reentry else "ENTRY"
        self.slog.log_signal(
            signal_type=trade_type.lower(),
            status="triggered",
            index_price=option_candle.close,
            option_price=option_candle.close,
            entry_trigger=entry_trigger,
            n_structure={
                "breakout_high": n_structure.breakout_high if n_structure else self.fsm.context.last_high_after_sl,
                "is_reentry": is_reentry,
                "reentry_count": self.fsm.reentry_count
            },
            reason=f"{trade_type} triggered"
        )
        
        # Place order (synchronous)
        response = self.order_manager.place_order(order_request)
        
        if response.success:
            # Set up SL
            exit_config = self.config.get("exit", {})
            sl_points = exit_config.get("initial_sl_points", 10.0)
            sl_price = entry_trigger - sl_points
            
            self.sl_manager.initialize_sl(
                symbol=self.current_option_symbol,
                token=self.current_option_token,
                exchange="NFO",
                quantity=quantity,
                entry_price=entry_trigger
            )
            
            # Update FSM
            if is_reentry:
                self.fsm.on_reentry_executed(
                    entry_price=entry_trigger,
                    initial_sl=sl_price
                )
            else:
                self.fsm.on_entry_triggered(
                    entry_price=entry_trigger,
                    initial_sl=sl_price
                )
            
            # Set position for reconciliation
            self.reconciler.set_bot_position(
                token=self.current_option_token,
                quantity=quantity
            )
            
            self.slog.log_order(
                action="place",
                order_id=response.order_id,
                symbol=self.current_option_symbol,
                side="BUY",
                quantity=quantity,
                order_type="STOPLOSS_LIMIT",
                trigger_price=entry_trigger,
                status="success",
                is_reentry=is_reentry
            )
            
            logger.success(
                f"{trade_type} executed: {self.current_option_symbol} @ {entry_trigger:.2f}, "
                f"Qty={quantity}, SL={sl_price:.2f}"
            )
            
            # Send Telegram entry alert
            if self.telegram:
                await self.telegram.send_entry_alert(
                    symbol=self.current_option_symbol,
                    entry_price=entry_trigger,
                    sl_price=sl_price,
                    quantity=quantity,
                    is_reentry=is_reentry
                )
        else:
            self.slog.log_order(
                action="place",
                order_id="",
                symbol=self.current_option_symbol,
                side="BUY",
                quantity=quantity,
                order_type="STOPLOSS_LIMIT",
                trigger_price=entry_trigger,
                status="failed",
                error=response.message
            )
            
            logger.error(f"{trade_type} failed: {response.message}")
    
    def _on_risk_event(self, event: RiskEvent, status) -> None:
        """Handle risk events (v1.2)."""
        logger.warning(f"Risk Event [{event.value}]: SL Hits={status.sl_hits_today}/{self.risk_manager.limits.max_sl_per_day}")
        self.slog.log_risk_event(
            event_type=event.value,
            daily_pnl=status.daily_pnl,
            trades_today=status.trades_today,
            sl_hits=status.sl_hits_today,
            can_trade=status.can_trade,
            reason=status.block_reason if status.block_reason else event.value
        )
        
        # Telegram alert for max SL reached
        if event == RiskEvent.MAX_SL_REACHED and self.telegram:
            asyncio.create_task(
                self.telegram.send_max_sl_reached(
                    sl_hits=status.sl_hits_today,
                    daily_pnl=status.daily_pnl
                )
            )
    
    def _on_position_mismatch(self, result) -> None:
        """Handle position mismatch."""
        logger.error(f"Position mismatch! Bot: {result.bot_position_qty}, Broker: {result.broker_position_qty}")
        
        # Telegram alert for position mismatch
        if self.telegram:
            asyncio.create_task(
                self.telegram.send_error_alert(
                    error_type="Position Mismatch",
                    error_message=result.mismatch_reason,
                    context={
                        "bot_qty": str(result.bot_position_qty),
                        "broker_qty": str(result.broker_position_qty)
                    }
                )
            )
    
    async def _wait_until(self, target_time: time) -> None:
        """Wait until a specific time of day."""
        now = datetime.now()
        target = datetime.combine(now.date(), target_time)
        
        if now > target:
            # Target time already passed today
            return
        
        wait_seconds = (target - now).total_seconds()
        logger.info(f"Waiting until {target_time} ({wait_seconds:.0f} seconds)")
        
        while not self._shutdown_flag:
            now = datetime.now()
            if now >= target:
                break
            await asyncio.sleep(1)
    
    async def run(self) -> None:
        """Main trading loop."""
        self._running = True
        
        trading_config = self.config.get("trading_hours", {})
        market_open = time(
            trading_config.get("market_open_hour", 9),
            trading_config.get("market_open_minute", 15)
        )
        market_close = time(
            trading_config.get("market_close_hour", 15),
            trading_config.get("market_close_minute", 30)
        )
        
        try:
            # Wait for market open
            await self._wait_until(market_open)
            
            if self._shutdown_flag:
                return
            
            # Set up market data (strike selection + WebSocket)
            await self._setup_market_data()
            
            # Send bot started notification
            if self.telegram:
                await self.telegram.send_bot_started(paper_mode=self.paper_mode)
            
            logger.info("Trading loop started")
            
            # Main loop
            while not self._shutdown_flag:
                now = datetime.now().time()
                
                # Check market close
                if now >= market_close:
                    logger.info("Market close time reached")
                    break
                
                # Small sleep to prevent busy loop
                await asyncio.sleep(0.1)
        
        except asyncio.CancelledError:
            logger.info("Trading loop cancelled")
        except Exception as e:
            logger.error(f"Error in trading loop: {e}", exc_info=True)
        finally:
            await self.shutdown()
    
    async def _reselect_strike(self) -> None:
        """Re-select option strike during trading."""
        # Unsubscribe from current option
        if self.market_feed and self.current_option_token:
            self.market_feed.unsubscribe(
                self.current_option_token, exchange="NSE_FO"
            )
        
        # Select new strike
        index_config = self.config.get("index", {})
        option_info = await self.strike_selector.select_strike(
            index_symbol=index_config.get("symbol", "NIFTY 50"),
            expiry_type="weekly"
        )
        
        if option_info:
            self.current_option_token = option_info["token"]
            self.current_option_symbol = option_info["symbol"]
            
            # Update candle aggregator
            self.option_candle_builder = CandleAggregator(
                timeframe_seconds=60
            )
            self.option_candle_builder.add_callback(
                lambda c: self.synchronizer.on_option_candle(c)
            )
            
            # Reset option EMAs
            self.option_emas = EMASet(
                periods=self.config.get("indicators", {}).get("ema", {}).get("periods", [9, 15])
            )
            
            # Subscribe to new option
            self.market_feed.subscribe_option(self.current_option_token)
            
            logger.info(f"Strike re-selected: {self.current_option_symbol}")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down trading bot...")
        
        self._shutdown_flag = True
        self._running = False
        
        # Send daily summary via Telegram
        if self.telegram and self.risk_manager:
            stats = self.risk_manager.get_daily_stats()
            await self.telegram.send_daily_summary(
                trades_today=stats.get("total_trades", 0),
                wins=stats.get("wins", 0),
                losses=stats.get("losses", 0),
                daily_pnl=stats.get("daily_pnl", 0),
                sl_hits=stats.get("sl_hits", 0),
                reentries=stats.get("reentries_used", 0)
            )
            await self.telegram.send_bot_stopped("End of day shutdown")
        
        # Clear reconciler position
        if self.reconciler:
            self.reconciler.clear_bot_position()
        
        # Close WebSocket
        if self.market_feed:
            self.market_feed.disconnect()
        
        # Close state store
        if self.state_store:
            self.state_store.close()
        
        # Logout
        if self.auth:
            self.auth.logout()
        
        logger.info("Trading bot shutdown complete")
    
    def request_shutdown(self) -> None:
        """Request graceful shutdown (called from signal handler)."""
        logger.info("Shutdown requested")
        self._shutdown_flag = True


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="N-Structure Trading Bot")
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Run in paper trading mode (no real orders)"
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--log-dir",
        default="data/logs",
        help="Directory for log files"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    setup_logging(
        log_dir=args.log_dir,
        level=args.log_level
    )
    
    logger.info("=" * 50)
    logger.info("N-Structure Trading Bot Starting")
    logger.info(f"Paper Mode: {args.paper}")
    logger.info(f"Config: {args.config}")
    logger.info("=" * 50)
    
    # Create bot
    bot = TradingBot(config_path=args.config)
    
    # Set up signal handlers
    def signal_handler(sig, frame):
        bot.request_shutdown()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Initialize
        await bot.setup(paper_mode=args.paper)
        
        # Run
        await bot.run()
    
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
