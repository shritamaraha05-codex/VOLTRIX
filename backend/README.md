# VOLTRIX Backend — Mrinmoy's README

FastAPI backend running on Cloud Run. Owns the full request lifecycle from
zone queries through to the `/simulate/advance` orchestration loop.

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
# → Fill in DATABASE_URL, GCP_PROJECT, GOOGLE_APPLICATION_CREDENTIALS

# 5. Apply Postgres schema (once)
psql $DATABASE_URL -f schema.sql

# 6. Run the API
uvicorn main:app --reload --port 8000
```

Swagger docs at: http://localhost:8000/docs

---

## First-Time Setup Order

1. `schema.sql` applied to Postgres
2. Shritama loads synthetic data into BigQuery (zones + readings)
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
| POST | `/recommendations/{id}/mark-sent` | Mark a nudge as delivered |
| GET | `/simulation/state` | Current simulated day |
| **POST** | **`/simulate/advance`** | **Advance day + run full pipeline** |
| POST | `/admin/seed-households` | One-time BQ→Postgres household sync |
| POST | `/admin/reset-simulation` | Reset to day 1 (use before demo rehearsal) |

---

## `/simulate/advance` — what it does step by step

```
1. current_day++ (capped at 30)
2. For each zone:
   a. Pull load window [0..current_day*24h] from BigQuery     → bq.get_zone_load_window()
   b. Fit Prophet on that window, forecast next 24h            → forecasting.forecast_zone()
   c. Save hourly forecasts to Postgres forecasts table        → db.save_forecasts()
   d. Check if any hour exceeds 85% of zone capacity           → forecasting.detect_stress()
   e. If stress detected:
      - Pull top 8 contributing households from BigQuery       → bq.get_household_load_for_zone()
      - Call Gemini for reasoning + nudges                     → reasoning.generate_reasoning_and_nudges()
      - INSERT stress_events row with Gemini reasoning         → db.save_stress_event()
      - INSERT recommendations rows (one per household + utility) → db.save_recommendation()
3. Return per-zone results array
```

---

## Deploy to Cloud Run

```bash
# Set env vars in your shell first
export DATABASE_URL="postgresql://..."
export RESEND_API_KEY="re_..."
export ALLOWED_ORIGINS="https://your-frontend.vercel.app"

chmod +x deploy.sh && ./deploy.sh
```

---

## File ownership summary

| File | You own | You call (teammate fills) |
|---|---|---|
| `main.py` | ✅ Full ownership | `forecasting.py`, `reasoning.py` |
| `db.py` | ✅ Full ownership | — |
| `bq.py` | ✅ Full ownership | — |
| `models.py` | ✅ Full ownership | — |
| `schema.sql` | ✅ Full ownership | — |
| `Dockerfile` | ✅ Full ownership | — |
| `deploy.sh` | ✅ Full ownership | — |
| `forecasting.py` | Interface/stubs | Debjyoti fills logic |
| `reasoning.py` | Interface/stubs | Debjyoti fills logic |

**Rule:** Never change the function signatures in `forecasting.py` or `reasoning.py` without syncing with Debjyoti — `main.py` depends on those exact signatures.
