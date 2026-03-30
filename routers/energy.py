import pandas as pd

from routers import *
from routers.natural_gas import load_natural_gas
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session

router = APIRouter(prefix="/energy")

@router.get("/sum/{sensor_code}")
@limiter.limit("10/minute")
async def get_data(request: Request, sensor_code, start=NEWEST, end="", _user=Depends(get_current_user_from_session)):

    #validation:
    san_code = validateCode(sensor_code)
    if san_code == False:
        return "enter valid sensor code"

    san_start = validateDate(start)
    if san_start == False:
        return "invalid start date"
    
    # sets end date range to the same day as start if it wasn't included
    if end == "":
        end = san_start
    
    san_end = validateDate(end)
    if san_end == False:
        return "invalid end date"
    
    if san_end < san_start:
        return "end date cannot be earlier than start date"
    
    column_name = f"{SENSOR_PRE}{san_code}"

    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT SUM({column_name})
        FROM GBTAC_data
        WHERE CAST(ts AS DATE) >= ?
        AND CAST(ts AS DATE) <= ?
    """

    #query database
    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = rows[0][0]

    #close connection and send data
    conn.close()
    return res


# returns card information
@router.get("/cards")
@limiter.limit("20/minute")
async def get_card_data(request: Request, start, end, _user=Depends(get_current_user_from_session)):

    san_start = validateDate(start)
    if san_start == False:
        return "invalid start date"
    
    san_end = validateDate(end)
    if san_end == False:
        return "invalid end date"
    
    if san_end < san_start:
        return "end date cannot be earlier than start date"
       
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        select avg(SaitSolarLab_30000_TL340) as "Average Generation",
        max(SaitSolarLab_30000_TL340) as "Maximum Generation", 
        min(SaitSolarLab_30000_TL340) as "Minimum Generation",
        avg(SaitSolarLab_30000_TL341) as "Average Consumption", 
        max(SaitSolarLab_30000_TL341) as "Maximum Consumption", 
        min(SaitSolarLab_30000_TL341) as "Minimum Consumption"
        from gbtac_data
        where cast(ts as date) >= ?
        and cast(ts as date) <= ?
    """

    #query database
    curs.execute(query, (san_start, san_end))
    columns = [column[0] for column in curs.description]
    rows = curs.fetchall()

    res = []
    i = 0
    for col in columns:
        res.append({
            "label": col,
            "value": rows[0][i]
        })
        i += 1

    conn.close()
    return res

@router.get("/total/{sensor_code}")
@limiter.limit("10/minute")
async def total_energy(
    request: Request,
    sensor_code,
    start="2023-01-01",
    end="",
    _user=Depends(get_current_user_from_session)
):
    san_code = validateCode(sensor_code)
    if san_code == False:
        return "enter valid sensor code"

    san_start = validateDate(start)
    if san_start == False:
        return "invalid start date"

    if end == "":
        end = san_start

    san_end = validateDate(end)
    if san_end == False:
        return "invalid end date"

    if san_end < san_start:
        return "end date cannot be earlier than start date"

    column_name = f"{SENSOR_PRE}{san_code}"

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    # natural gas
    gas_df = load_natural_gas()

    start_month = pd.to_datetime(san_start).strftime("%Y-%m")
    end_month = pd.to_datetime(san_end).strftime("%Y-%m")

    gas_df = gas_df[(gas_df["month"] >= start_month) & (gas_df["month"] <= end_month)]

    gas_lookup = {
        row["month"]: float(row["kwh"])
        for _, row in gas_df.iterrows()
    }

    # electricity (W → kWh)
    query = f"""
        SELECT 
            FORMAT(ts, 'yyyy-MM') AS month,
            SUM(ABS(CAST({column_name} AS FLOAT)) / 12000.0) AS electricity_kwh
        FROM GBTAC_data
        WHERE {column_name} IS NOT NULL
        AND CAST(ts AS DATE) >= ?
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