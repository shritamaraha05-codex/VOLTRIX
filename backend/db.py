"""
db.py — Supabase (Postgres) connection + helper wrappers
Owner: Mrinmoy

Connects to Supabase Postgres via its native connection string using psycopg2.
Set DATABASE_URL in your environment:
  postgresql://postgres:password@db.[ref].supabase.co:5432/postgres
"""

import os
import json
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from contextlib import contextmanager
from datetime import datetime, date
from decimal import Decimal

DATABASE_URL = os.environ["DATABASE_URL"]

# ─── Connection pool ──────────────────────────────────────────────────────────
_pool: pool.SimpleConnectionPool | None = None


def get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return _pool


@contextmanager
def get_conn():
    """Context manager that borrows/returns a connection from the pool."""
    p = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ─── JSON serialiser helper ───────────────────────────────────────────────────
def _serialise(obj):
    """Convert Postgres types that json.dumps can't handle by default."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def _to_json_safe(rows: list[dict]) -> list[dict]:
    """Round-trip through JSON to normalise all Postgres-specific types."""
    return json.loads(json.dumps(rows, default=_serialise))


# ─── Core helpers ─────────────────────────────────────────────────────────────


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return all rows as a list of plain dicts."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return _to_json_safe([dict(r) for r in rows])


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    """Run a SELECT and return a single row (or None)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    return _to_json_safe([dict(row)])[0] if row else None


def execute(query: str, params: tuple = ()) -> None:
    """Run an INSERT / UPDATE / DELETE with no return value needed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)


def insert_returning_id(query: str, params: tuple = ()) -> str:
    """
    Run an INSERT ... RETURNING id and return the UUID string.
    Query must end with  RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    return str(row["id"])


def execute_many(query: str, params_list: list[tuple]) -> None:
    """Bulk insert using executemany (faster than looping execute)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(query, params_list)


# ─── Domain-specific helpers (used by main.py) ────────────────────────────────


def get_simulation_day() -> int:
    row = fetch_one("SELECT current_day FROM simulation_state WHERE id = 1")
    return row["current_day"] if row else 1


def advance_simulation_day() -> int:
    """Increment the simulated day (caps at 30). Returns new day."""
    execute(
        "UPDATE simulation_state SET current_day = LEAST(current_day + 1, 30) WHERE id = 1"
    )
    return get_simulation_day()


def save_forecasts(zone_id: str, forecast_rows: list[dict]) -> None:
    """
    Bulk-upsert forecast rows into the forecasts table.
    forecast_rows: list of {"forecast_for": datetime, "predicted_load_kw": float}
    """
    zone = fetch_one("SELECT id FROM zones WHERE bq_zone_id = %s", (zone_id,))
    if not zone:
        return
    zone_uuid = zone["id"]
    params = [
        (zone_uuid, row["forecast_for"], row["predicted_load_kw"])
        for row in forecast_rows
    ]
    execute_many(
        """
        INSERT INTO forecasts (zone_id, forecast_for, predicted_load_kw)
        VALUES (%s, %s, %s)
        ON CONFLICT (zone_id, forecast_for) DO UPDATE
          SET predicted_load_kw = EXCLUDED.predicted_load_kw
        """,
        params,
    )


def save_stress_event(
    bq_zone_id: str,
    window_start,
    window_end,
    severity: str,
    predicted_peak_kw: float,
    capacity_kw: float,
    reasoning: str,
) -> str:
    """Insert a stress event and return its UUID."""
    zone = fetch_one("SELECT id FROM zones WHERE bq_zone_id = %s", (bq_zone_id,))
    if not zone:
        raise ValueError(f"Zone {bq_zone_id} not found in Postgres")
    return insert_returning_id(
        """
        INSERT INTO stress_events
          (zone_id, window_start, window_end, severity, predicted_peak_kw, capacity_kw, reasoning)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            zone["id"],
            window_start,
            window_end,
            severity,
            predicted_peak_kw,
            capacity_kw,
            reasoning,
        ),
    )


def save_recommendation(
    stress_event_id: str,
    target_type: str,
    message: str,
    action_suggested: str,
    bq_household_id: str | None = None,
) -> None:
    """
    Insert one recommendation row.
    For household nudges pass bq_household_id; for utility-facing rows leave it None.
    """
    household = None
    if bq_household_id:
        household = fetch_one(
            "SELECT id FROM households WHERE bq_household_id = %s", (bq_household_id,)
        )

    execute(
        """
        INSERT INTO recommendations
          (stress_event_id, target_type, household_id, bq_household_id, message, action_suggested)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            stress_event_id,
            target_type,
            household["id"] if household else None,
            bq_household_id,
            message,
            action_suggested,
        ),
    )


def mark_recommendation_sent(recommendation_id: str) -> None:
    execute(
        "UPDATE recommendations SET sent = true, sent_at = now() WHERE id = %s",
        (recommendation_id,),
    )


# ─── Ingestion helpers (used by /ingest/* endpoints) ───────────────────────────


def upsert_zone(
    bq_zone_id: str, name: str, capacity_kw: float, household_count: int | None = None
) -> None:
    execute(
        """
        INSERT INTO zones (name, bq_zone_id, household_count, baseline_capacity_kw)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (bq_zone_id) DO UPDATE
          SET name = EXCLUDED.name,
              baseline_capacity_kw = EXCLUDED.baseline_capacity_kw,
              household_count = COALESCE(EXCLUDED.household_count, zones.household_count)
        """,
        (name, bq_zone_id, household_count, capacity_kw),
    )


def upsert_household(
    bq_household_id: str,
    bq_zone_id: str,
    name: str | None,
    email: str | None,
    archetype: str | None,
) -> bool:
    zone = fetch_one("SELECT id FROM zones WHERE bq_zone_id = %s", (bq_zone_id,))
    if not zone:
        return False
    execute(
        """
        INSERT INTO households (zone_id, bq_household_id, name, email, archetype)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (bq_household_id) DO UPDATE
          SET zone_id = EXCLUDED.zone_id,
              name = EXCLUDED.name,
              email = EXCLUDED.email,
              archetype = EXCLUDED.archetype
        """,
        (zone["id"], bq_household_id, name, email, archetype),
    )
    return True


def known_zone_ids() -> set[str]:
    rows = fetch_all("SELECT bq_zone_id FROM zones")
    return {r["bq_zone_id"] for r in rows}
