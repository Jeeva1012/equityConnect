# utils/ltp_manager.py
import threading
import time
from logger import logger
from datetime import datetime

class LTPManager:
    def __init__(self, smartapi, fetch_interval=2):
        self.smartapi = smartapi
        self.fetch_interval = fetch_interval
        self._ltp_cache = {}
        self._tokens = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._thread.start()

    def register_token(self, token, symbol, exchange='BFO'):
        with self._lock:
            self._tokens.add((exchange, symbol, token))

    
    def get_cached_ltp(self, token):
        ltp = self._ltp_cache.get(token)
        return ltp


    def _fetch_loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                tokens_snapshot = list(self._tokens)
            for exchange, symbol, token in tokens_snapshot:
                try:
                    response = self.smartapi.ltpData(exchange, symbol, token)
                    ltp = response['data']['ltp']
                    self._ltp_cache[token] = ltp
                    logger.debug(f"[LTPManager] Updated LTP for {symbol} ({token}): {ltp}")
                except Exception as e:
                    logger.warning(f"[LTPManager] Failed to fetch LTP for {symbol} ({token}): {e}")
            time.sleep(self.fetch_interval)

    def stop(self):
        self._stop_event.set()
        self._thread.join()
