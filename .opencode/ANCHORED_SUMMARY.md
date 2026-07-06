## Goal
Complete VOLTRIX backend (FastAPI + Supabase + BigQuery + ADK agent + seasonal-forecast + SMTP) and deliver a deployable frontend — no auth, no CI/CD.

## Constraints & Preferences
- 100% free: BigQuery free tier, AI Studio (not Vertex AI), stdlib SMTP, Supabase free tier
- Never use `vertexai` or `google-cloud-aiplatform` — paid billing
- Never name a file `email.py` (shadows stdlib) — `email_service.py` used instead
- `/simulate/advance` must never crash on single-zone failure — `try/except` per zone, log and continue
- No auth for MVP, no CI/CD — manual deploy via `deploy.sh`
- No Prophet/CmdStan — zero compiled deps for reliable Docker builds

## Progress
### Done
- **Model**: `gemini-2.0-flash` → `gemma-4-31b-it` in agent.py (free AI Studio, same API key)
- **Session fix**: `Runner.auto_create_session=True` — resolves `SessionNotFoundError`
- **Token optimization**: System instruction ~300 chars, single-line prompts, compact JSON, temperature=0.3
- **Forecasting engine**: Prophet → custom seasonal-profile + anomaly-trend (seasonal_v3.py). `detect_actual_stress()` nowcast added
- **bq.py**: REF version — `get_zone_load_window`, `get_zone_load_for_day`, `get_zone_hourly_load`, `get_zone_load_for_chart`, `seed_households_from_bq`
- **Critical bug**: `forecast_zone(current_day=max(new_day-1, 0))` — trains on data before target day (was `new_day`, leaking target readings)
- **Agent nudge backstop**: Fills from fallback when ADK returns empty `household_nudges`
- **Chart flatline root cause**: Old 0.0 forecast rows persisted from earlier buggy runs — `save_forecasts` only upserted matching `(zone_id, forecast_for)` pairs, leaving stale rows
- **Stale forecast fix**: Added `DELETE FROM forecasts WHERE zone_id = %s` before insert in `save_forecasts` (db.py:140)
- **requirements.txt**: Dropped prophet/google-generativeai, added python-multipart/google-genai/Deprecated
- **.env.example**: Redacted placeholders (no real secrets)
- **generate.py enhanced**: Multi-day stress events with sine ramp, gradual ramp-up/down, correlated zone-level weather noise, temperature_c column, 5 household archetypes
- **load_to_bq.py**: Updated schema + loading for temperature_c column
- **bq.py READINGS_SCHEMA**: Added temperature_c
- **Chat streaming**: `/chat` replaced with SSE `StreamingResponse` yielding tokens from gemma-4-31b-it via google-genai's `generate_content_stream` — asyncio.Queue + background thread bridges sync SDK to async SSE
- **Tool call robustness**: `generate_household_nudges` now skips non-dict items (model was passing string-based household lists during chat, crashing the agent)
- **Frontend streaming**: `sendChat()` rewritten to consume SSE via `fetch` + `ReadableStream.getReader()`, builds bubble text incrementally (both `frontend/index.html` and `api_test.html`)

### Blocked
- `gcloud run deploy` requires GCP project + Supabase URL (infra not set up yet)

## Key Decisions
- V3 forecasting dropped Prophet entirely — CmdStan fails in CI/containers; custom seasonal-profile works everywhere
- `current_day=max(new_day-1, 0)` — critical: data through day N includes readings for day N, training on new_day leaks target
- Stale forecasts fix: DELETE before INSERT rather than filtering — each simulate/advance produces full 24h forecast, no reason to keep prior rows
- Chat streaming uses google-genai directly (bypassing ADK Runner) because ADK doesn't expose token-level streaming events through `run_async` — genai's `generate_content_stream` provides native chunk-level streaming
- `asyncio.Queue` + background thread bridges sync genai SDK into FastAPI's async SSE without blocking the event loop
- `file://` origin (`null`) in ALLOWED_ORIGINS for local HTML testing
- schema.sql: `UNIQUE (zone_id, forecast_for)` constraint on forecasts
- Normal evening peak ~36-44 kW per zone (12 households) → capacities 34-44 kW

## Next Steps
1. Fix `get_zone_hourly_load()` in bq.py to use dataset MIN/MAX instead of CURRENT_TIMESTAMP (or replace callers with `get_zone_load_window`)
2. Full integration test: `TRUNCATE forecasts;` → seed → advance simulation → verify chart shows gap (not flat zero) → backtest → chat stream test
3. Deploy: edit `frontend/config.js` with Cloud Run URL, run `frontend/deploy.sh`, set backend ALLOWED_ORIGINS

## Critical Context
- BQ data: **June 1–30, 2026** — no query should use CURRENT_TIMESTAMP
- Gemma 4 31B IT on AI Studio: model `gemma-4-31b-it`, ADK 0.3.0, `Runner.auto_create_session=True`
- Forecast rows with literal 0.0 persisted from earlier buggy runs — now fixed by DELETE-before-INSERT
- Chat SSE streaming tested: `curl -N` confirms tokens arrive as `data: {"token":"..."}` lines
- Reference production code: `C:\Users\ASUS\Documents\VOLTRIX-fixed-final\work\backend`

## Relevant Files
- `backend/main.py`: All endpoints + SSE streaming `/chat` + day-indexing fix + nowcast fallback
- `backend/forecasting.py`: V3 seasonal-profile + anomaly-trend + `detect_actual_stress()`
- `backend/agent.py`: ADK agent for stress analysis + `answer_question_stream()` async generator for chat SSE
- `backend/bq.py`: BigQuery client REF version with temperature_c in schema
- `backend/db.py`: Postgres pool + `save_forecasts` with stale-row cleanup
- `backend/models.py`: Pydantic schemas
- `backend/email_service.py`: SMTP delivery
- `backend/schema.sql`: Postgres DDL with UNIQUE constraint
- `backend/requirements.txt`: Pinned deps, no prophet
- `backend/deploy.sh` + `Dockerfile`: Cloud Run
- `frontend/index.html` + `config.js`: Dashboard with SSE streaming chat
- `api_test.html`: Standalone test page with SSE streaming chat
- `generate.py`: Synthetic data with multi-day stress, temperature
- `load_to_bq.py`: BQ upload with temperature support
