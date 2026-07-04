"""
bq.py — BigQuery client wrapper
Owner: Mrinmoy

Reads time-series load data from BigQuery for use in:
  1. Feeding Debjyoti's forecasting functions
  2. Serving historical load to the React chart via /zones/{zone_id}/load-history

All writes to BigQuery happen via Shritama's data generator / Cloud Function.
This module is read-only from the API's perspective.

Env required:
  GCP_PROJECT          e.g. "voltrix-app"
  GOOGLE_APPLICATION_CREDENTIALS  path to service-account JSON (or use Workload Identity on Cloud Run)
"""

import os
from datetime import datetime, timedelta
from google.cloud import bigquery

GCP_PROJECT = os.environ["GCP_PROJECT"]
DATASET = "voltrix"
TABLE = f"{GCP_PROJECT}.{DATASET}.load_readings"

_client: bigquery.Client | None = None


def get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=GCP_PROJECT)
    return _client


# ─── Core queries ─────────────────────────────────────────────────────────────


def get_zone_hourly_load(zone_id: str, days_back: int = 30) -> list[dict]:
    """
    Returns hourly aggregate load for a zone over the last N days.
    Used by Debjyoti's forecast_zone() as the training series.

    Returns: [{"timestamp": datetime, "load_kw": float}, ...]
    """
    client = get_client()
    query = f"""
        SELECT
            TIMESTAMP_TRUNC(timestamp, HOUR) AS timestamp,
            SUM(load_kw)                     AS load_kw
        FROM `{TABLE}`
        WHERE zone_id = @zone_id
          AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        GROUP BY 1
        ORDER BY 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
            bigquery.ScalarQueryParameter("days", "INT64", days_back),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [{"timestamp": row.timestamp, "load_kw": float(row.load_kw)} for row in rows]


def get_zone_load_for_chart(zone_id: str, days_back: int = 3) -> list[dict]:
    """
    Returns hourly load for the React dashboard chart (last N days of the dataset).
    Uses the dataset's own date range (via MIN/MAX timestamps) instead of
    CURRENT_TIMESTAMP, so it works correctly with historical data that doesn't
    span up to the present day.

    Shaped for Recharts: [{"hour": "06-26 18:00", "actual": 28.3}, ...]
    """
    client = get_client()
    # Find the boundaries for this zone
    bounds_query = f"""
        SELECT MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
        FROM `{TABLE}`
        WHERE zone_id = @zone_id
    """
    bounds = client.query(
        bounds_query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id)
            ]
        ),
    ).result()
    row = list(bounds)[0]
    if row.min_ts is None:
        return []
    min_ts = row.min_ts
    max_ts = row.max_ts
    total_hours = int((max_ts - min_ts).total_seconds() / 3600) + 1
    cutoff_hours = max(0, total_hours - days_back * 24)

    query = f"""
        SELECT
            TIMESTAMP_TRUNC(timestamp, HOUR) AS timestamp,
            SUM(load_kw)                     AS load_kw
        FROM `{TABLE}`
        WHERE zone_id = @zone_id
          AND TIMESTAMP_DIFF(timestamp, @min_ts, HOUR) >= @cutoff_hours
        GROUP BY 1
        ORDER BY 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
            bigquery.ScalarQueryParameter("min_ts", "TIMESTAMP", min_ts),
            bigquery.ScalarQueryParameter("cutoff_hours", "INT64", cutoff_hours),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "hour": r.timestamp.strftime("%m-%d %H:%M"),
            "actual": round(float(r.load_kw), 2),
        }
        for r in rows
    ]


