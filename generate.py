"""
generate.py — GridSense Synthetic Data Generator
Owner: Shritama

Produces:
  readings.csv  — hourly household load (30 days × 5 zones × 12 households)
  zones.csv     — zone metadata with capacity
  households.csv — household roster with emails for Resend

Three hardcoded stress events are injected so the demo is always reliable:
  Zone 2 (Ward 3) — Day 25, 5pm–10pm   heatwave spike  (2.3× multiplier)
  Zone 0 (Ward 1) — Day 18, 6pm–9pm    moderate surge  (1.7× multiplier)
  Zone 3 (Ward 4) — Day 28, 4pm–11pm   critical storm  (2.6× multiplier)

Usage:
    pip install numpy pandas
    python generate.py
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import random
import uuid

random.seed(42)
np.random.seed(42)

# ─── Household archetypes ─────────────────────────────────────────────────────
# morning / evening / base in kW
ARCHETYPES = {
    "family": {
        "morning": 2.2,   # breakfast rush
        "evening": 5.5,   # dinner + TV + AC
        "base":    0.9,
        "weekend_multiplier": 1.3,
    },
    "single_professional": {
        "morning": 0.7,
        "evening": 2.4,
        "base":    0.35,
        "weekend_multiplier": 1.1,
    },
    "wfh": {
        "morning": 1.4,
        "evening": 3.2,
        "base":    1.6,   # high base — always home, always on
        "weekend_multiplier": 0.9,
    },
    "retired": {
        "morning": 1.8,
        "evening": 2.0,
        "base":    1.1,
        "weekend_multiplier": 1.0,
    },
    "small_business": {
        "morning": 3.5,
        "evening": 1.2,
        "base":    0.6,
        "weekend_multiplier": 0.4,  # closed weekends
    },
}

# ─── Zone definitions ─────────────────────────────────────────────────────────
ZONES = [
    {"zone_id": "zone_0", "zone_name": "Ward 1 — Riverside",   "capacity_kw": 38.0},
    {"zone_id": "zone_1", "zone_name": "Ward 2 — Central",     "capacity_kw": 44.0},
    {"zone_id": "zone_2", "zone_name": "Ward 3 — East Heights","capacity_kw": 36.0},
    {"zone_id": "zone_3", "zone_name": "Ward 4 — Northside",   "capacity_kw": 40.0},
    {"zone_id": "zone_4", "zone_name": "Ward 5 — Greenfield",  "capacity_kw": 34.0},
]

HOUSEHOLDS_PER_ZONE = 12
DAYS = 30
START = datetime(2026, 6, 1)

# ─── Stress event windows ──────────────────────────────────────────────────────
# (zone_id, start_offset_days, start_hour, end_hour, multiplier, label)
STRESS_EVENTS = [
    ("zone_2", 25, 17, 22, 2.3, "heatwave"),     # CORE demo event
    ("zone_0", 18, 18, 21, 1.7, "moderate_surge"),
    ("zone_3", 28, 16, 23, 2.6, "storm_surge"),
]

def _stress_windows_for_zone(zone_id):
    windows = []
    for (zid, day_offset, sh, eh, mult, _) in STRESS_EVENTS:
        if zid == zone_id:
            ws = START + timedelta(days=day_offset, hours=sh)
            we = START + timedelta(days=day_offset, hours=eh)
            windows.append((ws, we, mult))
    return windows


def _hour_load(archetype_name, ts, stress_windows):
    """Return realistic kW load for a single hour timestamp."""
    p = ARCHETYPES[archetype_name]
    h = ts.hour
    is_weekend = ts.weekday() >= 5
    wm = p["weekend_multiplier"] if is_weekend else 1.0

    # Base daily curve
    if 6 <= h <= 9:
        base = p["morning"]
        noise_scale = 0.25
    elif 18 <= h <= 22:
        base = p["evening"]
        noise_scale = 0.40
    elif 12 <= h <= 14:
        base = (p["morning"] + p["base"]) / 2
        noise_scale = 0.20
    else:
        base = p["base"]
        noise_scale = 0.15

    load = (base * wm) + np.random.normal(0, noise_scale)
    load = max(load, 0.05)

    # Seasonal temperature effect — June = warm, ramp up mid-day
    day_of_month = (ts - START).days
    temp_factor = 1.0 + 0.008 * day_of_month   # gradual warmth creep
    if 13 <= h <= 16:
        temp_factor *= 1.15                      # peak heat hours
    load *= temp_factor

    # Stress event multiplier
    for (ws, we, mult) in stress_windows:
        if ws <= ts <= we:
            load *= mult

    return round(max(load, 0.05), 3)


def build_dataset():
    all_readings = []
    all_households = []
    archetypes_list = list(ARCHETYPES.keys())

    for zone in ZONES:
        zid = zone["zone_id"]
        stress_windows = _stress_windows_for_zone(zid)

        for h_idx in range(HOUSEHOLDS_PER_ZONE):
            archetype = archetypes_list[h_idx % len(archetypes_list)]
            hh_id = f"{zid}_hh_{h_idx:02d}"
            hh_name = f"Household {h_idx + 1}"
            # Generate demo-safe email addresses using resend.dev sandbox
            hh_email = f"demo+{zid}_hh{h_idx:02d}@resend.dev"

            all_households.append({
                "household_id": hh_id,
                "zone_id":       zid,
                "name":          hh_name,
                "email":         hh_email,
                "archetype":     archetype,
            })

            for day in range(DAYS):
                for hour in range(24):
                    ts = START + timedelta(days=day, hours=hour)
                    load = _hour_load(archetype, ts, stress_windows)
                    all_readings.append({
                        "household_id": hh_id,
                        "zone_id":      zid,
                        "archetype":    archetype,
                        "timestamp":    ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "load_kw":      load,
                    })

    df_readings    = pd.DataFrame(all_readings)
    df_zones       = pd.DataFrame(ZONES)
    df_households  = pd.DataFrame(all_households)
    return df_readings, df_zones, df_households


def validate(df_readings, df_zones):
    """Quick sanity check before loading to BigQuery."""
    total = len(df_readings)
    expected = len(ZONES) * HOUSEHOLDS_PER_ZONE * DAYS * 24
    assert total == expected, f"Expected {expected} rows, got {total}"

    for zone in ZONES:
        zid = zone["zone_id"]
        stress_windows = _stress_windows_for_zone(zid)
        if not stress_windows:
            continue
        for (ws, we, mult) in stress_windows:
            peak_ts = ws.strftime("%Y-%m-%d %H:%M:%S UTC")
            zone_peak = (
                df_readings[
                    (df_readings["zone_id"] == zid) &
                    (df_readings["timestamp"] == peak_ts)
                ]["load_kw"].sum()
            )
            cap = float(df_zones[df_zones["zone_id"] == zid]["capacity_kw"].iloc[0])
            print(f"  {zid} @ {peak_ts}: zone total = {zone_peak:.2f} kW | capacity = {cap:.1f} kW | {'⚡ OVER' if zone_peak > cap else 'OK'}")

    print(f"\n✅ Validation passed — {total:,} rows generated across {len(ZONES)} zones")


if __name__ == "__main__":
    print("Generating synthetic data...")
    df_readings, df_zones, df_households = build_dataset()

    validate(df_readings, df_zones)

    df_readings.to_csv("readings.csv", index=False)
    df_zones.to_csv("zones.csv", index=False)
    df_households.to_csv("households.csv", index=False)

    print(f"\nFiles written:")
    print(f"  readings.csv    — {len(df_readings):,} rows")
    print(f"  zones.csv       — {len(df_zones)} zones")
    print(f"  households.csv  — {len(df_households)} households")
    print(f"\nNext step: python load_to_bq.py")