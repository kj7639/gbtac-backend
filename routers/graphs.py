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

def load_natural_gas():
    csv_path = Path("data/natural_gas.csv")
    df = pd.read_csv(csv_path)

    # show actual column names in terminal
    print("CSV columns:", df.columns.tolist())

    # remove extra spaces from headers
    df.columns = df.columns.str.strip()

    # use the real client column names
    date_col = df.columns[0]
    usage_col = df.columns[2]

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[usage_col] = pd.to_numeric(df[usage_col], errors="coerce")

    df = df.dropna(subset=[date_col, usage_col])

    # convert GJ to kWh
    df["kwh"] = df[usage_col] * 277.777778

    # month key
    df["month"] = df[date_col].dt.strftime("%Y-%m")

    monthly = df.groupby("month", as_index=False)["kwh"].sum()
    return monthly

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

        if type == "mean":
            df_agg = df.resample(agg.lower()).mean()
        else:
            df_agg = df.resample(agg.lower()).sum()

        df_agg = df_agg.astype(object).where(pd.notna(df_agg), other=None)
        res = df_agg.reset_index().to_dict(orient="records")

    return res

@router.get("/total-energy/{sensor_code}")
@limiter.limit("10/minute")
async def total_energy(request: Request, sensor_code, start="2023-01-01", end="", _user=Depends(get_current_user_from_session)):
    # validation
    san_code = validateCode(sensor_code)
    if san_code == False:
        return "enter valid sensor code"

    san_start = validateDate(start)
    if san_start == False:
        return "invalid start date"

    # if no end date, use start
    if end == "":
        end = san_start

    san_end = validateDate(end)
    if san_end == False:
        return "invalid end date"

    if san_end < san_start:
        return "end date cannot be earlier than start date"

    # safe column name after validation
    column_name = f"{SENSOR_PRE}{san_code}"

    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    # Load and filter natural gas CSV
    gas_df = load_natural_gas()

    start_month = pd.to_datetime(san_start).strftime("%Y-%m")
    end_month = pd.to_datetime(san_end).strftime("%Y-%m")

    gas_df = gas_df[(gas_df["month"] >= start_month) & (gas_df["month"] <= end_month)]

    gas_lookup = {
        row["month"]: round(float(row["kwh"]), 2)
        for _, row in gas_df.iterrows()
    }

    # Query SQL sensor monthly total
    query = f"""
        SELECT 
            FORMAT(ts, 'yyyy-MM') AS month,
            SUM(ABS(CAST({column_name} AS FLOAT)) / 12000.0) AS monthly_value
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
        row[0]: round(float(row[1]), 2)
        for row in rows if row[1] is not None
    }

    # Merge months from both series
    all_months = sorted(set(gas_lookup.keys()) | set(sensor_lookup.keys()))

    res = []
    for month in all_months:
        natural_gas = gas_lookup.get(month, 0)
        electricity = sensor_lookup.get(month, 0)

        res.append({
            "month": month,
            "natural_gas_kwh": natural_gas,
            "electricity_kwh": electricity,
            "total_energy_kwh": round(natural_gas + electricity, 2)
        })

    conn.close()
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