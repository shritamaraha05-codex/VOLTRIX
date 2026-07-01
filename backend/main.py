"""
main.py — GridSense FastAPI backend
Owner: Mrinmoy

Run locally:
    uvicorn main:app --reload --port 8000

Environment variables required (set in .env or Cloud Run config):
    DATABASE_URL          postgresql://user:pass@host:5432/gridsense
    GCP_PROJECT           your GCP project id
    GCP_LOCATION          us-central1 (default)
    RESEND_API_KEY        your Resend key (optional — only needed for email delivery)
    GOOGLE_APPLICATION_CREDENTIALS   path to service-account JSON (local dev only)
"""

import os
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

import db
import bq as bq_module
from forecasting import forecast_zone, detect_stress
from reasoning import generate_reasoning_and_nudges
from models import (
    ZoneOut,
    ForecastPoint,
    StressEventOut,
    RecommendationOut,
    AdvanceResponse,
    ZoneAdvanceResult,
    SeedResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridsense")

app = FastAPI(
    title="GridSense API",
    version="1.0.0",
    description="AI-powered energy load forecasting and citizen nudge platform",
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
# Allow the React frontend (Vercel / localhost:5173) to call the API.
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "gridsense-api"}


# ─── Zones ────────────────────────────────────────────────────────────────────

@app.get("/zones", response_model=list[ZoneOut])
def list_zones():
    """Return all zones with their capacity metadata."""
    return db.fetch_all("SELECT * FROM zones ORDER BY name")


@app.get("/zones/{bq_zone_id}/load-history", response_model=list[ForecastPoint])
def zone_load_history(bq_zone_id: str, days: int = 3):
    """
    Returns hourly load data for the React zone chart.
    Pulls from BigQuery — last `days` days only to keep the chart readable.
    """
    zone = db.fetch_one("SELECT id FROM zones WHERE bq_zone_id = %s", (bq_zone_id,))
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone {bq_zone_id} not found")

    raw = bq_module.get_zone_load_for_chart(bq_zone_id, days_back=days)

    # Also merge in any stored forecasts for the same window so the frontend
    # can render the predicted dashed line alongside actual data.
    forecasts = db.fetch_all(
        """
        SELECT forecast_for, predicted_load_kw
        FROM forecasts
        WHERE zone_id = %s
        ORDER BY forecast_for DESC
        LIMIT %s
        """,
        (zone["id"], days * 24),
    )
    forecast_map = {
        row["forecast_for"][:13]: row["predicted_load_kw"]  # match on YYYY-MM-DDTHH
        for row in forecasts
    }

    result = []
    for r in raw:
        hour_key = r["hour"]  # "MM-DD HH:MM"
        result.append(
            ForecastPoint(
                hour=hour_key,
                actual=r["actual"],
                predicted=forecast_map.get(hour_key),
            )
        )
    return result


# ─── Stress events ────────────────────────────────────────────────────────────

@app.get("/stress-events", response_model=list[StressEventOut])
def list_stress_events(limit: int = 20):
    """Return the most recent stress events with zone name joined."""
    rows = db.fetch_all(
        """
        SELECT
            se.*,
            z.name AS zone_name
        FROM stress_events se
        JOIN zones z ON z.id = se.zone_id
        ORDER BY se.detected_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return rows


@app.get("/stress-events/{event_id}", response_model=StressEventOut)
def get_stress_event(event_id: str):
    row = db.fetch_one(
        """
        SELECT se.*, z.name AS zone_name
        FROM stress_events se
        JOIN zones z ON z.id = se.zone_id
        WHERE se.id = %s
        """,
        (event_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stress event not found")
    return row


# ─── Recommendations ──────────────────────────────────────────────────────────

@app.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(limit: int = 50, unsent_only: bool = False):
    """Return recommendations, optionally filtered to only unsent ones."""
    query = "SELECT * FROM recommendations"
    params: tuple = ()
    if unsent_only:
        query += " WHERE sent = false"
    query += " ORDER BY created_at DESC LIMIT %s"
    params = (*params, limit)
    return db.fetch_all(query, params)


@app.get("/stress-events/{event_id}/recommendations", response_model=list[RecommendationOut])
def get_event_recommendations(event_id: str):
    """Return all recommendations for a specific stress event (for the drill-down view)."""
    return db.fetch_all(
        "SELECT * FROM recommendations WHERE stress_event_id = %s ORDER BY target_type, created_at",
        (event_id,),
    )


@app.post("/recommendations/{recommendation_id}/mark-sent")
def mark_sent(recommendation_id: str):
    """
    Called by Shritama's Resend integration after an email is delivered.
    """
    db.mark_recommendation_sent(recommendation_id)
    return {"ok": True}


# ─── Simulation state ─────────────────────────────────────────────────────────

@app.get("/simulation/state")
def get_simulation_state():
    return {"current_day": db.get_simulation_day()}


# ─── /simulate/advance — the money endpoint ───────────────────────────────────

@app.post("/simulate/advance", response_model=AdvanceResponse)
def advance_simulation():
    """
    Advances the simulated clock by one day and runs the full pipeline:
      1. Increment current_day in simulation_state
      2. For each zone:
         a. Pull the load window up to current_day from BigQuery
         b. Run Prophet forecasting for the next 24 hours
         c. Detect stress (threshold check)
         d. If stress → call Gemini for reasoning + nudges
         e. Persist stress event + recommendations to Postgres
      3. Return per-zone results for the dashboard to re-render

    This is the button the judge clicks during the demo.
    """
    new_day = db.advance_simulation_day()
    logger.info(f"[simulate/advance] Advanced to day {new_day}")

    zones = db.fetch_all("SELECT * FROM zones ORDER BY name")
    results: list[ZoneAdvanceResult] = []

    for zone in zones:
        bq_zone_id = zone["bq_zone_id"]
        capacity   = float(zone["baseline_capacity_kw"])
        zone_name  = zone["name"]
        logger.info(f"  → Processing {zone_name} ({bq_zone_id})")

        try:
            # Step 1: Forecast
            forecast_df = forecast_zone(bq_zone_id, periods=24, current_day=new_day)

            # Persist forecasts for the chart
            forecast_rows = [
                {"forecast_for": row["ds"], "predicted_load_kw": float(row["yhat"])}
                for _, row in forecast_df.iterrows()
            ]
            db.save_forecasts(bq_zone_id, forecast_rows)

            # Step 2: Stress detection
            stress = detect_stress(bq_zone_id, forecast_df, capacity)

            if not stress:
                results.append(ZoneAdvanceResult(
                    zone_name=zone_name,
                    bq_zone_id=bq_zone_id,
                    stress_detected=False,
                ))
                continue

            # Step 3: Get household context for Gemini
            household_summaries = bq_module.get_household_load_for_zone(
                bq_zone_id, day=new_day, limit=8
            )

            # Step 4: Gemini reasoning
            logger.info(f"    ⚡ Stress detected — calling Gemini for {zone_name}")
            ai_output = generate_reasoning_and_nudges(stress, household_summaries)

            # Step 5: Persist stress event
            event_id = db.save_stress_event(
                bq_zone_id=bq_zone_id,
                window_start=stress["window_start"],
                window_end=stress["window_end"],
                severity=stress["severity"],
                predicted_peak_kw=stress["predicted_peak_kw"],
                capacity_kw=capacity,
                reasoning=ai_output.get("reasoning", ""),
            )

            # Step 6: Persist household nudges
            nudges = ai_output.get("household_nudges", [])
            for nudge in nudges:
                db.save_recommendation(
                    stress_event_id=event_id,
                    target_type="household",
                    message=nudge.get("message", ""),
                    action_suggested=nudge.get("suggested_shift", ""),
                    bq_household_id=nudge.get("household_id"),
                )

            # Step 7: Persist utility-facing alert
            db.save_recommendation(
                stress_event_id=event_id,
                target_type="utility",
                message=ai_output.get("utility_action", ""),
                action_suggested=ai_output.get("utility_action", ""),
            )

            results.append(ZoneAdvanceResult(
                zone_name=zone_name,
                bq_zone_id=bq_zone_id,
                stress_detected=True,
                severity=stress["severity"],
                reasoning=ai_output.get("reasoning"),
                nudges_generated=len(nudges),
            ))

        except Exception as exc:
            logger.error(f"  ✗ Error processing {zone_name}: {exc}", exc_info=True)
            results.append(ZoneAdvanceResult(
                zone_name=zone_name,
                bq_zone_id=bq_zone_id,
                stress_detected=False,
            ))

    return AdvanceResponse(new_day=new_day, results=results)


# ─── Admin / one-time seed ────────────────────────────────────────────────────

@app.post("/admin/seed-households", response_model=SeedResponse)
def seed_households():
    """
    One-time: reads distinct households from BigQuery and inserts them
    into the Postgres households table. Run once after loading synthetic data.
    Safe to re-run (skips existing rows).
    """
    try:
        bq_module.seed_households_from_bq(db)
        count = db.fetch_one("SELECT COUNT(*) AS n FROM households")
        return SeedResponse(
            message="Households seeded successfully",
            households_seeded=count["n"] if count else 0,
        )
    except Exception as e:
        logger.error(f"Seed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/reset-simulation")
def reset_simulation():
    """Reset the simulation clock back to day 1. Use before demo rehearsal."""
    db.execute("UPDATE simulation_state SET current_day = 1 WHERE id = 1")
    db.execute("DELETE FROM recommendations")
    db.execute("DELETE FROM stress_events")
    db.execute("DELETE FROM forecasts")
    return {"ok": True, "message": "Simulation reset to day 1"}
