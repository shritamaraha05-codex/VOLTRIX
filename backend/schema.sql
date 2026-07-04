-- VOLTRIX — Postgres Operational Schema
-- Owner: Mrinmoy
-- Apply this to Cloud SQL (or Supabase if using that instead)
-- Run once before starting the API

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Zones ────────────────────────────────────────────────────────────────────
-- Mirrors the zone_id used in BigQuery so the API can join across both stores.
CREATE TABLE IF NOT EXISTS zones (
  id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 TEXT        NOT NULL,
  bq_zone_id           TEXT        NOT NULL UNIQUE, -- e.g. "zone_0", "zone_2"
  household_count      INT,
  baseline_capacity_kw NUMERIC     NOT NULL
);

-- ─── Households ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS households (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id         UUID REFERENCES zones(id) ON DELETE CASCADE,
  bq_household_id TEXT NOT NULL UNIQUE, -- e.g. "zone_2_hh_3"
  name            TEXT,
  email           TEXT,
  archetype       TEXT  -- 'family' | 'single_professional' | 'wfh' | 'retired'
);

-- ─── Forecasts ────────────────────────────────────────────────────────────────
-- Stores each zone's hourly predicted load so the frontend can render
-- actual-vs-predicted chart without re-querying BigQuery every time.
CREATE TABLE IF NOT EXISTS forecasts (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id           UUID        REFERENCES zones(id) ON DELETE CASCADE,
  forecast_for      TIMESTAMPTZ NOT NULL,
  predicted_load_kw NUMERIC,
  actual_load_kw    NUMERIC,     -- backfilled when the real reading arrives
  created_at        TIMESTAMPTZ DEFAULT now()
);

-- ─── Stress Events ────────────────────────────────────────────────────────────
-- One row per detected stress window. reasoning is the Gemini-generated text.
CREATE TABLE IF NOT EXISTS stress_events (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id           UUID        REFERENCES zones(id) ON DELETE CASCADE,
  detected_at       TIMESTAMPTZ DEFAULT now(),
  window_start      TIMESTAMPTZ,
  window_end        TIMESTAMPTZ,
  severity          TEXT,        -- 'moderate' | 'critical'
  predicted_peak_kw NUMERIC,
  capacity_kw       NUMERIC,
  reasoning         TEXT         -- Gemini-generated plain-language explanation
);

-- ─── Recommendations ──────────────────────────────────────────────────────────
-- Household nudges + utility-facing action briefs.
-- household_id is NULL for utility-facing rows.
CREATE TABLE IF NOT EXISTS recommendations (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  stress_event_id  UUID        REFERENCES stress_events(id) ON DELETE CASCADE,
  target_type      TEXT        NOT NULL,  -- 'household' | 'utility'
  household_id     UUID        REFERENCES households(id),
  bq_household_id  TEXT,                  -- convenience: matches BigQuery id
  message          TEXT,
  action_suggested TEXT,
  sent             BOOLEAN     DEFAULT false,
  sent_at          TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT now()
);

-- ─── Simulation Clock ─────────────────────────────────────────────────────────
-- Tracks the current simulated "day" so /simulate/advance knows what window
-- of synthetic data to expose next.
CREATE TABLE IF NOT EXISTS simulation_state (
  id           INT  PRIMARY KEY DEFAULT 1,  -- always a single row
  current_day  INT  NOT NULL DEFAULT 1,     -- 1..30
  CHECK (id = 1)
);
INSERT INTO simulation_state (id, current_day) VALUES (1, 1)
ON CONFLICT (id) DO NOTHING;

-- ─── Indexes ──────────────────────────────────────────────────────────────────
ALTER TABLE forecasts ADD CONSTRAINT uq_forecasts_zone_hour UNIQUE (zone_id, forecast_for);
CREATE INDEX IF NOT EXISTS idx_forecasts_zone_ts    ON forecasts(zone_id, forecast_for DESC);
CREATE INDEX IF NOT EXISTS idx_stress_events_zone   ON stress_events(zone_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_evt  ON recommendations(stress_event_id);
CREATE INDEX IF NOT EXISTS idx_households_zone      ON households(zone_id);

-- ─── Seed reference data ──────────────────────────────────────────────────────
-- Debjyoti's synthetic data uses these zone_ids; adjust if zone count changes.
INSERT INTO zones (name, bq_zone_id, household_count, baseline_capacity_kw) VALUES
  ('Ward 1', 'zone_0', 12, 36.0),
  ('Ward 2', 'zone_1', 12, 36.0),
  ('Ward 3', 'zone_2', 12, 36.0),   -- stress event injected here on day 25
  ('Ward 4', 'zone_3', 12, 36.0),
  ('Ward 5', 'zone_4', 12, 36.0)
ON CONFLICT (bq_zone_id) DO NOTHING;
