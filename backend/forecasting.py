"""
forecasting.py — Prophet zone-level forecasting + stress detection
Primary owner: Debjyoti (fills in the logic)
Wiring owner:  Mrinmoy (imports + calls this from main.py)

Mrinmoy: do NOT change the function signatures.
         The interfaces below are what main.py depends on.
"""

import pandas as pd
from prophet import Prophet
import bq as bq_module


# ─── Forecasting ──────────────────────────────────────────────────────────────

def forecast_zone(bq_zone_id: str, periods: int = 24, current_day: int = 30) -> pd.DataFrame:
    """
    Train Prophet on `current_day` days of historical load from BigQuery
    and forecast the next `periods` hours.

    Returns a DataFrame with columns: ds (datetime), yhat (float)
    These are the next `periods` hours only (tail of the full forecast).
    """
    raw = bq_module.get_zone_load_window(bq_zone_id, day=current_day)
    if not raw:
        return pd.DataFrame(columns=["ds", "yhat"])

    df = pd.DataFrame(raw).rename(columns={"timestamp": "ds", "load_kw": "y"})
    df["ds"] = pd.to_datetime(df["ds"])

    model = Prophet(daily_seasonality=True, weekly_seasonality=False, changepoint_prior_scale=0.05)
    model.fit(df)

    future = model.make_future_dataframe(periods=periods, freq="h")
    forecast = model.predict(future)

    return forecast[["ds", "yhat"]].tail(periods).reset_index(drop=True)


# ─── Stress detection ─────────────────────────────────────────────────────────

def detect_stress(
    bq_zone_id: str,
    forecast_df: pd.DataFrame,
    capacity_kw: float,
    threshold_pct: float = 0.85,
) -> dict | None:
    """
    Checks if any forecast hour breaches the capacity threshold.

    Returns a stress dict if a breach is found, else None.
    The dict is passed directly to reasoning.generate_reasoning_and_nudges().
    """
    if forecast_df.empty:
        return None

    threshold = capacity_kw * threshold_pct
    stress_windows = forecast_df[forecast_df["yhat"] > threshold]

    if stress_windows.empty:
        return None

    peak_kw = float(stress_windows["yhat"].max())
    return {
        "zone_id": bq_zone_id,
        "window_start": stress_windows["ds"].min().to_pydatetime(),
        "window_end":   stress_windows["ds"].max().to_pydatetime(),
        "predicted_peak_kw": peak_kw,
        "capacity_kw": capacity_kw,
        "severity": "critical" if peak_kw > capacity_kw else "moderate",
    }
