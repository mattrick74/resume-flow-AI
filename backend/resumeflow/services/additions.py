"""Approved, operator-confirmed experience as profile facts (v1.8 CAN-13/16, VAL-05/06).

Low level on purpose (models and domain only) so the candidate snapshot can include approved additions without
importing the rest of the experience workflow. The imported resume and the extracted profile are never changed:
additions live in their own table and are merged into the snapshot, each bullet keeping its record id as provenance.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.db.models import CandidateRoleExperience, CandidateSkillEvidence
from resumeflow.domain.enums import AdditionStatus, SkillDecision
from resumeflow.domain.profile import CandidateProfile, Role
from resumeflow.domain.skills import normalize_skill


def role_label(role: Role | dict[str, Any]) -> str:
    get = role.get if isinstance(role, dict) else lambda key, default=None: getattr(role, key, default)
    return " | ".join([(get("employer") or "").strip(), (get("title") or "").strip(), (get("start") or "").strip()])[:500]


def _same_label(a: str, b: str) -> bool:
    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9|]+", "", value.lower())

    return norm(a) == norm(b)


def resolve_role(profile: CandidateProfile, role_id: str, label: str) -> Role | None:
    """The role a record belongs to: by id, or by employer/title/start after the resume was read again."""
    for role in profile.employment:
        if role.id == role_id:
            return role
    for role in profile.employment:
        if _same_label(role_label(role), label):
            return role
    return None


def approved_records(db: Session, candidate_id: str) -> list[CandidateRoleExperience]:
    return list(
        db.scalars(
            select(CandidateRoleExperience)
            .where(CandidateRoleExperience.candidate_id == candidate_id, CandidateRoleExperience.status == AdditionStatus.APPROVED)
            .order_by(CandidateRoleExperience.approved_at, CandidateRoleExperience.created_at)
        )
    )


def additions_by_role(db: Session, candidate_id: str, profile: CandidateProfile) -> dict[str, list[dict[str, Any]]]:
    """role id -> approved bullets ({id, text, origin}) for that role. Records whose role no longer exists are left out."""
    found: dict[str, list[dict[str, Any]]] = {}
    for record in approved_records(db, candidate_id):
        role = resolve_role(profile, record.role_id, record.role_label)
        if role is None or not (record.narrative or "").strip():
            continue
        found.setdefault(role.id, []).append({"id": record.id, "text": record.narrative.strip(), "origin": "confirmed"})
    return found


def role_refs(profile: CandidateProfile, role_ids: list[str]) -> list[dict[str, str]]:
    """How a skill answer stores its roles: id plus label, so it survives the resume being read again."""
    roles = {r.id: r for r in profile.employment}
    return [{"id": rid, "label": role_label(roles[rid])} for rid in dict.fromkeys(role_ids) if rid in roles]


def resolve_refs(profile: CandidateProfile, refs: list[Any]) -> list[Role]:
    found: list[Role] = []
    for ref in refs or []:
        rid, label = (ref.get("id", ""), ref.get("label", "")) if isinstance(ref, dict) else (str(ref), "")
        role = resolve_role(profile, rid, label)
        if role is not None and role not in found:
            found.append(role)
    return found


def confirmed_skill_roles(db: Session, candidate_id: str, profile: CandidateProfile) -> dict[str, list[str]]:
    """normalized skill -> current role ids where the operator confirmed using it ("Have used" + role, or an approved record)."""
    found: dict[str, list[str]] = {}
    for evidence in db.scalars(
        select(CandidateSkillEvidence).where(CandidateSkillEvidence.candidate_id == candidate_id, CandidateSkillEvidence.decision == SkillDecision.USED)
    ):
        found[evidence.normalized_name] = [r.id for r in resolve_refs(profile, evidence.role_ids_json)]
    for record in approved_records(db, candidate_id):
        role = resolve_role(profile, record.role_id, record.role_label)
        if role is None:
            continue
        for skill in record.skills_json or []:
            roles = found.setdefault(normalize_skill(skill), [])
            if role.id not in roles:
                roles.append(role.id)
    return found


def remap_records(db: Session, candidate_id: str, profile: CandidateProfile) -> int:
    """After a resume is read again (new role ids), point records and skill answers at the matching new roles."""
    moved = 0
    for record in db.scalars(select(CandidateRoleExperience).where(CandidateRoleExperience.candidate_id == candidate_id)):
        role = resolve_role(profile, record.role_id, record.role_label)
        if role is not None and role.id != record.role_id:
            record.role_id = role.id
            moved += 1
    for evidence in db.scalars(select(CandidateSkillEvidence).where(CandidateSkillEvidence.candidate_id == candidate_id)):
        refs = [{"id": r.id, "label": role_label(r)} for r in resolve_refs(profile, evidence.role_ids_json)]
        if refs and refs != evidence.role_ids_json:
            evidence.role_ids_json = refs
            moved += 1
    return moved
