"""
guest.py

Public (no-auth) graph endpoints for the guest Energy Trends page.
Mirrors a subset of routers/graphs.py but without session authentication.
Rate limits are tighter to discourage abuse.

Author: Dominique Anne Lee
"""

from routers import *
import pandas as pd
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request
from helpers.names import replace_name

router = APIRouter(prefix="/graphs/guest")

@router.get("/data/{sensor_code}")
@limiter.limit("5/minute")
async def guest_get_data(request: Request, sensor_code, start=NEWEST, end="", agg="none", type="mean"):
    """Guest-accessible sensor data endpoint — no authentication required."""

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

    allowedAgg = ["none", "H", "D", "M", "Y"]
    if agg not in allowedAgg:
        return "invalid aggregation interval"

    allowedType = ["mean", "sum"]
    if type not in allowedType:
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

    res = []
    for row in rows:
        res.append({"ts": row[0], "data": row[1]})

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
async def guest_get_name(request: Request, sensor_code):
    """Guest-accessible sensor name lookup — no authentication required."""

    san_code = validateCode(sensor_code)
    if san_code == False:
        return "enter valid sensor code"

    name = replace_name(san_code)
    if name != False:
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
