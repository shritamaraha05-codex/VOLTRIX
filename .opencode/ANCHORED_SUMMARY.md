## Goal
VOLTRIX: AI-powered smart grid dashboard. Ingests synthetic smart-meter data, runs seasonal-trend load forecasting, detects grid stress events, generates AI-powered household nudges and utility alerts via Google Gemma 4 31B, and delivers them through a responsive single-page dashboard. Backend: FastAPI + Supabase PostgreSQL + Google BigQuery + Google ADK. Frontend: Vanilla HTML/CSS/JS SPA with Vite dev server. Deployed via Docker to Google Cloud Run.

## Constraints & Preferences
- 100% free tier: BigQuery free tier, AI Studio (not Vertex AI), stdlib SMTP, Supabase free tier
- Never use `vertexai` or `google-cloud-aiplatform` — paid billing
- Never name a file `email.py` (shadows stdlib) — `email_service.py` used instead
- `/simulate/advance` must never crash on single-zone failure — `try/except` per zone, log and continue
- No auth for MVP, no CI/CD — manual deploy via `deploy.sh`
- No Prophet/CmdStan — zero compiled deps for reliable Docker builds
- Python 3.11-slim Docker base (matches psycopg2-binary, pandas, numpy compatibility)

---

## Architecture Overview

```
Frontend (Vite :5173) ──proxy──▶ Backend (FastAPI :8000) ──▶ Supabase PostgreSQL
                                      │                         zones, households,
                                      │                         forecasts, stress_events,
                                      │                         recommendations, simulation_state
                                      │
                                      ├──▶ Google BigQuery         voltrix.load_readings (43,200 rows)
                                      │    voltrix.zone_capacity   voltrix.households
                                      │
                                      ├──▶ Google Gemma 4 31B      ADK agent (stress analysis)
                                      │    via google-genai SDK    Direct streaming (chat SSE)
                                      │
                                      └──▶ SMTP (Gmail)            Nudge emails (household)
                                                                   Utility alerts (ops team)
```

---

## Layer 1: Synthetic Data Generation

### `generate.py` — Shritama's Data Generator

**Output:** `readings.csv` (43,200 rows), `zones.csv` (5 rows), `households.csv` (60 rows)

**Zone model:** 5 zones, each with 12 households, 30 days × 24 hours = 43,200 total readings. Timestamps: `2026-06-01 00:00 UTC` through `2026-06-30 23:00 UTC`.

**Zone capacities:** Calibrated so normal evening peaks (~28-30 kW) stay below 85% threshold (30.6 kW for 36 kW zones) but stress events push decisively above.

| Zone | Capacity | Stress Event |
|------|----------|-------------|
| zone_0 (Riverside) | 36.0 kW | Day 18, 6pm-9pm, 1.7× multiplier |
| zone_1 (Central) | 44.0 kW | None |
| zone_2 (East Heights) | 36.0 kW | Days 24-27, 4pm-10pm, 2.3× multiplier (multi-day heatwave) |
| zone_3 (Northside) | 40.0 kW | Days 28-29, 3pm-11pm, 2.6× multiplier (storm surge) |
| zone_4 (Greenfield) | 34.0 kW | None |

**5 Household Archetypes** — each with distinct diurnal load curves:

| Archetype | Morning (6-9) | Evening (18-22) | Base | Weekend Mult |
|-----------|--------------|-----------------|------|-------------|
| family | 2.2 kW | 5.5 kW | 0.9 kW | 1.3× |
| single_professional | 0.7 kW | 2.4 kW | 0.35 kW | 1.1× |
| wfh | 1.4 kW | 3.2 kW | 1.6 kW | 0.9× |
| retired | 1.8 kW | 2.0 kW | 1.1 kW | 1.0× |
| small_business | 3.5 kW | 1.2 kW | 0.6 kW | 0.4× |

**Stress event envelope:** Multi-day events use `sin(π × (day_in_event + 0.5) / duration)` for smooth ramp-up/peak/ramp-down. Hourly taper: `1.0 - 0.3 × |hour - window_mid| / (window_span/2)`. This gives the forecasting model a gradient to extrapolate from.

