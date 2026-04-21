"""
routers/__init__.py

Shared imports and constants used by all router modules. Establishes the
database connection string, validation helpers, and date boundary constants
(NEWEST / OLDEST) that are resolved once at startup.

Author: Dominique Anne Lee
"""

from fastapi import APIRouter
import pyodbc
from config import connection_str
from helpers.validation import validateDate, validateCode
from helpers.dates import get_oldest, get_newest, str_to_date, date_to_str


# any additional details
SENSOR_PRE = "SaitSolarLab_"
NEWEST = get_newest()
OLDEST = get_oldest()