"""
dates.py

Utility functions for querying the newest and oldest timestamps from GBTAC_data
and converting between date strings and date objects. Used to establish the
valid date range for the Ambient Temperature and Wall Temperature dashboards.

Author: Kiera Johnson
"""

from datetime import datetime, date
import pyodbc
from config import connection_str


def get_newest() -> str:
    """
    Retrieves the most recent timestamp from the GBTAC_data table.

    Returns:
        ISO formatted date string (YYYY-MM-DD) representing the newest timestamp.
    """
    # Open database connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT TOP 1 ts
        FROM GBTAC_data
        ORDER BY ts DESC;
    """

    # Execute query and fetch result
    curs.execute(query)
    rows = curs.fetchall()

    conn.close()

    res = rows[0][0].date()
    return res.isoformat()


def get_oldest() -> str:
    """
    Retrieves the earliest timestamp from the GBTAC_data table.

    Returns:
        ISO formatted date string (YYYY-MM-DD) representing the oldest timestamp.
    """
    # Open database connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT TOP 1 ts
        FROM GBTAC_data
        ORDER BY ts ASC;
    """

    # Execute query and fetch result
    curs.execute(query)
    rows = curs.fetchall()

    conn.close()

    res = rows[0][0].date()
    return res.isoformat()


def str_to_date(str_date: str) -> date:
    """
    Converts an ISO formatted date string into a date object.

    Args:
        str_date: Date string in ISO format (YYYY-MM-DD).

    Returns:
        Corresponding date object.
    """
    return datetime.fromisoformat(str_date).date()


def date_to_str(d: date | datetime) -> str:
    """
    Converts a date or datetime object into an ISO formatted string.

    Args:
        d: Date or datetime object.

    Returns:
        ISO formatted date string (YYYY-MM-DD).
    """
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()