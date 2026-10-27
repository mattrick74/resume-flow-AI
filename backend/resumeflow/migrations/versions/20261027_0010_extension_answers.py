"""v2.0 Chrome extension: job source/site, the answer bank and filled application answers

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("site", sa.String(length=100), nullable=True))

    op.create_table(
        "answer_bank",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], name=op.f("fk_answer_bank_candidate_id_candidates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_bank")),
        sa.UniqueConstraint("candidate_id", "key", name=op.f("uq_answer_bank_candidate_id_key")),
    )
    with op.batch_alter_table("answer_bank", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_answer_bank_candidate_id"), ["candidate_id"], unique=False)

    op.create_table(
        "application_answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("field_id", sa.String(length=300), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("field_type", sa.String(length=30), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], name=op.f("fk_application_answers_application_id_applications"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_answers")),
    )
    with op.batch_alter_table("application_answers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_application_answers_application_id"), ["application_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("application_answers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_application_answers_application_id"))
    op.drop_table("application_answers")
    with op.batch_alter_table("answer_bank", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_answer_bank_candidate_id"))
    op.drop_table("answer_bank")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("site")
        batch_op.drop_column("source")
