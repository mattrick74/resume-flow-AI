"""CandidateService (PRD §5): import, versioning, verification and isolation.

Every query here is scoped by candidate id; nothing returns or mixes another
candidate's data.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resumeflow.config import AppConfig
from resumeflow.db.models import Application, Candidate, CandidateSkill, CandidateSkillEvidence, ResumeVersion
from resumeflow.domain.enums import CandidateStatus, ResumeReviewStatus, VerificationStatus
from resumeflow.domain.experience import sort_key, total_years
from resumeflow.domain.profile import CandidatePreferences, CandidateProfile, preferences_from_json, profile_from_json
from resumeflow.domain.skills import display_name, normalize_skill
from resumeflow.errors import AppError
from resumeflow.services.ai.provider import AIError
from resumeflow.services.additions import additions_by_role, confirmed_skill_roles, remap_records
from resumeflow.services.ai.service import AIService
from resumeflow.services.files import ensure_within, sanitize_component, sha256_bytes, write_new_file
from resumeflow.services.resume_parser import (
    MIME_TYPES,
    ExtractedResume,
    ExtractedSkill,
    detect_format,
    extract_text,
    parse_resume,
)
from resumeflow.services.settings import load_settings

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def get_candidate(db: Session, candidate_id: str) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError(404, "candidate_not_found", "Candidate not found")
    return candidate


def _name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    words = re.split(r"[\s_.\-]+", stem)
    noise = {"resume", "cv", "final", "updated", "latest", "new", "copy", "draft", "v1", "v2", "v3"}
    kept = [w for w in words if w and w.lower() not in noise and not re.fullmatch(r"\d+", w)]
    return " ".join(w.capitalize() for w in kept)[:200] or "Unnamed candidate"


# ---------------------------------------------------------------- reads


def skill_counts(db: Session, candidate_ids: list[str] | None = None) -> dict[str, dict[str, int]]:
    query = select(CandidateSkill.candidate_id, CandidateSkill.verification_status, func.count()).group_by(
        CandidateSkill.candidate_id, CandidateSkill.verification_status
    )
    if candidate_ids is not None:
        query = query.where(CandidateSkill.candidate_id.in_(candidate_ids))
    counts: dict[str, dict[str, int]] = {}
    for candidate_id, status, count in db.execute(query):
        counts.setdefault(candidate_id, {s.value: 0 for s in VerificationStatus})[status.value] = count
    return counts


def usable_skill_count(stats: dict[str, int], require_verification: bool) -> int:
    """Skills that reach resumes: verified ones, or every one not rejected when verification is off."""
    return stats.get("verified", 0) + (0 if require_verification else stats.get("unverified", 0))


def readiness(candidate: Candidate, skill_count: int, *, require_verification: bool) -> dict[str, Any]:
    """``skill_count`` is ``usable_skill_count`` for the same setting."""
    profile = profile_from_json(candidate.profile_json)
    blockers = []
    if candidate.status != CandidateStatus.ACTIVE:
        blockers.append("Candidate is archived")
    if candidate.consent_confirmed_at is None:
        blockers.append("Candidate authorization not confirmed")
    if not require_verification:
        roles = len(profile.employment)
        if roles == 0:
            blockers.append("No work experience: add at least one role so resumes have an Experience section")
        warnings = []
        empty_roles = sum(1 for r in profile.employment if not r.bullets)
        if empty_roles:
            warnings.append(f"{empty_roles} role(s) have no bullets, so they appear without details")
        if skill_count == 0:
            warnings.append("No skills yet, so resumes will have no Skills section")
        return {"verified_roles": roles, "verified_skills": skill_count, "ready_for_matching": not blockers, "blockers": blockers, "warnings": warnings}
    verified_roles = sum(1 for r in profile.employment if r.verified)
    if candidate.profile_reviewed_at is None:
        blockers.append("Profile review not completed")
    if verified_roles == 0:
        blockers.append("No verified work experience: verify at least one role so resumes have an Experience section")
    # Not blockers, but each one leaves something out of every resume.
    warnings = []
    unverified_roles = len(profile.employment) - verified_roles
    if unverified_roles:
        warnings.append(f"{unverified_roles} role(s) aren't verified and won't appear on resumes")
    empty_roles = sum(1 for r in profile.employment if r.verified and not any(b.verified for b in r.bullets))
    if empty_roles:
        warnings.append(f"{empty_roles} verified role(s) have no verified bullets, so they appear without details")
    if profile.education and not any(e.verified for e in profile.education):
        warnings.append("Education isn't verified, so it won't appear on resumes")
    unverified_certs = sum(1 for c in profile.certifications if not c.verified)
    if unverified_certs:
        warnings.append(f"{unverified_certs} certification(s) aren't verified and won't appear on resumes")
    if profile.summary and not profile.summary_verified:
        warnings.append("The summary isn't verified; resumes will use one written from verified facts only")
    if skill_count == 0:
        warnings.append("No skills are verified, so resumes will have no Skills section")
    return {
        "verified_roles": verified_roles,
        "verified_skills": skill_count,
        "ready_for_matching": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def candidate_readiness(db: Session, candidate: Candidate) -> dict[str, Any]:
    require = load_settings(db).require_verification
    stats = skill_counts(db, [candidate.id]).get(candidate.id, {})
    return readiness(candidate, usable_skill_count(stats, require), require_verification=require)


def list_candidates(db: Session, status: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Candidate)
    if status in {s.value for s in CandidateStatus}:
        stmt = stmt.where(Candidate.status == CandidateStatus(status))
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(Candidate.full_name.ilike(like) | Candidate.email.ilike(like))
    candidates = list(db.scalars(stmt.order_by(Candidate.full_name)))
    ids = [c.id for c in candidates]
    counts = skill_counts(db, ids)
    require = load_settings(db).require_verification
    versions = dict(
        db.execute(
            select(ResumeVersion.candidate_id, func.max(ResumeVersion.version))
            .where(ResumeVersion.candidate_id.in_(ids))
            .group_by(ResumeVersion.candidate_id)
        ).all()
    )
    applications = dict(
        db.execute(
            select(Application.candidate_id, func.count())
            .where(Application.candidate_id.in_(ids))
            .group_by(Application.candidate_id)
        ).all()
    )
    items = []
    for c in candidates:
        skill_stats = counts.get(c.id, {s.value: 0 for s in VerificationStatus})
        items.append(
            {
                "id": c.id,
                "full_name": c.full_name,
                "email": c.email,
                "phone": c.phone,
                "location": c.location,
                "status": c.status,
                "latest_version": versions.get(c.id),
                "skills": skill_stats,
                "reviewed": c.profile_reviewed_at is not None,
                "consent_confirmed": c.consent_confirmed_at is not None,
                "applications": applications.get(c.id, 0),
                "readiness": readiness(c, usable_skill_count(skill_stats, require), require_verification=require),
                "updated_at": c.updated_at,
            }
        )
    return items


def _skill_dict(skill: CandidateSkill, answer: CandidateSkillEvidence | None = None, roles: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": skill.id,
        "skill_name": skill.skill_name,
        "normalized_name": skill.normalized_name,
        "evidence": skill.evidence,
        "verification_status": skill.verification_status,
        "source_resume_version_id": skill.source_resume_version_id,
        "updated_at": skill.updated_at,
        # v1.8 inventory: the operator's answer (used / not_used / unsure) and where it was used.
        "decision": answer.decision if answer else None,
        "decided_at": answer.decided_at if answer else None,
        "role_ids": roles or [],
        "years": answer.years if answer else None,
        "proficiency": answer.proficiency if answer else None,
        "last_used": answer.last_used if answer else None,
    }


def _version_dict(version: ResumeVersion, *, include_text: bool = False) -> dict[str, Any]:
    parsed = version.parsed_profile_json or {}
    data = {
        "id": version.id,
        "version": version.version,
        "original_filename": version.original_filename,
        "original_file_path": version.original_file_path,
        "mime_type": version.mime_type,
        "file_sha256": version.file_sha256,
        "verification_status": version.verification_status,
        "extraction_source": parsed.get("source"),
        "extraction_warnings": parsed.get("warnings", []),
        "created_at": version.created_at,
    }
    if include_text:
        data["extracted_text"] = version.extracted_text
        data["parsed_profile"] = parsed
    return data


def candidate_detail(db: Session, candidate: Candidate) -> dict[str, Any]:
    skills = list(
        db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id).order_by(CandidateSkill.skill_name))
    )
    versions = list(
        db.scalars(
            select(ResumeVersion).where(ResumeVersion.candidate_id == candidate.id).order_by(ResumeVersion.version.desc())
        )
    )
    profile = profile_from_json(candidate.profile_json)
    require = load_settings(db).require_verification
    usable = {VerificationStatus.VERIFIED} if require else {VerificationStatus.VERIFIED, VerificationStatus.UNVERIFIED}
    usable_skills = sum(1 for s in skills if s.verification_status in usable)
    application_count = db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == candidate.id))
    latest = versions[0] if versions else None
    answers = {e.normalized_name: e for e in db.scalars(select(CandidateSkillEvidence).where(CandidateSkillEvidence.candidate_id == candidate.id))}
    skill_roles = confirmed_skill_roles(db, candidate.id, profile)
    return {
        "id": candidate.id,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "phone": candidate.phone,
        "location": candidate.location,
        "status": candidate.status,
        "profile": profile.model_dump(),
        "preferences": preferences_from_json(candidate.preferences_json).model_dump(),
        "skills": [_skill_dict(s, answers.get(s.normalized_name), skill_roles.get(s.normalized_name)) for s in skills],
        "resume_versions": [_version_dict(v) for v in versions],
        "profile_revision": candidate.profile_revision,
        "profile_reviewed_at": candidate.profile_reviewed_at,
        "consent_confirmed_at": candidate.consent_confirmed_at,
        "newer_extraction_available": bool(latest and profile.source_version_id and profile.source_version_id != latest.id),
        "applications": application_count or 0,
        "readiness": readiness(candidate, usable_skills, require_verification=require),
        "years_experience": total_years([(r.start, r.end) for r in profile.employment if r.verified or not require]),
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }


# ---------------------------------------------------------------- import


def extract_resume(ai: AIService | None, db: Session, text: str, *, prefer_ai: bool = False) -> ExtractedResume:
    """Offline parse, upgraded to AI extraction when a key is set (on import only if the setting allows it)."""
    heuristic = parse_resume(text)
    settings = load_settings(db)
    if ai is None or not (prefer_ai or settings.ai_extract_on_import) or not ai.available():
        return heuristic
    try:
        from resumeflow.services.ai.extraction import ai_extract_resume

        extracted = ai_extract_resume(ai, text)
    except AIError as exc:
        heuristic.warnings.append(f"AI extraction failed, used offline parsing instead: {exc}")
        return heuristic
    # Keep contact details the offline parser found if the model missed them.
    extracted.email = extracted.email or heuristic.email
    extracted.phone = extracted.phone or heuristic.phone
    extracted.location = extracted.location or heuristic.location
    extracted.full_name = extracted.full_name or heuristic.full_name
    return extracted


def _merge_skills(db: Session, candidate_id: str, skills: list[ExtractedSkill], version_id: str) -> int:
    existing = {
        s.normalized_name: s for s in db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate_id))
    }
    added = 0
    for skill in skills:
        key = normalize_skill(skill.name)
        if not key or len(key) > 200:
            continue
        if key in existing:
            # Never un-reject or auto-verify; only fill in missing evidence.
            if not existing[key].evidence and skill.evidence:
                existing[key].evidence = skill.evidence
            continue
        new = CandidateSkill(
            candidate_id=candidate_id,
            skill_name=display_name(key, skill.name)[:200],
            normalized_name=key,
            evidence=skill.evidence,
            source_resume_version_id=version_id,
            verification_status=VerificationStatus.UNVERIFIED,
        )
        db.add(new)
        existing[key] = new
        added += 1
    return added


def import_resume(
    db: Session,
    config: AppConfig,
    ai: AIService | None,
    *,
    filename: str,
    data: bytes,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    fmt = detect_format(filename, data)
    digest = sha256_bytes(data)
    duplicate = db.execute(
        select(ResumeVersion, Candidate.full_name)
        .join(Candidate, Candidate.id == ResumeVersion.candidate_id)
        .where(ResumeVersion.file_sha256 == digest)
    ).first()
    if duplicate:
        version, name = duplicate
        raise AppError(
            409,
            "duplicate_resume",
            f"This exact file was already imported for {name} (version {version.version}).",
            {"candidate_id": version.candidate_id, "candidate_name": name},
        )

    text = extract_text(fmt, data)
    extracted = extract_resume(ai, db, text)

    created = candidate_id is None
    if created:
        candidate = Candidate(
            full_name=(extracted.full_name or _name_from_filename(filename))[:200],
            email=extracted.email,
            phone=extracted.phone,
            location=extracted.location,
        )
        db.add(candidate)
        db.flush()
    else:
        candidate = get_candidate(db, candidate_id)  # type: ignore[arg-type]
        candidate.email = candidate.email or extracted.email
        candidate.phone = candidate.phone or extracted.phone
        candidate.location = candidate.location or extracted.location

    next_version = (
        db.scalar(select(func.max(ResumeVersion.version)).where(ResumeVersion.candidate_id == candidate.id)) or 0
    ) + 1
    folder = config.originals_dir / candidate.id
    stored_name = f"v{next_version}_{sanitize_component(Path(filename).stem, max_length=80)}.{fmt}"
    path = write_new_file(folder / stored_name, data)

    try:
        version = ResumeVersion(
            candidate_id=candidate.id,
            version=next_version,
            original_filename=Path(filename).name[:255],
            original_file_path=str(path),
            file_sha256=digest,
            mime_type=MIME_TYPES[fmt],
            extracted_text=text,
            parsed_profile_json=extracted.model_dump(mode="json"),
            verification_status=ResumeReviewStatus.PENDING_REVIEW,
        )
        db.add(version)
        db.flush()

        profile = profile_from_json(candidate.profile_json)
        if candidate.profile_json is None or candidate.profile_reviewed_at is None:
            new_profile = extracted.profile.model_copy(update={"source_version_id": version.id})
            # Keep operator-entered work authorization across re-imports.
            if profile.work_authorization and not new_profile.work_authorization:
                new_profile.work_authorization = profile.work_authorization
                new_profile.work_authorization_verified = profile.work_authorization_verified
            candidate.profile_json = new_profile.model_dump(mode="json")
        added = _merge_skills(db, candidate.id, extracted.skills, version.id)
        candidate.updated_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise

    db.refresh(candidate)
    return {
        "candidate": candidate_detail(db, candidate),
        "created": created,
        "version": next_version,
        "skills_added": added,
        "extraction_source": extracted.source,
        "warnings": extracted.warnings,
    }


def _same(text: str | None) -> str:
    return re.sub(r"[^a-z0-9%$+#]+", " ", (text or "").lower()).strip()


def carry_over_verification(old: CandidateProfile, new: CandidateProfile) -> int:
    """Keeps the operator's ticks on facts that come back word for word; everything else needs review."""
    kept = 0
    old_roles = {(_same(r.employer), _same(r.title), r.start): r for r in old.employment}
    verified_bullets = {_same(b.text) for r in old.employment for b in r.bullets if b.verified}
    for role in new.employment:
        match = old_roles.get((_same(role.employer), _same(role.title), role.start))
        if match is not None and match.verified:
            role.verified = True
            kept += 1
        for bullet in role.bullets:
            if _same(bullet.text) in verified_bullets:
                bullet.verified = True
                kept += 1
    if old.summary_verified and old.summary and _same(old.summary) == _same(new.summary):
        new.summary_verified = True
        kept += 1
    old_education = {(_same(e.institution), _same(e.degree)) for e in old.education if e.verified}
    for item in new.education:
        if (_same(item.institution), _same(item.degree)) in old_education:
            item.verified = True
            kept += 1
    old_certifications = {_same(c.name) for c in old.certifications if c.verified}
    for cert in new.certifications:
        if _same(cert.name) in old_certifications:
            cert.verified = True
            kept += 1
    return kept


