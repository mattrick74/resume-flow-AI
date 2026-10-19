"""skill display names

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14 17:05:00

Skills auto-detected by early versions were stored with their lowercase canonical
names ("postgresql", "etl"). Give them proper display names ("PostgreSQL", "ETL").
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from resumeflow.domain.skills import display_name

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, skill_name, normalized_name FROM candidate_skills")).fetchall()
    for skill_id, name, normalized in rows:
        if name == normalized and name.islower():
            connection.execute(
                sa.text("UPDATE candidate_skills SET skill_name = :name WHERE id = :id"),
                {"name": display_name(normalized), "id": skill_id},
            )


def downgrade() -> None:
    pass  # display-only change; nothing to undo
