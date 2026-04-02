"""
forecasting.py

Generates and caches Prophet-based time-series forecasts for individual sensors.
Forecasts are stored as JSON files in a /forecasts directory and reused until
they fall within 7 days of the newest actual reading. Used by the graphs router
to extend data beyond the latest recorded date for the temperature dashboards.

Author: Kiera Johnson
"""

import json
from pathlib import Path
from datetime import timedelta
from helpers.dates import get_oldest, get_newest, str_to_date
import pandas as pd
from prophet import Prophet
import pyodbc
from config import connection_str

NEWEST = str_to_date(get_newest())
OLDEST = str_to_date(get_oldest())


def useable_forecast(file_path: Path) -> bool:
    """
    Determines whether a cached forecast file exists and is still valid.

    A forecast is considered usable if:
    - The file exists, and
    - The latest forecasted timestamp extends at least 7 days beyond the newest
      actual reading in the dataset.

    Args:
        file_path: Path to the forecast JSON file for a sensor.

    Returns:
        True if the forecast file exists and is still valid, otherwise False.
    """
    if not file_path.is_file():
        return False

    with open(file_path, "r") as f:
        data = json.load(f)

    most_recent = str_to_date(data[-1]["ts"])

    if most_recent > NEWEST + timedelta(days=7):
        return True

    return False


def get_forecast(
    sensor_code: str,
    start: str | None = None,
    end: str | None = None
) -> list[dict]:
    """
    Retrieves forecasted data for a sensor within a given date range.

    If a cached forecast is not available or is outdated, a new forecast is
    generated before returning results.

    Args:
        sensor_code: Sensor identifier used to query and cache forecast data.
        start: Start date (ISO string). Defaults to newest available data.
        end: End date (ISO string). Required for filtering results.

    Returns:
        List of forecast records with keys: ts (timestamp) and data (predicted value).

    Notes:
        Forecast data is stored as JSON files and reused to avoid unnecessary
        recomputation with Prophet.
    """
    start = start or NEWEST.isoformat()
    end = end or NEWEST.isoformat()

    forecasts_dir = Path(__file__).resolve().parent.parent / "forecasts"
    file_path = forecasts_dir / f"{sensor_code}.json"

    # Generate forecast if none exists or it is outdated
    if not useable_forecast(file_path):
        forecast(sensor_code)

    with open(file_path, "r") as f:
        data = json.load(f)

    filtered = [
        row for row in data
        if str_to_date(start) <= str_to_date(row["ts"]) <= str_to_date(end)
    ]

    return filtered


def forecast(sensor_code: str) -> None:
    """
    Generates a Prophet forecast for a given sensor and stores it as JSON.

    Retrieves historical sensor data from the database, trains a Prophet model,
    and produces a 10-day forecast. The result is saved to a file for reuse.

    Args:
        sensor_code: Sensor identifier used to query data and name the output file.

    Notes:
        The forecast frequency is inferred from the median interval between
        readings, with a minimum resolution of one hour to avoid excessive
        granularity.
    """
    conn = pyodbc.connect(connection_str)
    curs = conn.cursor()

    query = f"""
        SELECT ts, SaitSolarLab_{sensor_code}
        FROM GBTAC_data 
        WHERE SaitSolarLab_{sensor_code} IS NOT NULL
        ORDER BY ts
    """

    curs.execute(query)
    rows = curs.fetchall()

    res = []
    for row in rows:
        res.append({
            "timestamp": row[0],
            "value": row[1]
        })

    conn.close()

    df = pd.DataFrame(res)

    df = df.rename(columns={
        "timestamp": "ds",
        "value": "y"
    })

    df["ds"] = pd.to_datetime(df["ds"])

    # Infer sensor frequency from median time gap to support varying intervals
    deltas = df["ds"].diff().dropna()
    freq_seconds = int(deltas.median().total_seconds())

    # Enforce minimum frequency of 1 hour to prevent overly dense forecasts
    freq_secs = max(freq_seconds, 3600)
    freq = f"{freq_secs}s"

    # Generate approximately 10 days of future predictions
    periods = int((10 * 86400) / freq_seconds)

    model = Prophet()
    model.fit(df)

    future = model.make_future_dataframe(periods=periods, freq=freq)
    forecast_df = model.predict(future)

    last_actual = df["ds"].max()
    forecast_new = forecast_df[forecast_df["ds"] > last_actual]

    out = forecast_new[["ds", "yhat"]].rename(columns={
        "ds": "ts",
        "yhat": "data"
    })

    forecasts_dir = Path(__file__).resolve().parent.parent / "forecasts"
    forecasts_dir.mkdir(exist_ok=True)

    out.to_json(
        forecasts_dir / f"{sensor_code}.json",
        orient="records",
        date_format="iso"
    )