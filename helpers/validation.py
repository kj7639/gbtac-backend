"""
validation.py (helpers)

Date and sensor code validation helpers that use the actual database date
boundaries (newest/oldest) resolved at import time, plus a 7-day forecast
window for future date allowance. Used by the graph endpoints that serve
the Ambient Temperature and Wall Temperature dashboards.

Author: Dominique Anne Lee, Kiera Johnson
"""

import re
from helpers.dates import get_newest, get_oldest, str_to_date
from datetime import timedelta

newest = str_to_date(get_newest())
oldest = str_to_date(get_oldest())
MAX_FORECAST_DAYS = timedelta(days=7)


def validateDate(str_date: str) -> str | bool:
    """
    Validates and sanitizes a date string within allowed bounds.

    Ensures the input matches ISO date format and falls within the valid
    dataset range, including a future allowance for forecasted data.

    Args:
        str_date: Raw date string (expected format YYYY-MM-DD).

    Returns:
        Sanitized ISO date string if valid; otherwise False.

    Notes:
        Dates are constrained between the oldest available data point and
        up to 7 days beyond the newest reading to support forecast queries.
    """
    x = re.search(r"20[0-9]{2}-[0-1][0-9]-[0-3][0-9]", str_date)
    san_date = x.group() if x is not None else ""

    if san_date == "":
        return False

    date_object = str_to_date(san_date)

    if date_object > newest + MAX_FORECAST_DAYS or date_object < oldest:
        return False

    return san_date


def validateCode(code: str) -> str | bool:
    """
    Validates and sanitizes a sensor code.

    Ensures the code matches the expected sensor naming pattern used
    in the database schema.

    Args:
        code: Raw sensor code string.

    Returns:
        Sanitized sensor code if valid; otherwise False.
    """
    x = re.search(r"[0-9]{5}_TL[0-9]+", code)
    san_code = x.group() if x is not None else False
    return san_code