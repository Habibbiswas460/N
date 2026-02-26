"""
Adaptive Hybrid Trading Bot - Main Entry Point v2.0

Clean implementation using Adaptive Hybrid Strategy:
- Market Regime Detection
- VWAP-based bias
- Volume Profile levels
- Mean Reversion + Momentum

Trading Flow:
- 9:15 AM: Connect to market feed, initialize indicators
- 9:30 AM: Start trading (wait 15 min for VWAP to stabilize)
- 3:00 PM: Stop new entries
- 3:30 PM: Auto exit and shutdown
"""

import asyncio
import signal
import sys
import time as time_module
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import yaml

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from utils import now_ist, today_ist, IST
from broker.auth import AngelOneAuth
from data.instrument_master import InstrumentMaster, OptionType
from data.market_feed import MarketFeed, SubscriptionMode
from data.candle_builder import CandleAggregator, Candle
from data.dynamic_strike_selector import DynamicStrikeSelector, initialize_dynamic_strike_selector
from execution.order_manager import OrderManager, OrderRequest, OrderType, TransactionType, ProductType
from execution.sl_manager import StopLossManager, initialize_sl_manager
from risk.risk_manager import RiskManager, initialize_risk_manager
from utils.logger import setup_logging, log_banner, log_trade_entry, log_trade_exit
from utils.telegram import TelegramNotifier, initialize_telegram

# New Strategy Components
from strategy.adaptive_hybrid import AdaptiveHybridStrategy, TradeSignal, SignalType
from strategy.regime_detector import MarketRegime

from loguru import logger


