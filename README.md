<div align="center">

# ⚡ Voltrix

### AI-Powered Energy Load Forecasting & Citizen Nudge Platform



[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react)](https://react.dev)
[![BigQuery](https://img.shields.io/badge/BigQuery-Free_Tier-4285F4?style=flat-square&logo=google-cloud)](https://cloud.google.com/bigquery)
[![Gemini](https://img.shields.io/badge/Gemini-AI_Studio-8E75B2?style=flat-square&logo=google)](https://aistudio.google.com)
[![TimeFM](https://img.shields.io/badge/TimeFM-Google-EA4335?style=flat-square&logo=google)](https://github.com/google-research/timesfm)
[![Supabase](https://img.shields.io/badge/Supabase-Postgres-3ECF8E?style=flat-square&logo=supabase)](https://supabase.com)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## The Problem

Modern city grids generate enormous amounts of energy usage data — but utilities and citizens only find out about grid stress **after it happens**. By the time a brownout occurs, it's too late to act.

GridSense flips that. It watches household and zone-level energy consumption, forecasts demand up to 24 hours ahead using Google's **TimeFM** foundation model, detects predicted stress events before they occur, and automatically generates two things:

- A plain-language explanation of *why* the grid will be stressed (powered by Gemini)
- Personalized nudges to citizens + actionable briefs for utility operators — delivered automatically

**No manual analysis. No after-the-fact alerts. Full decision loop, closed.**

---

## Demo

> 🎬 *Coming soon — Demo Day recording*

**What you'll see in the demo:**

1. Dashboard loads with all zones in a normal state
2. "Advance Simulation" button fast-forwards to a heatwave event (Day 25)
3. Zone 3 flips to critical — forecast line crosses the capacity threshold in real time
4. Gemini reasoning auto-populates: *"Zone 3 is expected to exceed safe load between 6 PM–8 PM due to high residential demand from WFH households combined with elevated ambient temperature..."*
5. Household nudges and a utility action brief appear instantly
6. One real email lands in the demo inbox via Resend

---

## How It Works

```
Synthetic Smart Meter Data
        +
Weather / Occupancy / Tariff Data
               │
               ▼
           BigQuery
        (time-series warehouse)
               │
               ▼
  Google Colab + TimeFM Foundation Model
        (24-hour load forecasting)
               │
               ▼
       Stress Detection Service
     (threshold + anomaly check)
               │
               ▼
    Gemini 2.0 Flash (AI Studio)
  (reasoning + nudge generation)
               │
        ┌──────┴──────┐
        ▼             ▼
  Citizen Nudges  Utility Alerts
  (Resend email)  (Dashboard feed)
        │             │
        └──────┬──────┘
               ▼
      React Dashboard (Vercel)
      Cloud Run API (FastAPI)
```

Every stress event stores a full **reasoning chain** — forecast numbers → threshold breach → Gemini explanation → recommendation — so nothing is a black box.

---

## Tech Stack

### Core (100% Free — no trial credits)

| Layer | Technology | Why |
|---|---|---|
| **Forecasting** | [TimeFM](https://github.com/google-research/timesfm) on Google Colab | Google's own foundation model for time-series, free GPU |
| **LLM / Reasoning** | Gemini 2.0 Flash via [Google AI Studio](https://aistudio.google.com) | Free tier (1500 req/day), same model as Vertex AI |
| **Data Warehouse** | BigQuery Free Tier | 10GB storage, 1TB queries/month — more than enough |
| **Operational DB** | Supabase (Postgres) | Free tier, managed, instant setup |
| **Backend** | FastAPI on Cloud Run | Free tier (2M req/month), scales to zero |
| **Frontend** | React on Vercel | Free tier, instant deploys |
| **Email Delivery** | Resend | Free tier (100 emails/day) |

### Production Path (mentioned in pitch, not required live)

| Swap | From | To |
|---|---|---|
| Forecasting | TimeFM on Colab | Vertex AI Prediction Endpoint |
| LLM | AI Studio | Vertex AI Gemini |
| Operational DB | Supabase | AlloyDB |
| Analytics | Looker Studio free | Looker Enterprise |

The architecture is **modular by design** — each layer is independently replaceable without touching the rest.

---

## Repository Structure

```
voltrix/
├── backend/                  # FastAPI — Cloud Run
│   ├── main.py               # All API endpoints + /simulate/advance orchestration
│   ├── db.py                 # Postgres connection pool + domain helpers
│   ├── bq.py                 # BigQuery read wrapper (load history, household data)
│   ├── forecasting.py        # Prophet fallback + stress detection interface
│   ├── reasoning.py          # Gemini AI Studio call + nudge generation
│   ├── models.py             # Pydantic request/response schemas
│   ├── schema.sql            # Postgres schema (apply once)
│   ├── Dockerfile
│   ├── deploy.sh             # One-command Cloud Run deploy
│   └── requirements.txt
│
├── forecasting/              # TimeFM pipeline — runs in Google Colab
│   ├── data_prep.ipynb       # BigQuery → forecast-ready dataset
│   └── timefm_forecast.ipynb # TimeFM inference → results back to BigQuery
│
├── data/                     # Synthetic data generator
│   └── generate.py           # 5 zones × 15 households × 30 days + stress events
│
├── frontend/                 # React — Vercel
│   ├── src/
│   │   ├── components/
│   │   │   ├── ZoneCard.jsx          # Live load chart per zone
│   │   │   ├── StressEventFeed.jsx   # Detected events + Gemini reasoning
│   │   │   ├── RecommendationFeed.jsx
│   │   │   ├── ExplainabilityPanel.jsx  # Drill-down: why this recommendation?
│   │   │   └── SimulationControl.jsx    # The demo button
│   │   └── App.jsx
│   └── package.json
│
└── README.md
```

---

## API Reference

Base URL: `https://your-cloud-run-url.run.app`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/zones` | All zones with capacity metadata |
| `GET` | `/zones/{zone_id}/load-history` | Actual + predicted load for chart |
| `GET` | `/stress-events` | Recent stress events with Gemini reasoning |
| `GET` | `/stress-events/{id}` | Single event detail |
| `GET` | `/stress-events/{id}/recommendations` | Nudges + utility brief for one event |
| `GET` | `/recommendations` | All recommendations with sent status |
| `POST` | `/recommendations/{id}/mark-sent` | Mark nudge as delivered |
| `GET` | `/simulation/state` | Current simulated day |
| **`POST`** | **`/simulate/advance`** | **Advance simulation + run full AI pipeline** |
| `POST` | `/admin/seed-households` | One-time BQ → Postgres household sync |
| `POST` | `/admin/reset-simulation` | Reset to Day 1 (use before demo) |

### `/simulate/advance` — what it does

```
1. Increment simulation day (1 → 30)
2. For each zone:
   a. Pull load window from BigQuery up to current day
   b. Run forecasting for next 24 hours
   c. Detect stress (predicted load > 85% of capacity)
   d. If stress → call Gemini for reasoning + household nudges
   e. Persist stress event + recommendations to Postgres
3. Return per-zone results → dashboard re-renders live
```

Full Swagger docs at `/docs` once the API is running.

---

## Local Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- A [Supabase](https://supabase.com) project (free)
- A [Google Cloud](https://console.cloud.google.com) project with BigQuery API enabled (free tier)
- A [Google AI Studio](https://aistudio.google.com/app/apikey) API key (free)
- A [Resend](https://resend.com) account (free)

### 1. Clone

```bash
git clone https://github.com/YOUR_ORG/voltrix.git
cd voltrix
```

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# → Fill in DATABASE_URL, GCP_PROJECT, GEMINI_API_KEY, RESEND_API_KEY
```

### 3. Apply Postgres schema

```bash
psql $DATABASE_URL -f schema.sql
```

### 4. Generate + load synthetic data

```bash
cd ../data
python generate.py          # creates readings.csv + zones.csv

# Load into BigQuery
python load_to_bq.py        # uses GOOGLE_APPLICATION_CREDENTIALS from .env
```

### 5. Seed households into Postgres

```bash
# With the API running:
curl -X POST http://localhost:8000/admin/seed-households
```

### 6. Run forecasting (Google Colab)

Open `forecasting/timefm_forecast.ipynb` in [Google Colab](https://colab.research.google.com), set your BigQuery project ID in the first cell, and run all cells. Forecast results write back to BigQuery automatically.

### 7. Start the API

```bash
cd backend
uvicorn main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
```

### 8. Start the frontend

```bash
cd frontend
npm install
npm run dev
# App: http://localhost:5173
```

### 9. Test the loop

```bash
curl -X POST http://localhost:8000/simulate/advance
```

Watch the dashboard update — if Zone 3 shows a stress event with Gemini reasoning, everything is wired correctly.

---

## Environment Variables

| Variable | Where to get it | Required |
|---|---|---|
| `DATABASE_URL` | Supabase → Settings → Database | ✅ |
| `GCP_PROJECT` | Google Cloud Console | ✅ |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP → IAM → Service Accounts → JSON key | ✅ (local dev) |
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | ✅ |
| `RESEND_API_KEY` | [resend.com](https://resend.com) | ✅ |
| `ALLOWED_ORIGINS` | Your frontend URL(s), comma-separated | ✅ |

---

## Deploy to Cloud Run

```bash
cd backend

# Set env vars in shell
export DATABASE_URL="postgresql://..."
export RESEND_API_KEY="re_..."
export ALLOWED_ORIGINS="https://your-app.vercel.app"

chmod +x deploy.sh && ./deploy.sh
```

The script builds the Docker image via Cloud Build, pushes it to Container Registry, and deploys to Cloud Run — all in one command. Service URL is printed at the end.

---

## The AI Decision Loop (in depth)

### Forecasting — TimeFM

[TimeFM](https://github.com/google-research/timesfm) is Google Research's open-source foundation model for time-series forecasting. Unlike ARIMA or Prophet, it is a pre-trained decoder-only model (similar in concept to an LLM, but for time-series) that can forecast with minimal fine-tuning on new data.

In GridSense, TimeFM is run in **Google Colab on a free GPU**, ingesting zone-level hourly load data from BigQuery and producing 24-hour ahead predictions. Results are written back to BigQuery and consumed by the stress detection service.

For the live demo API, a lightweight **Prophet fallback** runs in-process so the simulation button is always responsive even if the Colab notebook hasn't been run yet.

### Reasoning — Gemini 2.0 Flash

When stress is detected, the backend calls Gemini with structured context:

- Zone name and predicted peak vs capacity
- Stress window timestamps
- Top contributing households with their archetypes and current load

Gemini returns three things in a single call:
- `reasoning` — why this is happening in plain English
- `utility_action` — what the grid operator should do
- `household_nudges` — one personalized, specific action per household

Every output is stored with the full input context, making the system fully auditable. Judges (or users) can inspect exactly what data produced each recommendation.

### Explainability

Each recommendation in the dashboard links to an **Explainability Panel** showing:

```
Forecast: 48.3 kW predicted at 7pm
Capacity: 36.0 kW (threshold: 30.6 kW)
Breach: 12.3 kW over capacity (34% excess)
Gemini reasoning: "..."
Generated nudge: "..."
```

This is what separates GridSense from a dashboard with charts — every decision has a traceable, inspectable chain.

---

## Team

| Person | Role |
|---|---|
| **Mrinmoy Chakraborty** | Backend lead — FastAPI, BigQuery, Cloud Run, pipeline orchestration |
| **Debjyoti** | AI/ML lead — TimeFM forecasting, Gemini prompts, stress detection |
| **Jeet** | Frontend — React dashboard, Recharts visualizations, simulation UI |
| **Shritama** | Data & delivery — synthetic data generation, Resend integration, demo script |

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built with ⚡ 
</div>