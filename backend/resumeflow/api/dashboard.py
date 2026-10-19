from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from resumeflow.db.session import get_db
from resumeflow.domain.enums import PipelineStatus
from resumeflow.services.ai.service import budget_status
from resumeflow.services.dashboard import build_summary
from resumeflow.services.settings import load_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class ActivityItem(BaseModel):
    application_id: str
    candidate_name: str
    company: str | None
    title: str | None
    status: PipelineStatus
    updated_at: datetime


class BudgetStatus(BaseModel):
    today_usd: float
    month_usd: float
    daily_budget_usd: float | None
    monthly_budget_usd: float | None
    daily_exceeded: bool
    monthly_exceeded: bool
    daily_warning: bool
    monthly_warning: bool


class DashboardSummary(BaseModel):
    date: date
    daily_goal: int
    processing_failed: int
    candidates_total: int
    candidates_ready: int
    saved_jobs: int
    jobs_needing_confirmation: int  # v1.8: waiting for answers about missing job skills
    jobs_failed: int
    budget: BudgetStatus
    jobs_today: int
    generated_today: int
    exported_today: int
    failed_today: int
    awaiting_review: int
    queue_depth: int
    cost_today_usd: float
    avg_generation_ms: int | None
    recent_activity: list[ActivityItem]


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(db: Annotated[Session, Depends(get_db)]) -> dict:
    settings = load_settings(db)
    return {**build_summary(db, daily_goal=settings.daily_goal), "budget": budget_status(db, settings)}
