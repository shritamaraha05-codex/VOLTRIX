"""
forecasting.py — Prophet zone-level forecasting + stress detection
Primary owner: Debjyoti (fills in the logic)
Wiring owner:  Mrinmoy (imports + calls this from main.py)

Mrinmoy: do NOT change the function signatures.
         The interfaces below are what main.py depends on.

Forecasting design notes (v2):
  - Prophet is fit with daily + weekly seasonality plus explicit hour-of-day
    (sin/cos) and is_weekend regressors, which gives it a much stronger
    signal for the evening-peak / weekday-vs-weekend shape of household load
    than seasonality terms alone, especially on short (~30 day) windows.
  - Uncertainty intervals are enabled (previously uncertainty_samples=0, which
    silently produced meaningless yhat_lower/yhat_upper). yhat_upper is used
    as a conservative secondary signal in stress detection so a borderline
    forecast doesn't get missed.
  - If Prophet can't be trained (too little / degenerate history) or throws,
    we fall back to a seasonal-naive forecast (same hour yesterday / same
    hour last week, averaged) instead of returning an empty frame. An empty
    frame silently skips stress detection for that zone, which is worse for
    a live demo than a slightly less precise fallback forecast.
  - backtest_zone() adds rolling-origin backtesting (MAPE / RMSE) so accuracy
    can be quoted with real numbers instead of asserted.
"""

import pandas as pd
import numpy as np
from prophet import Prophet
import bq as bq_module
import logging

logger = logging.getLogger("voltrix.forecasting")

MIN_TRAINING_HOURS = 48  # below this, Prophet's seasonality terms are unreliable
MIN_NAIVE_HOURS = 24  # below this, even the naive fallback has nothing to go on


# ─── Feature engineering ───────────────────────────────────────────────────────


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["hour_sin"] = np.sin(2 * np.pi * df["ds"].dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["ds"].dt.hour / 24)
    df["is_weekend"] = (df["ds"].dt.dayofweek >= 5).astype(float)
    return df


REGRESSORS = ["hour_sin", "hour_cos", "is_weekend"]


def _build_model() -> Prophet:
    model = Prophet(
        daily_seasonality="auto",
        weekly_seasonality="auto",
        yearly_seasonality="auto",
        changepoint_prior_scale=0.05,
        seasonality_mode="additive",
        interval_width=0.90,
        uncertainty_samples=300,
    )
    for reg in REGRESSORS:
        model.add_regressor(reg)
    return model


def _seasonal_naive_forecast(df: pd.DataFrame, periods: int) -> pd.DataFrame:
    """
    Fallback forecaster used when Prophet can't be trained (too little /
    degenerate history) or raises. Predicts each future hour as the average
    of the same hour 24h ago and the same hour 168h (1 week) ago, falling
    back further to the trailing 24h mean if neither is available.
    Always returns `periods` rows so downstream code behaves identically.
    """
    df = df.sort_values("ds")
    series = df.set_index("ds")["y"]
    last_ts = df["ds"].max()
    future_index = pd.date_range(
        start=last_ts + pd.Timedelta(hours=1), periods=periods, freq="h"
    )

    trailing_mean = float(series.tail(24).mean()) if len(series) else 0.0
    preds = []
    for ts in future_index:
        candidates = [
            series.loc[c]
            for c in (ts - pd.Timedelta(hours=24), ts - pd.Timedelta(hours=168))
            if c in series.index
        ]
        preds.append(float(np.mean(candidates)) if candidates else trailing_mean)

    preds = np.clip(preds, a_min=0, a_max=None)
    return pd.DataFrame(
        {
            "ds": future_index,
            "yhat": preds,
            "yhat_lower": preds,
            "yhat_upper": preds,
            "method": "seasonal_naive",
        }
    )


# ─── Forecasting ──────────────────────────────────────────────────────────────


