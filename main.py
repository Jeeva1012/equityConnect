# main.py
from trader import Trader
from config import get_user_credentials, STRATEGIES

if __name__ == '__main__':
    traders = []
    users = get_user_credentials()

    for user in users:
        for strategy in STRATEGIES:
            traders.append(Trader(user, strategy))

    for trader in traders:
        trader.start()

    for trader in traders:
        trader.join()
