"""
ingestion.py — CSV data ingestion pipeline
Owner: Mrinmoy

Lets someone upload real (or additional synthetic) data through the API
instead of only via the one-time generate.py -> load_to_bq.py script.

Three upload kinds, matching the three CSVs already in the repo root:
  readings.csv    -> household_id, zone_id, archetype, timestamp, load_kw
  zones.csv       -> zone_id, zone_name, capacity_kw
  households.csv  -> household_id, zone_id, name, email, archetype

Each `clean_*` function validates + cleans a raw DataFrame and returns
(cleaned_df, stats) where stats always has:
  rows_received, rows_loaded, rows_rejected, warnings (list[str])

Validation failures that make the whole upload unusable (missing required
columns, empty file, wrong type, file too large) raise IngestionError, which
main.py turns into a 400-style {"status": "error", ...} response rather than
a 500 — bad input shouldn't look like a server bug.
"""

import io
import logging
import pandas as pd

logger = logging.getLogger("voltrix.ingestion")

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_ROWS = 500_000
LOAD_OUTLIER_CAP_KW = 500.0


class IngestionError(Exception):
    """Raised for input that can't be processed at all (bad file / missing columns)."""


READING_COLUMNS = ["household_id", "zone_id", "archetype", "timestamp", "load_kw"]
ZONE_COLUMNS = ["zone_id", "zone_name", "capacity_kw"]
HOUSEHOLD_COLUMNS = ["household_id", "zone_id", "name", "email", "archetype"]


# ─── Shared CSV parsing ────────────────────────────────────────────────────────


def _read_csv(file_bytes: bytes, required_columns: list[str]) -> pd.DataFrame:
    if not file_bytes:
        raise IngestionError("Uploaded file is empty")
    if len(file_bytes) > MAX_FILE_BYTES:
        raise IngestionError(
            f"File too large ({len(file_bytes) / 1e6:.1f} MB) — limit is "
            f"{MAX_FILE_BYTES / 1e6:.0f} MB per upload"
        )

    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as e:
        raise IngestionError(f"Could not parse file as CSV: {e}")

    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise IngestionError(
            f"Missing required column(s): {', '.join(missing)}. "
            f"Expected columns: {', '.join(required_columns)}"
        )

    if len(df) == 0:
        raise IngestionError("CSV has a header row but no data rows")
    if len(df) > MAX_ROWS:
        raise IngestionError(
            f"Too many rows ({len(df):,}) — limit is {MAX_ROWS:,} rows per upload. "
            f"Split the file and upload in batches."
        )

    return df


def parse_readings_csv(file_bytes: bytes) -> pd.DataFrame:
    return _read_csv(file_bytes, READING_COLUMNS)


def parse_zones_csv(file_bytes: bytes) -> pd.DataFrame:
    return _read_csv(file_bytes, ZONE_COLUMNS)


def parse_households_csv(file_bytes: bytes) -> pd.DataFrame:
    return _read_csv(file_bytes, HOUSEHOLD_COLUMNS)


# ─── Cleaning ───────────────────────────────────────────────────────────────


