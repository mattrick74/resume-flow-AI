"""Database backup and restore (PRD §13, §15) using SQLite's online backup API."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from resumeflow.config import AppConfig
from resumeflow.db.migrate import upgrade_to_head
from resumeflow.errors import AppError
from resumeflow.services.files import ensure_within

logger = logging.getLogger(__name__)
_NAME = re.compile(r"^resumeflow-\d{8}-\d{6}(?:-[a-z]+)?(?:-\d+)?\.db$")


def _copy_live(engine: Engine, target: Path) -> None:
    raw = engine.raw_connection()
    try:
        destination = sqlite3.connect(target)
        try:
            raw.driver_connection.backup(destination)  # type: ignore[union-attr]
        finally:
            destination.close()
    finally:
        raw.close()


def list_backups(config: AppConfig) -> list[dict]:
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for path in config.backups_dir.glob("resumeflow-*.db"):
        if _NAME.match(path.name):
            stat = path.stat()
            items.append({"name": path.name, "size_bytes": stat.st_size, "created_at": datetime.fromtimestamp(stat.st_mtime).astimezone()})
    return sorted(items, key=lambda i: i["created_at"], reverse=True)


def _prune(config: AppConfig, keep: int) -> None:
    regular = [b for b in list_backups(config) if "-prerestore" not in b["name"]]
    for old in regular[keep:]:
        (config.backups_dir / old["name"]).unlink(missing_ok=True)


def create_backup(engine: Engine, config: AppConfig, retention: int, label: str | None = None) -> dict:
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"resumeflow-{stamp}" + (f"-{label}" if label else "")
    target = config.backups_dir / f"{base}.db"
    counter = 1
    while target.exists():
        counter += 1
        target = config.backups_dir / f"{base}-{counter}.db"
    _copy_live(engine, target)
    _prune(config, retention)
    stat = target.stat()
    logger.info("Created database backup %s", target.name)
    return {"name": target.name, "size_bytes": stat.st_size, "created_at": datetime.fromtimestamp(stat.st_mtime).astimezone()}


def restore_backup(engine: Engine, config: AppConfig, name: str, retention: int) -> dict:
    if not _NAME.match(name):
        raise AppError(422, "invalid_backup_name", "Unknown backup file")
    source = ensure_within(config.backups_dir, config.backups_dir / name)
    if not source.exists():
        raise AppError(404, "backup_not_found", "Backup not found")
    check = sqlite3.connect(source)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise AppError(422, "backup_corrupt", "That backup failed an integrity check and wasn't restored.")
    finally:
        check.close()
    safety = create_backup(engine, config, retention, label="prerestore")
    engine.dispose()
    raw = engine.raw_connection()
    try:
        backup_source = sqlite3.connect(source)
        try:
            backup_source.backup(raw.driver_connection)  # type: ignore[arg-type]
        finally:
            backup_source.close()
    finally:
        raw.close()
    upgrade_to_head(engine)  # an older backup may predate the current schema
    logger.warning("Database restored from %s (safety copy %s)", name, safety["name"])
    return {"restored": name, "safety_backup": safety["name"]}


def auto_backup_if_due(engine: Engine, config: AppConfig, retention: int, every: timedelta = timedelta(hours=24)) -> None:
    latest = next((b for b in list_backups(config) if "-prerestore" not in b["name"]), None)
    if latest is None or datetime.now().astimezone() - latest["created_at"] > every:
        try:
            create_backup(engine, config, retention)
        except Exception:
            logger.exception("Automatic backup failed")