def apply_version_profile(db: Session, candidate: Candidate, version_id: str) -> int:
    """Replaces the profile with a version's extraction. Returns how many verified facts kept their tick."""
    version = db.scalar(
        select(ResumeVersion).where(ResumeVersion.id == version_id, ResumeVersion.candidate_id == candidate.id)
    )
    if version is None:
        raise AppError(404, "resume_version_not_found", "Resume version not found")
    extracted = ExtractedResume.model_validate(version.parsed_profile_json or {})
    current = profile_from_json(candidate.profile_json)
    profile = extracted.profile.model_copy(
        update={
            "source_version_id": version.id,
            "work_authorization": current.work_authorization or extracted.profile.work_authorization,
            "work_authorization_verified": current.work_authorization_verified,
        }
    )
    kept = carry_over_verification(current, profile)
    candidate.profile_json = profile.model_dump(mode="json")
    candidate.profile_reviewed_at = None
    # Confirmed experience stays attached to the same employer, title and start under the new role ids.
    remap_records(db, candidate.id, profile)
    db.commit()
    return kept


def reextract_version(db: Session, ai: AIService | None, candidate: Candidate, version_id: str) -> dict[str, Any]:
    """Reads a stored resume again (AI when a key is set, else the offline parser) and replaces the profile with it."""
    version = db.scalar(select(ResumeVersion).where(ResumeVersion.id == version_id, ResumeVersion.candidate_id == candidate.id))
    if version is None:
        raise AppError(404, "resume_version_not_found", "Resume version not found")
    text = version.extracted_text
    if not text:
        path = Path(version.original_file_path)
        if not path.exists():
            raise AppError(410, "original_missing", "The original file for this resume version is no longer on disk.")
        data = path.read_bytes()
        text = extract_text(detect_format(version.original_filename, data), data)
        version.extracted_text = text
    extracted = extract_resume(ai, db, text, prefer_ai=True)
    version.parsed_profile_json = extracted.model_dump(mode="json")
    # Skills read from this resume before are replaced by this reading (a misread can produce junk such as "Full-time").
    # Skills added by hand or from other resume versions are kept.
    current = {normalize_skill(s.name) for s in extracted.skills}
    # A skill the operator answered about (v1.8) is theirs now, whatever this reading finds.
    answered = set(db.scalars(select(CandidateSkillEvidence.normalized_name).where(CandidateSkillEvidence.candidate_id == candidate.id)))
    stale = [
        s
        for s in db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id, CandidateSkill.source_resume_version_id == version.id))
        if s.normalized_name not in current and s.normalized_name not in answered
    ]
    for skill in stale:
        db.delete(skill)
    # Skills found again keep their status but take this reading's spelling (".NET", not "NET").
    spelled = {normalize_skill(s.name): s.name for s in extracted.skills}
    for skill in db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id, CandidateSkill.source_resume_version_id == version.id)):
        if skill.normalized_name in spelled:
            skill.skill_name = display_name(skill.normalized_name, spelled[skill.normalized_name])[:200]
    db.flush()
    added = _merge_skills(db, candidate.id, extracted.skills, version.id)
    candidate.updated_at = _now()
    db.flush()
    kept = apply_version_profile(db, candidate, version.id)
    db.refresh(candidate)
    roles = extracted.profile.employment
    return {
        "candidate": candidate_detail(db, candidate),
        "extraction_source": extracted.source,
        "warnings": extracted.warnings,
        "roles": len(roles),
        "bullets": sum(len(r.bullets) for r in roles),
        "education": len(extracted.profile.education),
        "skills_added": added,
        "skills_removed": len(stale),
        "kept_verified": kept,
    }


