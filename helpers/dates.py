"""
dates.py

Utility functions for querying the newest and oldest timestamps from GBTAC_data
and converting between date strings and date objects. Used to establish the
valid date range for the Ambient Temperature and Wall Temperature dashboards.

Author: Dominique Anne Lee
"""

from datetime import datetime, date
import pyodbc
from config import connection_str

def get_newest():
    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT TOP 1 ts
        FROM GBTAC_data
        ORDER BY ts DESC;
        """ 

    #query database
    curs.execute(query)
    rows = curs.fetchall()

    conn.close()
    res = rows[0][0].date()

    return res.isoformat()

def get_oldest():
    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT TOP 1 ts
        FROM GBTAC_data
        ORDER BY ts asc;
        """ 

    #query database
    curs.execute(query)
    rows = curs.fetchall()

    conn.close()
    res = rows[0][0].date()

    return res.isoformat()

def str_to_date(str_date):
    return datetime.fromisoformat(str_date).date()

def date_to_str(d):
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()