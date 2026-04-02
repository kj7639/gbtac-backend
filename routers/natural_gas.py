"""
natural_gas.py

Provides natural gas data processing and API endpoints. Loads raw CSV data,
converts units from GJ to kWh, aggregates monthly totals, and exposes a
filtered endpoint for dashboard use.

Author: Anna Yabut
"""

from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session
from helpers.rate_limit import limiter
import pandas as pd
from pathlib import Path

router = APIRouter(prefix="/natural-gas", tags=["Natural Gas"])


def load_natural_gas() -> pd.DataFrame:
    """
    Loads and processes natural gas data from a CSV file.

    Cleans column names, converts data types, removes invalid rows,
    converts energy from gigajoules (GJ) to kilowatt-hours (kWh), and
    aggregates values by month.

    Returns:
        Pandas DataFrame with columns:
            - month (YYYY-MM string)
            - kwh (monthly total energy usage)
    """
    csv_path = Path("data/natural_gas.csv")
    df = pd.read_csv(csv_path)

    # Clean column names to avoid hidden whitespace issues
    df.columns = df.columns.str.strip()

    date_col = df.columns[0]
    usage_col = df.columns[2]

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[usage_col] = pd.to_numeric(df[usage_col], errors="coerce")

    df = df.dropna(subset=[date_col, usage_col])

    # Convert GJ → kWh
    df["kwh"] = df[usage_col] * 277.777778

    # Format month as YYYY-MM
    df["month"] = df[date_col].dt.strftime("%Y-%m")

    # Aggregate monthly totals
    monthly = df.groupby("month", as_index=False)["kwh"].sum()

    # Round values for cleaner API output
    monthly["kwh"] = monthly["kwh"].round(2)

    return monthly


@router.get("/monthly")
@limiter.limit("10/minute")
async def get_natural_gas_monthly(
    request: Request,
    start: str,
    end: str,
    _user=Depends(get_current_user_from_session)
) -> list[dict]:
    """
    Retrieves monthly natural gas usage within a date range.

    Filters preprocessed natural gas data by month and returns
    aggregated kWh values.

    Args:
        request: Incoming request used for rate limiting.
        start: Start date (ISO string).
        end: End date (ISO string).
        _user: Authenticated user dependency.

    Returns:
        List of dictionaries containing:
            - month (YYYY-MM)
            - kwh (monthly energy usage)
    """
    df = load_natural_gas()

    start_month = pd.to_datetime(start).strftime("%Y-%m")
    end_month = pd.to_datetime(end).strftime("%Y-%m")

    df = df[(df["month"] >= start_month) & (df["month"] <= end_month)]

    return df.to_dict(orient="records")