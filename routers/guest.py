"""
guest.py

Public (no-auth) graph endpoints for the guest Energy Trends page.
Mirrors a subset of routers/graphs.py but without session authentication.
Rate limits are tighter to discourage abuse.

Author: Dominique Anne Lee
"""

from routers import *
import pandas as pd
import pyodbc
from fastapi.responses import JSONResponse

from helpers.rate_limit import limiter
from fastapi import APIRouter, Request
from helpers.names import replace_name

router = APIRouter(prefix="/graphs/guest")


@router.get("/data/{sensor_code}")
@limiter.limit("5/minute")
async def guest_get_data(
    request: Request,
    sensor_code: str,
    start: str = NEWEST,
    end: str = "",
    agg: str = "none",
    type: str = "mean",
) -> list | str:
    """
    Retrieves guest-accessible sensor data with optional aggregation.

    Supports filtering by date range and aggregating readings by hour, day,
    month, or year. This endpoint mirrors the authenticated graph data route
    but does not require a session.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier.
        start: Start date in ISO format (YYYY-MM-DD).
        end: End date in ISO format (YYYY-MM-DD). Defaults to the start date
            when not provided.
        agg: Aggregation interval (none, H, D, M, Y).
        type: Aggregation type (mean or sum).

    Returns:
        List of timestamped sensor readings or aggregated values. Returns
        an error message string if validation fails.
    """
    san_code = validateCode(sensor_code)
    if san_code is False:
        return JSONResponse(status_code=400, content={"error": "Invalid sensor code"})

    san_start = validateDate(start)
    if san_start is False:
        return JSONResponse(status_code=400, content={"error": "Invalid start date"})

    if end == "":
        end = san_start

    san_end = validateDate(end)
    if san_end is False:
        return JSONResponse(status_code=400, content={"error": "Invalid end date"})

    if san_end < san_start:
        return JSONResponse(status_code=400, content={"error": "End date cannot be earlier than start date"})

    if agg not in ["none", "H", "D", "M", "Y"]:
        return JSONResponse(status_code=400, content={"error": "Invalid aggregation interval"})

    if type not in ["mean", "sum"]:
        return JSONResponse(status_code=400, content={"error": "Invalid aggregation type"})

    column_name = f"{SENSOR_PRE}{san_code}"

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    agg_time = {
        "none": "none",
        "H": "hour",
        "D": "day",
        "M": "month",
        "Y": "year"
    }

    agg_type = {
        "mean": "AVG",
        "sum": "SUM"
    }

    q_agg = agg_time.get(agg, "none")
    q_type = agg_type.get(type, "AVG")

    if(q_agg == "none"):
        # Use direct datetime range comparison instead of CAST(ts AS DATE)
        # so SQL Server can use an index on the ts column
        query = f"""
            SELECT ts as time, {column_name}
            FROM GBTAC_data
            WHERE {column_name} IS NOT NULL
            AND ts >= ?
            AND ts < DATEADD(day, 1, CAST(? AS datetime))
            ORDER BY ts
        """
    else:
        query = f"""
            SELECT 
                DATEADD({q_agg}, DATEDIFF({q_agg}, 0, ts), 0) as time,
                {q_type}({column_name}) as data
            FROM GBTAC_data
            WHERE {column_name} IS NOT NULL
            AND ts >= ?
            AND ts < DATEADD(day, 1, CAST(? AS datetime))
            GROUP BY DATEADD({q_agg}, DATEDIFF({q_agg}, 0, ts), 0)
            ORDER BY time
        """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = [{"ts": row[0], "data": row[1]} for row in rows]

    conn.close()

    if res == []:
        return []

    return res


@router.get("/name/{sensor_code}")
@limiter.limit("10/minute")
async def guest_get_name(request: Request, sensor_code: str) -> str:
    """
    Retrieves a guest-accessible display name for a sensor code.

    Attempts to resolve the sensor name using the local replacement mapping
    first, then falls back to the sensor_names database table.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier.

    Returns:
        Human-readable sensor name, or an error message string if validation
        fails or the name cannot be found.
    """
    san_code = validateCode(sensor_code)
    if san_code is False:
        return "enter valid sensor code"

    name = replace_name(san_code)
    if name is not False:
        return name

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT * FROM sensor_names
        WHERE sensor_name_source = ?
    """

    curs.execute(query, (san_code,))
    rows = curs.fetchall()

    res = rows[0][2] if rows != [] else "name not found"
    conn.close()
    return res