# ---------------------------------------------------------------- updates


def update_candidate(db: Session, candidate: Candidate, changes: dict[str, Any]) -> None:
    for field in ("full_name", "email", "phone", "location"):
        if field in changes:
            value = changes[field]
            if field == "full_name" and not (value or "").strip():
                raise AppError(422, "invalid_name", "Name can't be empty")
            setattr(candidate, field, value.strip() if isinstance(value, str) else value)
    if "status" in changes and changes["status"] is not None:
        candidate.status = CandidateStatus(changes["status"])
    if "profile" in changes and changes["profile"] is not None:
        profile = CandidateProfile.model_validate(changes["profile"])
        previous = profile_from_json(candidate.profile_json)
        profile.source_version_id = previous.source_version_id
        candidate.profile_json = profile.model_dump(mode="json")
    if "preferences" in changes and changes["preferences"] is not None:
        candidate.preferences_json = CandidatePreferences.model_validate(changes["preferences"]).model_dump(mode="json")
    if "consent_confirmed" in changes and changes["consent_confirmed"] is not None:
        candidate.consent_confirmed_at = _now() if changes["consent_confirmed"] else None
    candidate.updated_at = _now()
    db.commit()


def complete_review(db: Session, candidate: Candidate) -> None:
    profile = profile_from_json(candidate.profile_json)
    pending = db.scalar(
        select(func.count())
        .select_from(CandidateSkill)
        .where(CandidateSkill.candidate_id == candidate.id, CandidateSkill.verification_status == VerificationStatus.UNVERIFIED)
    )
    fully = (pending or 0) == 0 and all(r.verified for r in profile.employment)
    latest = db.scalar(
        select(ResumeVersion).where(ResumeVersion.candidate_id == candidate.id).order_by(ResumeVersion.version.desc()).limit(1)
    )
    if latest is not None:
        latest.verification_status = ResumeReviewStatus.VERIFIED if fully else ResumeReviewStatus.PARTIALLY_VERIFIED
    candidate.profile_reviewed_at = _now()
    db.commit()


