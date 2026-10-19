"""Run Alembic migrations programmatically at sidecar launch (PRD section 15)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config() -> Config:
    # No sqlalchemy.url: callers always hand env.py a live connection (see
    # config.attributes["connection"]) so the SQLite pragmas apply.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def upgrade_to_head(engine: Engine) -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@cache
def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()