**Load computation per (household, hour):**
```
load = archetype_base(hour) × stress_multiplier(zone, hour) × (1 + zone_weather_noise) + household_jitter
```
- `zone_weather_noise`: `N(0, 0.12)` — correlated across all households in a zone (grid-level weather)
- `household_jitter`: `N(0, 0.08)` — independent per household
- Temperature: sinusoidal daily curve `22 + 12 × max(0, cos(2π(hour-6)/24))` + heatwave boost (+6°C) + `N(0, 0.5)` noise

**Injected stress events (3 total):**
1. Zone 2, days 24-27: Multi-day heatwave, 2.3× peak multiplier, 4pm-10pm window
2. Zone 0, day 18: Single-day moderate surge, 1.7× multiplier, 6pm-9pm
3. Zone 3, days 28-29: Two-day storm surge, 2.6× multiplier, 3pm-11pm

### `load_to_bq.py` — BigQuery Loader

Reads `backend/.env` for `GCP_PROJECT` and `GOOGLE_APPLICATION_CREDENTIALS`. Creates dataset `voltrix` (US region). Three tables:
- `voltrix.load_readings` — 43,200 rows, schema: household_id, zone_id, archetype, timestamp, load_kw, temperature_c. Clustered by [zone_id, timestamp]. Drop+recreate to ensure temperature_c column exists.
- `voltrix.zone_capacity` — 5 rows, schema: zone_id, zone_name, baseline_capacity_kw, household_count
- `voltrix.households` — 60 rows, clustered by [zone_id]

---

## Layer 2: Database Schema (`schema.sql`)

PostgreSQL on Supabase. Extension: `pgcrypto` (for `gen_random_uuid()`).

### Tables

**`zones`** — Grid zone metadata. `bq_zone_id TEXT NOT NULL UNIQUE` is the join key to BigQuery.
```sql
id UUID PK, name TEXT, bq_zone_id TEXT UNIQUE, household_count INT, baseline_capacity_kw NUMERIC
```
Seed data: 5 zones (Ward 1-5), capacities 34-44 kW, 12 households each.

**`households`** — Individual households. FK → zones. `bq_household_id` matches BigQuery's household_id.
```sql
id UUID PK, zone_id UUID FK→zones, bq_household_id TEXT UNIQUE, name TEXT, email TEXT, archetype TEXT
```

**`forecasts`** — Hourly predicted load per zone. UNIQUE constraint: `(zone_id, forecast_for)`.
```sql
id UUID PK, zone_id UUID FK→zones, forecast_for TIMESTAMPTZ, predicted_load_kw NUMERIC, actual_load_kw NUMERIC, created_at TIMESTAMPTZ DEFAULT now()
```
**Critical fix:** `db.save_forecasts()` runs `DELETE FROM forecasts WHERE zone_id = %s` before INSERT to prevent stale zero-valued rows from persisting across simulate/advance cycles.

**`stress_events`** — Detected grid stress windows.
```sql
id UUID PK, zone_id UUID FK→zones, detected_at TIMESTAMPTZ DEFAULT now(),
window_start TIMESTAMPTZ, window_end TIMESTAMPTZ, severity TEXT ('moderate'|'critical'),
predicted_peak_kw NUMERIC, capacity_kw NUMERIC, reasoning TEXT
```

**`recommendations`** — Household nudges + utility alerts.
```sql
id UUID PK, stress_event_id UUID FK→stress_events, target_type TEXT ('household'|'utility'),
household_id UUID FK→households (NULL for utility), bq_household_id TEXT,
message TEXT, action_suggested TEXT, sent BOOLEAN DEFAULT false, sent_at TIMESTAMPTZ, created_at TIMESTAMPTZ
```

**`simulation_state`** — Singleton clock. `CHECK (id = 1)`. Default `current_day = 1`. Auto-seeded.
```sql
id INT PK DEFAULT 1, current_day INT DEFAULT 1 (range 1..30)
```

**Indexes:**
- `idx_forecasts_zone_ts` ON `forecasts(zone_id, forecast_for DESC)`
- `idx_stress_events_zone` ON `stress_events(zone_id, detected_at DESC)`
- `idx_recommendations_evt` ON `recommendations(stress_event_id)`
- `idx_households_zone` ON `households(zone_id)`

---

