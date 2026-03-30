from fastapi import APIRouter, Request, Depends
from helpers.auth_dependencies import get_current_user_from_session
from helpers.rate_limit import limiter
import pandas as pd
from pathlib import Path

router = APIRouter(prefix="/natural-gas", tags=["Natural Gas"])


def load_natural_gas():
    csv_path = Path("data/natural_gas.csv")
    df = pd.read_csv(csv_path)

    # clean column names
    df.columns = df.columns.str.strip()

    # use first column as date, third column as usage in GJ
    date_col = df.columns[0]
    usage_col = df.columns[2]

    # convert types
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[usage_col] = pd.to_numeric(df[usage_col], errors="coerce")

    # remove bad rows
    df = df.dropna(subset=[date_col, usage_col])

    # convert GJ to kWh
    df["kwh"] = df[usage_col] * 277.777778

    # month format like 2023-02
    df["month"] = df[date_col].dt.strftime("%Y-%m")

    # monthly total
    monthly = df.groupby("month", as_index=False)["kwh"].sum()

    # round for cleaner API response
    monthly["kwh"] = monthly["kwh"].round(2)

    return monthly


@router.get("/monthly")
@limiter.limit("10/minute")
async def get_natural_gas_monthly(
    request: Request,
    start: str,
    end: str,
    _user=Depends(get_current_user_from_session)
):
    df = load_natural_gas()

    start_month = pd.to_datetime(start).strftime("%Y-%m")
    end_month = pd.to_datetime(end).strftime("%Y-%m")

    df = df[(df["month"] >= start_month) & (df["month"] <= end_month)]

    return df.to_dict(orient="records")