def get_skill(db: Session, candidate_id: str, skill_id: str) -> CandidateSkill:
    skill = db.scalar(select(CandidateSkill).where(CandidateSkill.id == skill_id, CandidateSkill.candidate_id == candidate_id))
    if skill is None:
        raise AppError(404, "skill_not_found", "Skill not found for this candidate")
    return skill


def add_skill(db: Session, candidate: Candidate, name: str, evidence: str | None, status: VerificationStatus) -> CandidateSkill:
    key = normalize_skill(name)
    if not key:
        raise AppError(422, "invalid_skill", "Skill name can't be empty")
    if db.scalar(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id, CandidateSkill.normalized_name == key)):
        raise AppError(409, "duplicate_skill", f"{display_name(key, name)} is already listed")
    if status == VerificationStatus.VERIFIED and not (evidence or "").strip():
        raise AppError(422, "evidence_required", "Add evidence (where this skill appears) before marking it verified")
    skill = CandidateSkill(
        candidate_id=candidate.id,
        skill_name=display_name(key, name)[:200],
        normalized_name=key,
        evidence=(evidence or "").strip() or None,
        verification_status=status,
    )
    db.add(skill)
    db.commit()
    return skill


_SKILL_LIST_SEP = re.compile(r"[,;\n\r•|]+")


