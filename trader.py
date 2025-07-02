# trader.py

from utils.angelone_api import AngelOneClient
import threading
import time
import pytz
from datetime import datetime, timedelta
from logger import logger
from utils.login_manager import LoginManager

class Trader(threading.Thread):
    def __init__(self, user_config, strategy, ltp_manager):
        threading.Thread.__init__(self)
        self.config = user_config
        self.strategy = strategy
        self.strategy_name = strategy['name']
        self.smartapi = LoginManager.get_client(user_config)
        self.ltp_manager = ltp_manager
        self.client = AngelOneClient(user_config, strategy, ltp_manager)



    def run(self):
        try:
            self.client.get_symbol_data(self.strategy['symbol'])

            start_time = datetime.now(pytz.timezone('Asia/Kolkata'))
            trigger_time = start_time.replace(hour=self.strategy['start_time'][0], minute=self.strategy['start_time'][1], second=self.strategy['start_time'][2])
            wait_time = max(0, (trigger_time - start_time).total_seconds())
            logger.info(f"[{self.config['username']}][{self.strategy_name}] Waiting {wait_time} seconds to start {self.strategy['type']} strategy")
            time.sleep(wait_time)

            ltp = self.client.get_ltp('BSE', self.client.spotSymInfo['symbol'], self.client.spotSymInfo['token'])
            logger.info(f"[{self.config['username']}][{self.strategy_name}] ltp: {ltp}")

            if not ltp:
                raise ValueError("LTP fetch failed for spot symbol")

            if self.strategy['symbol'] == 'SENSEX': 
                round_value = 100 
            else:
                round_value = 50

            atm_strike = round(ltp / round_value) * round_value

            if self.strategy['type'] == 'strangle':
                ce_strike = atm_strike + (round_value * self.strategy['otm'])
                pe_strike = atm_strike - (round_value * self.strategy['otm'])
            else:  # straddle
                ce_strike = atm_strike
                pe_strike = atm_strike

            logger.info(f"[{self.config['username']}][{self.strategy_name}] CE STRIKE: {ce_strike}")
            logger.info(f"[{self.config['username']}][{self.strategy_name}] PE STRIKE: {pe_strike}")

            ce_match = self.client.symbolDf[
                (self.client.symbolDf.strike == ce_strike * 100) & (self.client.symbolDf.symbol.str.endswith('CE'))
            ]
            if ce_match.empty:
                raise ValueError(f"CE symbol not found for strike {ce_strike}")

            pe_match = self.client.symbolDf[
                (self.client.symbolDf.strike == pe_strike * 100) & (self.client.symbolDf.symbol.str.endswith('PE'))
            ]
            if pe_match.empty:
                raise ValueError(f"PE symbol not found for strike {pe_strike}")

            ce = ce_match.iloc[0]
            pe = pe_match.iloc[0]

            ce_order_id = self.client.place_order(ce['token'], ce['symbol'], self.strategy['lot'] * int(ce['lotsize']), 'SELL')
            pe_order_id = self.client.place_order(pe['token'], pe['symbol'], self.strategy['lot'] * int(pe['lotsize']), 'SELL')

            ce_price = self.client.get_executed_price(ce_order_id)
            ce_time = datetime.now(pytz.timezone('Asia/Kolkata')).replace(second=0, microsecond=0) - timedelta(minutes=1)
            try:
                ce_signal_price = self.client.get_previous_1min_close('BFO', ce['token'], ce_time)
            except Exception as e:
                logger.warning(f"[{self.config['username']}][{self.strategy_name}] Failed to get candle for CE. Fallback to entry price.")
                ce_signal_price = ce_price

            pe_price = self.client.get_executed_price(pe_order_id)
            pe_time = datetime.now(pytz.timezone('Asia/Kolkata')).replace(second=0, microsecond=0) - timedelta(minutes=1)
            try:
                pe_signal_price = self.client.get_previous_1min_close('BFO', pe['token'], pe_time)
            except Exception as e:
                logger.warning(f"[{self.config['username']}][{self.strategy_name}] Failed to get candle for PE. Fallback to entry price.")
                pe_signal_price = pe_price

            logger.info(f"[{self.config['username']}][{self.strategy_name}] Entry CE: {ce_price}, Signal Price CE: {ce_signal_price}")
            logger.info(f"[{self.config['username']}][{self.strategy_name}] Entry PE: {pe_price}, Signal Price PE: {pe_signal_price}")

            end_time = datetime.now(pytz.timezone('Asia/Kolkata')).replace(hour=self.strategy['end_time'][0], minute=self.strategy['end_time'][1], second=self.strategy['end_time'][2])

            sl_status = {
                'CE': {
                    'sl_hit': False,
                    'lock': threading.Lock(),
                    'reentry_count': 0,
                    'switch_to_static_sl': False
                },
                'PE': {
                    'sl_hit': False,
                    'lock': threading.Lock(),
                    'reentry_count': 0,
                    'switch_to_static_sl': False
                }
            }

            monitor_method = {
                "with_reentry": self.client.monitor_sl_with_reentry_and_static_sl,
                "without_reentry": self.client.monitor_sl_static_only
            }.get(self.strategy.get("sl_monitoring_type", "with_reentry"))

            self.ltp_manager.register_token(ce['token'], ce['symbol'], 'BFO')
            self.ltp_manager.register_token(pe['token'], pe['symbol'], 'BFO')


            threads = []
            if ce_price > 0:
                ce_thread = threading.Thread(target=monitor_method, args=(
                    ce['token'], ce['symbol'], int(ce['lotsize']), ce_price, ce_signal_price, 'SELL',
                    self.strategy['sl'], self.strategy['sl_limit'], self.strategy['lot'], end_time,
                    'CE', 'PE', sl_status
                ))
                ce_thread.start()
                threads.append(ce_thread)

            if pe_price > 0:
                pe_thread = threading.Thread(target=monitor_method, args=(
                    pe['token'], pe['symbol'], int(pe['lotsize']), pe_price, pe_signal_price, 'SELL',
                    self.strategy['sl'], self.strategy['sl_limit'], self.strategy['lot'], end_time,
                    'PE', 'CE', sl_status
                ))
                pe_thread.start()
                threads.append(pe_thread)

            for t in threads:
                t.join()

        except Exception as e:
            logger.exception(f"[{self.config['username']}][{self.strategy_name}] Unhandled error: {e}")