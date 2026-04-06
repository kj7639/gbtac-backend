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


@router.get("", response_model=None)
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
        message string if validation fails.
    """

    sensor_list = [s.strip() for s in sensors.split(",")]
    san_sensors = []

    # Validate sensor codes
    for sensor in sensor_list:
        san_code = validateCode(sensor)
        if san_code is False:
            return f"enter valid sensor code: {sensor}"
        san_sensors.append(san_code)

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

    # Build sensor column list
    sens_str = ", ".join(f"{SENSOR_PRE}{sensor}" for sensor in san_sensors)

    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT ts, {sens_str}
        FROM GBTAC_data
        WHERE ({sens_str.replace(", ", " IS NOT NULL OR ")} IS NOT NULL)
        AND (CAST(ts AS DATE) >= ? AND CAST(ts AS DATE) <= ?)
        ORDER BY ts
    """

    curs.execute(query, (san_start, san_end))
    rows = curs.fetchall()

    res = []
    for row in rows:
        dataset = {"ts": row[0]}
        for i, sensor in enumerate(san_sensors, start=1):
            dataset[sensor] = row[i]
        res.append(dataset)

    # Resolve sensor display names
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
        if name is False:
            name = row[1]
        name_map[row[0]] = name

    names = [name_map[sensor] for sensor in san_sensors]

    conn.close()

    df = pd.DataFrame(res, columns=["ts"] + san_sensors)
    df["ts"] = pd.to_datetime(df["ts"])

    # Apply aggregation if requested
    if agg != "none":
        df = df.set_index("ts")
        df_agg = df.resample(agg.lower()).mean() if type == "mean" else df.resample(agg.lower()).sum()
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

    display_df = res.copy()
    display_df["ts"] = display_df["ts"].astype(str).str[:19]

    for col in san_sensors:
        display_df[col] = display_df[col].apply(
            lambda v: f"{v:,.4f}" if pd.notna(v) else "-"
        )

    headers = ["Timestamp"] + names
    table_data = [headers] + display_df.values.tolist()

    page_width = portrait(letter)[0] - inch
    col_width = page_width / len(headers)

    table = Table(table_data, colWidths=[col_width] * len(headers), repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DA291C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 13),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("ROWBACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
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