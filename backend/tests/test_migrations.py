from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from resumeflow.db.migrate import alembic_config, current_revision, head_revision, upgrade_to_head
from resumeflow.db.models import Base
from resumeflow.db.session import create_db_engine

PRD_TABLES = {
    "candidates",
    "resume_versions",
    "candidate_skills",
    "jobs",
    "candidate_matches",
    "applications",
    "documents",
    "generation_runs",
    "processing_jobs",
    "settings",
}


@pytest.fixture
def engine(tmp_path):
    engine = create_db_engine(tmp_path / "test.db")
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


def test_upgrade_creates_every_prd_table(engine):
    assert PRD_TABLES <= set(inspect(engine).get_table_names())
    assert current_revision(engine) == head_revision()


def test_migrations_match_the_orm_models(engine):
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_required_indexes_exist(engine):
    inspector = inspect(engine)

    def indexed_columns(table: str) -> set[str]:
        return {col for ix in inspector.get_indexes(table) for col in ix["column_names"]}

    assert {"normalized_url", "jd_hash"} <= indexed_columns("jobs")
    assert {"application_date", "status", "candidate_id"} <= indexed_columns("applications")


def test_sqlite_pragmas_are_applied(engine):
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_foreign_keys_are_enforced(engine):
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resume_versions (id, candidate_id, version, original_filename, "
                "original_file_path, file_sha256, mime_type, verification_status, created_at) "
                "VALUES ('r1', 'missing', 1, 'a.pdf', 'x', 'h', 'application/pdf', 'pending_review', "
                "'2026-01-01 00:00:00')"
            )
        )


def test_upgrade_is_idempotent_and_downgrade_round_trips(engine):
    upgrade_to_head(engine)  # second launch must be a no-op

    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert not (PRD_TABLES & set(inspect(engine).get_table_names()))

    upgrade_to_head(engine)
    assert PRD_TABLES <= set(inspect(engine).get_table_names())
