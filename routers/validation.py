"""
validation.py

Simple validation helpers for date strings and sensor codes using
regular expressions and fixed date boundaries.

Author: Dominique Anne Lee
"""

import re


def validateDate(date: str) -> str | bool:
    """
    Validates and sanitizes a date string.

    Ensures the input matches ISO format (YYYY-MM-DD) and falls within
    a predefined valid range.

    Args:
        date: Raw date string.

    Returns:
        Sanitized date string if valid; otherwise False.
    """
    x = re.fullmatch(r"20[0-9]{2}-[0-1][0-9]-[0-3][0-9]", date)
    san_date = x.group() if x is not None else ""

    if san_date == "":
        return False

    if san_date > "2025-12-31" or san_date < "2018-04-08":
        return False

    return san_date


def validateCode(code: str) -> str | bool:
    """
    Validates and sanitizes a sensor code.

    Ensures the code matches the expected format used in the system.

    Args:
        code: Raw sensor code string.

    Returns:
        Sanitized sensor code if valid; otherwise False.
    """
    x = re.fullmatch(r"[0-9]{5}_TL[0-9]+", code)
    san_code = x.group() if x is not None else False
    return san_code