def add_skills(db: Session, candidate: Candidate, text: str) -> dict[str, list[str]]:
    """Adds every skill in a pasted list (comma, semicolon or line separated). A rejected one is restored."""
    existing = {s.normalized_name: s for s in db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id))}
    added: list[str] = []
    already: list[str] = []
    for raw in _SKILL_LIST_SEP.split(text):
        name = raw.strip(" \t-*").strip()
        key = normalize_skill(name)
        if not key or len(name) > 200:
            continue
        skill = existing.get(key)
        if skill is None:
            skill = CandidateSkill(
                candidate_id=candidate.id,
                skill_name=display_name(key, name)[:200],
                normalized_name=key,
                evidence=None,
                verification_status=VerificationStatus.UNVERIFIED,
            )
            db.add(skill)
            db.flush()  # keeps created_at in list order, which is the order resumes list them
            existing[key] = skill
            added.append(skill.skill_name)
        elif skill.verification_status == VerificationStatus.REJECTED:
            skill.verification_status = VerificationStatus.UNVERIFIED
            added.append(skill.skill_name)
        else:
            already.append(skill.skill_name)
    db.commit()
    return {"added": added, "already": already}


def update_skill(db: Session, skill: CandidateSkill, changes: dict[str, Any]) -> CandidateSkill:
    if "skill_name" in changes and changes["skill_name"]:
        key = normalize_skill(changes["skill_name"])
        clash = db.scalar(
            select(CandidateSkill).where(
                CandidateSkill.candidate_id == skill.candidate_id,
                CandidateSkill.normalized_name == key,
                CandidateSkill.id != skill.id,
            )
        )
        if clash:
            raise AppError(409, "duplicate_skill", "Another skill already has that name")
        skill.skill_name, skill.normalized_name = changes["skill_name"].strip()[:200], key
    if "evidence" in changes:
        skill.evidence = (changes["evidence"] or "").strip() or None
    if "verification_status" in changes and changes["verification_status"] is not None:
        status = VerificationStatus(changes["verification_status"])
        if status == VerificationStatus.VERIFIED and not skill.evidence:
            raise AppError(422, "evidence_required", "Add evidence (where this skill appears) before marking it verified")
        skill.verification_status = status
    db.commit()
    return skill


