"""
validation.py (helpers)

Date and sensor code validation helpers that use the actual database date
boundaries (newest/oldest) resolved at import time, plus a 7-day forecast
window for future date allowance. Used by the graph endpoints that serve
the Ambient Temperature and Wall Temperature dashboards.

Author: Dominique Anne Lee
"""

import re
from helpers.dates import get_newest, get_oldest, str_to_date
from datetime import timedelta

newest = str_to_date(get_newest())
oldest = str_to_date(get_oldest())
MAX_FORECAST_DAYS = timedelta(days=7)

def validateDate(str_date):
    x = re.search("20[0-9]{2}-[0-1][0-9]-[0-3][0-9]", str_date)
    san_date = x.group() if x != None else ""

    if san_date == "":
        return False
    
    date_object = str_to_date(san_date)
    
    if date_object > newest + MAX_FORECAST_DAYS or date_object < oldest:
        return False

    return san_date

def validateCode(code):
    x = re.search("[0-9]{5}_TL[0-9]+", code)
    san_code = x.group() if x != None else False
    return san_code