def get_zone_load_window(zone_id: str, day: int, total_days: int = 30) -> list[dict]:
    """
    Returns the load readings for a specific simulated day (1-indexed).
    Used by /simulate/advance to expose only the data up to the current day.

    day=1  → first 24 hours of the dataset
    day=25 → up to and including day 25 (the injected stress event day)
    """
    client = get_client()
    query = f"""
        SELECT
            TIMESTAMP_TRUNC(timestamp, HOUR) AS timestamp,
            SUM(load_kw)                     AS load_kw
        FROM `{TABLE}`
        WHERE zone_id = @zone_id
          AND TIMESTAMP_DIFF(timestamp, (
                SELECT MIN(timestamp) FROM `{TABLE}` WHERE zone_id = @zone_id
              ), HOUR) < @cutoff_hours
        GROUP BY 1
        ORDER BY 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
            bigquery.ScalarQueryParameter("cutoff_hours", "INT64", day * 24),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [{"timestamp": row.timestamp, "load_kw": float(row.load_kw)} for row in rows]


def get_household_load_for_zone(zone_id: str, day: int, limit: int = 10) -> list[dict]:
    """
    Returns per-household load aggregates for a given simulated day.
    Used to build the household_summaries list passed to Gemini.

    Returns: [{"household_id": str, "archetype": str, "avg_load_kw": float}, ...]
    sorted descending by avg_load_kw (highest contributors first).
    """
    client = get_client()
    lower_hour = (day - 1) * 24
    upper_hour = day * 24 - 1
    query = f"""
        SELECT
            household_id,
            archetype,
            AVG(load_kw) AS avg_load_kw
        FROM `{TABLE}`
        WHERE zone_id = @zone_id
          AND TIMESTAMP_DIFF(timestamp, (
                SELECT MIN(timestamp) FROM `{TABLE}` WHERE zone_id = @zone_id
              ), HOUR) BETWEEN @lower_hour AND @upper_hour
        GROUP BY household_id, archetype
        ORDER BY avg_load_kw DESC
        LIMIT @lim
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("zone_id", "STRING", zone_id),
            bigquery.ScalarQueryParameter("lower_hour", "INT64", lower_hour),
            bigquery.ScalarQueryParameter("upper_hour", "INT64", upper_hour),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "household_id": row.household_id,
            "archetype": row.archetype,
            "avg_load_kw": round(float(row.avg_load_kw), 3),
        }
        for row in rows
    ]


READINGS_SCHEMA = [
    bigquery.SchemaField("household_id", "STRING"),
    bigquery.SchemaField("zone_id", "STRING"),
    bigquery.SchemaField("archetype", "STRING"),
    bigquery.SchemaField("timestamp", "TIMESTAMP"),
    bigquery.SchemaField("load_kw", "FLOAT64"),
]


def ensure_load_readings_table() -> None:
    client = get_client()
    table = bigquery.Table(TABLE, schema=READINGS_SCHEMA)
    table.clustering_fields = ["zone_id", "timestamp"]
    client.create_table(table, exists_ok=True)


def load_readings_dataframe(df) -> int:
    client = get_client()
    ensure_load_readings_table()
    payload = df[
        ["household_id", "zone_id", "archetype", "timestamp", "load_kw"]
    ].copy()
    job_config = bigquery.LoadJobConfig(
        schema=READINGS_SCHEMA,
        write_disposition="WRITE_APPEND",
    )
    job = client.load_table_from_dataframe(payload, TABLE, job_config=job_config)
    job.result()
    return len(payload)


def get_zone_ids() -> list[str]:
    """Returns all distinct zone_ids in BigQuery (sanity check / seed helper)."""
    client = get_client()
    query = f"SELECT DISTINCT zone_id FROM `{TABLE}` ORDER BY zone_id"
    rows = client.query(query).result()
    return [row.zone_id for row in rows]


def seed_households_from_bq(db_module) -> None:
    """
    One-time helper: reads distinct household_id + zone_id + archetype from
    BigQuery and inserts them into the Postgres households table.

    Call this once after applying schema.sql and loading BigQuery data.
    Usage: from bq import seed_households_from_bq; import db; seed_households_from_bq(db)
    """
    client = get_client()
    query = f"""
        SELECT DISTINCT household_id, zone_id, archetype
        FROM `{TABLE}`
        ORDER BY zone_id, household_id
    """
    rows = list(client.query(query).result())
    inserted = 0
    for row in rows:
        zone = db_module.fetch_one(
            "SELECT id FROM zones WHERE bq_zone_id = %s", (row.zone_id,)
        )
        if not zone:
            continue
        existing = db_module.fetch_one(
            "SELECT id FROM households WHERE bq_household_id = %s", (row.household_id,)
        )
        if existing:
            continue
        db_module.execute(
            """
            INSERT INTO households (zone_id, bq_household_id, name, archetype)
            VALUES (%s, %s, %s, %s)
            """,
            (
                zone["id"],
                row.household_id,
                row.household_id.replace("_", " ").title(),
                row.archetype,
            ),
        )
        inserted += 1
    print(f"Seeded {inserted} households from BigQuery → Postgres")