## Layer 3: Database Operations (`db.py`)

**Connection pool:** `psycopg2.pool.SimpleConnectionPool` (minconn=1, maxconn=10). Uses `RealDictCursor` so rows are dicts. Each connection sets `SET TIME ZONE 'UTC'`.

**Context manager `get_conn()`:** Borrows connection → yields → commit on success, rollback on exception → return to pool.

**Type serialization:** `_serialise()` converts `datetime`→ISO string, `Decimal`→float. `_to_json_safe()` round-trips through `json.dumps/loads` to normalize all Postgres types for Pydantic.

**Core helpers:**
- `fetch_all(query, params)` → `list[dict]`
- `fetch_one(query, params)` → `dict | None`
- `execute(query, params)` → None
- `insert_returning_id(query, params)` → UUID string
- `execute_many(query, params_list)` → bulk insert

**Domain helpers:**
- `get_simulation_day()` → reads `simulation_state WHERE id = 1`
- `advance_simulation_day()` → `UPDATE simulation_state SET current_day = LEAST(current_day + 1, 30)` — caps at 30
- `save_forecasts(zone_id, rows)` → DELETE old + bulk INSERT. Looks up zone UUID from `bq_zone_id` first.
- `save_stress_event(...)` → INSERT RETURNING id
- `save_recommendation(...)` → Resolves `bq_household_id` → `household_id` FK. INSERT.
- `mark_recommendation_sent(id)` → `UPDATE sent = true, sent_at = now()`
- `upsert_zone(...)` → `INSERT ... ON CONFLICT (bq_zone_id) DO UPDATE`
- `upsert_household(...)` → `INSERT ... ON CONFLICT (bq_household_id) DO UPDATE`. Returns False if zone not found.
- `known_zone_ids()` → `set[str]` of all bq_zone_ids in DB

---

## Layer 4: BigQuery Client (`bq.py`)

**Client:** `google.cloud.bigquery.Client(project=GCP_PROJECT)`. Lazy singleton.

**Key tables:** `{GCP_PROJECT}.voltrix.load_readings`

### Read Functions

**`get_zone_load_window(zone_id, day, total_days=30)`** — Primary function for forecasting. Returns hourly aggregated load from hour 0 through `day × 24`. Uses `TIMESTAMP_DIFF(timestamp, MIN(timestamp) for zone, HOUR) < day*24`. This means day 1 = first 24h of data, day 30 = all 43,200 rows.

**`get_zone_load_for_day(zone_id, day)`** — Returns a single day's 24h window. `BETWEEN (day-1)*24 AND day*24-1` relative to the zone's minimum timestamp. Used by the nowcast fallback.

**`get_zone_hourly_load(zone_id, days_back=30)`** — Uses `CURRENT_TIMESTAMP()` (less reliable for synthetic data). Primarily for chart display.

**`get_zone_load_for_chart(zone_id, days_back=3)`** — Wraps `get_zone_hourly_load`, formats as `{hour: "MM-DD HH:MM", actual: float}`.

**`get_household_load_for_zone(zone_id, day, limit=8)`** — Top N households by average load during a specific day. Returns `{household_id, archetype, avg_load_kw}`. Used to build context for the ADK agent.

### Write Functions

**`load_readings_dataframe(df)`** — Appends cleaned DataFrame to BigQuery. Calls `ensure_load_readings_table()` first (creates if not exists with clustering). Schema: household_id, zone_id, archetype, timestamp, load_kw, temperature_c. Write disposition: `WRITE_APPEND`.

**`seed_households_from_bq(db_module)`** — Reads `SELECT DISTINCT household_id, zone_id, archetype FROM load_readings`. For each, looks up zone UUID in Postgres, skips if household already exists. Inserts with auto-generated name (`zone_0_hh_03` → `Zone 0 Hh 03`).

---

## Layer 5: CSV Ingestion Pipeline (`ingestion.py`)

