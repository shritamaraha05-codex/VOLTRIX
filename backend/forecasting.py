"""
forecasting.py — Prophet zone-level forecasting + stress detection
Primary owner: Debjyoti (fills in the logic)
Wiring owner:  Mrinmoy (imports + calls this from main.py)

Mrinmoy: do NOT change the function signatures.
         The interfaces below are what main.py depends on.
"""

import pandas as pd
import numpy as np
from prophet import Prophet
import bq as bq_module
import logging

logger = logging.getLogger("voltrix.forecasting")


# ─── Forecasting ──────────────────────────────────────────────────────────────


def forecast_zone(
    bq_zone_id: str, periods: int = 24, current_day: int = 30
) -> pd.DataFrame:
    """
    Train Prophet on `current_day` days of historical load from BigQuery
    and forecast the next `periods` hours.

    Returns a DataFrame with columns: ds (datetime), yhat (float)
    These are the next `periods` hours only (tail of the full forecast).
    """
    try:
        raw = bq_module.get_zone_load_window(bq_zone_id, day=current_day)
        if not raw:
            return pd.DataFrame(columns=["ds", "yhat"])

        df = pd.DataFrame(raw).rename(columns={"timestamp": "ds", "load_kw": "y"})

        # BigQuery returns timezone-aware timestamps; Prophet needs naive
        df["ds"] = pd.to_datetime(df["ds"], utc=True).dt.tz_localize(None)

        if len(df) < 48:
            return pd.DataFrame(columns=["ds", "yhat"])

        # Add hour-of-day cyclical regressors to help Prophet capture evening peak
        df["hour"] = df["ds"].dt.hour
        df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)

        model = Prophet(
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False,
            changepoint_prior_scale=0.05,
            uncertainty_samples=0,
        )
        model.add_regressor("hour_sin")
        model.add_regressor("hour_cos")
        model.fit(df[["ds", "y", "hour_sin", "hour_cos"]])

        future = model.make_future_dataframe(periods=periods, freq="h")
        future["hour"] = future["ds"].dt.hour
        future["hour_sin"] = np.sin(2 * np.pi * future["hour"] / 24)
        future["hour_cos"] = np.cos(2 * np.pi * future["hour"] / 24)

        forecast = model.predict(future)

        # Load cannot be negative
        forecast["yhat"] = forecast["yhat"].clip(lower=0)

        # Return only the forecast horizon
        return forecast[["ds", "yhat"]].tail(periods).reset_index(drop=True)

    except Exception as e:
        logger.error(f"forecast_zone failed for {bq_zone_id}: {e}")
        return pd.DataFrame(columns=["ds", "yhat"])


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
    try:
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
            "window_end": stress_windows["ds"].max().to_pydatetime(),
            "predicted_peak_kw": peak_kw,
            "capacity_kw": capacity_kw,
            "overage_pct": round((peak_kw / capacity_kw - 1) * 100, 1),
            "hours_stressed": int(len(stress_windows)),
            "severity": "critical" if peak_kw > capacity_kw else "moderate",
        }

    except Exception as e:
        logger.error(f"detect_stress failed for {bq_zone_id}: {e}")
        return None
