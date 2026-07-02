# VOLTRIX Backend

FastAPI backend running on Cloud Run. Owns the full request lifecycle from
zone queries through to the `/simulate/advance` orchestration loop.

Stack: FastAPI + Supabase (Postgres) + BigQuery + Prophet + Google ADK (Gemini 2.0 Flash) + stdlib SMTP.

---

## Quick Start (local)

```bash
# 1. Clone and enter the backend directory
cd voltrix-backend

# 2. Create virtualenv
python -m venv .venv && source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Set environment variables
cp .env.example .env
# → Fill in DATABASE_URL, GCP_PROJECT, GEMINI_API_KEY, SMTP_* vars

# 5. Apply Postgres schema (once)
psql $DATABASE_URL -f schema.sql

# 6. Run the API
uvicorn main:app --reload --port 8000
```

Swagger docs at: http://localhost:8000/docs

---

## First-Time Setup Order

1. `schema.sql` applied to Postgres
2. Run `load_to_bq.py` (from repo root) to load synthetic data into BigQuery
3. Hit `POST /admin/seed-households` once to sync households from BigQuery → Postgres
4. Check `GET /zones` returns 5 zones
5. Hit `POST /simulate/advance` manually once to verify the full loop works

---

## API Contract (for Jeet's frontend)

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/zones` | All zones with capacity metadata |
| GET | `/zones/{bq_zone_id}/load-history?days=3` | Actual + predicted load for chart |
| GET | `/stress-events?limit=20` | Recent stress events with zone name |
| GET | `/stress-events/{id}` | Single stress event |
| GET | `/stress-events/{id}/recommendations` | Recommendations for drill-down view |
| GET | `/recommendations?limit=50&unsent_only=false` | All recommendations |
| POST | `/recommendations/{recommendation_id}/send` | Send recommendation via SMTP email |
| POST | `/chat` | Conversational Q&A with ADK agent |
| GET | `/simulation/state` | Current simulated day |
| **POST** | **`/simulate/advance`** | **Advance day + run full pipeline** |
| POST | `/admin/seed-households` | One-time BQ→Postgres household sync |
| POST | `/admin/reset-simulation` | Reset to day 1 (use before demo rehearsal) |

---

## `/simulate/advance` — what it does step by step

```
1. current_day++ (capped at 30)
2. For each zone (skip if active stress event exists):
   a. Pull load window [0..current_day*24h] from BigQuery     → bq.get_zone_load_window()
   b. Fit Prophet on that window, forecast next 24h            → forecasting.forecast_zone()
   c. Save hourly forecasts to Postgres forecasts table        → db.save_forecasts()
   d. Check if any hour exceeds 85% of zone capacity           → forecasting.detect_stress()
   e. If stress detected:
      - Pull top 8 contributing households from BigQuery       → bq.get_household_load_for_zone()
      - Call ADK agent for reasoning + nudges                  → agent.run_stress_analysis()
      - INSERT stress_events row with ADK reasoning            → db.save_stress_event()
      - INSERT recommendations rows (one per household + utility) → db.save_recommendation()
3. Return per-zone results array
```

---

## Deploy to Cloud Run

```bash
# Set env vars in your shell first
export DATABASE_URL="postgresql://..."
export GEMINI_API_KEY="AIza..."
export ALLOWED_ORIGINS="https://your-frontend.vercel.app"

chmod +x deploy.sh && ./deploy.sh
```

---

## File ownership summary

| File | Owned by | Description |
|---|---|---|
| `main.py` | Mrinmoy | All API endpoints + pipeline orchestration |
| `agent.py` | Mrinmoy | Google ADK agent — stress analysis + Q&A |
| `forecasting.py` | Debjyoti / Mrinmoy | Prophet forecasting + stress detection |
| `email_service.py` | Mrinmoy | SMTP email delivery (stdlib smtplib) |
| `db.py` | Mrinmoy | Postgres connection pool + domain helpers |
| `bq.py` | Mrinmoy | BigQuery read-only wrapper |
| `models.py` | Mrinmoy | Pydantic request/response schemas |
| `schema.sql` | Mrinmoy | Postgres schema |
| `Dockerfile` | Mrinmoy | Cloud Run container |
| `deploy.sh` | Mrinmoy | One-command Cloud Run deploy |

**Rule:** Never change the function signatures in `forecasting.py` without syncing with Debjyoti — `main.py`, `agent.py` depend on those exact signatures.