Three upload types, each with parse → clean → load stages. All cleaning is lenient (drop bad rows, don't reject entire file).

### Readings Ingestion (`POST /ingest/readings`)

**Required columns:** `household_id, zone_id, archetype, timestamp, load_kw`

**Cleaning pipeline (`clean_readings`):**
1. Drop rows with NULL household_id/zone_id/timestamp
2. Parse timestamps with `pd.to_datetime(utc=True, errors="coerce")` → drop unparseable
3. Parse load_kw with `pd.to_numeric(errors="coerce")` → drop non-numeric
4. Clip negative loads to 0
5. Cap outliers at 500 kW (`LOAD_OUTLIER_CAP_KW`)
6. Fill missing archetype with "unknown"
7. Strip whitespace from string columns
8. De-duplicate on `(household_id, timestamp)` — keep last

**Post-clean:** Load to BigQuery via `bq.load_readings_dataframe()`. Then check `db.known_zone_ids()` for any unregistered zones → warn but don't block.

### Zones Ingestion (`POST /ingest/zones`)

**Required columns:** `zone_id, zone_name, capacity_kw`

**Cleaning:** Drop NULL zone_id/zone_name, drop non-positive capacity_kw, de-duplicate on zone_id.

**Load:** Per-row `db.upsert_zone()` — `INSERT ... ON CONFLICT (bq_zone_id) DO UPDATE`.

### Households Ingestion (`POST /ingest/households`)

**Required columns:** `household_id, zone_id, name, email, archetype`

**Cleaning:** Drop NULL household_id/zone_id, de-duplicate on household_id.

**Load:** Per-row `db.upsert_household()`. Returns False if zone not yet registered → counted as skipped, reported in warnings.

### Limits
- Max file size: 25 MB
- Max rows: 500,000
- Error type: `IngestionError` → 400 response (not 500)

---

## Layer 6: Forecasting Engine (`forecasting.py`)

### V3 Seasonal-Trend Model

**Why v3 dropped Prophet:** Prophet requires CmdStan compilation. Fails in fresh sandboxes/CI/containers. The old naive fallback ("same hour yesterday/last week averaged") can't detect active demand spikes. V3 fixes this with zero native/compiled dependencies.

**Algorithm:**

1. **Build seasonal profile** (`_build_seasonal_profile`):
   - Group all historical data by `(hour_of_day, is_weekend)`
   - Compute `mean` and `std` of load_kw per group
   - Fill missing std with `mean × 0.15`
   - Result: 48-row profile (24 hours × 2 day-types)

2. **Compute anomaly multiplier:**
   - Take last `ANOMALY_WINDOW_HOURS = 24` data points
   - For each, look up `expected = profile[hour, weekend]`
   - Compute `ratio = actual / expected`
   - `anomaly_multiplier = median(ratios)` — clipped to [0.4, 3.0]
   - `anomaly_volatility = std(ratios)` — how volatile the anomaly signal is

3. **Forecast each future hour:**
   ```
   decay = 0.5 ^ (i / ANOMALY_DECAY_HALFLIFE)    # halflife = 6 hours
   effective_multiplier = 1.0 + (anomaly_multiplier - 1.0) × decay
   yhat = max(0, profile_mean[hour] × effective_multiplier)
   spread = profile_std × (1 + anomaly_volatility) × (1 + i/periods)
   yhat_lower = max(0, yhat - 1.28 × spread)     # ~80% CI
   yhat_upper = yhat + 1.28 × spread
   ```
   The multiplier decays toward 1.0 over the forecast horizon — a spike happening now is a stronger signal for the next few hours than 24h out.

**Entry point:** `forecast_zone(bq_zone_id, periods=24, current_day=30)` → returns DataFrame with columns `[ds, yhat, yhat_lower, yhat_upper, method]`. Returns empty DataFrame if <24h of history.

### Stress Detection

**Forecast-based (`detect_stress`):**
- Threshold: `capacity_kw × 0.85` (default)
- If any `yhat > threshold` → stress detected
- Severity: `"critical"` if `peak_kw > capacity_kw`, else `"moderate"`
- Returns `{zone_id, window_start, window_end, predicted_peak_kw, capacity_kw, overage_pct, hours_stressed, severity, confidence: "point_forecast"}`

**Nowcast fallback (`detect_actual_stress`):**
- Used when forecast-based detection finds nothing (novel first-time spike)
- Checks actual readings for the current day: `load_kw > threshold`
- Same severity logic
- Returns `{..., confidence: "observed"}`

### Backtesting (`backtest_zone`)

Rolling-origin backtest. For each of the last `test_days` days:
1. Train on data through `train_day`
2. Forecast 24h ahead
3. Compare against actuals for `test_day = train_day + 1`
4. Compute MAPE and RMSE per day

Returns `{zone_id, days_evaluated, overall_mape_pct, overall_rmse_kw, daily[{train_day, test_day, hours_compared, mape_pct, rmse_kw}]}`.

---

## Layer 7: AI Agent (`agent.py`)

### ADK Agent (Stress Analysis)

**Model:** `gemma-4-31b-it` (Google Gemma 4 31B Instruct) via AI Studio
**Framework:** Google ADK 0.3.0 with `Runner` + `InMemorySessionService`

**3 Tools (FunctionTool wrappers):**

1. **`get_zone_details(zone_id, predicted_peak_kw, capacity_kw)`**
   - Computes overage_kw, overage_pct, risk_level
   - Returns structured dict for agent consumption

2. **`analyse_stress_cause(zone_id, window_start, window_end, archetype_breakdown)`**
   - Rule-based root cause determination from hour-of-day and archetype mix
   - Causes: evening residential peak, WFH baseline, family appliances, aggregate spike

3. **`generate_household_nudges(households_json)`**
   - Maps each household's archetype to a personalized nudge template
   - 5 archetype templates with specific message + suggested action
   - Skips non-dict items (model sometimes passes string lists)

**Agent execution flow:**
1. `_build_agent()` creates Agent with temperature=0.3, system instruction ~300 chars
2. `_get_runner()` creates Runner with `auto_create_session=True` (resolves SessionNotFoundError)
3. `_run_agent(prompt)` → `runner.run_async()` → collects final response text from event stream
4. `run_stress_analysis(stress_event, household_summaries)` → serializes to JSON prompt → `_run_agent()` → parse JSON response → fallback if unparseable
5. JSON parsing: strip ` ```json ``` ` fences → `json.loads()` → regex `\{.*\}` fallback → `_fallback_response()` last resort

**Nudge backstop:** If agent returns empty `household_nudges` list, falls back to `_fallback_response()` which generates generic nudges for top 6 households.

### Chat Streaming (Direct genai, no ADK)

**Model:** Same `gemma-4-31b-it`
**SDK:** `google.genai.Client` directly (ADK Runner doesn't expose token-level streaming)

**Architecture:**
1. `_get_genai_client()` → singleton `genai_sdk.Client(api_key=GEMINI_API_KEY)`
2. `answer_question_stream(question, context)` → async generator yielding tokens
3. Bridge pattern: `_produce()` runs sync `generate_content_stream()` in `loop.run_in_executor(None, ...)`, pushes chunks into `asyncio.Queue`, background thread signals completion with `None`
4. Async generator consumes queue, yields tokens to FastAPI
5. FastAPI `StreamingResponse` wraps generator as SSE: `data: {"token": "..."}\n\n` + `data: [DONE]\n\n`

**System instruction:** "You are a VOLTRIX grid expert helping users understand their smart grid data. Answer concisely using the provided context. Be specific and data-driven. If you don't know, say so. No markdown formatting."

**Context building (main.py `/chat`):**
- If `household_id` provided: loads household + zone + recent stress events + recommendations for that household
- If no household_id: loads all zones with capacity, household count, and stress event count
- Context is newline-joined string prepended to user question

---

## Layer 8: Email Service (`email_service.py`)

**Transport:** Python stdlib `smtplib` only. Zero external dependencies.

**Connection logic:**
- Port 465: `SMTP_SSL` (direct SSL)
- Port 587: `SMTP` + `ehlo()` + `starttls()` + `login()`

**Two public functions:**

1. **`send_nudge_email(to_email, household_name, nudge_message, action_suggested)`**
   - Subject: "⚡ Energy alert for your zone tonight — VOLTRIX"
   - Dark-themed HTML email (slate palette, Inter font)
   - Shows nudge message + suggested action in blue accent card
   - Footer: "VOLTRIX — AI-Powered Grid Intelligence · Automated Citizen Alert"

2. **`send_utility_alert(to_email, zone_name, utility_action, reasoning)`**
   - Subject: "[VOLTRIX] Grid stress predicted — {zone_name}"
   - Red accent, shows zone name, AI analysis, recommended action
   - Footer: "VOLTRIX — AI-Powered Grid Intelligence · Automated Utility Alert"

Both return `True`/`False`. Never raise exceptions. All errors logged.

---

## Layer 9: API Endpoints (`main.py`)

FastAPI app. CORS middleware reads `ALLOWED_ORIGINS` (comma-split). `python-dotenv` loads `.env` automatically.

### Core Endpoints

| Method | Path | Handler | Key Behavior |
|--------|------|---------|-------------|
| GET | `/health` | `health()` | Returns `{status, service, day}`. Reads simulation_state. |
| GET | `/zones` | `list_zones()` | `SELECT * FROM zones ORDER BY name` |
| GET | `/zones/{id}/load-history?days=3` | `zone_load_history()` | Merges BQ actuals + Postgres forecasts. Scoped to actuals' time window. |
| GET | `/zones/{id}/backtest?test_days=3` | `zone_backtest()` | Rolling-origin backtest via forecasting.backtest_zone() |
| GET | `/stress-events?limit=20` | `list_stress_events()` | JOIN with zones for zone_name. Newest first. |
| GET | `/recommendations?limit=50&unsent_only=false` | `list_recommendations()` | Optional unsent filter |
| POST | `/recommendations/{id}/send` | `send_recommendation()` | Resolves target_type → email. Never raises. |
| GET | `/simulation/state` | `get_simulation_state()` | `{current_day: N}` |

### `/simulate/advance` — The Pipeline Endpoint

The main orchestrator. Per-zone execution with fault isolation:

```
1. db.advance_simulation_day()  → increment day (cap 30)
2. For each zone:
   a. Skip if active stress event exists (window_end > NOW())
   b. forecast_zone(bq_zone_id, periods=24, current_day=max(new_day-1, 0))
      ← CRITICAL: trains on data BEFORE target day (prevents target leakage)
   c. db.save_forecasts() → DELETE old + INSERT new 24h forecast
   d. detect_stress() → check if any yhat > 85% of capacity
   e. If no stress: fallback to detect_actual_stress() (nowcast on day's actuals)
   f. If stress detected:
      i.   bq.get_household_load_for_zone() → top 8 households by avg load
      ii.  agent.run_stress_analysis(stress_event, household_summaries)
      iii. db.save_stress_event() → insert + return UUID
      iv.  db.save_recommendation() × N household nudges
      v.   db.save_recommendation() × 1 utility alert
   g. Append ZoneAdvanceResult to results
   h. Exception per zone → log + append stress_detected=False
3. Return AdvanceResponse(new_day, results)
```

**Day-indexing fix:** `forecast_zone(current_day=max(new_day-1, 0))` — trains on data through day N-1, forecasts day N. Previously used `new_day` which leaked target day's readings into training data.

### `/chat` — SSE Streaming

```
1. Build context string from DB (zones, stress events, recommendations)
2. async def event_stream():
     async for token in answer_question_stream(question, context):
       yield f"data: {json.dumps({'token': token})}\n\n"
     yield "data: [DONE]\n\n"
3. Return StreamingResponse(event_stream, media_type="text/event-stream")
```

### `/ingest/*` — CSV Upload

Three endpoints (readings, zones, households). Each: parse → clean → load. Returns `IngestResponse{status, rows_received, rows_loaded, rows_rejected, warnings, errors}`.

### `/admin/*` — Operations

- `POST /admin/seed-households` → BQ → Postgres household sync
- `GET /admin/health` → DB connectivity, env var status, zone/household counts
- `POST /admin/reset-simulation` → Reset day to 1, delete all recommendations, stress_events, forecasts

---

## Layer 10: Frontend

### `frontend/index.html` — 986-line SPA

Single HTML file with inline CSS (242 lines) and JS (572 lines). External deps: Chart.js 4.4.7 (CDN), Inter font (Google Fonts).

**7 Pages:**
1. **Dashboard** — KPI cards (zones, active events, sim day, households) + zone grid with status dots (green/amber/red based on stress severity)
2. **Zones** — Per-zone cards → click for detail view with load history chart (actual vs predicted), line/bar/area toggle, peak/latest stats, raw data viewer
3. **Events** — Stress event list with severity filter, click "Recs" to drill into per-event recommendations
4. **Recommendations** — All/Unsent/High filter, send button per recommendation (triggers SMTP)
5. **Simulation** — Progress bar (day/30), "Advance +1 Day" button, per-zone result cards
6. **Chat** — SSE streaming with Gemma 4, optional household_id context, typing animation
7. **Admin** — CSV upload (3 types), seed households, reset simulation, health check + diagnostics

**Features:** Dark mode (localStorage), responsive (mobile sidebar, resized grids), 30s auto-refresh on dashboard, toast notifications, SSE streaming chat via `ReadableStream.getReader()`.

### `frontend/config.js`

Sets `window.__BACKEND_URL = ""` (empty for Vite proxy) or production Cloud Run URL. Also supports `?api=` query parameter override.

### Vite Setup

- `package.json`: Vite 6.3.5 dev dependency only
- `vite.config.js`: Dev server on port 5173, proxy all API paths (`/health`, `/zones`, `/stress-events`, `/recommendations`, `/simulation`, `/simulate`, `/chat`, `/ingest`, `/admin`) to `http://localhost:8000`
- Build: `npm run build` → `dist/index.html` (~55KB single file)
- Deploy: Vercel, Netlify, any static host

---

## Layer 11: Deployment

### Backend (Cloud Run)

**Dockerfile:**
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
```

**Cloud Run settings:** 2GB memory, 2 CPU, 120s timeout, 40 concurrency, us-central1, unauthenticated.

**deploy.sh:** `gcloud builds submit --tag gcr.io/PROJECT/SERVICE` → `gcloud run deploy` with env vars.

### Frontend

**Vercel:** `npm run build && npx vercel --prod`. `vercel.json`: `{version: 2, cleanUrls: true, trailingSlash: false}`.

**CORS:** After deploying, add frontend URL to `ALLOWED_ORIGINS` in backend `.env` or Cloud Run env vars.

---

## Critical Context

- BQ data: **June 1–30, 2026** — no query should use CURRENT_TIMESTAMP for data queries
- `get_zone_load_window(day=N)` returns data from hour 0 through hour N×24 relative to the zone's MIN(timestamp)
- Forecast training uses `current_day=max(new_day-1, 0)` to prevent target leakage
- Stale forecasts: DELETE-before-INSERT in `save_forecasts` prevents flatline chart artifacts
- Chat SSE: `asyncio.Queue` + background thread bridges sync genai SDK into async FastAPI
- ADK `auto_create_session=True` resolves `SessionNotFoundError`
- `detect_actual_stress()` is the nowcast fallback for novel spikes the forecast model can't predict
- Agent nudge backstop: if ADK returns empty nudges, `_fallback_response()` generates generic ones

## Relevant Files

| File | Lines | Purpose |
|------|-------|---------|
| `backend/main.py` | 828 | All API endpoints, pipeline orchestration, SSE streaming |
| `backend/forecasting.py` | 300 | V3 seasonal-trend forecast, stress detection, backtesting |
| `backend/agent.py` | 333 | ADK agent (3 tools) + direct genai chat streaming |
| `backend/db.py` | 277 | Postgres connection pool, domain helpers, upserts |
| `backend/bq.py` | 225 | BigQuery read/write, window queries, schema |
| `backend/models.py` | 131 | Pydantic request/response schemas |
| `backend/email_service.py` | 143 | SMTP delivery (stdlib smtplib) |
| `backend/ingestion.py` | 238 | CSV parse + clean + validate pipeline |
| `backend/schema.sql` | 96 | Postgres DDL with indexes and seed data |
| `backend/requirements.txt` | 12 | Python deps (no prophet) |
| `backend/Dockerfile` | 10 | Cloud Run container |
| `generate.py` | 312 | Synthetic data with multi-day stress events |
| `load_to_bq.py` | 123 | BigQuery CSV loader |
| `frontend/index.html` | 986 | Complete dashboard SPA (7 pages) |
| `frontend/config.js` | 10 | Backend URL configuration |
| `frontend/vite.config.js` | 18 | Dev server + API proxy |