def forecast_zone(
    bq_zone_id: str, periods: int = 24, current_day: int = 30
) -> pd.DataFrame:
    """
    Train Prophet on `current_day` days of historical load from BigQuery
    and forecast the next `periods` hours.

    Returns a DataFrame with columns: ds (datetime), yhat (float), plus
    yhat_lower / yhat_upper (90% interval) and method ("prophet" or
    "seasonal_naive"). main.py only reads ds/yhat, so this is backwards
    compatible with the existing contract; the extra columns are available
    to any caller (e.g. explainability panel) that wants them.

    These are the next `periods` hours only (tail of the full forecast).
    """
    try:
        raw = bq_module.get_zone_load_window(bq_zone_id, day=current_day)
    except Exception as e:
        logger.error(f"forecast_zone: could not fetch history for {bq_zone_id}: {e}")
        return pd.DataFrame(
            columns=["ds", "yhat", "yhat_lower", "yhat_upper", "method"]
        )

    if len(raw) < MIN_NAIVE_HOURS:
        logger.warning(
            f"forecast_zone: only {len(raw)}h of history for {bq_zone_id} "
            f"(need >= {MIN_NAIVE_HOURS}h) — returning empty forecast"
        )
        return pd.DataFrame(
            columns=["ds", "yhat", "yhat_lower", "yhat_upper", "method"]
        )

    df = pd.DataFrame(raw).rename(columns={"timestamp": "ds", "load_kw": "y"})
    # BigQuery returns timezone-aware timestamps; Prophet needs naive
    df["ds"] = pd.to_datetime(df["ds"], utc=True).dt.tz_localize(None)
    df = df.dropna(subset=["ds", "y"]).sort_values("ds").reset_index(drop=True)

    if len(df) < MIN_TRAINING_HOURS:
        logger.info(
            f"forecast_zone: {len(df)}h of history for {bq_zone_id} is below the "
            f"{MIN_TRAINING_HOURS}h Prophet threshold — using seasonal-naive fallback"
        )
        return _seasonal_naive_forecast(df, periods)

    try:
        df = _add_calendar_features(df)
        model = _build_model()
        model.fit(df[["ds", "y"] + REGRESSORS])

        future = model.make_future_dataframe(periods=periods, freq="h")
        future = _add_calendar_features(future)

        forecast = model.predict(future)

        # Load cannot be negative
        for col in ("yhat", "yhat_lower", "yhat_upper"):
            forecast[col] = forecast[col].clip(lower=0)
        forecast["method"] = "prophet"

        return (
            forecast[["ds", "yhat", "yhat_lower", "yhat_upper", "method"]]
            .tail(periods)
            .reset_index(drop=True)
        )

    except Exception as e:
        logger.error(
            f"forecast_zone: Prophet failed for {bq_zone_id}, falling back to "
            f"seasonal-naive: {e}"
        )
        return _seasonal_naive_forecast(df, periods)


# ─── Backtesting / accuracy reporting ─────────────────────────────────────────


