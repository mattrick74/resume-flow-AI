"""auto export and writing instructions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 12:00:00

Who approved an application (operator or the export-automatically setting) and the operator's
own instructions for the AI writer.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("approved_by", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("instructions", sa.Text(), nullable=True))
    # Everything approved so far was approved by a person.
    op.execute("UPDATE applications SET approved_by = 'operator' WHERE approved_at IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_column("instructions")
        batch_op.drop_column("approved_by")
