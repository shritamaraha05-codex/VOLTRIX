"""
main.py — VOLTRIX FastAPI backend

Run locally:
    uvicorn main:app --reload --port 8000

Environment variables required (set in .env or Cloud Run config):
    DATABASE_URL          postgresql://user:pass@host:5432/postgres
    GCP_PROJECT           your GCP project id
    GEMINI_API_KEY        from aistudio.google.com (free, no credit card)
    SMTP_*                SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (for email delivery)
    GOOGLE_APPLICATION_CREDENTIALS   path to service-account JSON (local dev only)
"""

import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from dotenv import load_dotenv

load_dotenv()

import db
import bq as bq_module
import ingestion
from forecasting import (
    forecast_zone,
    detect_stress,
    detect_actual_stress,
    backtest_zone,
)
from agent import run_stress_analysis, answer_question, answer_question_stream
from email_service import send_nudge_email, send_utility_alert, SMTP_USER
from models import (
    ZoneOut,
    ForecastPoint,
    StressEventOut,
    RecommendationOut,
    AdvanceResponse,
    ZoneAdvanceResult,
    SeedResponse,
    ChatRequest,
    ChatResponse,
    IngestResponse,
    BacktestResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voltrix")

app = FastAPI(title="VOLTRIX API", version="1.0.0")

# ─── CORS ─────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000,null",
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
    return {"status": "ok", "service": "voltrix-api", "day": db.get_simulation_day()}


# ─── Zones ────────────────────────────────────────────────────────────────────


@app.get("/zones", response_model=list[ZoneOut])
def list_zones():
    """Return all zones with their capacity metadata."""
    return db.fetch_all("SELECT * FROM zones ORDER BY name")


@app.get("/zones/{bq_zone_id}/load-history", response_model=list[ForecastPoint])
def zone_load_history(bq_zone_id: str, days: int = 3):
    """
    Returns hourly load data for the React zone chart.
    Merges actual (from BigQuery) with predicted (from forecasts table).
    Forecasts are filtered to the same time window as the actuals so they
    overlap on the chart — only forecasts whose forecast_for falls within
    the actuals' date range are included.
    """
    zone = db.fetch_one("SELECT id FROM zones WHERE bq_zone_id = %s", (bq_zone_id,))
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone {bq_zone_id} not found")

    sim_day = db.get_simulation_day()
    window = bq_module.get_zone_load_window(bq_zone_id, day=sim_day)
    if not window:
        return []

    if len(window) > days * 24:
        window = window[-(days * 24) :]

    # Determine the actuals time range to scope the forecast query
    actual_min = min(r["timestamp"] for r in window)
    actual_max = max(r["timestamp"] for r in window)

    raw = [
        {
            "hour": r["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "actual": round(r["load_kw"], 2),
        }
        for r in window
    ]

    # Fetch forecasts whose forecast_for falls within the actuals window
    forecasts = db.fetch_all(
        """
        SELECT forecast_for, predicted_load_kw
        FROM forecasts
        WHERE zone_id = %s
          AND forecast_for >= %s
          AND forecast_for <= %s
        ORDER BY forecast_for
        """,
        (zone["id"], actual_min, actual_max),
    )

    # Build forecast map keyed on the same hour format
    forecast_map = {}
    for row in forecasts:
        try:
            raw_ts = row["forecast_for"]
            if isinstance(raw_ts, str):
                raw_ts = raw_ts.replace("Z", "+00:00")
                f_dt = datetime.fromisoformat(raw_ts)
            else:
                f_dt = raw_ts
            hour_key = f_dt.strftime("%Y-%m-%d %H:%M")
            forecast_map[hour_key] = float(row["predicted_load_kw"])
        except (ValueError, TypeError):
            pass

    return [
        ForecastPoint(
            hour=r["hour"],
            actual=r["actual"],
            predicted=forecast_map.get(r["hour"]),
        )
        for r in raw
    ]


@app.get("/zones/{bq_zone_id}/backtest", response_model=BacktestResponse)
def zone_backtest(bq_zone_id: str, test_days: int = 3):
    """
    Rolling-origin backtest of the forecasting model for one zone: trains on
    data up to each of the last `test_days` days and compares the 24h
    forecast against what actually happened. Returns MAPE / RMSE so accuracy
    can be quoted with real numbers (e.g. before a demo or in the pitch).
    """
    day = db.get_simulation_day()
    return backtest_zone(bq_zone_id, test_days=test_days, current_day=day)


# ─── Stress events ────────────────────────────────────────────────────────────


@app.get("/stress-events", response_model=list[StressEventOut])
def list_stress_events(limit: int = 20):
    """Return the most recent stress events with zone name joined."""
    rows = db.fetch_all(
        """
        SELECT se.*, z.name AS zone_name
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


@app.get(
    "/stress-events/{event_id}/recommendations", response_model=list[RecommendationOut]
)
def get_event_recommendations(event_id: str):
    """Return all recommendations for a specific stress event."""
    return db.fetch_all(
        "SELECT * FROM recommendations WHERE stress_event_id = %s ORDER BY target_type, created_at",
        (event_id,),
    )


# ─── Recommendations ──────────────────────────────────────────────────────────


@app.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(limit: int = 50, unsent_only: bool = False):
    """Return recommendations, optionally filtered to only unsent ones."""
    query = "SELECT * FROM recommendations"
    params = []
    if unsent_only:
        query += " WHERE sent = false"
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    return db.fetch_all(query, tuple(params))


@app.post("/recommendations/{recommendation_id}/send")
def send_recommendation(recommendation_id: str):
    """
    Send a recommendation via email (SMTP).
    Never raises — catches all exceptions and returns {"ok": false, "reason": ...}.
    """
    try:
        rec = db.fetch_one(
            "SELECT * FROM recommendations WHERE id = %s",
            (recommendation_id,),
        )
        if not rec:
            return {"ok": False, "reason": "recommendation not found"}

        target_type = rec["target_type"]

        if target_type == "household":
            if not rec["household_id"]:
                return {"ok": False, "reason": "no household linked"}
            household = db.fetch_one(
                "SELECT email, name FROM households WHERE id = %s",
                (rec["household_id"],),
            )
            if not household or not household.get("email"):
                return {"ok": False, "reason": "no email on file"}
            ok = send_nudge_email(
                to_email=household["email"],
                household_name=household["name"] or "Resident",
                nudge_message=rec["message"] or "",
                action_suggested=rec["action_suggested"] or "",
            )
            if not ok:
                return {"ok": False, "reason": "email delivery failed"}
            db.mark_recommendation_sent(recommendation_id)
            return {"ok": True, "sent_to": household["email"]}

        elif target_type == "utility":
            ops_email = os.environ.get("VOLTRIX_OPS_EMAIL", SMTP_USER)
            stress_event = db.fetch_one(
                """
                SELECT z.name AS zone_name
                FROM stress_events se
                JOIN zones z ON z.id = se.zone_id
                WHERE se.id = %s
                """,
                (rec["stress_event_id"],),
            )
            zone_name = stress_event["zone_name"] if stress_event else "Unknown Zone"
            reasoning_text = ""
            if stress_event:
                full_event = db.fetch_one(
                    "SELECT reasoning FROM stress_events WHERE id = %s",
                    (rec["stress_event_id"],),
                )
                reasoning_text = full_event.get("reasoning", "") if full_event else ""
            ok = send_utility_alert(
                to_email=ops_email,
                zone_name=zone_name,
                utility_action=rec["message"] or rec["action_suggested"] or "",
                reasoning=reasoning_text,
            )
            if not ok:
                return {"ok": False, "reason": "email delivery failed"}
            db.mark_recommendation_sent(recommendation_id)
            return {"ok": True, "sent_to": ops_email}

        return {"ok": False, "reason": f"unknown target_type: {target_type}"}

    except Exception as e:
        logger.error(f"send_recommendation failed: {e}", exc_info=True)
        return {"ok": False, "reason": str(e)}


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
         a. Skip if zone already has an active (future) stress event
         b. Pull the load window up to current_day from BigQuery
         c. Run Prophet forecasting for the next 24 hours
         d. Detect stress (threshold check)
         e. If stress -> call ADK agent for reasoning + nudges
         f. Persist stress event + recommendations to Postgres
      3. Return per-zone results for the dashboard to re-render
    """
    new_day = db.advance_simulation_day()
    logger.info(f"[simulate/advance] Advanced to day {new_day}")

    zones = db.fetch_all("SELECT * FROM zones ORDER BY name")
    results: list[ZoneAdvanceResult] = []

    for zone in zones:
        bq_zone_id = zone["bq_zone_id"]
        capacity = float(zone["baseline_capacity_kw"])
        zone_name = zone["name"]
        logger.info(f"  -> Processing {zone_name} ({bq_zone_id})")

        try:
            # Perf optimisation: skip zone if it already has an active stress event
            existing = db.fetch_one(
                """
                SELECT se.id FROM stress_events se
                JOIN zones z ON z.id = se.zone_id
                WHERE z.bq_zone_id = %s AND se.window_end > NOW()
                LIMIT 1
                """,
                (bq_zone_id,),
            )
            if existing:
                logger.info(f"    Skipping {zone_name} - active stress event exists")
                results.append(
                    ZoneAdvanceResult(
                        zone_name=zone_name,
                        bq_zone_id=bq_zone_id,
                        stress_detected=True,
                    )
                )
                continue

            # Step 1: Forecast
            # IMPORTANT: train through the day *before* new_day, not new_day
            # itself. get_zone_load_window(day=N) returns data up to and
            # including day N, so calling this with current_day=new_day would
            # train on data that already contains new_day's own readings
            # (including any injected stress event) and then forecast the day
            # *after* that — meaning the system would never actually predict
            # the day it's supposed to be evaluating. household_summaries
            # below already correctly uses day=new_day, so the forecast needs
            # to target the same day, trained on everything strictly before it.
            forecast_df = forecast_zone(
                bq_zone_id, periods=24, current_day=max(new_day - 1, 0)
            )

            # Persist forecasts for the chart
            forecast_rows = [
                {"forecast_for": row["ds"], "predicted_load_kw": float(row["yhat"])}
                for _, row in forecast_df.iterrows()
            ]
            db.save_forecasts(bq_zone_id, forecast_rows)

            # Step 2: Stress detection
            stress = detect_stress(bq_zone_id, forecast_df, capacity)

            if not stress:
                # Forecast-based detection only has a chance against stress
                # that's building up gradually (there's a precedent in the
                # preceding hours to extrapolate from). A genuinely novel,
                # first-time spike with zero lead-up can't be predicted a day
                # ahead by any load-history model. Fall back to checking
                # today's own actual readings directly.
                try:
                    day_readings = bq_module.get_zone_load_for_day(bq_zone_id, new_day)
                    stress = detect_actual_stress(bq_zone_id, day_readings, capacity)
                except Exception as e:
                    logger.error(f"    Nowcast check failed for {zone_name}: {e}")

            if not stress:
                results.append(
                    ZoneAdvanceResult(
                        zone_name=zone_name,
                        bq_zone_id=bq_zone_id,
                        stress_detected=False,
                    )
                )
                continue

            # Step 3: Get household context for the ADK agent
            household_summaries = bq_module.get_household_load_for_zone(
                bq_zone_id, day=new_day, limit=8
            )

            # Step 4: ADK agent analysis
            logger.info(f"    Stress detected -- calling ADK agent for {zone_name}")
            agent_output = run_stress_analysis(stress, household_summaries)

            # Step 5: Persist stress event
            event_id = db.save_stress_event(
                bq_zone_id=bq_zone_id,
                window_start=stress["window_start"],
                window_end=stress["window_end"],
                severity=stress["severity"],
                predicted_peak_kw=stress["predicted_peak_kw"],
                capacity_kw=capacity,
                reasoning=agent_output.get("reasoning", ""),
            )

            # Step 6: Persist household nudges
            nudges = agent_output.get("household_nudges", [])
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
                message=agent_output.get("utility_action", ""),
                action_suggested=agent_output.get("utility_action", ""),
            )

            results.append(
                ZoneAdvanceResult(
                    zone_name=zone_name,
                    bq_zone_id=bq_zone_id,
                    stress_detected=True,
                    severity=stress["severity"],
                    reasoning=agent_output.get("reasoning"),
                    nudges_generated=len(nudges),
                )
            )

        except Exception as exc:
            logger.error(f"  X Error processing {zone_name}: {exc}", exc_info=True)
            results.append(
                ZoneAdvanceResult(
                    zone_name=zone_name,
                    bq_zone_id=bq_zone_id,
                    stress_detected=False,
                )
            )

    return AdvanceResponse(new_day=new_day, results=results)


# ─── Chat ──────────────────────────────────────────────────────────────────────


@app.post("/chat")
async def chat(body: ChatRequest):
    """
    Conversational Q&A via SSE streaming from Gemma.
    Builds context from stress events, recommendations, and simulation state.
    """
    try:
        day = db.get_simulation_day()
        context_parts = [f"Current simulation day: {day}"]

        if body.household_id:
            household = db.fetch_one(
                "SELECT * FROM households WHERE bq_household_id = %s",
                (body.household_id,),
            )
            if household:
                zone = db.fetch_one(
                    "SELECT bq_zone_id, name FROM zones WHERE id = %s",
                    (household["zone_id"],),
                )
                bq_zone_id = zone["bq_zone_id"] if zone else None
                zone_name = zone["name"] if zone else "Unknown"
                context_parts.append(f"Zone: {zone_name} ({bq_zone_id})")
                context_parts.append(
                    f"Household: {household['name']} ({household['archetype']})"
                )

                if zone:
                    recent_stress = db.fetch_all(
                        """
                        SELECT detected_at, severity, predicted_peak_kw, capacity_kw, reasoning
                        FROM stress_events
                        WHERE zone_id = %s
                        ORDER BY detected_at DESC LIMIT 5
                        """,
                        (zone["id"],),
                    )
                    if recent_stress:
                        context_parts.append(f"Recent stress events: {recent_stress}")
                    else:
                        context_parts.append("No recent stress events.")

                    recent_recs = db.fetch_all(
                        """
                        SELECT target_type, message, action_suggested, sent
                        FROM recommendations
                        WHERE household_id = %s
                        ORDER BY created_at DESC LIMIT 10
                        """,
                        (household["id"],),
                    )
                    if recent_recs:
                        context_parts.append(f"Recommendations: {recent_recs}")
            else:
                context_parts.append(f"Household {body.household_id} not found.")
        else:
            context_parts.append("All zones overview:")
            zones = db.fetch_all("SELECT * FROM zones ORDER BY name")
            for z in zones:
                stress_count = db.fetch_one(
                    "SELECT COUNT(*) AS n FROM stress_events WHERE zone_id = %s",
                    (z["id"],),
                )
                context_parts.append(
                    f"{z['name']} ({z['bq_zone_id']}): "
                    f"capacity={z['baseline_capacity_kw']} kW, "
                    f"households={z['household_count']}, "
                    f"stress_events={stress_count['n'] if stress_count else 0}"
                )

        context = "\n".join(context_parts)

        async def event_stream():
            try:
                async for token in answer_question_stream(body.question, context):
                    yield f"data: {json.dumps({'token': token})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error("chat stream error: %s", e, exc_info=True)
                yield f"data: {json.dumps({'token': 'Sorry, I had trouble answering that.'})}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        logger.error(f"/chat error: {e}", exc_info=True)
        return StreamingResponse(
            iter(
                [
                    f"data: {json.dumps({'token': 'Sorry, I could not process that right now.'})}\n\n",
                    "data: [DONE]\n\n",
                ]
            ),
            media_type="text/event-stream",
        )


# ─── Data ingestion (CSV upload) ───────────────────────────────────────────────


@app.post("/ingest/readings", response_model=IngestResponse)
async def ingest_readings(file: UploadFile = File(...)):
    """
    Upload a CSV of smart-meter readings to append to BigQuery's
    load_readings table (the same table the synthetic data generator and
    forecasting both read/write). Columns required:
        household_id, zone_id, archetype, timestamp, load_kw

    Bad/missing values are cleaned rather than rejecting the whole file:
    unparseable timestamps and non-numeric loads are dropped, negative loads
    are clipped to 0, extreme outliers are capped, and duplicate
    (household_id, timestamp) rows are de-duplicated. The response reports
    exactly what was dropped/adjusted so nothing happens silently.
    """
    try:
        raw_bytes = await file.read()
        df = ingestion.parse_readings_csv(raw_bytes)
        cleaned, stats = ingestion.clean_readings(df)

        if cleaned.empty:
            return IngestResponse(
                status="error",
                rows_received=stats["rows_received"],
                rows_loaded=0,
                rows_rejected=stats["rows_rejected"],
                warnings=stats["warnings"],
                errors=["No valid rows remained after cleaning — nothing was loaded"],
            )

        bq_module.load_readings_dataframe(cleaned)

        # Flag (don't block on) readings for zones not yet registered in
        # Postgres — those rows land in BigQuery fine but won't show up in
        # /simulate/advance until zone metadata is uploaded via /ingest/zones.
        known_zones = db.known_zone_ids()
        unregistered = sorted(set(cleaned["zone_id"]) - known_zones)
        warnings = list(stats["warnings"])
        if unregistered:
            warnings.append(
                f"{len(cleaned[cleaned['zone_id'].isin(unregistered)])} row(s) reference "
                f"zone(s) not yet registered in the app: {', '.join(unregistered)}. "
                f"Upload zone metadata via POST /ingest/zones to include them in forecasting."
            )

        return IngestResponse(
            status="ok" if stats["rows_rejected"] == 0 else "partial",
            rows_received=stats["rows_received"],
            rows_loaded=stats["rows_loaded"],
            rows_rejected=stats["rows_rejected"],
            warnings=warnings,
        )

    except ingestion.IngestionError as e:
        return IngestResponse(status="error", errors=[str(e)])
    except Exception as e:
        logger.error(f"/ingest/readings failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/zones", response_model=IngestResponse)
async def ingest_zones(file: UploadFile = File(...)):
    """
    Upload a CSV of zone metadata to register/update zones. Columns required:
        zone_id, zone_name, capacity_kw
    Upserted directly into Postgres (the operational source of truth for
    zone capacity), so newly uploaded zones are immediately picked up by
    /zones and /simulate/advance.
    """
    try:
        raw_bytes = await file.read()
        df = ingestion.parse_zones_csv(raw_bytes)
        cleaned, stats = ingestion.clean_zones(df)

        if cleaned.empty:
            return IngestResponse(
                status="error",
                rows_received=stats["rows_received"],
                rows_loaded=0,
                rows_rejected=stats["rows_rejected"],
                warnings=stats["warnings"],
                errors=["No valid rows remained after cleaning — nothing was loaded"],
            )

        for row in cleaned.itertuples():
            db.upsert_zone(row.zone_id, row.zone_name, float(row.capacity_kw))

        return IngestResponse(
            status="ok" if stats["rows_rejected"] == 0 else "partial",
            rows_received=stats["rows_received"],
            rows_loaded=stats["rows_loaded"],
            rows_rejected=stats["rows_rejected"],
            warnings=stats["warnings"],
        )

    except ingestion.IngestionError as e:
        return IngestResponse(status="error", errors=[str(e)])
    except Exception as e:
        logger.error(f"/ingest/zones failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/households", response_model=IngestResponse)
async def ingest_households(file: UploadFile = File(...)):
    """
    Upload a CSV of household roster data. Columns required:
        household_id, zone_id, name, email, archetype
    Upserted into Postgres. Rows referencing a zone_id that isn't registered
    yet are skipped (reported in warnings) — upload that zone via
    /ingest/zones first.
    """
    try:
        raw_bytes = await file.read()
        df = ingestion.parse_households_csv(raw_bytes)
        cleaned, stats = ingestion.clean_households(df)

        if cleaned.empty:
            return IngestResponse(
                status="error",
                rows_received=stats["rows_received"],
                rows_loaded=0,
                rows_rejected=stats["rows_rejected"],
                warnings=stats["warnings"],
                errors=["No valid rows remained after cleaning — nothing was loaded"],
            )

        skipped_unknown_zone = 0
        for row in cleaned.itertuples():
            ok = db.upsert_household(
                row.household_id,
                row.zone_id,
                row.name or None,
                row.email or None,
                row.archetype,
            )
            if not ok:
                skipped_unknown_zone += 1

        warnings = list(stats["warnings"])
        rows_loaded = stats["rows_loaded"] - skipped_unknown_zone
        if skipped_unknown_zone:
            warnings.append(
                f"Skipped {skipped_unknown_zone} row(s) referencing a zone_id not yet "
                f"registered — upload that zone via POST /ingest/zones first, then re-upload."
            )

        return IngestResponse(
            status="ok"
            if stats["rows_rejected"] == 0 and skipped_unknown_zone == 0
            else "partial",
            rows_received=stats["rows_received"],
            rows_loaded=rows_loaded,
            rows_rejected=stats["rows_rejected"] + skipped_unknown_zone,
            warnings=warnings,
        )

    except ingestion.IngestionError as e:
        return IngestResponse(status="error", errors=[str(e)])
    except Exception as e:
        logger.error(f"/ingest/households failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingest/template/{kind}", response_class=PlainTextResponse)
def ingest_template(kind: str):
    """Download an example CSV for kind in {readings, zones, households}."""
    if kind not in ingestion.TEMPLATES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown template kind '{kind}'. Valid kinds: {', '.join(ingestion.TEMPLATES)}",
        )
    return PlainTextResponse(
        content=ingestion.TEMPLATES[kind],
        headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'},
    )


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


@app.get("/admin/health")
def admin_health():
    """Detailed health diagnostic for deployment verification."""
    diagnostics = {}
    checks = {"status": "ok"}

    # DB check
    try:
        day = db.get_simulation_day()
        zone_count = db.fetch_one("SELECT COUNT(*) AS n FROM zones")
        diagnostics["database"] = {
            "connected": True,
            "simulation_day": day,
            "zones": zone_count["n"] if zone_count else 0,
        }
    except Exception as e:
        diagnostics["database"] = {"connected": False, "error": str(e)}
        checks["status"] = "degraded"

    # Env vars check (without revealing values)
    required_vars = ["DATABASE_URL", "GCP_PROJECT", "GEMINI_API_KEY"]
    optional_vars = ["SMTP_HOST", "SMTP_USER", "SMTP_FROM", "VOLTRIX_OPS_EMAIL"]
    env_status = {}
    for var in required_vars:
        env_status[var] = "set" if os.environ.get(var) else "MISSING"
        if not os.environ.get(var):
            checks["status"] = "degraded"
    for var in optional_vars:
        env_status[var] = "set" if os.environ.get(var) else "not set (optional)"
    diagnostics["environment"] = env_status

    # CORS
    diagnostics["cors"] = {
        "allowed_origins": os.environ.get("ALLOWED_ORIGINS", "not set"),
    }

    return {"checks": checks, "diagnostics": diagnostics, "service": "voltrix-api"}


@app.post("/admin/reset-simulation")
def reset_simulation():
    """Reset the simulation clock back to day 1. Use before demo rehearsal."""
    db.execute("UPDATE simulation_state SET current_day = 1 WHERE id = 1")
    db.execute("DELETE FROM recommendations")
    db.execute("DELETE FROM stress_events")
    db.execute("DELETE FROM forecasts")
    return {"ok": True, "message": "Simulation reset to day 1"}