def backtest_zone(bq_zone_id: str, test_days: int = 3, current_day: int = 30) -> dict:
    """
    Rolling-origin backtest: for each of the last `test_days` days, trains on
    everything before that day and forecasts the next 24h, then compares to
    what actually happened. Returns MAPE / RMSE per day and in aggregate.

    This gives a real, defensible accuracy number for the demo/pitch instead
    of an unverified claim — call it from an admin/debug endpoint or print it
    before a demo run.
    """
    daily_results = []
    all_errors_pct = []
    all_sq_errors = []

    for offset in range(test_days, 0, -1):
        train_day = current_day - offset
        test_day = train_day + 1
        if train_day < 2:
            continue

        try:
            raw = bq_module.get_zone_load_window(bq_zone_id, day=train_day)
        except Exception as e:
            logger.error(
                f"backtest_zone: history fetch failed for day {train_day}: {e}"
            )
            continue
        if len(raw) < MIN_NAIVE_HOURS:
            continue

        forecast = forecast_zone(bq_zone_id, periods=24, current_day=train_day)
        if forecast.empty:
            continue

        try:
            actual_raw = bq_module.get_zone_load_window(bq_zone_id, day=test_day)
        except Exception as e:
            logger.error(f"backtest_zone: actuals fetch failed for day {test_day}: {e}")
            continue

        actual_df = pd.DataFrame(actual_raw).rename(
            columns={"timestamp": "ds", "load_kw": "actual"}
        )
        if actual_df.empty:
            continue
        actual_df["ds"] = pd.to_datetime(actual_df["ds"], utc=True).dt.tz_localize(None)

        merged = forecast.merge(actual_df, on="ds", how="inner")
        merged = merged[
            merged["actual"] > 0.01
        ]  # avoid div-by-near-zero blowing up MAPE
        if merged.empty:
            continue

        errors_pct = (
            (merged["yhat"] - merged["actual"]).abs() / merged["actual"]
        ) * 100
        sq_errors = (merged["yhat"] - merged["actual"]) ** 2

        daily_results.append(
            {
                "train_day": train_day,
                "test_day": test_day,
                "hours_compared": int(len(merged)),
                "mape_pct": round(float(errors_pct.mean()), 2),
                "rmse_kw": round(float(np.sqrt(sq_errors.mean())), 3),
            }
        )
        all_errors_pct.extend(errors_pct.tolist())
        all_sq_errors.extend(sq_errors.tolist())

    if not daily_results:
        return {
            "zone_id": bq_zone_id,
            "days_evaluated": 0,
            "overall_mape_pct": None,
            "overall_rmse_kw": None,
            "daily": [],
            "note": "Not enough history to backtest yet — need at least 2 full days before current_day.",
        }

    return {
        "zone_id": bq_zone_id,
        "days_evaluated": len(daily_results),
        "overall_mape_pct": round(float(np.mean(all_errors_pct)), 2),
        "overall_rmse_kw": round(float(np.sqrt(np.mean(all_sq_errors))), 3),
        "daily": daily_results,
    }


# ─── Stress detection ─────────────────────────────────────────────────────────


def detect_stress(
    bq_zone_id: str,
    forecast_df: pd.DataFrame,
    capacity_kw: float,
    threshold_pct: float = 0.85,
) -> dict | None:
    """
    Checks if any forecast hour breaches the capacity threshold.

    Uses yhat as the primary signal (unchanged contract), but if yhat alone
    doesn't breach the threshold, also checks yhat_upper (the top of the 90%
    interval) when available — catching cases where the point forecast sits
    just under the line but there's meaningful risk of exceeding it. Those
    are flagged with a lower confidence rather than treated identically to a
    clear point-forecast breach.

    Returns a stress dict if a breach is found, else None.
    The dict is passed directly to agent.run_stress_analysis().
    """
    try:
        if forecast_df.empty:
            return None

        threshold = capacity_kw * threshold_pct
        stress_rows = forecast_df[forecast_df["yhat"] > threshold]
        confidence = "point_forecast"

        if stress_rows.empty and "yhat_upper" in forecast_df.columns:
            at_risk_rows = forecast_df[forecast_df["yhat_upper"] > threshold]
            if not at_risk_rows.empty:
                stress_rows = at_risk_rows
                confidence = "upper_interval"

        if stress_rows.empty:
            return None

        peak_kw = float(stress_rows["yhat"].max())

        return {
            "zone_id": bq_zone_id,
            "window_start": stress_rows["ds"].min().to_pydatetime(),
            "window_end": stress_rows["ds"].max().to_pydatetime(),
            "predicted_peak_kw": peak_kw,
            "capacity_kw": capacity_kw,
            "overage_pct": round(((peak_kw - capacity_kw) / capacity_kw) * 100, 1),
            "hours_stressed": int(len(stress_rows)),
            "severity": "critical" if peak_kw > capacity_kw else "moderate",
            "confidence": confidence,
        }

    except Exception as e:
        logger.error(f"detect_stress failed for {bq_zone_id}: {e}")
        return None
