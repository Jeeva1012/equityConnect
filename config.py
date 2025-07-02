
import os
from dotenv import load_dotenv

load_dotenv()

def get_user_credentials():
    user_configs = []
    user_count = int(os.getenv("USER_COUNT", 0))

    for i in range(1, user_count + 1):
        config = {
            "username": os.getenv(f"USER{i}_USERNAME"),
            "apikey": os.getenv(f"USER{i}_APIKEY"),
            "pwd": os.getenv(f"USER{i}_PWD"),
            "token": os.getenv(f"USER{i}_TOKEN")
        }
        if all(config.values()):
            user_configs.append(config)
        else:
            print(f"Missing details for USER{i}, skipping.")

    return user_configs

ORDER_TYPE = 'INTRADAY'

STRATEGIES = [
    {
        "name": "Strangle-S1",
        "type": "strangle",
        "symbol": "SENSEX",
        "sl_monitoring_type": "without_reentry",
        "start_time": (11, 2, 0),
        "end_time": (14, 59, 0),
        "sl": 25,
        "sl_limit": 1,
        "lot": 1,
        "otm": 1
    },
    {
        "name": "Straddle-S1",
        "type": "straddle",
        "symbol": "SENSEX",
        "sl_monitoring_type": "with_reentry",
        "start_time": (11, 2, 0),
        "end_time": (14, 59, 0),
        "sl": 20,
        "sl_limit": 1,
        "lot": 1
    },
    #    {
    #     "name": "Strangle-S2",
    #     "type": "strangle",
    #     "symbol": "SENSEX",
    #     "sl_monitoring_type": "without_reentry",
    #     "start_time": (9, 18, 0),
    #     "end_time": (15, 14, 0),
    #     "sl": 77,
    #     "sl_limit": 1,
    #     "lot": 1,
    #     "otm": 1
    # },
]