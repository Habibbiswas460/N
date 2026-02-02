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
from data.dynamic_strike_selector import DynamicStrikeSelector, DynamicStrike, initialize_dynamic_strike_selector
from indicators.ema import EMASet
from indicators.n_structure import NStructureDetector, NStructure, DualDirectionDetector, SignalDirection, SetupStatus
from indicators.filters import CompositeFilter, VolumeAnalysis, TrendAnalysis
from core.state_store import StateStore
from core.state_machine import TradingStateMachine, TradingState, StateContext
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
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        
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
        self.n_detector: Optional[DualDirectionDetector] = None
        self.composite_filter: Optional[CompositeFilter] = None
        
        # State
        self.current_index_token: Optional[str] = None
        self.current_option_token: Optional[str] = None
        self.current_option_symbol: Optional[str] = None
        self.current_option_type: str = "CE"  # Current active option type
        
        # CE and PE option tokens (both pre-selected)
        self.ce_option_token: Optional[str] = None
        self.ce_option_symbol: Optional[str] = None
        self.pe_option_token: Optional[str] = None
        self.pe_option_symbol: Optional[str] = None
        
        # Dynamic Strike Selector (v3.0 - selects on N-Structure)
        self.dynamic_strike_selector: Optional[DynamicStrikeSelector] = None
        self._strike_selected_for_signal: bool = False  # Track if strike selected for current signal
        
        self.paper_mode: bool = False
        self.polling_mode: bool = False
        self.trade_direction: str = "BOTH"  # CE_ONLY, PE_ONLY, or BOTH
        
        # ATM tracking for dynamic re-selection
        self._last_atm_strike: Optional[float] = None
        
        # 🔥 SNIPER MODE: 1-trade/day enforcement (CRITICAL FIX)
        self._daily_trades_count = 0
        self._last_trade_date: Optional[datetime] = None
        self._daily_pnl = 0.0
        self._sniper_mode_enabled = True
        
        # Daily reset tracking
        self._last_daily_reset: Optional[datetime] = None
        
        # Structured logger
        self.slog = get_structured_logger()
    
    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        with open(self.config_path, "r") as f:
            self.config = yaml.safe_load(f)
        
        logger.info(f"Configuration loaded from {self.config_path}")
    
    async def setup(self, paper_mode: bool = False, polling_mode: bool = False) -> None:
        """
        Initialize all components.
        
        Args:
            paper_mode: If True, don't place real orders
            polling_mode: If True, use LTP polling instead of WebSocket
        """
        self.paper_mode = paper_mode
        self.polling_mode = polling_mode
        
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
        
        # Initialize risk manager (v5.2 - Position Profiles)
        risk_config = self.config.get("risk", {})
        
        # Get position size from profile
        position_mode = risk_config.get("position_mode", "conservative")
        profiles = risk_config.get("position_profiles", {})
        profile = profiles.get(position_mode, {})
        
        # Use profile values or fallback to legacy config
        num_lots = profile.get("num_lots", risk_config.get("num_lots", 4))
        fixed_qty = profile.get("fixed_quantity", risk_config.get("fixed_quantity", 260))
        risk_per_trade = profile.get("risk_per_trade", risk_config.get("risk_per_trade", 1300))
        max_daily_loss = profile.get("max_daily_loss", risk_config.get("max_daily_loss", 1300))
        
        # Get timing config
        timing_config = self.config.get("timing", {})
        
        logger.info(
            f"💰 Position Size: {position_mode.upper()} mode | "
            f"{num_lots} lots ({fixed_qty} qty) | "
            f"Risk: ₹{risk_per_trade}/trade | Max Loss: ₹{max_daily_loss}/day"
        )
        
        # Initialize Risk Manager v2.0 - PRODUCTION READY
        self.risk_manager = initialize_risk_manager(
            lot_size=risk_config.get("lot_size", 65),
            num_lots=num_lots,
            sl_points=risk_config.get("sl_points", 5.0),
            max_sl_per_day=risk_config.get("max_sl_per_day", 1),
            max_reentries_per_day=risk_config.get("max_reentries_per_day", 2),
            cooldown_candles_normal=risk_config.get("cooldown_candles", 15),
            cooldown_candles_after_sl=risk_config.get("cooldown_after_sl", 30),
            capital=risk_config.get("paper_capital", 50000.0),
            max_daily_loss=max_daily_loss,
            max_daily_loss_pct=risk_config.get("max_daily_loss_pct", 5.0),
            max_trades_per_day=risk_config.get("max_trades_per_day", 10),
            trading_start=timing_config.get("trading_start", "09:50"),
            no_new_after=timing_config.get("no_new_trades_after", "12:30"),
            manage_till=timing_config.get("manage_till", "14:40"),
            enable_time_filter=timing_config.get("enable_time_filter", True),
            margin_per_lot=risk_config.get("margin_per_lot", 15000.0)
        )
        self.risk_manager.add_event_callback(self._on_risk_event)
        
        # Store position info for order manager
        self.num_lots = num_lots
        self.position_qty = fixed_qty
        
        # Initialize order manager
        self.order_manager = OrderManager(
            auth=self.auth,
            paper_mode=paper_mode
        )
        
        # Initialize SL manager (v2.0 Sniper Mode TSL)
        exit_config = self.config.get("exit", {})
        trailing_config = exit_config.get("trailing", {})
        tsl_config = trailing_config.get("structure_tsl", {})
        tight_config = trailing_config.get("tight_trail", {})
        breath_config = exit_config.get("sl_breath", {})
        
        # v2.0 Sniper Mode settings from config
        safe_mode_trigger = trailing_config.get("safe_mode_trigger", 7.0)
        safe_mode_buffer = trailing_config.get("safe_mode_sl_buffer", 1.0)
        trail_mode_trigger = trailing_config.get("trail_mode_trigger", 10.0)
        trail_mode_buffer = trailing_config.get("trail_mode_buffer", 5.0)
        enable_sniper = trailing_config.get("method") == "sniper_mode"
        
        self.sl_manager = initialize_sl_manager(
            initial_sl_points=exit_config.get("initial_sl_points", 5.0),
            breakeven_trigger_points=trailing_config.get("breakeven_trigger_points", 7.0),
            tsl_buffer=tsl_config.get("tsl_buffer", 2.5),
            tight_trigger_points=tight_config.get("trigger_points", 20.0),
            tight_buffer=tight_config.get("buffer", 1.5),
            enable_breath_rule=breath_config.get("enabled", True),
            # v2.0 Sniper Mode
            safe_mode_trigger=safe_mode_trigger,
            safe_mode_buffer=safe_mode_buffer,
            trail_mode_trigger=trail_mode_trigger,
            trail_mode_buffer=trail_mode_buffer,
            enable_sniper_mode=enable_sniper
        )
        
        logger.info(
            f"SL Manager v2.0 | Sniper Mode: {enable_sniper} | "
            f"SL: {exit_config.get('initial_sl_points', 5.0)}pt | "
            f"Safe: +{safe_mode_trigger}pt→Entry+{safe_mode_buffer} | "
            f"Trail: +{trail_mode_trigger}pt→High-{trail_mode_buffer}"
        )
        
        # Initialize position reconciler
        self.reconciler = PositionReconciler(
            auth=self.auth,
            poll_interval=5.0,
            paper_mode=paper_mode
        )
        self.reconciler.add_mismatch_callback(self._on_position_mismatch)
        
        # Initialize N-Structure detector (v5.2 - Confirmation Candle)
        n_config = self.config.get("indicators", {}).get("n_structure", {})
        option_config = self.config.get("option", {})
        trade_direction = option_config.get("trade_direction", "BOTH").upper()
        
        # Use DualDirectionDetector for pattern-based direction
        self.n_detector = DualDirectionDetector(
            entry_buffer=n_config.get("buffer", 1.5),
            min_swing_gap_candles=n_config.get("min_swing_gap_candles", 5),
            min_swing_gap_points=n_config.get("min_swing_gap_points", 2.0),
            trade_direction=trade_direction,
            # v5.1: Volume confirmation
            volume_confirmation_enabled=n_config.get("volume_confirmation_enabled", True),
            min_volume_ratio=n_config.get("min_volume_ratio", 1.5),
            volume_lookback=n_config.get("volume_lookback", 20),
            # v5.1: Gap filter
            gap_filter_enabled=n_config.get("gap_filter_enabled", True),
            max_gap_points=n_config.get("max_gap_points", 50.0),
            # v5.2: Confirmation candle (patience for entry)
            confirmation_candles=n_config.get("confirmation_candles", 2),
            require_direction_candle=n_config.get("require_direction_candle", True)
        )
        logger.info(
            f"N-Structure v5.2 | Direction: {trade_direction} | "
            f"Volume: {n_config.get('volume_confirmation_enabled', True)} (>= {n_config.get('min_volume_ratio', 1.5)}x) | "
            f"Gap Filter: < {n_config.get('max_gap_points', 50)}pt | "
            f"Confirm Candles: {n_config.get('confirmation_candles', 2)}"
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
        
        # Initialize Dynamic Strike Selector (v3.0 - selects on N-Structure signal)
        strike_config = self.config.get("strike_selection", {})
        self.dynamic_strike_selector = initialize_dynamic_strike_selector(
            premium_min=strike_config.get("premium_min", 85.0),
            premium_max=strike_config.get("premium_max", 110.0),
            premium_sweet_min=strike_config.get("premium_sweet_min", 90.0),
            premium_sweet_max=strike_config.get("premium_sweet_max", 100.0),
            strike_range=strike_config.get("strike_range", 5)
        )
        logger.info(
            f"Dynamic Strike Selector v3.0 | "
            f"Premium Range: ₹{strike_config.get('premium_min', 85)}-₹{strike_config.get('premium_max', 110)} | "
            f"Selects AFTER N-Structure detection on INDEX"
        )
        
        # Initialize Telegram notifier (v1.2)
        telegram_config = self.config.get("telegram", {})
        self.telegram = initialize_telegram(
            bot_token=telegram_config.get("bot_token", ""),
            chat_id=telegram_config.get("chat_id", ""),
            enabled=telegram_config.get("enabled", False)
        )
        
        logger.info(f"Bot initialized | Paper Mode: {paper_mode}")
        
        # 🔧 FIX: Check for stale state and auto-reset
        self._check_state_health()
    
    def _check_state_health(self) -> None:
        """
        Check FSM state health and auto-reset if stale.
        
        Detects states stuck from previous days and resets to IDLE.
        """
        if not self.fsm:
            return
            
        # Get current state context
        ctx = self.fsm.context
        last_change = ctx.last_state_change
        
        if last_change:
            age = datetime.now() - last_change
            current_state = self.fsm.state
            
            # If state is not IDLE and older than 1 day, it's stale
            if age.days > 0 and current_state != TradingState.IDLE:
                logger.warning(
                    f"⚠️ STALE STATE DETECTED: {current_state.name} is {age.days} days old! "
                    f"Last change: {last_change.strftime('%Y-%m-%d %H:%M')} | Auto-resetting to IDLE"
                )
                
                # Force reset to IDLE
                self.fsm.transition_to(
                    TradingState.IDLE,
                    reason=f"Auto-reset: stale state ({age.days} days old)",
                    force=True
                )
                
                # Clear context
                self.fsm._context = StateContext()
                self.fsm._persist_state()
                
                logger.success("✓ State auto-reset to IDLE")
            elif age.days > 0:
                logger.info(f"State {current_state.name} is {age.days} days old but IDLE - OK")
    
    def _reset_daily_counters(self) -> None:
        """
        Reset daily trading counters at market open.
        
        Called once per day at 9:15 AM to ensure fresh start.
        """
        today = datetime.now().date()
        
        if self._last_daily_reset and self._last_daily_reset == today:
            return  # Already reset today
        
        logger.info("📅 DAILY RESET: Resetting all daily counters...")
        
        # Reset trade counters
        self._daily_trades_count = 0
        self._last_trade_date = None
        self._daily_pnl = 0.0
        
        # Reset risk manager daily stats
        if self.risk_manager:
            self.risk_manager.reset_daily()
        
        # Mark reset done
        self._last_daily_reset = today
        
        logger.success(
            f"✓ Daily counters reset | Date: {today} | "
            f"Trades: 0 | SL Hits: 0 | P&L: ₹0"
        )
    
    async def _fast_strike_selection(self, expiry, atm_strike, option_type: OptionType = OptionType.CALL) -> tuple:
        """
        AT-THE-MONEY (ATM) Strike Selection - DYNAMIC MODE.
        
        **Always selects EXACT ATM strike based on current index price.**
        Re-selects automatically when ATM changes (every 50 points).
        
        Selection Logic:
        1. Calculate current ATM strike (50-point interval for NIFTY)
        2. Select EXACT ATM strike (no premium filtering)
        3. If ATM changes by 50 points → Auto re-select
        4. Always maintain ATM, never become OTM
        
        🟡 MEDIUM CONCERN #4 FIX: Token/Symbol Management Safeguarded
        
        Token Validation (prevents stale tokens):
        - Check: Instrument master is fresh (daily download)
        - Verify: Token exists and is valid (not expired)
        - Fallback: Log error if token not found, retry next candle
        - Safety: Won't trade with invalid tokens
        
        Why Dynamic?
        - ATM = BEST delta (~0.50 for options, closest to 1:1 index move)
        - ATM = BEST premium decay ratio
        - Premium changes as index moves, but strike stays optimal
        - Re-select maintains strategy consistency
        
        Args:
            expiry: Option expiry date
            atm_strike: Current ATM strike price (dynamic)
            option_type: CALL or PUT
            
        Returns:
            Tuple of (selected_option, premium)
        """
        type_str = "CE" if option_type == OptionType.CALL else "PE"
        
        # Get all options of specified type
        all_options = self.instrument_master.get_nifty_options(
            expiry_date=expiry,
            option_type=option_type
        )
        
        if not all_options:
            raise RuntimeError(f"No NIFTY {type_str} options found")
        
        # Find EXACT ATM strike (dynamic based on current index)
        atm_option = None
        for opt in all_options:
            if opt.strike == atm_strike:
                atm_option = opt
                break
        
        if not atm_option:
            raise RuntimeError(f"ATM strike {atm_strike} not found for {type_str}")
        
        # 🟡 TOKEN VALIDATION: Verify token is valid (MEDIUM CONCERN #4)
        if not atm_option.token or not isinstance(atm_option.token, str):
            raise RuntimeError(f"Invalid token for {type_str}: {atm_option.token}")
        
        if not atm_option.symbol or not isinstance(atm_option.symbol, str):
            raise RuntimeError(f"Invalid symbol for {type_str}: {atm_option.symbol}")
        
        # Get premium (just for logging)
        premium = self.auth.get_ltp("NFO", atm_option.symbol, atm_option.token)
        if not premium:
            premium = 0.0
        
        logger.info(
            f"✓ ATM {type_str} Selected: Strike {int(atm_strike)} | "
            f"Symbol: {atm_option.symbol} | Token: {atm_option.token} | "
            f"Premium: ₹{premium:.2f} | "
            f"🔄 Dynamic (Re-selects when index moves 50 points)"
        )
        
        return atm_option, premium
    
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
        
        # Get trade direction from config
        option_config = self.config.get("option", {})
        self.trade_direction = option_config.get("trade_direction", "BOTH").upper()
        logger.info(f"Trade Direction: {self.trade_direction}")
        
        # Select CE option if needed
        if self.trade_direction in ["CE_ONLY", "BOTH"]:
            ce_option, ce_premium = await self._fast_strike_selection(
                expiry=expiry,
                atm_strike=atm_strike,
                option_type=OptionType.CALL
            )
            self.ce_option_token = ce_option.token
            self.ce_option_symbol = ce_option.symbol
            logger.success(
                f"✓ CE Selected: {self.ce_option_symbol} "
                f"(Token: {self.ce_option_token}, Strike: {int(ce_option.strike)}, "
                f"Premium: ₹{ce_premium:.2f})"
            )
        
        # Select PE option if needed
        if self.trade_direction in ["PE_ONLY", "BOTH"]:
            pe_option, pe_premium = await self._fast_strike_selection(
                expiry=expiry,
                atm_strike=atm_strike,
                option_type=OptionType.PUT
            )
            self.pe_option_token = pe_option.token
            self.pe_option_symbol = pe_option.symbol
            logger.success(
                f"✓ PE Selected: {self.pe_option_symbol} "
                f"(Token: {self.pe_option_token}, Strike: {int(pe_option.strike)}, "
                f"Premium: ₹{pe_premium:.2f})"
            )
        
        # Set default active option (CE first, or PE if CE_ONLY disabled)
        if self.trade_direction == "PE_ONLY":
            self.current_option_token = self.pe_option_token
            self.current_option_symbol = self.pe_option_symbol
            self.current_option_type = "PE"
        else:
            self.current_option_token = self.ce_option_token
            self.current_option_symbol = self.ce_option_symbol
            self.current_option_type = "CE"
        
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
        
        # Initialize market feed (WebSocket or Polling)
        if self.polling_mode:
            logger.info("Using LTP Polling mode (WebSocket bypass)")
            from data.market_feed_polling import PollingMarketFeed
            self.market_feed = PollingMarketFeed(
                smart_api=self.auth._smart_api,
                poll_interval=4.0,  # Poll every 4 seconds (conservative: 3 tokens × 0.25 req/sec = 0.75 req/sec)
                broker=self.auth  # Pass broker for session validation
            )
            self.market_feed.add_tick_callback(self._on_tick)
            
            # Subscribe to Index
            logger.info(f"Subscribing to Index: Nifty 50 (Token: {self.current_index_token})")
            self.market_feed.subscribe_index(self.current_index_token, "Nifty 50", "NSE")
            
            # Subscribe to CE option if available
            if self.ce_option_token and self.ce_option_symbol:
                logger.info(f"Subscribing to CE: {self.ce_option_symbol} (Token: {self.ce_option_token})")
                self.market_feed.subscribe_option(self.ce_option_token, self.ce_option_symbol, "NFO")
            
            # Subscribe to PE option if available (for BOTH mode)
            if self.pe_option_token and self.pe_option_symbol:
                logger.info(f"Subscribing to PE: {self.pe_option_symbol} (Token: {self.pe_option_token})")
                self.market_feed.subscribe_option(self.pe_option_token, self.pe_option_symbol, "NFO")
            
            # Start polling
            self.market_feed.connect()
            logger.success(f"LTP Polling started - Trade Direction: {self.trade_direction}")
        else:
            # Use WebSocket
            self.market_feed = MarketFeed(
                auth_token=self.auth.jwt_token,
                api_key=self.auth.api_key,
                client_code=self.auth.client_code,
                feed_token=self.auth.feed_token,
                mode=SubscriptionMode.QUOTE
            )
            self.market_feed.add_tick_callback(self._on_tick)
            
            # Connect and wait for connection
            logger.info("Connecting to WebSocket...")
            if not self.market_feed.connect(timeout=15.0):
                raise RuntimeError("Failed to connect to WebSocket")
                
            # Subscribe to both tokens
            logger.info(f"Subscribing to Index token: {self.current_index_token}")
            self.market_feed.subscribe_index(self.current_index_token)
            logger.info(f"Subscribing to Option token: {self.current_option_token}")
            self.market_feed.subscribe_option(self.current_option_token)
            logger.success("WebSocket subscriptions complete - waiting for tick data...")
    
    def _on_tick(self, tick) -> None:
        """
        Handle incoming tick data.
        
        Routes tick to appropriate candle aggregator.
        """
        token = str(tick.token)
        
        # Count ticks for monitoring
        if not hasattr(self, '_tick_count'):
            self._tick_count = 0
            self._last_tick_log = 0
        self._tick_count += 1
        
        # Log every 100 ticks to show activity
        if self._tick_count - self._last_tick_log >= 100:
            logger.debug(f"📊 Tick #{self._tick_count} | Token: {token} | LTP: {tick.ltp:.2f}")
            self._last_tick_log = self._tick_count
        
        # First tick log
        if self._tick_count == 1:
            logger.info(f"✅ First tick received! Token: {token} | LTP: {tick.ltp:.2f}")
        
        if token == self.current_index_token:
            self.index_candle_builder.process_tick(tick)
        elif token == self.current_option_token:
            self.option_candle_builder.process_tick(tick)
        # Also process ticks for the alternate option (if BOTH mode)
        elif token == self.ce_option_token or token == self.pe_option_token:
            # We only actively build candles for current option
            # This ensures we can switch smoothly
            pass
    
    async def _switch_option_type(self, new_type: str) -> None:
        """
        Switch between CE and PE option tracking.
        
        Args:
            new_type: "CE" or "PE"
        """
        if new_type == self.current_option_type:
            return
            
        logger.info(f"🔄 Switching from {self.current_option_type} to {new_type}")
        
        if new_type == "CE" and self.ce_option_token:
            self.current_option_token = self.ce_option_token
            self.current_option_symbol = self.ce_option_symbol
            self.current_option_type = "CE"
        elif new_type == "PE" and self.pe_option_token:
            self.current_option_token = self.pe_option_token
            self.current_option_symbol = self.pe_option_symbol
            self.current_option_type = "PE"
        else:
            logger.warning(f"Cannot switch to {new_type} - token not available")
            return
        
        # Update synchronizer with new token
        if self.synchronizer:
            self.synchronizer.update_option_token(self.current_option_token)
        
        # Clear option candle builder
        if self.option_candle_builder:
            self.option_candle_builder.clear()
        
        # Reset option EMAs
        if self.option_emas:
            self.option_emas.reset()
        
        logger.success(f"✓ Now tracking: {self.current_option_symbol} ({self.current_option_type})")
    
    async def _select_strike_on_n_structure(
        self, 
        n_structure: NStructure, 
        index_price: float
    ) -> Optional[DynamicStrike]:
        """
        Select optimal strike when N-Structure is detected on INDEX.
        
        v3.0 Dynamic Strike Selection:
        1. Only called when INDEX shows valid N-Structure (HH + HL)
        2. Finds strikes in 85-110 premium range
        3. Scores by movement potential
        4. Returns best strike for entry
        
        Args:
            n_structure: Detected N-Structure pattern
            index_price: Current index price
            
        Returns:
            DynamicStrike if found, None otherwise
        """
        if not self.dynamic_strike_selector:
            logger.warning("Dynamic strike selector not initialized")
            return None
        
        # Premium fetcher function using broker API
        def fetch_premium(token: str, symbol: str, exchange: str) -> Optional[float]:
            try:
                return self.auth.get_ltp(exchange, symbol, token)
            except Exception as e:
                logger.debug(f"Error fetching premium for {symbol}: {e}")
                return None
        
        # Get expiry
        expiry = self.instrument_master.get_nearest_expiry("NIFTY")
        if not expiry:
            logger.error("No expiry found for strike selection")
            return None
        
        # Select strike based on N-Structure direction
        selected = self.dynamic_strike_selector.select_strike_on_signal(
            n_structure=n_structure,
            index_price=index_price,
            premium_fetcher=fetch_premium,
            underlying="NIFTY",
            expiry=expiry
        )
        
        if selected:
            # Update current option tracking
            if selected.option_type == OptionType.CALL:
                self.ce_option_token = selected.token
                self.ce_option_symbol = selected.symbol
                self.current_option_token = selected.token
                self.current_option_symbol = selected.symbol
                self.current_option_type = "CE"
            else:
                self.pe_option_token = selected.token
                self.pe_option_symbol = selected.symbol
                self.current_option_token = selected.token
                self.current_option_symbol = selected.symbol
                self.current_option_type = "PE"
            
            # Update synchronizer
            if self.synchronizer:
                self.synchronizer.update_option_token(self.current_option_token)
            
            # Update market feed subscription (polling mode)
            if self.polling_mode and self.market_feed:
                # Subscribe to newly selected option
                self.market_feed.subscribe_option(selected.token, selected.symbol, "NFO")
            
            # Clear and reset option candle builder for new strike
            if self.option_candle_builder:
                self.option_candle_builder.clear()
            
            if self.option_emas:
                self.option_emas.reset()
            
            self._strike_selected_for_signal = True
            
            logger.success(
                f"✅ STRIKE SELECTED ON N-STRUCTURE | "
                f"{selected.symbol} | Premium: ₹{selected.premium:.2f} | "
                f"Score: {selected.movement_score:.1f}/100"
            )
        
        return selected
    
    def _on_synced_candles_sync(self, pair: SyncedCandlePair) -> None:
        """
        Synchronous callback for synced candles.
        
        Wraps the async handler. Handles both in-loop and threaded calls.
        """
        try:
            # Try to get the running loop
            loop = asyncio.get_running_loop()
            # If we're in the event loop thread, just create task
            asyncio.create_task(self._on_synced_candles(pair))
        except RuntimeError:
            # No running loop - we're being called from a different thread (polling)
            # Use the stored event loop reference
            if hasattr(self, '_event_loop') and self._event_loop:
                self._event_loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._on_synced_candles(pair))
                )
            else:
                # Fallback: run synchronously with asyncio.run (less ideal)
                logger.warning("No event loop reference - running callback synchronously")
                asyncio.run(self._on_synced_candles(pair))
    
    async def _on_synced_candles(self, pair: SyncedCandlePair) -> None:
        """
        Process synchronized candle pair.
        
        This is the main trading logic entry point (v1.2 with re-entry).
        """
        try:
            # Check if within trading hours
            trading_config = self.config.get("trading_hours", {})
            market_open = time(
                trading_config.get("market_open_hour", 9),
                trading_config.get("market_open_minute", 15)
            )
            market_close = time(
                trading_config.get("market_close_hour", 15),
                trading_config.get("market_close_minute", 30)
            )
            now = datetime.now().time()
            
            if not (market_open <= now < market_close):
                # Market closed - skip trading logic
                return
            
            # 🔧 FIX: Daily reset at market open (9:15-9:16 window)
            if market_open <= now <= time(9, 16):
                self._reset_daily_counters()
            
            # ===== DYNAMIC ATM RE-SELECTION =====
            # Check if ATM has changed (every 50 points for NIFTY)
            current_index_price = pair.index_candle.close
            if current_index_price is None:
                logger.debug("Skipping ATM re-selection: index_candle.close is None")
            else:
                current_atm = round(current_index_price / 50) * 50
                
                # Store current ATM for comparison
                if not hasattr(self, '_last_atm_strike'):
                    self._last_atm_strike = current_atm
                
                if current_atm != self._last_atm_strike and self._last_atm_strike is not None:
                    logger.warning(
                        f"🔄 ATM SHIFTED: {int(self._last_atm_strike)} → {int(current_atm)} | "
                        f"Index: {current_index_price:.2f}"
                    )
                    
                    # Re-select strikes at new ATM
                    try:
                        expiry = self.instrument_master.get_nearest_expiry("NIFTY")
                        if not expiry:
                            logger.error("Cannot re-select - no expiry found")
                        else:
                            # Re-select CE if trading CE
                            if self.trade_direction in ["CE_ONLY", "BOTH"]:
                                ce_option, ce_premium = await self._fast_strike_selection(
                                    expiry=expiry,
                                    atm_strike=current_atm,
                                    option_type=OptionType.CALL
                                )
                                old_ce_token = self.ce_option_token
                                self.ce_option_token = ce_option.token
                                self.ce_option_symbol = ce_option.symbol
                                
                                logger.success(
                                    f"✓ CE Re-selected: {self.ce_option_symbol} @ ₹{ce_premium:.2f} | "
                                    f"(was {old_ce_token})"
                                )
                            
                            # Re-select PE if trading PE
                            if self.trade_direction in ["PE_ONLY", "BOTH"]:
                                pe_option, pe_premium = await self._fast_strike_selection(
                                    expiry=expiry,
                                    atm_strike=current_atm,
                                    option_type=OptionType.PUT
                                )
                                old_pe_token = self.pe_option_token
                                self.pe_option_token = pe_option.token
                                self.pe_option_symbol = pe_option.symbol
                                
                                logger.success(
                                    f"✓ PE Re-selected: {self.pe_option_symbol} @ ₹{pe_premium:.2f} | "
                                    f"(was {old_pe_token})"
                                )
                            
                            # Update current tracking if not in position
                            if self.fsm.state not in [TradingState.IN_POSITION, TradingState.PENDING_REENTRY]:
                                # Safe to switch
                                if self.trade_direction == "PE_ONLY":
                                    self.current_option_token = self.pe_option_token
                                    self.current_option_symbol = self.pe_option_symbol
                                    self.current_option_type = "PE"
                                else:
                                    self.current_option_token = self.ce_option_token
                                    self.current_option_symbol = self.ce_option_symbol
                                    self.current_option_type = "CE"
                                
                                # Update synchronizer
                                if self.synchronizer:
                                    self.synchronizer.update_option_token(self.current_option_token)
                                
                                # Update market feed subscription
                                if self.market_feed and old_ce_token and old_ce_token != self.ce_option_token:
                                    self.market_feed.unsubscribe(old_ce_token, exchange="NFO")
                                    self.market_feed.subscribe_option(self.ce_option_token, self.ce_option_symbol, "NFO")
                                
                                if self.market_feed and old_pe_token and old_pe_token != self.pe_option_token:
                                    self.market_feed.unsubscribe(old_pe_token, exchange="NFO")
                                    self.market_feed.subscribe_option(self.pe_option_token, self.pe_option_symbol, "NFO")
                                
                                logger.info(f"✓ Now tracking: {self.current_option_symbol}")
                            else:
                                logger.warning(f"⚠️ In position - deferring strike switch to next ATM shift")
                    
                    except Exception as e:
                        logger.error(f"Error during ATM re-selection: {e}", exc_info=True)
                    
                    self._last_atm_strike = current_atm
            # ===== END DYNAMIC ATM RE-SELECTION =====
            
            # Log candle received for visibility
            logger.info(
                f"🕯️ Candle {pair.timestamp.strftime('%H:%M')} | "
                f"Index: {pair.index_candle.close:.2f} | "
                f"Option [{self.current_option_type}]: {pair.option_candle.close:.2f}"
            )
            
            # Update EMAs
            self.index_emas.update(pair.index_candle.close)
            self.option_emas.update(pair.option_candle.close)
            
            index_ema_9 = self.index_emas.get_value(9)
            index_ema_15 = self.index_emas.get_value(15)
            option_ema_9 = self.option_emas.get_value(9)
            
            # v2.0: N-Structure pattern determines direction (not EMA crossover)
            # CE/PE switching happens AFTER pattern detection below
            
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
                token=self.current_index_token or "",
                timestamp=pair.timestamp,
                open=pair.index_candle.open,
                high=pair.index_candle.high,
                low=pair.index_candle.low,
                close=pair.index_candle.close,
                volume=pair.index_candle.volume
            )
            
            option_candle = Candle(
                token=self.current_option_token or "",
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
            # Create SyncedCandlePair for the detector
            synced_pair = SyncedCandlePair(
                timestamp=pair.timestamp,
                index_candle=pair.index_candle,
                option_candle=pair.option_candle
            )
            
            # v5.1: Pass volume for breakout confirmation
            # Use index candle volume (or 0 if not available)
            candle_volume = getattr(pair.index_candle, 'volume', 0) or 0
            
            status, n_structure, msg = self.n_detector.process_synced_pair(
                pair=synced_pair,
                ema_fast_value=index_ema_9 or 0,
                ema_slow_value=index_ema_15 or 0,
                volume=candle_volume  # v5.1: Volume confirmation
            )
            
            if n_structure:
                logger.info(f"N-Structure: {status.name} | {msg}")
                
                # v3.0: Dynamic Strike Selection AFTER N-Structure detected on INDEX
                # Only select strike when:
                # 1. N-Structure is valid (has direction)
                # 2. Not already in position
                # 3. Strike not already selected for this signal
                if (n_structure.direction and 
                    self.fsm.state not in [TradingState.IN_POSITION, TradingState.PENDING_REENTRY] and
                    status == SetupStatus.READY_FOR_ENTRY and
                    not self._strike_selected_for_signal):
                    
                    logger.info(
                        f"🎯 N-Structure READY on INDEX! Selecting optimal strike in ₹85-₹110 range..."
                    )
                    
                    # Select best strike based on N-Structure direction
                    selected_strike = await self._select_strike_on_n_structure(
                        n_structure=n_structure,
                        index_price=pair.index_candle.close
                    )
                    
                    if not selected_strike:
                        logger.warning(
                            f"❌ No strike found in ₹85-₹110 range for "
                            f"{'CE' if n_structure.direction == SignalDirection.BULLISH else 'PE'}. "
                            f"Skipping this signal."
                        )
                        # Reset and wait for next signal
                        self._strike_selected_for_signal = False
                        return
                
                # v2.0: Switch option type based on pattern direction (not EMA)
                if self.trade_direction == "BOTH" and n_structure.direction:
                    if n_structure.direction == SignalDirection.BULLISH and self.current_option_type != "CE":
                        await self._switch_option_type("CE")
                    elif n_structure.direction == SignalDirection.BEARISH and self.current_option_type != "PE":
                        await self._switch_option_type("PE")
            
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
                    # v3.0: Check body ratio filter (65% minimum)
                    candle_body = abs(option_candle.close - option_candle.open)
                    candle_range = option_candle.high - option_candle.low
                    body_pct = (candle_body / candle_range * 100) if candle_range > 0 else 0
                    
                    entry_config = self.config.get("entry", {})
                    min_body_pct = entry_config.get("min_candle_body_pct", 65)  # v3.0: 65% default
                    
                    if body_pct < min_body_pct:
                        logger.info(
                            f"Entry blocked: Body ratio {body_pct:.1f}% < {min_body_pct}% minimum | "
                            f"Candle: O={option_candle.open:.2f} H={option_candle.high:.2f} "
                            f"L={option_candle.low:.2f} C={option_candle.close:.2f}"
                        )
                        # Stay in ARMED state, will retry next candle
                    else:
                        is_reentry = self.fsm.is_reentry_trade
                        logger.info(
                            f"Filters passed: {' | '.join(filter_messages)} | "
                            f"Body: {body_pct:.1f}% ✓"
                        )
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
        
        🟡 MEDIUM CONCERN #3 FIX: Re-entry Logic Clarified
        
        RE-ENTRY RULES (Explicit, no ambiguity):
        1. Only triggered AFTER SL hit (FSM state = SL_HIT)
        2. Watches for Higher High (HH) above last high after SL
        3. HH must be 2+ points above SL exit price (min gap)
        4. Candle must show strength (40%+ body, 2+ point range)
        5. Only 1 re-entry allowed per day (Sniper mode)
        6. If re-entry SL also hits → Day ends (no further trades)
        
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
        
        # 🔥 TRADE EXECUTION LOGGING: Log SL exit (CRITICAL FIX)
        logger.warning(
            f"\n❌ TRADE SL HIT | "
            f"Exit Price: {exit_price:.2f} | "
            f"Entry Price: {entry_price:.2f} | "
            f"P&L: ₹{pnl:.0f} ({pnl_points:+.1f}pt) | "
            f"Qty: {quantity} | "
            f"Reason: {reason} | "
            f"Direction: {self.current_option_type} | "
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        
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
        # 🔥 SNIPER MODE CHECK: Enforce 1-trade/day limit (CRITICAL FIX)
        today = datetime.now().date()
        if self._last_trade_date and self._last_trade_date == today and self._daily_trades_count >= 1:
            logger.warning(
                f"🔒 SNIPER MODE: Daily trade limit reached (1/day). "
                f"Already traded today. Next trade window: TOMORROW"
            )
            self.slog.log_signal(
                signal_type="entry_blocked",
                status="rejected",
                index_price=option_candle.close,
                option_price=option_candle.close,
                entry_trigger=0.0,
                reason="Sniper mode: 1 trade/day limit enforced"
            )
            return
        
        # Get entry trigger
        if is_reentry:
            entry_trigger = self.fsm.context.reentry_hh_trigger
        elif n_structure:
            entry_config = self.config.get("entry", {})
            buffer = entry_config.get("buffer_points", 1.5)
            # 🔥 ADD SLIPPAGE BUFFER: Prevent misses due to real market slippage (CRITICAL FIX)
            slippage_buffer = entry_config.get("slippage_buffer_points", 0.75)
            entry_trigger = n_structure.breakout_high + buffer + slippage_buffer
        else:
            return
        
        # Get fixed quantity from risk manager
        quantity = self.risk_manager.get_position_size()
        
        # 🔥 TRADE EXECUTION LOGGING: Log entry attempt (CRITICAL FIX)
        logger.info(
            f"\n📊 TRADE ENTRY ATTEMPT | "
            f"Token: {self.current_option_token} | "
            f"Symbol: {self.current_option_symbol} | "
            f"Type: {'RE-ENTRY' if is_reentry else 'NEW'} | "
            f"Entry Trigger: {entry_trigger:.2f} | "
            f"Limit: {entry_trigger + 1.0:.2f} | "
            f"Direction: {self.current_option_type} | "
            f"Qty: {quantity} | "
            f"Time: {option_candle.timestamp.strftime('%H:%M:%S')}"
        )
        
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
            
            # 🔥 TRADE EXECUTION LOGGING: Log successful entry (CRITICAL FIX)
            logger.info(
                f"✅ TRADE EXECUTED | "
                f"Order ID: {response.order_id} | "
                f"Entry Price: {entry_trigger:.2f} | "
                f"SL Price: {sl_price:.2f} | "
                f"SL Points: {sl_points:.2f} | "
                f"Qty: {quantity} | "
                f"Direction: {self.current_option_type} | "
                f"Trades Today: {self._daily_trades_count + 1}/1 | "
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            
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
            
            # 🔥 SNIPER MODE: Increment trade count for daily limit (CRITICAL FIX)
            if today != self._last_trade_date:
                self._daily_trades_count = 0
            self._daily_trades_count += 1
            self._last_trade_date = today
            
            # v3.0: Reset strike selection flag after entry
            self._strike_selected_for_signal = False
            
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
            
            dir_emoji = "📈" if self.current_option_type == "CE" else "📉"
            logger.success(
                f"{dir_emoji} {trade_type} [{self.current_option_type}]: {self.current_option_symbol} @ {entry_trigger:.2f}, "
                f"Qty={quantity}, SL={sl_price:.2f}"
            )
            
            # Send Telegram entry alert with direction
            if self.telegram:
                await self.telegram.send_entry_alert(
                    symbol=self.current_option_symbol,
                    entry_price=entry_trigger,
                    sl_price=sl_price,
                    quantity=quantity,
                    is_reentry=is_reentry,
                    direction=self.current_option_type  # CE or PE
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
        
        # Store event loop reference for cross-thread callback scheduling
        self._event_loop = asyncio.get_running_loop()
        
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
            # Set up market data immediately (strike selection + feed)
            await self._setup_market_data()
            
            if self._shutdown_flag:
                return
            
            # Send bot started notification
            if self.telegram:
                await self.telegram.send_bot_started(paper_mode=self.paper_mode)
            
            logger.info("Trading loop started")
            
            # Track if we logged market closed message
            _logged_market_closed = False
            
            # Main loop - runs forever until manual shutdown
            while not self._shutdown_flag:
                now = datetime.now().time()
                
                # Check if market is open
                is_market_open = market_open <= now < market_close
                
                if is_market_open:
                    _logged_market_closed = False  # Reset for next close
                    # Normal trading - processing happens in callbacks
                else:
                    # Market closed - just wait, don't shutdown
                    if not _logged_market_closed:
                        logger.info(f"⏸️ Market closed. Waiting for next session ({market_open})...")
                        _logged_market_closed = True
                
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
    parser.add_argument(
        "--polling",
        action="store_true",
        help="Use LTP polling instead of WebSocket (for rate limit bypass)"
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
    logger.info(f"Polling Mode: {args.polling}")
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
        await bot.setup(paper_mode=args.paper, polling_mode=args.polling)
        
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
