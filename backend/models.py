"""
models.py — Pydantic schemas for request/response typing
Owner: Mrinmoy

Used by FastAPI for automatic validation + OpenAPI docs.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ─── Zones ────────────────────────────────────────────────────────────────────


class ZoneOut(BaseModel):
    id: str
    name: str
    bq_zone_id: str
    household_count: Optional[int]
    baseline_capacity_kw: float


# ─── Forecasts ────────────────────────────────────────────────────────────────


class ForecastPoint(BaseModel):
    hour: str  # formatted label for Recharts
    actual: Optional[float]
    predicted: Optional[float]


# ─── Stress Events ────────────────────────────────────────────────────────────


class StressEventOut(BaseModel):
    id: str
    zone_id: str
    zone_name: Optional[str] = None  # joined from zones table
    detected_at: str
    window_start: Optional[str]
    window_end: Optional[str]
    severity: str
    predicted_peak_kw: float
    capacity_kw: float
    reasoning: Optional[str]


# ─── Recommendations ──────────────────────────────────────────────────────────


class RecommendationOut(BaseModel):
    id: str
    stress_event_id: str
    target_type: str
    household_id: Optional[str]
    bq_household_id: Optional[str]
    message: Optional[str]
    action_suggested: Optional[str]
    sent: bool
    sent_at: Optional[str]
    created_at: str


# ─── Simulation ───────────────────────────────────────────────────────────────


class ZoneAdvanceResult(BaseModel):
    zone_name: str
    bq_zone_id: str
    stress_detected: bool
    severity: Optional[str] = None
    reasoning: Optional[str] = None
    nudges_generated: int = 0


class AdvanceResponse(BaseModel):
    new_day: int
    results: list[ZoneAdvanceResult]


# ─── Admin / seed ─────────────────────────────────────────────────────────────


class SeedResponse(BaseModel):
    message: str
    households_seeded: Optional[int] = None


# ─── Chat ──────────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    question: str
    household_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str


# ─── Ingestion ─────────────────────────────────────────────────────────────────


class IngestResponse(BaseModel):
    status: str  # "ok" | "partial" | "error"
    rows_received: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ─── Backtesting ───────────────────────────────────────────────────────────────


class BacktestDay(BaseModel):
    train_day: int
    test_day: int
    hours_compared: int
    mape_pct: float
    rmse_kw: float


class BacktestResponse(BaseModel):
    zone_id: str
    days_evaluated: int
    overall_mape_pct: Optional[float] = None
    overall_rmse_kw: Optional[float] = None
    daily: list[BacktestDay] = Field(default_factory=list)
    note: Optional[str] = None
