# main.py
from trader import Trader
from config import get_user_credentials, STRATEGIES
from utils.ltp_manager import LTPManager
from utils.login_manager import LoginManager

if __name__ == '__main__':
    traders = []
    ltp_managers = []
    users = get_user_credentials()

    for user in users:
        smartapi = LoginManager.get_client(user)
        ltp_manager = LTPManager(smartapi)
        ltp_managers.append(ltp_manager)

        for strategy in STRATEGIES:
            traders.append(Trader(user, strategy, ltp_manager))

    for trader in traders:
        trader.start()

    for trader in traders:
        trader.join()

    for ltp_manager in ltp_managers:
        ltp_manager.stop()
