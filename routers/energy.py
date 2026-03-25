from routers import *
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