def verify_skills_with_evidence(db: Session, candidate: Candidate) -> int:
    skills = list(
        db.scalars(
            select(CandidateSkill).where(
                CandidateSkill.candidate_id == candidate.id,
                CandidateSkill.verification_status == VerificationStatus.UNVERIFIED,
                CandidateSkill.evidence.is_not(None),
            )
        )
    )
    for skill in skills:
        skill.verification_status = VerificationStatus.VERIFIED
    db.commit()
    return len(skills)


def delete_candidate(db: Session, config: AppConfig, candidate: Candidate) -> None:
    if db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == candidate.id)):
        raise AppError(
            409,
            "candidate_has_applications",
            "This candidate has application records. Archive them instead to keep the audit history.",
        )
    folder = ensure_within(config.originals_dir, config.originals_dir / candidate.id)
    db.delete(candidate)
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- verified snapshot


def verified_snapshot(db: Session, candidate: Candidate) -> dict[str, Any]:
    """The facts matching and generation may use, and nothing else.

    With Settings → "Require verification" on, only ticked facts and verified skills; off (the default), every fact in
    the profile and every skill not rejected.
    """
    trust = not load_settings(db).require_verification
    profile = profile_from_json(candidate.profile_json)
    # Approved additions are experience the operator confirmed role by role (v1.8); their ids are the record ids.
    additions = additions_by_role(db, candidate.id, profile)
    skill_roles = confirmed_skill_roles(db, candidate.id, profile)
    roles = []
    for role in sorted((r for r in profile.employment if r.verified or trust), key=lambda r: sort_key(r.start, r.end), reverse=True):
        roles.append(
            {
                "id": role.id,
                "employer": role.employer,
                "title": role.title,
                "location": role.location,
                "start": role.start,
                "end": role.end,
                "bullets": [{"id": b.id, "text": b.text} for b in role.bullets if (b.verified or trust) and b.text.strip()]
                + additions.get(role.id, []),
            }
        )
    usable = [VerificationStatus.VERIFIED, VerificationStatus.UNVERIFIED] if trust else [VerificationStatus.VERIFIED]
    skills = list(
        db.scalars(
            select(CandidateSkill)
            .where(CandidateSkill.candidate_id == candidate.id, CandidateSkill.verification_status.in_(usable))
            .order_by(CandidateSkill.created_at)  # the order the resume lists them
        )
    )
    return {
        "candidate_id": candidate.id,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "phone": candidate.phone,
        "location": candidate.location,
        "links": profile.links,
        "headline": profile.headline if profile.summary_verified or trust else None,
        "summary": profile.summary if profile.summary_verified or trust else None,
        "employment": roles,
        "education": [e.model_dump() for e in profile.education if e.verified or trust],
        "certifications": [c.model_dump() for c in profile.certifications if c.verified or trust],
        "skills": [
            {
                "id": s.id,
                "name": s.skill_name,
                "normalized": s.normalized_name,
                "evidence": s.evidence,
                # Roles where the operator confirmed using it; empty = a general skill, never a company-specific claim.
                "roles": skill_roles.get(s.normalized_name, []),
            }
            for s in skills
        ],
        "work_authorization": profile.work_authorization if profile.work_authorization_verified or trust else None,
        "profile_revision": candidate.profile_revision,
        "addition_ids": [b["id"] for bullets in additions.values() for b in bullets],
        "preferences": preferences_from_json(candidate.preferences_json).model_dump(),
        "total_years_experience": total_years([(r["start"], r["end"]) for r in roles]),
        "snapshot_at": _now().isoformat(),
    }