def clean_readings(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    received = len(df)

    df = df[READING_COLUMNS].copy()

    before = len(df)
    df = df.dropna(subset=["household_id", "zone_id", "timestamp"])
    if len(df) < before:
        warnings.append(
            f"Dropped {before - len(df)} row(s) missing household_id/zone_id/timestamp"
        )

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    before = len(df)
    df = df.dropna(subset=["timestamp"])
    if len(df) < before:
        warnings.append(
            f"Dropped {before - len(df)} row(s) with unparseable timestamps"
        )

    df["load_kw"] = pd.to_numeric(df["load_kw"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["load_kw"])
    if len(df) < before:
        warnings.append(f"Dropped {before - len(df)} row(s) with non-numeric load_kw")

    neg_count = int((df["load_kw"] < 0).sum())
    if neg_count:
        df["load_kw"] = df["load_kw"].clip(lower=0)
        warnings.append(f"Clipped {neg_count} negative load_kw value(s) to 0")

    outlier_count = int((df["load_kw"] > LOAD_OUTLIER_CAP_KW).sum())
    if outlier_count:
        df["load_kw"] = df["load_kw"].clip(upper=LOAD_OUTLIER_CAP_KW)
        warnings.append(
            f"Capped {outlier_count} implausible load_kw value(s) "
            f"(> {LOAD_OUTLIER_CAP_KW} kW) at {LOAD_OUTLIER_CAP_KW} kW"
        )

    df["archetype"] = df["archetype"].fillna("unknown").astype(str).str.strip()
    df["household_id"] = df["household_id"].astype(str).str.strip()
    df["zone_id"] = df["zone_id"].astype(str).str.strip()

    before = len(df)
    df = df.drop_duplicates(subset=["household_id", "timestamp"], keep="last")
    if len(df) < before:
        warnings.append(
            f"Removed {before - len(df)} duplicate (household_id, timestamp) row(s)"
        )

    stats = {
        "rows_received": received,
        "rows_loaded": len(df),
        "rows_rejected": received - len(df),
        "warnings": warnings,
    }
    return df.reset_index(drop=True), stats


def clean_zones(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    received = len(df)

    df = df[ZONE_COLUMNS].copy()

    before = len(df)
    df = df.dropna(subset=["zone_id", "zone_name"])
    if len(df) < before:
        warnings.append(f"Dropped {before - len(df)} row(s) missing zone_id/zone_name")

    df["capacity_kw"] = pd.to_numeric(df["capacity_kw"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["capacity_kw"])
    df = df[df["capacity_kw"] > 0]
    if len(df) < before:
        warnings.append(
            f"Dropped {before - len(df)} row(s) with missing/non-positive capacity_kw"
        )

    df["zone_id"] = df["zone_id"].astype(str).str.strip()
    df["zone_name"] = df["zone_name"].astype(str).str.strip()

    before = len(df)
    df = df.drop_duplicates(subset=["zone_id"], keep="last")
    if len(df) < before:
        warnings.append(f"Removed {before - len(df)} duplicate zone_id row(s)")

    stats = {
        "rows_received": received,
        "rows_loaded": len(df),
        "rows_rejected": received - len(df),
        "warnings": warnings,
    }
    return df.reset_index(drop=True), stats


def clean_households(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    received = len(df)

    df = df[HOUSEHOLD_COLUMNS].copy()

    before = len(df)
    df = df.dropna(subset=["household_id", "zone_id"])
    if len(df) < before:
        warnings.append(
            f"Dropped {before - len(df)} row(s) missing household_id/zone_id"
        )

    df["household_id"] = df["household_id"].astype(str).str.strip()
    df["zone_id"] = df["zone_id"].astype(str).str.strip()
    df["name"] = df["name"].fillna("").astype(str).str.strip()
    df["email"] = df["email"].fillna("").astype(str).str.strip()
    df["archetype"] = df["archetype"].fillna("unknown").astype(str).str.strip()

    before = len(df)
    df = df.drop_duplicates(subset=["household_id"], keep="last")
    if len(df) < before:
        warnings.append(f"Removed {before - len(df)} duplicate household_id row(s)")

    stats = {
        "rows_received": received,
        "rows_loaded": len(df),
        "rows_rejected": received - len(df),
        "warnings": warnings,
    }
    return df.reset_index(drop=True), stats


# ─── Templates (for GET /ingest/template/{kind}) ──────────────────────────────

TEMPLATES = {
    "readings": (
        "household_id,zone_id,archetype,timestamp,load_kw\n"
        "zone_0_hh_00,zone_0,family,2026-07-01 00:00:00 UTC,0.95\n"
        "zone_0_hh_00,zone_0,family,2026-07-01 01:00:00 UTC,0.88\n"
    ),
    "zones": ("zone_id,zone_name,capacity_kw\nzone_5,Ward 6 — Harbor District,40.0\n"),
    "households": (
        "household_id,zone_id,name,email,archetype\n"
        "zone_5_hh_00,zone_5,Household 1,demo+zone5hh00@resend.dev,family\n"
    ),
}
