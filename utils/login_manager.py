from SmartApi.smartConnect import SmartConnect
import pyotp
from logger import logger

class LoginManager:
    _instances = {}

    @staticmethod
    def get_client(user_config):
        user_key = user_config['username']
        if user_key not in LoginManager._instances:
            logger.info(f"[{user_key}] Logging in...")
            smartapi = SmartConnect(api_key=user_config['apikey'])
            data = smartapi.generateSession(
                user_config['username'],
                user_config['pwd'],
                pyotp.TOTP(user_config['token']).now()
            )
            smartapi.session_data = data['data']
            LoginManager._instances[user_key] = smartapi
            logger.info(f"[{user_key}] Logged in")
        return LoginManager._instances[user_key]