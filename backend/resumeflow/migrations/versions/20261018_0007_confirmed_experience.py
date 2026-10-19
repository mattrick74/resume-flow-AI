"""confirmed experience

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18 09:00:00

v1.8: the operator's answers about skills a job asked for, role-level experience records with the bullets written
from them, two match scores, paused-for-confirmation jobs and per-application provenance. Purely additive: no
existing row is changed, so 1.7 data and applications are kept as they are.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_skill_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("skill_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("decision", sa.Enum("used", "not_used", "unsure", name="skilldecision", native_enum=False, create_constraint=False, length=32), nullable=False),
        sa.Column("role_ids_json", sa.JSON(), nullable=False),
        sa.Column("proficiency", sa.String(length=20), nullable=True),
        sa.Column("years", sa.Double(), nullable=True),
        sa.Column("last_used", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_job_ids_json", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], name=op.f("fk_candidate_skill_evidence_candidate_id_candidates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_skill_evidence")),
        sa.UniqueConstraint("candidate_id", "normalized_name", name=op.f("uq_candidate_skill_evidence_candidate_id_normalized_name")),
    )
    with op.batch_alter_table("candidate_skill_evidence", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_candidate_skill_evidence_candidate_id"), ["candidate_id"], unique=False)

    op.create_table(
        "candidate_role_experience",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=40), nullable=False),
        sa.Column("role_label", sa.String(length=500), nullable=False),
        sa.Column("skills_json", sa.JSON(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("project", sa.String(length=300), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("suggested", "approved", "rejected", name="additionstatus", native_enum=False, create_constraint=False, length=32), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("drafted_by", sa.String(length=20), nullable=True),
        sa.Column("issues_json", sa.JSON(), nullable=False),
        sa.Column("source_job_ids_json", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], name=op.f("fk_candidate_role_experience_candidate_id_candidates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate_role_experience")),
    )
    with op.batch_alter_table("candidate_role_experience", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_candidate_role_experience_candidate_id"), ["candidate_id"], unique=False)

    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("profile_revision", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("candidate_matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("potential_score", sa.Double(), nullable=True))
        batch_op.add_column(sa.Column("previous_score", sa.Double(), nullable=True))
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confirmation_requested_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("confirmation_resolved_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("profile_revision", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("addition_ids_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("coverage_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_column("coverage_json")
        batch_op.drop_column("addition_ids_json")
        batch_op.drop_column("profile_revision")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("confirmation_resolved_at")
        batch_op.drop_column("confirmation_requested_at")
    with op.batch_alter_table("candidate_matches", schema=None) as batch_op:
        batch_op.drop_column("previous_score")
        batch_op.drop_column("potential_score")
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_column("profile_revision")
    with op.batch_alter_table("candidate_role_experience", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_role_experience_candidate_id"))
    op.drop_table("candidate_role_experience")
    with op.batch_alter_table("candidate_skill_evidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_skill_evidence_candidate_id"))
    op.drop_table("candidate_skill_evidence")
