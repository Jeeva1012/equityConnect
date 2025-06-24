import logging 
import sys 
from  datetime import datetime
td = datetime.today().date()

logging.basicConfig(filename=f"ANGLE_STRDLE_{td}.log", format='%(asctime)s - %(levelname)s - %(message)s',level=logging.INFO) 
logger=logging.getLogger() 

stdout_handler = logging.StreamHandler(sys.stdout)
logger.addHandler(stdout_handler)
