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
        return "enter valid sensor code"

    san_start = validateDate(start)
    if san_start is False:
        return "invalid start date"

    if end == "":
        end = san_start

    san_end = validateDate(end)
    if san_end is False:
        return "invalid end date"

    if san_end < san_start:
        return "end date cannot be earlier than start date"

    allowed_agg = ["none", "H", "D", "M", "Y"]
    if agg not in allowed_agg:
        return "invalid aggregation interval"

    allowed_type = ["mean", "sum"]
    if type not in allowed_type:
        return "invalid aggregation type"

    column_name = f"{SENSOR_PRE}{san_code}"

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT ts, {column_name}
        FROM GBTAC_data
        WHERE {column_name} IS NOT NULL
        AND CAST(ts AS DATE) >= ?
        AND CAST(ts AS DATE) <= ?
        ORDER BY ts
    """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = [{"ts": row[0], "data": row[1]} for row in rows]

    conn.close()

    if res == []:
        return []

    if agg != "none":
        df = pd.DataFrame(res)
        df = df.dropna()
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts")

        freq_map = {"H": "h", "D": "D", "M": "MS", "Y": "YS"}
        freq = freq_map[agg]

        if type == "mean":
            df_agg = df.resample(freq).mean()
        else:
            df_agg = df.resample(freq).sum()

        df_agg = df_agg.astype(object).where(pd.notna(df_agg), other=None)
        res = df_agg.reset_index().to_dict(orient="records")

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