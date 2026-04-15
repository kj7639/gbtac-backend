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
from fastapi import HTTPException

from helpers.forecasting import get_forecast
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session
from helpers.names import replace_name

router = APIRouter(prefix="/graphs")


@router.get("/data/batch")
@limiter.limit("30/minute")
async def get_batch_data(
    request: Request,
    sensors: str,
    start: str = NEWEST,
    end: str = "",
    agg: str = "none",
    type: str = "mean",
    _user=Depends(get_current_user_from_session)
) -> dict:
    """
    Retrieves time-series data for multiple sensors in a single DB query.

    Args:
        request: Incoming request used for rate limiting.
        sensors: Comma-separated sensor codes.
        start: Start date (ISO string).
        end: End date (ISO string).
        agg: Aggregation interval (none, H, D, M, Y).
        type: Aggregation type (mean or sum).
        _user: Authenticated user dependency.

    Returns:
        Dict mapping sensor codes to their time-series data arrays.
    """
    codes = [c.strip() for c in sensors.split(",") if c.strip()]
    if not codes:
        return {}

    san_start = validateDate(start)
    if san_start is False:
        return {}

    if end == "":
        end = san_start
    san_end = validateDate(end)
    if san_end is False:
        return {}

    if san_end < san_start:
        return {}

    if agg not in ["none", "H", "D", "M", "Y"]:
        return {}
    if type not in ["mean", "sum"]:
        return {}

    # Validate all codes and build column list
    validated = []
    for code in codes:
        san_code = validateCode(code)
        if san_code is False:
            continue
        validated.append(san_code)

    if not validated:
        return {}

    columns = [f"{SENSOR_PRE}{c}" for c in validated]
    col_select = ", ".join(columns)

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT ts, {col_select}
        FROM GBTAC_data
        WHERE ts >= ?
        AND ts < DATEADD(day, 1, CAST(? AS datetime))
        ORDER BY ts
    """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()
    conn.close()

    result = {}
    for i, code in enumerate(validated):
        sensor_rows = [
            {"ts": row[0], "data": row[i + 1]}
            for row in rows
            if row[i + 1] is not None
        ]

        if sensor_rows and san_end > NEWEST:
            forecasted_data = get_forecast(code, NEWEST, san_end)
            sensor_rows = sensor_rows + forecasted_data

        if agg != "none" and sensor_rows:
            df = pd.DataFrame(sensor_rows)
            df = df.dropna()
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.set_index("ts")
            freq_map = {"H": "h", "D": "D", "M": "MS", "Y": "YS"}
            freq = freq_map[agg]
            df_agg = df.resample(freq).mean() if type == "mean" else df.resample(freq).sum()
            df_agg = df_agg.astype(object).where(pd.notna(df_agg), other=None)
            sensor_rows = df_agg.reset_index().to_dict(orient="records")

        result[code] = sensor_rows

    return result


@router.get("/data/{sensor_code}")
@limiter.limit("30/minute")
async def get_data(
    request: Request,
    sensor_code: str,
    start: str = NEWEST,
    end: str = "",
    agg: str = "none",
    type: str = "mean",
    _user=Depends(get_current_user_from_session)
) -> list | dict:
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
        an error code and message if fails

    Raises:
        HTTPException: Not used directly; validation errors return messages.
    """
    san_code = validateCode(sensor_code)
    if san_code is False:
        raise HTTPException(status_code=400, detail="Invalid sensor code")

    san_start = validateDate(start)
    if san_start is False:
        raise HTTPException(status_code=400, detail="Invalid start date")

    if end == "":
        end = san_start

    san_end = validateDate(end)
    if san_end is False:
        raise HTTPException(status_code=400, detail="Invalid end date")

    if san_end < san_start:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    if agg not in ["none", "H", "D", "M", "Y"]:
        raise HTTPException(status_code=400, detail="Invalid aggregation interval")

    if type not in ["mean", "sum"]:
        raise HTTPException(status_code=400, detail="Invalid aggregation type")

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

    # Append forecast data when requesting beyond available dataset
    if san_end > NEWEST:
        forecasted_data = get_forecast(san_code, NEWEST, san_end)
        # Apply aggregation if requested - only to forecast data now
        if agg != "none":
            df = pd.DataFrame(forecasted_data)
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
            forecasted_data = df_agg.reset_index().to_dict(orient="records")

        res = res + forecasted_data

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
        Human-readable sensor name or an error code and message if invalid.
    """
    san_code = validateCode(sensor_code)
    if san_code is False:
        raise HTTPException(status_code=400, detail="Invalid sensor code")

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

    if rows != []:
        res = rows[0][2]  
    else:
        raise HTTPException(status_code=404, detail="Name not found")

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