"""profiles review and export metadata

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14 15:30:46.656507
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('pdf_status', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('profile_json', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('profile_reviewed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('consent_confirmed_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('warnings_json', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('origin', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requirements_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('warnings_json', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('analyzed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('analyzed_at')
        batch_op.drop_column('warnings_json')
        batch_op.drop_column('requirements_source')

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('approved_at')
        batch_op.drop_column('origin')
        batch_op.drop_column('warnings_json')

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('consent_confirmed_at')
        batch_op.drop_column('profile_reviewed_at')
        batch_op.drop_column('profile_json')

    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_column('approved_at')
        batch_op.drop_column('pdf_status')
        batch_op.drop_column('error')
