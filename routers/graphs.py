"""
graphs.py

Router for sensor graph data endpoints. Provides time-series data retrieval,
sensor name lookups, code-name listings, and newest/oldest timestamp queries
against the GBTAC_data and sensor_names tables. Used by the Ambient Temperature
and Wall Temperature dashboards.

Author: Dominique Anne Lee, Anna Yabut, Kiera Johnson
"""

from routers import *
import pandas as pd
import pyodbc

from helpers.forecasting import get_forecast
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session
from helpers.names import replace_name

router = APIRouter(prefix="/graphs")


@router.get("/data/{sensor_code}")
@limiter.limit("10/minute")
async def get_data(
    request: Request,
    sensor_code: str,
    start: str = NEWEST,
    end: str = "",
    agg: str = "none",
    type: str = "mean",
    _user=Depends(get_current_user_from_session)
) -> list | str:
    """
    Retrieves time-series sensor data with optional aggregation and forecasting.

    Supports filtering by date range, aggregation intervals (hourly, daily,
    monthly, yearly), and aggregation types (mean or sum). If the requested
    end date extends beyond available data, forecasted values are appended.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier.
        start: Start date (ISO string).
        end: End date (ISO string). Defaults to start date if not provided.
        agg: Aggregation interval (none, H, D, M, Y).
        type: Aggregation type (mean or sum).
        _user: Authenticated user dependency.

    Returns:
        List of timestamped sensor readings or aggregated values. Returns
        an error message string if validation fails.

    Raises:
        HTTPException: Not used directly; validation errors return messages.
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

    if agg not in ["none", "H", "D", "M", "Y"]:
        return "invalid aggregation interval"

    if type not in ["mean", "sum"]:
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

    # Append forecast data when requesting beyond available dataset
    if san_end > NEWEST:
        forecasted_data = get_forecast(san_code, NEWEST, san_end)
        res = res + forecasted_data

    # Apply aggregation if requested
    if agg != "none":
        df = pd.DataFrame(res)
        df = df.dropna()
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.set_index("ts")

        freq_map = {
            "H": "h",
            "D": "D",
            "M": "MS",
            "Y": "YS",
        }

        freq = freq_map[agg]

        df_agg = df.resample(freq).mean() if type == "mean" else df.resample(freq).sum()

        df_agg = df_agg.astype(object).where(pd.notna(df_agg), other=None)
        res = df_agg.reset_index().to_dict(orient="records")

    return res


@router.get("/name/{sensor_code}")
@limiter.limit("30/minute")
async def get_name(
    request: Request,
    sensor_code: str,
    _user=Depends(get_current_user_from_session)
) -> str:
    """
    Retrieves the display name for a given sensor code.

    Attempts to resolve the name using a local mapping first, then falls back
    to the sensor_names database table if not found.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier.
        _user: Authenticated user dependency.

    Returns:
        Human-readable sensor name or an error message string if invalid.
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


@router.get("/codesnames")
@limiter.limit("30/minute")
async def get_codesnames(
    request: Request,
    _user=Depends(get_current_user_from_session)
) -> list[dict]:
    """
    Retrieves all sensor codes and their corresponding display names.

    Combines database values with local replacement mappings when available.

    Args:
        request: Incoming request used for rate limiting.
        _user: Authenticated user dependency.

    Returns:
        List of dictionaries containing sensor codes and display names.
    """
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT sensor_name_source, sensor_name_report 
        FROM sensor_names
        ORDER BY sensor_name_source
    """

    curs.execute(query)
    rows = curs.fetchall()

    res = []
    for row in rows:
        code = row[0]
        name = replace_name(code)
        if name is False:
            name = row[1]

        res.append({
            "code": code,
            "name": name
        })

    conn.close()
    return res


@router.get("/newest")
async def return_newest(_user=Depends(get_current_user_from_session)) -> str:
    """
    Retrieves the most recent timestamp in the dataset.

    Args:
        _user: Authenticated user dependency.

    Returns:
        ISO formatted date of the newest available data point.
    """
    # newest = await get_newest()
    return NEWEST


@router.get("/oldest")
async def return_oldest(_user=Depends(get_current_user_from_session)) -> str:
    """
    Retrieves the oldest timestamp in the dataset.

    Args:
        _user: Authenticated user dependency.

    Returns:
        ISO formatted date of the oldest available data point.
    """
    # oldest
    return OLDEST