from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from resumeflow import __version__
from resumeflow.db.migrate import head_revision

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    schema_revision: str | None
    schema_head: str | None


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Unauthenticated readiness probe for the desktop shell; exposes no user data."""
    with request.app.state.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    # Migrations only run at startup, so the revision recorded then is current.
    revision = request.app.state.schema_revision
    head = head_revision()
    return HealthResponse(
        status="ok" if revision == head else "degraded",
        version=__version__,
        schema_revision=revision,
        schema_head=head,
    )
