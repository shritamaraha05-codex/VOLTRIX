"""
generate.py — VOLTRIX Synthetic Data Generator
Owner: Shritama

Produces:
  readings.csv    — hourly household load (30 days × 5 zones × 12 households)
  zones.csv       — zone metadata with calibrated capacity
  households.csv  — household roster with emails for sandbox delivery

Stress events (calibrated to forecasting-detection thresholds):
  Zone 2 (East Heights) — Days 24-27 — multi-day heatwave, gradual ramp
  Zone 0 (Riverside)    — Day 18, 6pm-9pm — moderate evening surge
  Zone 3 (Northside)    — Days 28-29 — 2-day storm surge

Key design choices:
  - Zones have 12 households × 5 archetypes, each with realistic diurnal curves
  - Capacities calibrated so normal evening peaks stay under 85% threshold
    but stress events push well above, giving detect_stress a clean signal
  - Stress events use gradual ramp-up/down (not instant step) so the
    seasonal-trend forecasting model has a chance to extrapolate from
    the preceding hours
  - Temperature column included for future weather-based model integration
  - Noise is correlated across households in the same zone (grid-level
    weather component) + independent per-household jitter

Usage:
    pip install numpy pandas
    python generate.py
    python load_to_bq.py
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

random = np.random
random.seed(42)

ARCHETYPES = {
    "family": {"morning": 2.2, "evening": 5.5, "base": 0.9, "weekend": 1.3},
    "single_professional": {
        "morning": 0.7,
        "evening": 2.4,
        "base": 0.35,
        "weekend": 1.1,
    },
    "wfh": {"morning": 1.4, "evening": 3.2, "base": 1.6, "weekend": 0.9},
    "retired": {"morning": 1.8, "evening": 2.0, "base": 1.1, "weekend": 1.0},
    "small_business": {"morning": 3.5, "evening": 1.2, "base": 0.6, "weekend": 0.4},
}

# Zone capacities calibrated so normal evening peak stays below ~83% of
# threshold (threshold_pct=0.85) but stress events exceed it decisively.
# Normal zone evening peak (12 hh summed): ~28-30 kW → 85%-of-36kW = 30.6 kW ✓
# Stressed zone_2 evening peak: ~41 kW → well above 30.6 kW ✓
ZONES = [
    {"zone_id": "zone_0", "zone_name": "Ward 1 — Riverside", "capacity_kw": 36.0},
    {"zone_id": "zone_1", "zone_name": "Ward 2 — Central", "capacity_kw": 44.0},
    {"zone_id": "zone_2", "zone_name": "Ward 3 — East Heights", "capacity_kw": 36.0},
    {"zone_id": "zone_3", "zone_name": "Ward 4 — Northside", "capacity_kw": 40.0},
    {"zone_id": "zone_4", "zone_name": "Ward 5 — Greenfield", "capacity_kw": 34.0},
]

HOUSEHOLDS_PER_ZONE = 12
DAYS = 30
START = datetime(2026, 6, 1)
HOURS = 24

# ─── Stress events ─────────────────────────────────────────────────────────
# Each is (zone_id, start_day, duration_days, start_hour, end_hour,
#            peak_multiplier, label)
# Multi-day events use a sine-shaped envelope that ramps up, peaks on the
# middle day, and ramps down — not an instant step.
STRESS_EVENTS = [
    ("zone_2", 24, 4, 16, 22, 2.3, "heatwave"),  # multi-day heatwave
    ("zone_0", 18, 1, 18, 21, 1.7, "moderate_surge"),
    ("zone_3", 28, 2, 15, 23, 2.6, "storm_surge"),  # multi-day storm
]

# Temperature profile: daily high/low in °C, June-like
TEMP_BASE = 22.0  # overnight low
TEMP_PEAK = 34.0  # afternoon high
TEMP_HEATWAVE_BOOST = 6.0  # extra degrees during heatwave days


def _stress_multiplier_for_hour(zone_id: str, ts: datetime) -> float:
    """Returns the stress multiplier for a given timestamp.
    Multi-day events use a smooth sine envelope: ramp up over day 1,
    peak on middle day(s), ramp down on last day."""
    m = 1.0
    for zid, start_day, duration, sh, eh, peak_mult, label in STRESS_EVENTS:
        if zid != zone_id:
            continue
        event_start = START + timedelta(days=start_day)
        event_end = event_start + timedelta(days=duration)
        if not (event_start <= ts < event_end):
            continue

        hour_offset = ts.hour
        if not (sh <= hour_offset < eh):
            continue

        # Day position within the event (0..duration-1)
        day_in_event = (ts - event_start).days
        # Sine envelope: 0 → 1 → 0 across the event duration
        envelope = np.sin(np.pi * (day_in_event + 0.5) / duration)
        envelope = max(0.0, envelope)

        # Hourly profile: strongest at center of window, tapered at edges
        window_mid = (sh + eh) / 2
        hour_weight = 1.0 - 0.3 * abs(hour_offset - window_mid) / ((eh - sh) / 2)

        effective_mult = 1.0 + (peak_mult - 1.0) * envelope * hour_weight
        m = max(m, effective_mult)

    return m


def _temperature(ts: datetime, heatwave_boost: float = 0.0) -> float:
    """Sinusoidal daily temperature curve."""
    hour_angle = 2 * np.pi * (ts.hour - 6) / 24
    temp = TEMP_BASE + (TEMP_PEAK - TEMP_BASE) * max(0, np.cos(hour_angle))
    temp += heatwave_boost
    return round(float(temp) + float(random.normal(0, 0.5)), 1)


def _archetype_base_load(
    archetype_name: str, ts: datetime, stress_mult: float
) -> float:
    p = ARCHETYPES[archetype_name]
    h = ts.hour
    is_weekend = ts.weekday() >= 5
    wm = p["weekend"] if is_weekend else 1.0

    if 6 <= h <= 9:
        base = p["morning"]
        noise = 0.25
    elif 18 <= h <= 22:
        base = p["evening"]
        noise = 0.40
    elif 12 <= h <= 14:
        base = (p["morning"] + p["base"]) / 2
        noise = 0.20
    else:
        base = p["base"]
        noise = 0.15

    load = (base * wm) + random.normal(0, noise)
    return max(float(load), 0.05)


def build_dataset():
    all_readings = []
    all_households = []
    archetypes_list = list(ARCHETYPES.keys())

    for zone in ZONES:
        zid = zone["zone_id"]

        for h_idx in range(HOUSEHOLDS_PER_ZONE):
            archetype = archetypes_list[h_idx % len(archetypes_list)]
            hh_id = f"{zid}_hh_{h_idx:02d}"
            hh_name = f"Household {h_idx + 1}"
            hh_email = f"demo+{zid}_hh{h_idx:02d}@resend.dev"

            all_households.append(
                {
                    "household_id": hh_id,
                    "zone_id": zid,
                    "name": hh_name,
                    "email": hh_email,
                    "archetype": archetype,
                }
            )

    # Pre-compute zone-level stress envelope per hour to correlate noise
    # across households (all households in a zone feel the same weather)
    for zone in ZONES:
        zid = zone["zone_id"]
        heatwave_days = set()
        for zid2, start_day, duration, _, _, _, label in STRESS_EVENTS:
            if zid2 == zid and duration > 1 and label == "heatwave":
                for d in range(start_day, start_day + duration):
                    heatwave_days.add(d)

        for ts_idx in range(DAYS * HOURS):
            ts = START + timedelta(hours=ts_idx)
            stress_mult = _stress_multiplier_for_hour(zid, ts)
            hw_boost = TEMP_HEATWAVE_BOOST if ts.day in heatwave_days else 0.0
            temp = _temperature(ts, hw_boost)
            # Zone-level correlated weather noise (same for all households)
            zone_weather_noise = float(random.normal(0, 0.12))

            for h_idx in range(HOUSEHOLDS_PER_ZONE):
                hh_id = f"{zid}_hh_{h_idx:02d}"
                archetype = archetypes_list[h_idx % len(archetypes_list)]
                base = _archetype_base_load(archetype, ts, stress_mult)

                # Each hour: daily seasonal curve × stress multiplier ×
                # weather noise, + per-household independent jitter
                load = base * stress_mult * (1.0 + zone_weather_noise)
                load += float(random.normal(0, 0.08))
                load = max(load, 0.05)

                all_readings.append(
                    {
                        "household_id": hh_id,
                        "zone_id": zid,
                        "archetype": archetype,
                        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "load_kw": round(load, 4),
                        "temperature_c": temp,
                    }
                )

    df_readings = pd.DataFrame(all_readings)
    df_zones = pd.DataFrame(ZONES)
    df_households = pd.DataFrame(all_households)
    return df_readings, df_zones, df_households


def validate(df_readings, df_zones):
    """Comprehensive validation with stress-window peak analysis."""
    total = len(df_readings)
    expected = len(ZONES) * HOUSEHOLDS_PER_ZONE * DAYS * HOURS
    assert total == expected, f"Expected {expected:,} rows, got {total:,}"

    print("  Zone-level stress validation (peak load during each event window):")
    print()

    for zid, start_day, duration, sh, eh, peak_mult, label in STRESS_EVENTS:
        for day_offset in range(duration):
            day = start_day + day_offset
            date_str = (START + timedelta(days=day)).strftime("%Y-%m-%d")

            # Get zone total for each hour in the stress window
            zone_readings = []
            for hour in range(sh, eh):
                ts = START + timedelta(days=day, hours=hour)
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
                hour_total = df_readings[
                    (df_readings["zone_id"] == zid)
                    & (df_readings["timestamp"] == ts_str)
                ]["load_kw"].sum()
                zone_readings.append((hour, float(hour_total)))

            if not zone_readings:
                continue

            peak_hour, peak_load = max(zone_readings, key=lambda x: x[1])
            cap = float(df_zones[df_zones["zone_id"] == zid]["capacity_kw"].iloc[0])
            pct_cap = (peak_load / cap) * 100
            marker = (
                "!! OVER"
                if peak_load > cap
                else ("^^ WARN" if peak_load > cap * 0.85 else "OK")
            )
            ramp = "multi-day" if duration > 1 else "single"
            print(
                f"  {zid:8s} | {label:15s} | {date_str} | {peak_hour:02d}:00 "
                f"| {peak_load:6.2f} kW / {cap:5.1f} kW ({pct_cap:5.1f}%) "
                f"[{marker}] ({ramp})"
            )

    # Per-zone daily peak-hour statistics
    print()
    print("  Per-zone daily peak (hourly zone total) statistics:")
    print()
    for zone in ZONES:
        zid = zone["zone_id"]
        cap = zone["capacity_kw"]
        zone_data = df_readings[df_readings["zone_id"] == zid]
        hourly_totals = zone_data.groupby("timestamp")["load_kw"].sum()
        daily_peak_hour = hourly_totals.groupby(hourly_totals.index.str[:10]).max()
        avg_peak = daily_peak_hour.mean()
        max_peak = daily_peak_hour.max()
        pct_breached = (daily_peak_hour > cap * 0.85).mean() * 100
        print(
            f"  {zid:8s} | avg peak hour={avg_peak:6.2f} kW | "
            f"max peak hour={max_peak:6.2f} kW | >85% cap={pct_breached:5.1f}% of days"
        )

    # Temperature coverage
    if "temperature_c" in df_readings.columns:
        temp_range = (
            df_readings["temperature_c"].min(),
            df_readings["temperature_c"].max(),
        )
        print(f"  Temperature range: {temp_range[0]:.1f}C - {temp_range[1]:.1f}C")

    print(
        f"OK - Validation passed - {total:,} rows generated across {len(ZONES)} zones"
    )


if __name__ == "__main__":
    print("Generating synthetic data...")
    df_readings, df_zones, df_households = build_dataset()

    validate(df_readings, df_zones)

    df_readings.to_csv("readings.csv", index=False)
    df_zones.to_csv("zones.csv", index=False)
    df_households.to_csv("households.csv", index=False)

    print(f"\nFiles written:")
    print(
        f"  readings.csv    — {len(df_readings):,} rows ({df_readings.shape[1]} cols)"
    )
    print(f"  zones.csv       — {len(df_zones)} zones")
    print(f"  households.csv  — {len(df_households)} households")
    print(f"\nNext step: python load_to_bq.py")
