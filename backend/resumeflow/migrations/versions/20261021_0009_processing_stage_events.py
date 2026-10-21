"""processing stage completion events

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processing_stage_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("processing_job_id", sa.String(length=36), nullable=True),
        sa.Column("application_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_processing_stage_events_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_processing_stage_events_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_processing_stage_events_processing_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_stage_events")),
    )
    with op.batch_alter_table("processing_stage_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_processing_stage_events_application_id"), ["application_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_processing_stage_events_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_processing_stage_events_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_processing_stage_events_processing_job_id"), ["processing_job_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("processing_stage_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_processing_stage_events_processing_job_id"))
        batch_op.drop_index(batch_op.f("ix_processing_stage_events_job_id"))
        batch_op.drop_index(batch_op.f("ix_processing_stage_events_created_at"))
        batch_op.drop_index(batch_op.f("ix_processing_stage_events_application_id"))
    op.drop_table("processing_stage_events")
