"""renormalize skill names

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18 15:00:00

Earlier versions stripped a leading dot when normalizing skill names, so ".NET" was stored as "net" and never matched a
job asking for ".NET" or ".NET Core". Recompute the normalized names of stored skills and skill answers from their
display names. A row whose new name already exists for the same candidate is left as it is.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from resumeflow.domain.skills import normalize_skill

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    for table in ("candidate_skills", "candidate_skill_evidence"):
        rows = connection.execute(sa.text(f"SELECT id, candidate_id, skill_name, normalized_name FROM {table}")).fetchall()
        taken = {(candidate_id, normalized) for _, candidate_id, _, normalized in rows}
        for row_id, candidate_id, name, normalized in rows:
            fresh = normalize_skill(name or "")
            if not fresh or fresh == normalized or (candidate_id, fresh) in taken:
                continue
            connection.execute(sa.text(f"UPDATE {table} SET normalized_name = :value WHERE id = :id"), {"value": fresh, "id": row_id})
            taken.discard((candidate_id, normalized))
            taken.add((candidate_id, fresh))


def downgrade() -> None:
    pass  # a data repair; nothing to undo
