"""
load_to_bq.py — Load synthetic CSVs into BigQuery
Owner: Shritama

Usage:
    python load_to_bq.py

Requires:
    - GOOGLE_APPLICATION_CREDENTIALS set (or Cloud Run identity)
    - CSVs in the same directory (readings.csv, zones.csv, households.csv)
"""

import os
import pandas as pd
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "backend", ".env"))

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "backend")
sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if sa_path and not os.path.isabs(sa_path):
    sa_path = os.path.join(BACKEND_DIR, sa_path)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path

GCP_PROJECT = os.environ.get("GCP_PROJECT", "voltrix-app")
DATASET_ID = f"{GCP_PROJECT}.voltrix"

client = bigquery.Client(project=GCP_PROJECT)


def ensure_dataset():
    dataset = bigquery.Dataset(DATASET_ID)
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    print(f"[OK] Dataset {DATASET_ID} ready")


def load_readings():
    table_id = f"{DATASET_ID}.load_readings"
    schema = [
        bigquery.SchemaField("household_id", "STRING"),
        bigquery.SchemaField("zone_id", "STRING"),
        bigquery.SchemaField("archetype", "STRING"),
        bigquery.SchemaField("timestamp", "TIMESTAMP"),
        bigquery.SchemaField("load_kw", "FLOAT64"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    table.clustering_fields = ["zone_id", "timestamp"]
    client.create_table(table, exists_ok=True)

    df = pd.read_csv("readings.csv")
    df["load_kw"] = df["load_kw"].astype(float)
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], format="%Y-%m-%d %H:%M:%S UTC", utc=True
    )

    job = client.load_table_from_dataframe(df, table_id)
    job.result()
    print(f"[OK] load_readings: {len(df):,} rows loaded")


def load_zone_capacity():
    table_id = f"{DATASET_ID}.zone_capacity"
    schema = [
        bigquery.SchemaField("zone_id", "STRING"),
        bigquery.SchemaField("zone_name", "STRING"),
        bigquery.SchemaField("baseline_capacity_kw", "FLOAT64"),
        bigquery.SchemaField("household_count", "INT64"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    client.create_table(table, exists_ok=True)

    zones = pd.read_csv("zones.csv")
    households = pd.read_csv("households.csv")
    hh_counts = households.groupby("zone_id").size().reset_index(name="household_count")

    df = zones.merge(hh_counts, on="zone_id")
    df = df.rename(columns={"capacity_kw": "baseline_capacity_kw"})
    df = df[["zone_id", "zone_name", "baseline_capacity_kw", "household_count"]]

    job = client.load_table_from_dataframe(df, table_id)
    job.result()
    print(f"[OK] zone_capacity: {len(df)} rows loaded")


def load_households():
    table_id = f"{DATASET_ID}.households"
    schema = [
        bigquery.SchemaField("household_id", "STRING"),
        bigquery.SchemaField("zone_id", "STRING"),
        bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("email", "STRING"),
        bigquery.SchemaField("archetype", "STRING"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    table.clustering_fields = ["zone_id"]
    client.create_table(table, exists_ok=True)

    df = pd.read_csv("households.csv")

    job = client.load_table_from_dataframe(df, table_id)
    job.result()
    print(f"[OK] households: {len(df)} rows loaded")


if __name__ == "__main__":
    ensure_dataset()
    load_readings()
    load_zone_capacity()
    load_households()
    print("\n[OK] All data loaded. Run the pipeline with:")
    print("   1. POST /admin/seed-households   (sync households to Postgres)")
    print("   2. POST /admin/reset-simulation   (reset clock to day 1)")
    print("   3. POST /simulate/advance          (run the demo)")
