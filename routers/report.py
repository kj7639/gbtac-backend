"""
report.py

Generates PDF reports for selected sensors over a given date range.
Supports multiple sensors, optional aggregation, and formatted output
using ReportLab. Used by reporting features in the dashboard.

Author: Kiera Johnson
"""

from helpers.auth_dependencies import get_current_user_from_session
from helpers.names import replace_name
from routers import *

from fastapi.responses import StreamingResponse
from fastapi import APIRouter, Request, Depends, HTTPException

import pyodbc
import pandas as pd
import io

from reportlab.lib.pagesizes import letter, portrait
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch

router = APIRouter(prefix="/report")


@router.get("/", response_model=None)
async def generate_table_report(
    sensors: str,
    start: str,
    end: str,
    agg: str = "none",
    type: str = "mean",
    title: str = "",
    _user=Depends(get_current_user_from_session)
) -> StreamingResponse | str:
    """
    Generates a PDF report for selected sensors over a date range.

    Retrieves time-series data for one or more sensors, optionally applies
    aggregation, and formats the result into a downloadable PDF table.

    Args:
        sensors: Comma-separated list of sensor codes.
        start: Start date (ISO string).
        end: End date (ISO string). Defaults to start date if not provided.
        agg: Aggregation interval (none, H, D, M, Y).
        type: Aggregation type (mean or sum).
        title: Optional custom report title.
        _user: Authenticated user dependency.

    Returns:
        StreamingResponse containing a generated PDF report, or an error
        code and description if validation fails.
    """

    sensor_list = [s.strip() for s in sensors.split(",")]
    san_sensors = []

    # Validate sensor codes
    for sensor in sensor_list:
        san_code = validateCode(sensor)
        if san_code is False:
            raise HTTPException(status_code=400, detail=f"Invalid sensor code: ${sensor}")
        san_sensors.append(san_code)

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

    # Build sensor column list
    sens_str = ", ".join(f"{SENSOR_PRE}{sensor}" for sensor in san_sensors)

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    letters = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z"]
    coal = ""
    values = ""
    join = ""
    count = 0
    for code in san_sensors:
        coal += f"{letters[count]}.ts, "
        values += f"{letters[count]}.value AS {letters[count]}, "
        if count != 0:
            join += f" FULL OUTER JOIN {SENSOR_PRE}{code} AS {letters[count]} ON {letters[0]}.ts = {letters[count]}.ts"
        count += 1

    coal = coal[:-2]
    values = values[:-2]

    if len(san_sensors) == 1:
        query = f"""
            SELECT ts, value
            FROM {SENSOR_PRE}{san_sensors[0]}
            WHERE CAST(ts AS DATE) >= ?
            AND CAST(ts AS DATE) < DATEADD(day, 1, CAST(? AS datetime))
            ORDER BY ts
        """
    else:
        query = f"""
            SELECT COALESCE({coal}), {values}
            FROM {SENSOR_PRE}{san_sensors[0]} AS {letters[0]}
            {join}
            WHERE COALESCE({coal}) >= ?
            AND COALESCE({coal}) < DATEADD(day, 1, CAST(? AS datetime))
            ORDER BY COALESCE({coal})
        """
    print(query)

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = []
    for row in rows:
        dataset = {"ts": row[0]}
        for i, sensor in enumerate(san_sensors, start=1):
            dataset[sensor] = row[i]
        res.append(dataset)

    conn.close()
    
    names = []
    for sensor in san_sensors:
        name = SENSOR_PRE + sensor
        names.append(name)

    df = pd.DataFrame(res, columns=["ts"] + san_sensors)
    df["ts"] = pd.to_datetime(df["ts"])

    # Apply aggregation if requested
    agg_freq_map = {
        "H": "h",
        "D": "D",
        "M": "ME",
        "Y": "YE",
    }

    if agg != "none":
        df = df.set_index("ts")
        freq = agg_freq_map[agg]
        df_agg = df.resample(freq).mean() if type == "mean" else df.resample(freq).sum()
        res = df_agg.reset_index()
    else:
        res = df

    # Default title
    if title == "":
        title = f"Sensor Data Report, {san_start} to {san_end}"

    # Generate PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=portrait(letter),
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(title, styles["Title"]))
    story.append(Paragraph(f"{san_start} to {san_end}", styles["Normal"]))
    story.append(Spacer(1, 16))

    # Trim timestamp to match aggregation level
    ts_formats = {
        "none": "%Y-%m-%d %H:%M:%S",
        "H":    "%Y-%m-%d %H:%M",
        "D":    "%Y-%m-%d",
        "M":    "%Y-%m",
        "Y":    "%Y",
    }
    ts_fmt = ts_formats.get(agg, "%Y-%m-%d %H:%M:%S")

    display_df = res.copy()
    display_df["ts"] = pd.to_datetime(display_df["ts"]).dt.strftime(ts_fmt)

    for col in san_sensors:
        display_df[col] = display_df[col].apply(
            lambda v: f"{v:,.4f}" if pd.notna(v) else "-"
        )

    # Scale font size and padding down as column count increases
    num_cols = 1 + len(names)
    if num_cols <= 4:
        header_font, body_font = 13, 10
        h_pad, b_pad = 8, 5
    elif num_cols <= 7:
        header_font, body_font = 10, 8
        h_pad, b_pad = 5, 3
    else:
        header_font, body_font = 8, 7
        h_pad, b_pad = 4, 2

    page_width = portrait(letter)[0] - inch
    col_width = page_width / num_cols

    # Use Paragraph objects for headers so long names wrap within the cell
    # instead of overflowing. Plain strings in ReportLab tables never wrap.
    header_style = styles["Normal"].clone("HeaderStyle")
    header_style.textColor = colors.white
    header_style.fontName = "Helvetica-Bold"
    header_style.fontSize = header_font
    header_style.leading = header_font + 2

    headers = [Paragraph("Timestamp", header_style)] + [Paragraph(n, header_style) for n in names]
    table_data = [headers] + display_df.values.tolist()

    table = Table(table_data, colWidths=[col_width] * num_cols, repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DA291C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), header_font),
        ("BOTTOMPADDING", (0, 0), (-1, 0), h_pad),
        ("TOPPADDING", (0, 0), (-1, 0), h_pad),
        ("ROWBACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTSIZE", (0, 1), (-1, -1), body_font),
        ("TOPPADDING", (0, 1), (-1, -1), b_pad),
        ("BOTTOMPADDING", (0, 1), (-1, -1), b_pad),
        ("GRID", (0, 0), (-1, -1), 1.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]))

    story.append(table)
    doc.build(story)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="report.pdf"'}
    )