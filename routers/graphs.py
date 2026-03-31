from routers import *
import pandas as pd
from helpers.forecasting import get_forecast
from pathlib import Path 
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session
from datetime import datetime
from helpers.names import replace_name

router = APIRouter(prefix="/graphs")

# url format "http://127.0.0.1:8000/graphs/data/{sensor code}?start={start date}&end={end date}"
# example url: http://127.0.0.1:8000/graphs/data/20000_TL92?start=2025-06-13&end=2025-06-18&agg=D
# - code is the end part of the sensor name (not including the 'SaitSolarLab' part), mandatory
# - start and end date, YYYY-MM-DD
# - agg is the time range, H for hourly, D for daily, W for weekly, M for monthly, Y for yearly
# - type is for kind of aggregation, mean or sum
@router.get("/data/{sensor_code}")
@limiter.limit("10/minute")
async def get_data(request: Request, sensor_code, start=NEWEST, end="", agg="none", type="mean", _user=Depends(get_current_user_from_session)):
    
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
    
    allowedAgg = ["none", "H", "D", "M", "Y"]
    if agg not in allowedAgg:
        return "invalid aggregation interval"

    allowedType = ["mean", "sum"]
    if type not in allowedType:
        return "invalid aggregation type"
    
    column_name = f"{SENSOR_PRE}{san_code}"

    # open connection
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

    #query database
    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    #format data 
    res = []
    for row in rows:
        res.append({
            "ts": row[0],
            "data": row[1]
        })


    conn.close()

    if res == []:
        return []
    
    # forecast data if end date is in the future
    if san_end > NEWEST:
        forecasted_data = get_forecast(san_code, NEWEST, san_end)
        res = res + forecasted_data

    # aggregates data
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

        if type == "mean":
            df_agg = df.resample(freq).mean()
        else:
            df_agg = df.resample(freq).sum()

        df_agg = df_agg.astype(object).where(pd.notna(df_agg), other=None)
        res = df_agg.reset_index().to_dict(orient="records")

    return res

# url format: "http://127.0.0.1:8000/graphs/name/{sensor code}"
# example url: http://127.0.0.1:8000/graphs/name/20000_TL92
@router.get("/name/{sensor_code}")
@limiter.limit("30/minute")
async def get_name(request: Request, sensor_code, _user=Depends(get_current_user_from_session)):

    # validation
    san_code = validateCode(sensor_code)
    if san_code == False:
        return "enter valid sensor code"
    
    name = replace_name(san_code)
    if name != False:
        return name

    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT * FROM sensor_names
        WHERE sensor_name_source = ?
    """    

    #query database
    curs.execute(query, (san_code,))
    rows = curs.fetchall()

    res = rows[0][2] if rows != [] else "name not found"
    conn.close()
    return res


@router.get("/codesnames")
@limiter.limit("30/minute")
async def get_codesnames(request: Request, _user=Depends(get_current_user_from_session)):
    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = """
        SELECT sensor_name_source, sensor_name_report 
        FROM sensor_names
        ORDER BY sensor_name_source
        """ 

    #query database
    curs.execute(query)
    rows = curs.fetchall()

    res = []
    for row in rows:
        code = row[0]
        name = replace_name(code)
        if name == False:
            name = row[1]
        res.append({
            "code": code,
            "name": name
        })

    conn.close()
    return res

@router.get("/newest")
async def get_newest(_user=Depends(get_current_user_from_session)):
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
    res = rows[0][0]

    return res.date()

@router.get("/oldest")
async def get_oldest(_user=Depends(get_current_user_from_session)):
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
    res = rows[0][0]

    return res.date()