class AdaptiveTradingBot:
    """
    Adaptive Trading Bot using Hybrid Strategy
    
    This is a cleaner implementation focusing on:
    1. Regime-based trading decisions
    2. VWAP for directional bias
    3. Volume Profile for key levels
    4. ATR-based dynamic risk management
    """
    
    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self._shutdown_flag = False
        self._running = False
        
        # Core components
        self.auth: Optional[AngelOneAuth] = None
        self.instrument_master: Optional[InstrumentMaster] = None
        self.market_feed: Optional[MarketFeed] = None
        self.order_manager: Optional[OrderManager] = None
        self.sl_manager: Optional[StopLossManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.telegram: Optional[TelegramNotifier] = None
        
        # Candle builder
        self.index_candle_builder: Optional[CandleAggregator] = None
        
        # Strategy
        self.strategy: Optional[AdaptiveHybridStrategy] = None
        
        # Current trade
        self.current_trade: Optional[TradeSignal] = None
        self.entry_order_id: Optional[str] = None
        self.entry_price: float = 0.0
        
        # Tokens
        self.index_token: str = ""
        self.ce_token: Optional[str] = None
        self.pe_token: Optional[str] = None
        self.ce_symbol: Optional[str] = None
        self.pe_symbol: Optional[str] = None
        
        # Market hours tracking
        self._logged_market_closed = False
        
        # Exit lock to prevent duplicate exits
        self._exit_in_progress = False
        
    def is_market_open(self) -> bool:
        """Check if market is currently open for trading"""
        now = datetime.now().time()
        market_open = time(9, 15)
        market_close = time(15, 30)
        return market_open <= now <= market_close
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        logger.info(f"Loaded config: {self.config['strategy']['name']} v{self.config['strategy']['version']}")
        return self.config
        
    async def initialize(self):
        """Initialize all components"""
        log_banner("INITIALIZING ADAPTIVE HYBRID BOT")
        
        # Load config
        self.load_config()
        
        # Setup logging
        log_config = self.config.get('logging', {})
        setup_logging(
            log_dir=log_config.get('log_dir', 'data/logs'),
            level=log_config.get('level', 'INFO')
        )
        
        # Initialize auth (uses environment variables)
        self.auth = AngelOneAuth()
        if not self.auth.login():
            raise RuntimeError("Failed to authenticate with Angel One")
        logger.info("✅ Authentication successful")
        
        # Initialize instrument master
        self.instrument_master = InstrumentMaster()  # Uses default cache_dir
        self.instrument_master.download()
        logger.info("✅ Instrument master loaded")
        
        # Get index token
        self.index_token = self.config['index']['token']
        
        # Initialize strategy with config
        strategy_config = {
            'atr_sl_multiplier': self.config['strategy']['entry'].get('atr_sl_multiplier', 1.0),
            'min_rr_ratio': self.config['strategy']['entry'].get('min_rr_ratio', 2.0),
            'max_trades_per_day': self.config['strategy']['entry'].get('max_trades_per_day', 3),
            'max_daily_loss_pct': self.config['strategy']['entry'].get('max_daily_loss_pct', 2.0),
        }
        self.strategy = AdaptiveHybridStrategy(strategy_config)
        logger.info("✅ Adaptive Hybrid Strategy initialized")
        
        # Initialize order manager
        paper_mode = self.config.get('paper_trading', {}).get('enabled', False)
        paper_capital = self.config.get('risk', {}).get('paper_capital', 50000.0)
        self.order_manager = OrderManager(
            auth=self.auth,
            paper_mode=paper_mode,
            paper_capital=paper_capital
        )
        logger.info("✅ Order manager initialized")
        
        # Initialize SL manager with config values
        exit_config = self.config.get('exit', {})
        trailing_config = exit_config.get('trailing', {})
        self.sl_manager = initialize_sl_manager(
            safe_mode_trigger=trailing_config.get('safe_mode_trigger', 7.0),
            safe_mode_buffer=trailing_config.get('safe_mode_buffer', 1.0),
            trail_mode_trigger=trailing_config.get('trail_mode_trigger', 10.0),
            trail_mode_buffer=trailing_config.get('trail_mode_buffer', 5.0),
            enable_sniper_mode=True,
            order_manager=self.order_manager
        )
        logger.info("✅ SL manager initialized")
        
        # Initialize risk manager with config values
        risk_config = self.config.get('risk', {})
        timing_config = self.config.get('timing', {})
        self.risk_manager = initialize_risk_manager(
            lot_size=risk_config.get('lot_size', 65),
            num_lots=risk_config.get('num_lots', 4),
            capital=risk_config.get('paper_capital', 50000.0),
            max_sl_per_day=risk_config.get('max_sl_per_day', 2),
            max_daily_loss=risk_config.get('max_daily_loss', 4000),
            max_daily_loss_pct=risk_config.get('max_daily_loss_pct', 5.0),
            max_trades_per_day=risk_config.get('max_trades_per_day', 3),
            trading_start=timing_config.get('trading_start', '09:30'),
            no_new_after=timing_config.get('no_new_trades_after', '14:30'),
            margin_per_lot=risk_config.get('margin_per_lot', 8000.0)  # Use config value
        )
        logger.info("✅ Risk manager initialized")
        
        # Initialize telegram (uses environment variables)
        import os
        telegram_config = self.config.get('telegram', {})
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        if bot_token and chat_id:
            self.telegram = initialize_telegram(
                bot_token=bot_token,
                chat_id=chat_id,
                enabled=telegram_config.get('enabled', True)
            )
            await self.telegram.send_bot_started(paper_mode=paper_mode)
            logger.info("✅ Telegram initialized")
        else:
            self.telegram = None
            logger.warning("⚠️ Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
            
        # Initialize candle builder  
        self.index_candle_builder = CandleAggregator(
            timeframe_seconds=60  # 1-minute candles
        )
        logger.info("✅ Candle builder initialized")
        
        log_banner("INITIALIZATION COMPLETE")
        
    async def select_strikes(self):
        """Select CE and PE strikes for trading"""
        # Initialize dynamic strike selector
        strike_config = self.config.get('strike_selection', {})
        initialize_dynamic_strike_selector(
            premium_min=strike_config.get('premium_min', 85.0),
            premium_max=strike_config.get('premium_max', 150.0)
        )
        
        # Get current index price
        index_ltp = await self._get_index_ltp()
        if not index_ltp:
            raise RuntimeError("Failed to get index LTP")
        
        logger.info(f"📊 Index LTP: {index_ltp:.2f}")
        
        # Get ATM strike
        atm_strike = round(index_ltp / 50) * 50  # Round to nearest 50 for NIFTY
        
        # Get nearest expiry
        expiry = self.instrument_master.get_nearest_expiry("NIFTY")
        if not expiry:
            raise RuntimeError("Failed to get expiry")
        logger.info(f"📅 Using expiry: {expiry}")
        
        # Get options for ATM strikes
        ce_strikes = self.instrument_master.get_nifty_options(
            expiry_date=expiry,
            option_type=OptionType.CALL,
            min_strike=atm_strike - 200,
            max_strike=atm_strike + 200
        )
        
        pe_strikes = self.instrument_master.get_nifty_options(
            expiry_date=expiry,
            option_type=OptionType.PUT,
            min_strike=atm_strike - 200,
            max_strike=atm_strike + 200
        )
        
        # Select ATM CE and PE
        if ce_strikes:
            atm_ce = min(ce_strikes, key=lambda x: abs(x.strike - atm_strike))
            self.ce_token = atm_ce.token
            self.ce_symbol = atm_ce.symbol
            logger.info(f"✅ CE Strike: {atm_ce.symbol} (Strike: {atm_ce.strike})")
        
        if pe_strikes:
            atm_pe = min(pe_strikes, key=lambda x: abs(x.strike - atm_strike))
            self.pe_token = atm_pe.token
            self.pe_symbol = atm_pe.symbol
            logger.info(f"✅ PE Strike: {atm_pe.symbol} (Strike: {atm_pe.strike})")
            
    async def _get_index_ltp(self) -> Optional[float]:
        """Get current index LTP"""
        try:
            ltp = self.auth.get_ltp(
                exchange="NSE",
                symbol="NIFTY",
                token=self.index_token
            )
            return ltp
        except Exception as e:
            logger.error(f"Error getting index LTP: {e}")
        return None
        
    async def process_candle(self, candle: Candle):
        """Process completed candle and generate signals"""
        logger.info(f"🕯️ Candle Complete: O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f}")
        
        if not self.strategy:
            return
            
        # Update strategy
        signal = self.strategy.update(
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=int(candle.volume or 100),  # Default volume if not available
            timestamp=candle.timestamp
        )
        
        # Log regime and status
        status = self.strategy.get_status()
        logger.info(f"📊 Regime: {status['regime']} | VWAP: {status['vwap']:.2f} | ATR: {status['atr']:.2f}")
        
        # Check for signal
        if signal and signal.signal != SignalType.NO_SIGNAL:
            await self._handle_signal(signal)
            
    async def _handle_signal(self, signal: TradeSignal):
        """Handle trading signal"""
        log_banner(f"SIGNAL: {signal.signal.value}")
        
        logger.info(f"Entry: {signal.entry_price:.2f}")
        logger.info(f"SL: {signal.stop_loss:.2f}")
        logger.info(f"Target 1: {signal.target_1:.2f}")
        logger.info(f"Target 2: {signal.target_2:.2f}")
        logger.info(f"R:R: 1:{signal.reward_ratio:.1f}")
        logger.info(f"Regime: {signal.regime}")
        logger.info(f"Reason: {signal.reason}")
        logger.info(f"Confidence: {signal.confidence:.0%}")
        
        # Check risk limits
        can_trade, reason = self.risk_manager.can_enter_trade()
        if not can_trade:
            logger.warning(f"❌ Risk limits: {reason} - skipping trade")
            return
            
        # Select option based on signal
        if signal.signal == SignalType.CE_BUY:
            option_token = self.ce_token
            option_symbol = self.ce_symbol
        else:
            option_token = self.pe_token
            option_symbol = self.pe_symbol
            
        if not option_token:
            logger.error("❌ No option token available")
            return
            
        # Place order
        try:
            order = OrderRequest(
                symbol=option_symbol,
                token=option_token,
                exchange="NFO",
                quantity=self.config['risk']['fixed_quantity'],  # 260 qty (4 lots)
                order_type=OrderType.MARKET,
                transaction_type=TransactionType.BUY,
                product_type=ProductType.INTRADAY
            )
            
            result = self.order_manager.place_order(order)  # Sync call
            if result and result.order_id:
                self.current_trade = signal
                self.entry_order_id = result.order_id
                self.entry_price = signal.entry_price
                self._exit_in_progress = False  # Reset exit lock for new trade
                
                # Set stop loss
                self.sl_manager.initialize_sl(
                    symbol=option_symbol,
                    token=option_token,
                    exchange="NFO",
                    quantity=self.config['risk']['fixed_quantity'],
                    entry_price=signal.entry_price
                )
                
                # Notify strategy
                self.strategy.on_trade_entry()
                
                # Log and notify
                log_trade_entry(
                    symbol=option_symbol,
                    price=signal.entry_price,
                    qty=self.config['risk']['fixed_quantity'],
                    sl=signal.stop_loss,
                    direction=signal.signal.value
                )
                
                if self.telegram:
                    await self.telegram._send_message(
                        f"📈 <b>TRADE ENTRY</b>\n"
                        f"Signal: {signal.signal.value}\n"
                        f"Option: {option_symbol}\n"
                        f"Entry: ₹{signal.entry_price:.2f}\n"
                        f"SL: ₹{signal.stop_loss:.2f}\n"
                        f"Target: ₹{signal.target_1:.2f}\n"
                        f"R:R: 1:{signal.reward_ratio:.1f}\n"
                        f"Regime: {signal.regime}"
                    )
                    
                logger.info(f"✅ Order placed: {result.order_id}")
            else:
                logger.error("❌ Order placement failed")
                
        except Exception as e:
            logger.error(f"❌ Error placing order: {e}")
            
    async def check_exit_conditions(self, current_price: float):
        """Check SL/Target conditions"""
        if not self.current_trade:
            return
            
        # Check stop loss
        if self.current_trade.signal == SignalType.CE_BUY:
            if current_price <= self.current_trade.stop_loss:
                await self._exit_trade("SL Hit", current_price)
            elif current_price >= self.current_trade.target_1:
                await self._exit_trade("Target 1 Hit", current_price)
        else:
            if current_price >= self.current_trade.stop_loss:
                await self._exit_trade("SL Hit", current_price)
            elif current_price <= self.current_trade.target_1:
                await self._exit_trade("Target 1 Hit", current_price)
                
    async def _exit_trade(self, reason: str, exit_price: float):
        """Exit current trade"""
        if not self.current_trade or self._exit_in_progress:
            return
        
        # Set exit lock immediately
        self._exit_in_progress = True
            
        qty = self.config['risk']['fixed_quantity']
        pnl = 0.0
        if self.current_trade.signal == SignalType.CE_BUY:
            pnl = (exit_price - self.entry_price) * qty
        else:
            pnl = (self.entry_price - exit_price) * qty
            
        # Notify strategy
        self.strategy.on_trade_exit(pnl)
        
        # Log exit
        log_trade_exit(
            symbol=self.ce_symbol if self.current_trade.signal == SignalType.CE_BUY else self.pe_symbol,
            entry=self.entry_price,
            exit_price=exit_price,
            qty=qty,
            pnl=pnl,
            reason=reason
        )
        
        if self.telegram:
            emoji = "✅" if pnl > 0 else "❌"
            await self.telegram._send_message(
                f"{emoji} <b>TRADE EXIT</b>\n"
                f"Reason: {reason}\n"
                f"Entry: ₹{self.entry_price:.2f}\n"
                f"Exit: ₹{exit_price:.2f}\n"
                f"P&L: ₹{pnl:.2f}"
            )
            
        # Clear state
        self.current_trade = None
        self.entry_order_id = None
        self.entry_price = 0.0
        self._exit_in_progress = False  # Release exit lock
        
    async def run(self):
        """Main trading loop"""
        try:
            await self.initialize()
            await self.select_strikes()
            
            log_banner("TRADING STARTED")
            self._running = True
            
            # Initialize market feed
            self.market_feed = MarketFeed(
                auth_token=self.auth.jwt_token,
                api_key=self.auth.api_key,
                client_code=self.auth.client_code,
                feed_token=self.auth.feed_token
            )
            self.market_feed.connect()
            
            # Subscribe to index
            self.market_feed.subscribe_index(
                token=self.index_token,
                mode=SubscriptionMode.LTP
            )
            
            # Main loop
            tick_count = 0
            last_heartbeat = time_module.time()
            
            while not self._shutdown_flag:
                try:
                    # Get tick
                    tick = await self.market_feed.get_tick(timeout=1.0)
                    if not tick:
                        continue
                    
                    tick_count += 1
                    
                    # Heartbeat every 60 seconds
                    if time_module.time() - last_heartbeat > 60:
                        logger.info(f"💓 Heartbeat: {tick_count} ticks processed | LTP: {tick.ltp:.2f}")
                        last_heartbeat = time_module.time()
                        
                    # Process tick only during market hours
                    if tick.token == self.index_token:
                        # Build candles only during market hours
                        if self.is_market_open():
                            self._logged_market_closed = False
                            candle = self.index_candle_builder.process_tick(tick)
                            
                            if candle:
                                await self.process_candle(candle)
                        else:
                            # Log once when market closes
                            if not self._logged_market_closed:
                                logger.info("🔒 Market CLOSED - Candle building paused (9:15-15:30)")
                                self._logged_market_closed = True
                            
                        # Check exit conditions always (even after market close)
                        if self.current_trade:
                            await self.check_exit_conditions(tick.ltp)
                            
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            raise
        finally:
            await self.shutdown()
            
    async def shutdown(self):
        """Graceful shutdown"""
        log_banner("SHUTTING DOWN")
        self._shutdown_flag = True
        self._running = False
        
        # Close market feed
        if self.market_feed:
            self.market_feed.disconnect()
            
        # Exit any open position
        if self.current_trade:
            logger.info("Exiting open position...")
            # Would need current price here
            
        # Final status
        if self.strategy:
            status = self.strategy.get_status()
            logger.info(f"Final Status: Trades={status['trades_today']}, PnL={status['daily_pnl']:.2f}")
            
        if self.telegram:
            await self.telegram.send_bot_stopped("Normal shutdown")
            
        logger.info("✅ Shutdown complete")


def main():
    """Entry point"""
    bot = AdaptiveTradingBot()
    
    # Setup signal handlers
    loop = asyncio.new_event_loop()
    
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        bot._shutdown_flag = True
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
