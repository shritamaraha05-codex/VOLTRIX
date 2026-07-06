<div align="center">

# ⚡ VOLTRIX

### AI-Powered Smart Grid — Load Forecasting, Stress Detection & Citizen Nudging

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![BigQuery](https://img.shields.io/badge/BigQuery-Free_Tier-4285F4?style=flat-square&logo=google-cloud)](https://cloud.google.com/bigquery)
[![Gemma](https://img.shields.io/badge/Gemma-4_AI_Studio-8E75B2?style=flat-square&logo=google)](https://aistudio.google.com)
[![Supabase](https://img.shields.io/badge/Supabase-Free-3ECF8E?style=flat-square&logo=supabase)](https://supabase.com)
[![Chart.js](https://img.shields.io/badge/Chart.js-4.4-FF6384?style=flat-square&logo=chart.js)](https://www.chartjs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## The Problem

City grids generate enormous volumes of energy data — but utilities and citizens only discover grid stress **after it happens**. By the time a brownout occurs, it's too late to act.

VOLTRIX flips that. It watches zone-level energy consumption, forecasts demand 24 hours ahead using **seasonal-trend anomaly detection** (zero compiled dependencies — no Prophet, no CmdStan), detects predicted stress events before they occur, and automatically generates:

- A plain-language explanation of *why* the grid will be stressed (powered by a Google ADK agent using Gemma 4 31B)
- Personalized nudges to citizens + actionable briefs for utility operators — delivered automatically via SMTP email

**No manual analysis. No after-the-fact alerts. Full decision loop, closed.**

---

## How It Works

```
Synthetic Smart Meter Data
           │
           ▼
       BigQuery Free Tier
    (load_readings: 43,200 hourly rows)
           │
           ▼
  Seasonal-Trend Forecasting (v3)
  ┌─────────────────────────────────┐
  │ 1. Build hourly profile (mean   │
  │    + std per hour-of-day ×      │
  │    weekday/weekend) from full   │
  │    history                      │
  │ 2. Compute recent anomaly       │
  │    multiplier (how much the     │
  │    last 24h differs from        │
  │    seasonal expectation)        │
  │ 3. Forecast: profile[hour] ×    │
  │    anomaly, decaying toward 1.0 │
  └─────────────────────────────────┘
           │
           ▼
     Stress Detection
   (≥85% of capacity threshold)
           │
           ▼
  Google ADK Agent (Gemma 4 31B)
  ┌─────────────────────────────────┐
  │ 1. get_zone_details — severity  │
  │ 2. analyse_stress_cause — why   │
  │ 3. generate_household_nudges    │
  │    — per-home actions           │
  └─────────────────────────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
Citizen      Utility
Nudges       Alerts
(SMTP)       (SMTP)
     │           │
     └─────┬─────┘
           ▼
   HTML Dashboard (Vercel)
   FastAPI Backend (Cloud Run)
```

Every stress event stores a full **reasoning chain** — forecast numbers → threshold breach → ADK agent explanation → recommendations — so nothing is a black box.

---

## Tech Stack (100% Free)

| Layer | Technology | Cost |
|---|---|---|
| **Forecasting** | Seasonal-trend v3 (custom) — no Prophet, zero compiled deps | Free |
| **LLM / Agent** | Google ADK + Gemma 4 31B via [AI Studio](https://aistudio.google.com) | Free (15 req/min) |
| **Data Warehouse** | [BigQuery Free Tier](https://cloud.google.com/bigquery/pricing) — 10 GB storage, 1 TB queries/month | Free |
| **Operational DB** | [Supabase](https://supabase.com) Postgres — 500 MB, 5 GB bandwidth | Free |
| **Backend** | FastAPI on [Cloud Run](https://cloud.google.com/run) — 2M requests/month | Free |
| **Frontend** | Static HTML + Chart.js on [Vercel](https://vercel.com) — 100 GB bandwidth | Free |
| **Email** | stdlib `smtplib` (Gmail App Password or any SMTP) | Free |
| **Synthetic Data** | Custom generator — 5 zones × 12 households × 30 days | Free |

### Design Principle

Every layer is independently replaceable — swap in TimeFM forecasting, Vertex AI agent, SendGrid email, or Looker analytics without touching the rest.

---

## Repository Structure

```
voltrix/
├── backend/                       # FastAPI — Cloud Run
│   ├── main.py                    # All endpoints + /simulate/advance pipeline
│   ├── agent.py                   # ADK agent (3 tools) + SSE streaming chat
│   ├── forecasting.py             # Seasonal-trend v3 — stress detection + backtest
│   ├── bq.py                      # BigQuery client (read/write)
│   ├── db.py                      # Postgres pool + domain helpers
│   ├── ingestion.py               # CSV upload validation + cleaning
│   ├── email_service.py           # SMTP delivery (stdlib only)
│   ├── models.py                  # Pydantic request/response schemas
│   ├── schema.sql                 # Postgres DDL (run once)
│   ├── Dockerfile                 # Cloud Run container
│   ├── deploy.sh                  # One-command Cloud Run deploy
│   ├── run.sh                     # Local dev launcher
│   ├── requirements.txt           # Python dependencies
│   ├── .env.example               # Template for env vars
│   └── .env.production            # Production env var template
│
├── frontend/                      # Static SPA — Vercel
│   ├── index.html                 # Complete dashboard (7 pages, SSE chat, dark mode)
│   ├── config.js                  # Backend URL (edit before deploy)
│   ├── vercel.json                # Vercel static config
│   └── deploy.sh                  # Backend deploy script reference
│
├── generate.py                    # Synthetic data generator (5 archetypes, stress events)
├── load_to_bq.py                  # BigQuery CSV loader
├── api_test.html                  # Standalone API test page
├── readings.csv                   # 43,200 hourly readings (generated)
├── zones.csv                      # 5 zone metadata rows
├── households.csv                 # 60 household roster rows
└── README.md
```

---

## API Reference

Base URL: `http://localhost:8000` (local) or `https://your-service.run.app` (prod)

### Core

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — returns `{"status":"ok","day":N}` |
| `GET` | `/zones` | All zones with capacity metadata |
| `GET` | `/zones/{bq_zone_id}/load-history?days=3` | Actual + predicted load for chart (ForecastPoint[]) |
| `GET` | `/zones/{bq_zone_id}/backtest?test_days=3` | Rolling-origin backtest — MAPE / RMSE per day |

### Stress Events

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/stress-events?limit=20` | Recent stress events with zone name + reasoning |
| `GET` | `/stress-events/{id}` | Single event detail |
| `GET` | `/stress-events/{id}/recommendations` | Nudges + utility brief for one event |

### Recommendations

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/recommendations?limit=50&unsent_only=false` | All recommendations |
| `POST` | `/recommendations/{id}/send` | Send via SMTP email — never raises |

### Simulation

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/simulation/state` | Current simulated day |
| **`POST`** | **`/simulate/advance`** | **+1 day — runs full AI pipeline** |

### Chat

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat` | Conversational Q&A — **SSE streaming** (`data: {"token":"..."}\n\n`) |

### CSV Ingestion

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ingest/readings` | Upload readings CSV → BigQuery |
| `POST` | `/ingest/zones` | Upload zones CSV → Postgres upsert |
| `POST` | `/ingest/households` | Upload households CSV → Postgres upsert |
| `GET` | `/ingest/template/{kind}` | Download example CSV (`readings`, `zones`, or `households`) |

### Admin

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/admin/health` | Detailed diagnostics (DB, env vars, CORS config) |
| `POST` | `/admin/seed-households` | One-time: sync BigQuery households → Postgres |
| `POST` | `/admin/reset-simulation` | Reset to Day 1 + clear all events/forecasts |

### `/simulate/advance` — the money endpoint

```
1. Increment current_day in simulation_state
2. For each zone:
   a. Skip if zone already has an active (future) stress event
   b. Pull load window from BigQuery up to current_day
   c. Seasonal-trend forecast for next 24 hours
   d. Detect stress (predicted load > 85% of capacity)
   e. If stress → call ADK agent (3 tools) for reasoning + nudges
   f. Persist stress event + recommendations to Postgres
   g. If no forecast stress → fallback: check today's actual readings
3. Return per-zone results → dashboard re-renders live
```

Full Swagger docs at `/docs` once the API is running.

---

## Forecasting — Seasonal-Trend v3 (no Prophet)

Forecasting runs **in-process** inside FastAPI — zero compiled dependencies, guaranteed to build on any Python 3.11+ environment.

### Why v3 dropped Prophet

Prophet needs a compiled CmdStan backend. In practice, `pip install prophet` frequently fails in CI/container builds (network blocks, missing compilers). The old v2 code was silently falling back to a "same hour yesterday" average — which works on quiet days but **misses active demand spikes entirely** (a heatwave, a storm).

### How v3 works

1. **Build a seasonal profile** from the entire history — mean + std load per (hour-of-day × weekday/weekend). This is a de-noised version of what the old fallback approximated from 1-2 sample points.

2. **Compute a recent anomaly multiplier** — how much the last 24 hours actually ran vs. the seasonal expectation. This detects "we're in a heatwave right now" — the old fallback had no equivalent.

3. **Forecast each future hour** as `seasonal_profile[hour] × anomaly_multiplier`, **decaying** the multiplier back toward 1.0 across the horizon (a spike happening now is a stronger signal for the next few hours than 24h out).

4. **Uncertainty bands** from each hour's historical spread, widened by recent anomaly volatility.

Backtested against the synthetic dataset: **~6.1% MAPE** across zones, correctly flags the injected Zone 2 heatwave.

---

## The AI Decision Loop

### Reasoning — Google ADK Agent (Gemma 4 31B)

When stress is detected, the backend calls a **Google ADK agent** with three tools:

1. **`get_zone_details`** — fetches capacity, peak prediction, overage summary
2. **`analyse_stress_cause`** — determines root causes from timing + archetype mix
3. **`generate_household_nudges`** — personalised actions per archetype

The agent returns structured JSON:
- `reasoning` — plain-language explanation
- `utility_action` — what the operator should do
- `household_nudges` — one action per household

A **deterministic fallback** (same format) activates if the agent fails to produce valid JSON — the demo never shows zero nudges for a real stress event.

### Chat (SSE Streaming)

The `/chat` endpoint streams tokens from Gemma 4 31B directly via `generate_content_stream()`, bridged into FastAPI's async event loop via `asyncio.Queue` + background thread. The frontend consumes SSE with `ReadableStream.getReader()`.

### Explainability

Each recommendation is traceable:
```
Forecast: 48.3 kW predicted at 7pm
Capacity: 36.0 kW (threshold: 30.6 kW = 85%)
Breach: 17.7 kW over threshold (58% excess)
ADK agent reasoning: "Evening residential peak + WFH households..."
Generated nudge: "Delay dishwasher to after 10pm"
```

---

## Local Setup

### Prerequisites

- Python 3.11+
- [Supabase](https://supabase.com) project (free)
- [Google Cloud](https://console.cloud.google.com) project with BigQuery API enabled
- [Google AI Studio](https://aistudio.google.com/app/apikey) API key (free)
- Gmail App Password (or any SMTP credentials)

### 1. Clone

```bash
git clone https://github.com/YOUR_ORG/voltrix.git
cd voltrix
```

### 2. Configure environment

```bash
cd backend
cp .env.example .env
# Fill in: DATABASE_URL, GCP_PROJECT, GEMINI_API_KEY, SMTP_*
```

### 3. Python setup

```bash
python -m venv .venv
.venv\Scripts\activate     # Windows
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 4. Apply Postgres schema

```bash
# Connect to your Supabase project via psql or the SQL Editor UI
# Run schema.sql contents — creates all tables + seeds zones
psql $DATABASE_URL -f backend/schema.sql
```

### 5. Generate synthetic data

```bash
# Creates readings.csv, zones.csv, households.csv
python generate.py
```

### 6. Load data into BigQuery

```bash
python load_to_bq.py
```

### 7. Start the backend

```bash
cd backend
uvicorn main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
```

### 8. Seed households

```bash
curl -X POST http://localhost:8000/admin/seed-households
curl -X POST http://localhost:8000/admin/reset-simulation
```

### 9. Serve the frontend

```bash
cd frontend
# Edit config.js: set window.__BACKEND_URL = "http://localhost:8000"
python -m http.server 8080
# Open http://localhost:8080
```

### 10. Test the loop

```bash
curl -X POST http://localhost:8000/simulate/advance
```

Watch the dashboard update — if a zone shows a stress event with ADK reasoning, everything is wired correctly.

---

## Environment Variables

| Variable | Source | Required |
|---|---|---|
| `DATABASE_URL` | Supabase → Settings → Database | ✅ |
| `GCP_PROJECT` | Google Cloud Console | ✅ |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP → IAM → Service Account JSON key | Local dev only |
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | ✅ |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Gmail App Password (or any SMTP) | ✅ |
| `SMTP_FROM` | Sender name + email | ✅ |
| `VOLTRIX_OPS_EMAIL` | Recipient for utility alerts | ✅ |
| `ALLOWED_ORIGINS` | Comma-separated frontend URLs | ✅ |

---

## Deploy

### Backend → Cloud Run

```bash
cd backend
export DATABASE_URL="postgresql://..."
export GEMINI_API_KEY="AIza..."
export ALLOWED_ORIGINS="https://voltrix.vercel.app"
chmod +x deploy.sh && ./deploy.sh
```

Requires: `gcloud auth login`, `gcloud config set project YOUR_PROJECT_ID`.

### Frontend → Vercel

```bash
cd frontend
# 1. Edit config.js — set window.__BACKEND_URL to your Cloud Run URL
# 2. Deploy:
npx vercel --prod
```

Or connect the GitHub repo to Vercel (root directory: `frontend`).

---

## Frontend Dashboard

Single HTML file (`frontend/index.html`), no build step. Features:

- **7 pages**: Dashboard, Zones, Events, Recommendations, Simulation, Chat, Admin
- **Dark mode** — toggle persisted in localStorage
- **SSE streaming chat** — real-time token-by-token responses from Gemma 4 31B
- **Interactive charts** — Chart.js with gradient fills, tooltip drill-down
- **Zone detail** — load-history + backtest results + chart type toggle (line/bar/area)
- **Simulation progress bar** — visual day counter with animated fill
- **CSV file ingestion** — upload households, readings, or zones directly
- **Responsive** — mobile sidebar, resized KPIs, full-width content on small screens
- **SHADCN-inspired design** — Inter font, slate palette, glassmorphism topbar, gradient cards

---

## Synthetic Data

`generate.py` produces 30 days of smart meter data for 5 zones × 12 households × 5 archetypes:

| Archetype | Pattern |
|---|---|
| `family` | Morning 2.2 kW, evening 5.5 kW, high weekend baseline |
| `single_professional` | Low daytime, evening 2.4 kW, higher weekend |
| `wfh` | High daytime baseline 1.6 kW, evening 3.2 kW |
| `retired` | Steady 1.1 kW daytime, moderate evening |
| `small_business` | High morning 3.5 kW, low evening |

**Stress events** use gradual sine-envelope ramps (not instant steps) so the seasonal-trend model can extrapolate from preceding hours:

- **Zone 2 (East Heights)** — Days 24-27, multi-day heatwave
- **Zone 0 (Riverside)** — Day 18, 6pm-9pm moderate surge
- **Zone 3 (Northside)** — Days 28-29, storm surge

---

## Dashboard Pages

| Page | What it shows |
|---|---|
| **Dashboard** | KPI cards (zones, events, day, households) + zone grid + cumulative stress status |
| **Zones** | Per-zone detail: load/temp history, forecast chart (line/bar/area), backtest MAPE |
| **Events** | All stress events with severity filter + per-event recommendations |
| **Recommendations** | All recommendations with send status + one-click SMTP delivery |
| **Simulation** | Progress bar, advance button, per-zone results table |
| **Chat** | SSE streaming chat with optional household context filter |
| **Admin** | Seed/reset actions, CSV file uploads, health diagnostics |

---

## Team

| Person | Role |
|---|---|
| **Mrinmoy Chakraborty** | Backend — FastAPI, BigQuery, Cloud Run, pipeline orchestration, ingestion |
| **Debjyoti** | AI/ML — Forecasting v3, seasonal-trend model, backtesting, stress detection |
| **Jeet** | Frontend — HTML/CSS/JS dashboard, Chart.js visualizations, Vercel deployment |
| **Shritama** | Data — synthetic data generation, BigQuery schema + loading, demo script |

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">

Built with ⚡ by Team VOLTRIX

</div>
