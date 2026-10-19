"""Analytics and backups."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from resumeflow.api.deps import Config, DbSession
from resumeflow.services import backups
from resumeflow.services.analytics import analytics
from resumeflow.services.settings import load_settings

router = APIRouter(tags=["operations"])


class RestoreRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/analytics")
def get_analytics(db: DbSession, days: int = 30) -> dict[str, Any]:
    return analytics(db, days)


@router.get("/backups")
def list_backups(config: Config) -> list[dict[str, Any]]:
    return backups.list_backups(config)


@router.post("/backups", status_code=201)
def create_backup(request: Request, db: DbSession, config: Config) -> dict[str, Any]:
    retention = load_settings(db).backup_retention_count
    db.close()
    return backups.create_backup(request.app.state.engine, config, retention)


@router.post("/backups/restore")
def restore_backup(body: RestoreRequest, request: Request, db: DbSession, config: Config) -> dict[str, Any]:
    retention = load_settings(db).backup_retention_count
    db.close()
    queue = request.app.state.queue
    queue.pause()
    try:
        result = backups.restore_backup(request.app.state.engine, config, body.name, retention)
        queue.recover()
    finally:
        queue.resume()
    return result
