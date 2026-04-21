"""
energy.py

Provides endpoints for retrieving aggregated energy data, including sensor-based
summations, dashboard card metrics, and combined electricity and natural gas
totals. Used by energy-related dashboards.

Author: Kiera Johnson 
"""

import pandas as pd
import pyodbc

from routers import *
from routers.natural_gas import load_natural_gas
from helpers.rate_limit import limiter
from fastapi import APIRouter, HTTPException, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session

router = APIRouter(prefix="/energy")


@router.get("/sum/{sensor_code}")
@limiter.limit("10/minute")
async def get_data(
    request: Request,
    sensor_code: str,
    start: str = NEWEST,
    end: str = "",
    _user=Depends(get_current_user_from_session)
) -> float | str:
    """
    Returns the summed sensor value over a given date range.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier.
        start: Start date (ISO string).
        end: End date (ISO string). Defaults to start date if not provided.
        _user: Authenticated user dependency.

    Returns:
        Sum of sensor values for the given range, or an error message string
        if validation fails.

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

    column_name = f"{SENSOR_PRE}{san_code}"

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT SUM(value)
        FROM {column_name}
        WHERE CAST(ts AS DATE) >= ?
        AND CAST(ts AS DATE) <= ?
    """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = rows[0][0]

    conn.close()
    return res


@router.get("/cards")
@limiter.limit("20/minute")
async def get_card_data(
    request: Request,
    start: str,
    end: str,
    _user=Depends(get_current_user_from_session)
) -> list[dict]:
    """
    Returns aggregated energy metrics for dashboard cards.

    Provides average, maximum, and minimum values for generation and consumption.

    Args:
        request: Incoming request used for rate limiting.
        start: Start date (ISO string).
        end: End date (ISO string).
        _user: Authenticated user dependency.

    Returns:
        List of dictionaries containing metric labels and values.

    Raises:
        HTTPException: Not used directly; validation errors return messages.
    """
    san_start = validateDate(start)
    if san_start is False:
        raise HTTPException(status_code=400, detail="Invalid start date")

    san_end = validateDate(end)
    if san_end is False:
        raise HTTPException(status_code=400, detail="Invalid end date")

    if san_end < san_start:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT 
            t1.avg_value  AS "Average Generation",
            t1.max_value  AS "Maximum Generation",
            t1.min_value  AS "Minimum Generation",
            t2.avg_value  AS "Average Consumption",
            t2.max_value  AS "Maximum Consumption",
            t2.min_value  AS "Minimum Consumption"
        FROM 
            (SELECT 
                AVG(value) AS avg_value,
                MAX(value) AS max_value,
                MIN(value) AS min_value
            FROM SaitSolarLab_30000_TL340
            WHERE CAST(ts AS DATE) BETWEEN ? AND ?
            ) t1,
            (SELECT 
                AVG(value) AS avg_value,
                MAX(value) AS max_value,
                MIN(value) AS min_value
            FROM SaitSolarLab_30000_TL341
            WHERE CAST(ts AS DATE) BETWEEN ? AND ?
            ) t2
    """

    curs.execute(query, (san_start, san_end, san_start, san_end))
    columns = [column[0] for column in curs.description]
    rows = curs.fetchall()

    res = []
    for i, col in enumerate(columns):
        res.append({
            "label": col,
            "value": rows[0][i]
        })

    conn.close()
    return res


@router.get("/total/{sensor_code}")
@limiter.limit("10/minute")
async def total_energy(
    request: Request,
    sensor_code: str,
    start: str = "2023-01-01",
    end: str = "",
    _user=Depends(get_current_user_from_session)
) -> list[dict]:
    """
    Returns combined monthly energy totals for electricity and natural gas.

    Electricity data is aggregated from sensor readings, while natural gas
    values are loaded from a separate dataset and merged by month.

    Args:
        request: Incoming request used for rate limiting.
        sensor_code: Sensor identifier for electricity data.
        start: Start date (ISO string).
        end: End date (ISO string). Defaults to start date if not provided.
        _user: Authenticated user dependency.

    Returns:
        List of monthly energy records containing natural gas, electricity,
        and total energy values in kWh.

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

    column_name = f"{SENSOR_PRE}{san_code}"

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    # Load natural gas data and filter by month range
    gas_df = load_natural_gas()

    start_month = pd.to_datetime(san_start).strftime("%Y-%m")
    end_month = pd.to_datetime(san_end).strftime("%Y-%m")

    gas_df = gas_df[(gas_df["month"] >= start_month) & (gas_df["month"] <= end_month)]

    gas_lookup = {
        row["month"]: float(row["kwh"])
        for _, row in gas_df.iterrows()
    }

    # Aggregate electricity data (W → kWh conversion)
    query = f"""
        SELECT 
            FORMAT(ts, 'yyyy-MM') AS month,
            SUM(ABS(CAST(value AS FLOAT)) / 12000.0) AS electricity_kwh
        FROM {column_name}
        WHERE CAST(ts AS DATE) >= ?
        AND CAST(ts AS DATE) <= ?
        GROUP BY FORMAT(ts, 'yyyy-MM')
        ORDER BY month
    """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    sensor_lookup = {
        row[0]: float(row[1])
        for row in rows if row[1] is not None
    }

    all_months = sorted(set(gas_lookup.keys()) | set(sensor_lookup.keys()))

    res = []
    for month in all_months:
        gas = gas_lookup.get(month, 0)
        elec = sensor_lookup.get(month, 0)

        res.append({
            "month": month,
            "natural_gas_kwh": round(gas, 2),
            "electricity_kwh": round(elec, 2),
            "total_energy_kwh": round(gas + elec, 2)
        })

    conn.close()
    return res