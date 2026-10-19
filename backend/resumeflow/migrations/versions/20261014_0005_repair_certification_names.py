"""repair certification names

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 18:40:00

Earlier versions of the resume parser removed a closing parenthesis together with the
year, turning "Certified Kubernetes Administrator (CKA), 2021" into "... (CKA". Restore the
missing bracket in stored profiles and extractions. Nothing else about the facts changes,
so their verified flags stay as they are.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from resumeflow.services.resume_parser import balance_parentheses

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repair(profile: dict) -> bool:
    changed = False
    for cert in profile.get("certifications") or []:
        name = cert.get("name")
        if isinstance(name, str) and name.count("(") > name.count(")"):
            fixed = balance_parentheses(name)
            if fixed and fixed != name:
                cert["name"] = fixed
                changed = True
    return changed


def upgrade() -> None:
    connection = op.get_bind()
    for table, column in (("candidates", "profile_json"), ("resume_versions", "parsed_profile_json")):
        rows = connection.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
        for row_id, raw in rows:
            profile = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(profile, dict) and _repair(profile):
                connection.execute(sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"), {"value": json.dumps(profile), "id": row_id})


def downgrade() -> None:
    pass  # a data repair; nothing to undo
