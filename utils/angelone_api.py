from logger import logger
import requests
from utils.login_manager import LoginManager
from config import ORDER_TYPE
import pandas as pd
import math
import time
from datetime import timedelta, datetime
import pytz
from dateutil import parser
from threading import Event

class AngelOneClient:
    def __init__(self, config, strategy, ltp_manager=None):
        self.config = config
        self.strategy_name = strategy['name']
        self.smartapi = LoginManager.get_client(config)
        self.symbolDf = None
        self.spotSymInfo = None
        self.ltp_manager = ltp_manager

    def _log(self, level, message):
        log_prefix = f"[{self.config['username']} | {self.strategy_name}]"
        getattr(logger, level)(f"{log_prefix} {message}")

    def get_symbol_data(self, SYMBOL):
        url = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json'
        d = requests.get(url).json()
        token_df = pd.DataFrame.from_dict(d)

        token_df['expiry'] = pd.to_datetime(token_df['expiry']).dt.date
        # token_df = token_df.dropna(subset=['expiry'])
        self._log('info', f"ceck: {token_df[(token_df.name == SYMBOL) & (token_df.exch_seg == 'BSE')]}")
        spot_rows = token_df[(token_df.name == SYMBOL) & (token_df.exch_seg == 'BSE')]
        if spot_rows.empty:
            self._log('warning', f"SYMBOL '{SYMBOL}' spot not found. Using fallback token.")
            self.spotSymInfo = {
                'symbol': SYMBOL,
                'token': '99926000',
                'exch_seg': 'NSE'
            }
        else:
            self.spotSymInfo = spot_rows.iloc[0].to_dict()

        token_df = token_df.astype({'strike': float})
        expiryList = token_df[(token_df.name == SYMBOL) & (token_df.instrumenttype == 'OPTIDX')]['expiry'].unique().tolist()
        if not expiryList:
            raise Exception(f"No expiries found for SYMBOL '{SYMBOL}'!")

        expiryList.sort()
        recentExpiry = expiryList[0]

        self.symbolDf = token_df[
            (token_df.name == SYMBOL) &
            (token_df.expiry == recentExpiry) &
            (token_df.instrumenttype == 'OPTIDX')
        ]

        self._log('info', f"Using expiry: {self.symbolDf}")

    def get_ltp(self, exchange, symbol, token):
        if self.ltp_manager:
            cached = self.ltp_manager.get_cached_ltp(token)
            if cached is not None:
                return cached
        try:
            response = self.smartapi.ltpData(exchange, symbol, token)
            return response['data']['ltp']
        except Exception as e:
            self._log('error', f"LTP fetch failed for {symbol}: {e}")
            return None


    @staticmethod
    def round_up_to_tick(price, tick_size=0.05):
        return round(math.ceil(price / tick_size) * tick_size, 2)

    def place_order(self, token, symbol, qty, buy_sell, variety='NORMAL', ordertype='MARKET', triggerprice=0, price=0):
        try:
            orderparams = {
                "variety": variety,
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": buy_sell,
                "exchange": 'BFO',
                "ordertype": ordertype,
                "producttype": ORDER_TYPE,
                "duration": "DAY",
                "price": self.round_up_to_tick(price),
                "squareoff": "0",
                "stoploss": "0",
                "quantity": qty,
                "triggerprice": self.round_up_to_tick(triggerprice),
                "ordertag": 'DFT'
            }
            order_id = self.smartapi.placeOrder(orderparams)
            self._log('info', f"Order placed: {order_id} -> {orderparams}")
            return order_id
        except Exception as e:
            self._log('exception', f"Order failed: {e}")

    def get_executed_price(self, orderid):
        for attempt in range(4):
            try:
                orderbook = self.smartapi.orderBook()
                df = pd.DataFrame(orderbook['data'])
                row = df[df['orderid'] == str(orderid)]
                if not row.empty and int(row['filledshares'].iloc[0]) == int(row['quantity'].iloc[0]):
                    price = float(row['averageprice'].iloc[0])
                    self._log('info', f"Executed price for order {orderid}: {price}")
                    return price
            except Exception as e:
                self._log('error', f"Error getting executed price for {orderid}: {e}")
            time.sleep(2)
        return 0

    def get_previous_1min_close(self, exchange, symbol_token, executed_time, retries=3, delay=2):
        for attempt in range(retries):
            try:
                to_date = executed_time.strftime('%Y-%m-%d %H:%M')
                from_date = (executed_time - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M')

                historicParam = {
                    "exchange": exchange,
                    "symboltoken": str(symbol_token),
                    "interval": "ONE_MINUTE",
                    "fromdate": from_date,
                    "todate": to_date
                }

                data = self.smartapi.getCandleData(historicParam)
                self._log('warning', f"candles {data}")

                candles = data.get("data", [])
                if len(candles) < 2:
                    self._log('warning', f"Not enough candles yet (attempt {attempt + 1}/{retries})")
                    time.sleep(delay)
                    continue

                prev_candle = None
                for c in reversed(candles):
                    try:
                        candle_time = parser.isoparse(c[0]).astimezone(pytz.timezone('Asia/Kolkata'))
                    except Exception as e:
                        self._log('error', f"Failed to parse candle time: {c[0]}, error: {e}")
                        continue

                    if candle_time == executed_time:
                        prev_candle = c
                        break

                if prev_candle is None:
                    raise Exception("No previous candle found before executed time")
                self._log('warning', f"prev_candle {prev_candle}")
                prev_close = float(prev_candle[4])
                self._log('info', f"Previous close before executed_time={executed_time}: {prev_close}")
                return prev_close

            except Exception as e:
                self._log('error', f"Error fetching candle data: {e}")
                time.sleep(delay)

        raise Exception("Not enough candle data to get previous close")


    def monitor_sl_with_reentry_and_static_sl(self, token, symbol, lotsize, entry_price, signal_price, trans_type, SL, SL_LIMIT, LOT, end_time, leg_key, other_leg_key, shared_leg_status):
        sl_price = self.round_up_to_tick(
            signal_price * (1 + SL / 100) if trans_type == 'SELL' else signal_price * (1 - SL / 100)
        )
        self._log('info', f"Monitoring SL for {symbol} - Initial SL Price: {sl_price}")

        is_position_open = True
        has_reentered = False
        reentry_price = entry_price
        use_dynamic_sl = True
        static_sl_price = None
        static_exit_no_reentry = False

        while True:
            current_time = datetime.now(pytz.timezone('Asia/Kolkata'))
            if current_time >= end_time:
                if is_position_open:
                    self._log('info', f"END TIME reached, exiting open position for {symbol}...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                    except Exception as e:
                        self._log('error', f"Error placing END TIME exit order for {symbol}: {e}")
                else:
                    self._log('info', f"END TIME reached, no open position for {symbol}. Exiting monitor.")
                break

            ltp = self.get_ltp('BFO', symbol, token)
            if ltp is None:
                self._log('warning', f"LTP fetch error for {symbol}, retrying...")
                time.sleep(2)
                continue

            # ✅ Use dynamic or static SL for display
            current_sl = sl_price if use_dynamic_sl else static_sl_price
            self._log('info', f"LTP: {ltp} | SL Price: {current_sl} | Entry Price: {entry_price} | Symbol: {symbol} | Position Open: {is_position_open} | Reentered: {has_reentered} | Dynamic SL: {use_dynamic_sl}")

            if is_position_open and not has_reentered:
                if use_dynamic_sl and ((trans_type == 'SELL' and ltp >= sl_price) or (trans_type == 'BUY' and ltp <= sl_price)):
                    self._log('info', f"SL HIT for {symbol}, exiting via MARKET order...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                        is_position_open = False
                        shared_leg_status[leg_key]['sl_hit'] = True
                        shared_leg_status[leg_key]['reentry_count'] = shared_leg_status[leg_key].get('reentry_count', 0) + 1

                        # Notify other leg to switch to static SL
                        if shared_leg_status[other_leg_key]['sl_hit'] is False:
                            shared_leg_status[other_leg_key]['switch_to_static_sl'] = True
                            self._log('info', f"Notified other leg {other_leg_key} to switch to static SL")

                        self._log('info', f"SL Exit done for {symbol}. Monitoring for possible re-entry...")
                    except Exception as e:
                        self._log('error', f"Error placing SL exit order for {symbol}: {e}")

                elif not use_dynamic_sl and ((trans_type == 'SELL' and ltp >= static_sl_price) or (trans_type == 'BUY' and ltp <= static_sl_price)):
                    self._log('info', f"STATIC SL HIT for {symbol}, exiting...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                        is_position_open = False
                        shared_leg_status[leg_key]['sl_hit'] = True
                        shared_leg_status[leg_key]['reentry_count'] += 1
                        if static_exit_no_reentry:
                            self._log('info', f"STATIC SL HIT after first leg SL. No re-entry allowed. Exiting monitor for {symbol}.")
                            break  # 🚨 Exit thread permanently
                        else:
                            self._log('info', f"Static SL HIT. Monitoring for possible re-entry...")
                    except Exception as e:
                        self._log('error', f"Error placing static SL exit order for {symbol}: {e}")

            else:
                if not has_reentered and shared_leg_status[leg_key]['reentry_count'] == 1:
                    self._log('info', f"Checking for re-entry | LTP: {ltp} | rentry Price: {reentry_price}")
                    if (trans_type == 'SELL' and ltp <= reentry_price) or (trans_type == 'BUY' and ltp >= reentry_price):
                        self._log('info', f"Re-entry condition met for {symbol}, entering again...")
                        try:
                            self.place_order(token, symbol, LOT * lotsize, trans_type)
                            is_position_open = True
                            has_reentered = True
                            time.sleep(2)
                            reentry_order_id = self.smartapi.orderBook()['data'][-1]['orderid']
                            reentry_entry_price = self.get_executed_price(reentry_order_id)
                            executed_time_reentry = datetime.now(pytz.timezone('Asia/Kolkata')).replace(second=0, microsecond=0) - timedelta(minutes=1)

                            entry_price = reentry_entry_price
                            try:
                                signal_price_reentry = self.get_previous_1min_close('BFO', token, executed_time_reentry)
                                sl_price = self.round_up_to_tick(
                                    signal_price_reentry * (1 + SL / 100) if trans_type == 'SELL' else signal_price_reentry * (1 - SL / 100)
                                )
                                self._log('info', f"Re-entry done for {symbol} at {entry_price}. New Signal Price: {signal_price_reentry}, New SL price: {sl_price}")
                            except Exception as e:
                                self._log('error', f"Error fetching candle data for re-entry SL: {e}")
                                sl_price = self.round_up_to_tick(
                                    entry_price * (1 + SL / 100) if trans_type == 'SELL' else entry_price * (1 - SL / 100)
                                )
                                self._log('info', f"Fallback: SL set using entry price: {entry_price} -> SL: {sl_price}")
                                self._log('info', f"Re-entry done for {symbol} at {entry_price}. New Signal Price: {entry_price}, New SL price: {sl_price}")

                        except Exception as e:
                            self._log('error', f"Error placing re-entry order for {symbol}: {e}")

                elif has_reentered and ((trans_type == 'SELL' and ltp >= sl_price) or (trans_type == 'BUY' and ltp <= sl_price)):
                    self._log('info', f"SL HIT after re-entry for {symbol}, exiting and ending monitor...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                    except Exception as e:
                        self._log('error', f"Error placing SL exit order after re-entry for {symbol}: {e}")
                    break

            # ✅ Switch to static SL using entry price instead of previous 1-min close
            if shared_leg_status[leg_key].get('switch_to_static_sl') and use_dynamic_sl:
                try:
                    # ❗Use entry_price as base for static SL
                    static_sl_price = entry_price
                    use_dynamic_sl = False
                    shared_leg_status[leg_key]['switch_to_static_sl'] = False  # reset
                    static_exit_no_reentry = True
                    self._log('info', f"Switched to static SL for {symbol} - Static SL Price: {static_sl_price}")
                except Exception as e:
                    self._log('error', f"Error switching to static SL for {symbol}: {e}")
            
            # ✅ Exit loop if no reentry is allowed and position is closed
            if not is_position_open and (has_reentered or static_exit_no_reentry):
                self._log('info', f"SL exit complete and re-entry not expected for {symbol}. Exiting monitor.")
                break

            time.sleep(3)

    def monitor_sl_static_only(self, token, symbol, lotsize, entry_price, signal_price, trans_type, SL, SL_LIMIT, LOT, end_time, leg_key, other_leg_key, shared_leg_status):
        sl_price = self.round_up_to_tick(
            signal_price * (1 + SL / 100) if trans_type == 'SELL' else signal_price * (1 - SL / 100)
        )
        self._log('info', f"[StaticOnly] Monitoring SL for {symbol} - Initial SL Price: {sl_price}")

        is_position_open = True
        use_dynamic_sl = True
        static_sl_price = None

        while True:
            current_time = datetime.now(pytz.timezone('Asia/Kolkata'))
            if current_time >= end_time:
                if is_position_open:
                    self._log('info', f"[StaticOnly] END TIME reached, exiting open position for {symbol}...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                    except Exception as e:
                        self._log('error', f"[StaticOnly] Error placing END TIME exit order for {symbol}: {e}")
                else:
                    self._log('info', f"[StaticOnly] END TIME reached, no open position for {symbol}. Exiting monitor.")
                break

            # ltp = self.get_ltp('BFO', symbol, token)
            ltp = self.get_ltp('BFO', symbol, token)

            if ltp is None:
                self._log('warning', f"[StaticOnly] LTP fetch error for {symbol}, retrying...")
                time.sleep(2)
                continue

            current_sl = sl_price if use_dynamic_sl else static_sl_price
            self._log('info', f"[StaticOnly] LTP: {ltp} | SL Price: {current_sl} | Entry Price: {entry_price} | Symbol: {symbol} | Position Open: {is_position_open} | Dynamic SL: {use_dynamic_sl}")

            if is_position_open:
                if use_dynamic_sl and ((trans_type == 'SELL' and ltp >= sl_price) or (trans_type == 'BUY' and ltp <= sl_price)):
                    self._log('info', f"[StaticOnly] SL HIT for {symbol}, exiting...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                        is_position_open = False
                        shared_leg_status[leg_key]['sl_hit'] = True
                        if shared_leg_status[other_leg_key]['sl_hit'] is False:
                            shared_leg_status[other_leg_key]['switch_to_static_sl'] = True
                            self._log('info', f"[StaticOnly] Notified other leg {other_leg_key} to switch to static SL")
                    except Exception as e:
                        self._log('error', f"[StaticOnly] Error placing SL exit order for {symbol}: {e}")
                elif not use_dynamic_sl and ((trans_type == 'SELL' and ltp >= static_sl_price) or (trans_type == 'BUY' and ltp <= static_sl_price)):
                    self._log('info', f"[StaticOnly] STATIC SL HIT for {symbol}, exiting...")
                    try:
                        self.place_order(token, symbol, LOT * lotsize, 'BUY' if trans_type == 'SELL' else 'SELL')
                        is_position_open = False
                        shared_leg_status[leg_key]['sl_hit'] = True
                        break
                    except Exception as e:
                        self._log('error', f"[StaticOnly] Error placing STATIC SL exit order for {symbol}: {e}")

            if shared_leg_status[leg_key].get('switch_to_static_sl') and use_dynamic_sl:
                try:
                    static_sl_price = entry_price
                    use_dynamic_sl = False
                    shared_leg_status[leg_key]['switch_to_static_sl'] = False
                    self._log('info', f"[StaticOnly] Switched to STATIC SL for {symbol} - Static SL Price: {static_sl_price}")
                except Exception as e:
                    self._log('error', f"[StaticOnly] Error switching to STATIC SL for {symbol}: {e}")

            # ✅ Exit once position is closed
            if not is_position_open:
                self._log('info', f"[StaticOnly] Position closed for {symbol}. Stopping monitor.")
                break


            time.sleep(3)