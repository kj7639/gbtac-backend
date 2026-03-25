from helpers.auth_dependencies import get_current_user_from_session
from helpers.names import replace_name
from routers import *
from fastapi.responses import StreamingResponse
from fastapi import APIRouter, Request, Depends
import pyodbc
import pandas as pd
import io
from reportlab.lib.pagesizes import letter, portrait
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch

router = APIRouter(prefix="/report")


@router.get("")
async def generate_table_report(sensors, start, end, agg= "none", type= "mean", title="", _user=Depends(get_current_user_from_session)):

    sensor_list = [s.strip() for s in sensors.split(",")]
    san_sensors = []

    #validation:
    for sensor in sensor_list:
        san_code = validateCode(sensor)
        if san_code == False:
            return f"enter valid sensor code: {sensor}"
        san_sensors.append(san_code)

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

    sens_str = ""
    for sensor in san_sensors:
        column_name = f"{SENSOR_PRE}{sensor}"
        sens_str += f"{column_name}, "
    sens_str = sens_str[:-2]

    # open connection
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT ts, {sens_str}
        FROM GBTAC_data
        WHERE ({sens_str.replace(", ", " IS NOT NULL OR ")} IS NOT NULL)
        AND (CAST(ts AS DATE) >= ? AND CAST(ts AS DATE) <= ?)
        ORDER BY ts
    """

    #query database
    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = []
    for row in rows:
        dataset = {"ts": row[0]}
        i = 1
        for sensor in san_sensors:
            dataset[sensor] = row[i]
            i += 1
        
        res.append(dataset)
        
    query = f"""
        SELECT sensor_name_source, sensor_name_report
        FROM sensor_names
        WHERE sensor_name_source IN ({', '.join('?' for _ in san_sensors)})
    """

    curs.execute(query, san_sensors)
    rows = curs.fetchall()

    name_map = {}
    for row in rows:
        name = replace_name(row[0])
        if name == False:
            name = row[1]
        name_map[row[0]] = name

    names = [name_map[sensor] for sensor in san_sensors]

    conn.close()

    df = pd.DataFrame(res, columns=["ts"] + san_sensors)
    df["ts"] = pd.to_datetime(df["ts"])

    # aggregates data
    if agg != "none":
        df = df.set_index("ts")

        if type == "mean":
            df_agg = df.resample(agg.lower()).mean()
        else:
            df_agg = df.resample(agg.lower()).sum()
            
        res = df_agg.reset_index()
    else:
        res = df

    # title
    if title == "":
        title = f"Sensor Data Report, {san_start} to {san_end}"

    # pdf and table generation
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=portrait(letter),
        leftMargin=0.5*inch,
        rightMargin=0.5*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch,
    )
    styles = getSampleStyleSheet()
    story = []

    # Title + date range
    story.append(Paragraph(title, styles["Title"]))
    story.append(Paragraph(f"{san_start} to {san_end}", styles["Normal"]))
    story.append(Spacer(1, 16))

    # Format the DataFrame values for display
    display_df = res.copy()
    display_df["ts"] = display_df["ts"].astype(str).str[:19]  # trim microseconds
    for col in san_sensors:
        display_df[col] = display_df[col].apply(
            lambda v: f"{v:,.4f}" if pd.notna(v) else "-"
        )

    # Build table data
    headers = ["Timestamp"] + names
    table_data = [headers] + display_df.values.tolist()

    # Auto-size columns based on page width
    page_width = portrait(letter)[0] - inch  # total usable width
    col_width = page_width / len(headers)

    table = Table(table_data, colWidths=[col_width] * len(headers), repeatRows=1)
    table.setStyle(TableStyle([
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#DA291C")),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  13),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  8),
        ("TOPPADDING",    (0, 0), (-1, 0),  8),
        # Data rows — alternating background
        ("ROWBACKGROUND",(0, 1), (-1, -1), colors.white),
        ("FONTSIZE",      (0, 1), (-1, -1), 10),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        # Grid
        ("GRID",          (0, 0), (-1, -1), 1.5, colors.HexColor("#000000")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (1, 1), (-1, -1), "LEFT"),  # right-align numbers
        ("ALIGN",         (0, 0), (0, -1),  "LEFT"),   # left-align timestamps
    ]))

    story.append(table)
    doc.build(story)

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="report.pdf"'}
    )

    
