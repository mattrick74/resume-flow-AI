"""Confirmed experience (v1.8): job-skill gaps, the operator's answers, role-level experience records and the bullets
written from them, waiting jobs, batch confirmation, coverage reports and change lists.

A job description shows what an employer wants, never what the candidate did. Nothing reaches a document until the
operator confirmed it: a skill answered "Have used" (Skills section), or an approved record whose bullet was written
from the operator's own description of the work at that employer (GEN-03/04, VAL-05/08).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.db.models import (
    Candidate,
    CandidateMatch,
    CandidateRoleExperience,
    CandidateSkill,
    CandidateSkillEvidence,
    Job,
    ResumeVersion,
)
from resumeflow.domain.content import ResumeContent
from resumeflow.domain.enums import AdditionStatus, GenerationOperation, ProcessingJobType, SkillDecision, VerificationStatus
from resumeflow.domain.experience import total_years
from resumeflow.domain.profile import CandidateProfile, Role, profile_from_json
from resumeflow.domain.requirements import JobRequirements
from resumeflow.domain.skills import detect_tools, display_name, find_skill_in_text, normalize_skill, skill_covers
from resumeflow.errors import AppError
from resumeflow.services.additions import resolve_refs, resolve_role, role_label, role_refs
from resumeflow.services.ai.provider import AIConfigError, AIError, BudgetExceeded
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.candidates import get_candidate, verified_snapshot
from resumeflow.services.evidence import bullet_index, numbers_in
from resumeflow.services.inference import infer_role_skills
from resumeflow.services.jobs import requirements_of
from resumeflow.services.matching import _evidence_text, evaluate_gates, run_matching
from resumeflow.services.queue import JobContext, ProcessingQueue
from resumeflow.services.settings import load_settings
from resumeflow.services.validation import resume_plain_text

GapStatus = Literal["matched", "confirmed", "plausible", "unknown", "rejected"]
NEEDS_CONFIRMATION = "needs_confirmation"

# Requirements that are credentials, clearance, authorization, degrees or years: hard gates, never a "skill used" (MAT-08).
_GATE_LIKE = re.compile(
    r"\b(certifi\w*|certified|licen[cs]\w*|clearance|citizen\w*|green card|visa|sponsorship|degree|bachelor\w*|master'?s|ph\.?d|mba|years?)\b",
    re.IGNORECASE,
)
_LEADERSHIP = re.compile(
    r"\b(led|lead|leading|managed|manage|managing|mentored|mentoring|supervised|directed|headed|spearheaded|oversaw|owned|team of)\b",
    re.IGNORECASE,
)
# First public release year of common technologies (VAL-07: "experience before a technology existed").
RELEASE_YEAR: dict[str, int] = {
    "kubernetes": 2014, "docker": 2013, "terraform": 2014, "react": 2013, "vue": 2014, "angular": 2016, "next.js": 2016,
    "typescript": 2012, "swift": 2014, "kotlin": 2016, "rust": 2015, "golang": 2009, "node.js": 2009, "graphql": 2015,
    "fastapi": 2018, "flutter": 2017, "react native": 2015, "apache spark": 2014, "apache kafka": 2011,
    "apache airflow": 2015, "dbt": 2016, "snowflake": 2014, "databricks": 2013, "bigquery": 2011, "redshift": 2013,
    "tensorflow": 2015, "pytorch": 2016, "scikit-learn": 2010, "large language models": 2018, "openshift": 2011,
    "helm": 2016, "prometheus": 2015, "grafana": 2014, "power bi": 2014, "power automate": 2016, "power apps": 2016,
    "dynamics 365": 2016, "playwright": 2020, "cypress": 2017, "jest": 2014, "tailwind css": 2017, "mongodb": 2009,
    "elasticsearch": 2010, "redis": 2009, "microsoft azure": 2010, "google cloud platform": 2008,
    "amazon web services": 2006, ".net": 2002, "c#": 2000, "ansible": 2012, "jenkins": 2011, "git": 2005,
    "spring boot": 2014, "langchain": 2022, "llamaindex": 2022, "azure openai": 2021, "openai": 2020, "chatgpt": 2022,
    "gpt-4": 2023, "rag": 2020, "retrieval-augmented generation": 2020, "vector databases": 2019, "pinecone": 2019,
    "hugging face": 2016, "github copilot": 2021,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _year(value: str | None) -> int | None:
    if not value:
        return None
    if value.strip().lower() in {"present", "current", "now"}:
        return date.today().year
    match = re.match(r"(\d{4})", value.strip())
    return int(match.group(1)) if match else None


def _role_name(role: Role) -> str:
    dates = " – ".join(filter(None, [role.start, role.end or "present"]))
    return f"{role.title or 'Role'} at {role.employer or 'unknown employer'}" + (f" ({dates})" if dates else "")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def require_authorized(candidate: Candidate) -> None:
    """NFR-07: only a selected, authorized candidate's profile is ever updated."""
    if candidate.consent_confirmed_at is None:
        raise AppError(409, "candidate_not_authorized", "Confirm the candidate's authorization before adding experience to their profile.")


# ---------------------------------------------------------------- job skills and gaps (JOB-09/10)


def job_skill_items(req: JobRequirements) -> tuple[list[dict[str, str]], list[str]]:
    """Required and preferred skills (de-duplicated by alias), plus requirements that are really hard gates."""
    items: list[dict[str, str]] = []
    gate_like: list[str] = []
    seen: set[str] = set()
    for kind, names in (("required", req.mandatory_skills), ("preferred", req.preferred_skills)):
        for name in names:
            key = normalize_skill(name)
            if not key or key in seen:
                continue
            seen.add(key)
            if _GATE_LIKE.search(name):
                gate_like.append(name.strip())
                continue
            items.append({"skill": display_name(key, name), "normalized": key, "kind": kind})
    return items, gate_like


def _answers(db: Session, candidate_id: str) -> dict[str, CandidateSkillEvidence]:
    return {a.normalized_name: a for a in db.scalars(select(CandidateSkillEvidence).where(CandidateSkillEvidence.candidate_id == candidate_id))}


def _skill_rows(db: Session, candidate_id: str) -> dict[str, CandidateSkill]:
    return {s.normalized_name: s for s in db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate_id))}


def _partial_line(key: str, text: str) -> str | None:
    """A line that shares a distinctive word with a multi-word skill ("Azure" for "Azure OpenAI"): plausible, not proof."""
    words = [w for w in re.findall(r"[a-z0-9+#.]{3,}", key) if w not in {"and", "the", "for", "with", "services", "development", "engineering"}]
    if len(words) < 2:
        return None
    for line in text.split("\n"):
        lowered = line.lower()
        if any(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", lowered) for w in words):
            return line.strip()
    return None


def top_match(db: Session, job: Job) -> CandidateMatch | None:
    return db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == job.id, CandidateMatch.rank == 1))


def analyze_gaps(db: Session, job: Job, candidate: Candidate, *, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every job skill classified for one candidate: matched (shown in a role), confirmed (in the profile but no role
    shows it), plausible (related wording only), unknown (no evidence, or answered "Unsure"), rejected ("Have not used").
    A skill is never treated as confirmed because the job description asks for it."""
    req = requirements_of(job)
    snap = snap or verified_snapshot(db, candidate)
    profile = profile_from_json(candidate.profile_json)
    answers = _answers(db, candidate.id)
    rows = _skill_rows(db, candidate.id)
    match = db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == job.id, CandidateMatch.candidate_id == candidate.id))
    semantic = {
        normalize_skill(m["skill"]): m.get("evidence")
        for name in ("mandatory_skills", "preferred_skills")
        for m in ((match.matching_details_json or {}).get("components", {}).get(name, {}).get("matched", []) if match else [])
        if m.get("method") == "semantic"
    }
    role_text = "\n".join(
        line for role in snap.get("employment", []) for line in [role["title"], *(b["text"] for b in role["bullets"])]
    )
    evidence_text = _evidence_text(snap)
    snap_skills = {s["normalized"]: s for s in snap.get("skills", [])}
    role_names = {r.id: _role_name(r) for r in profile.employment}
    pending = _pending_records(db, candidate.id)
    # v1.8.5: skills an employer's own bullets show (C# work → .NET, PostgreSQL → SQL, or an AI-cited bullet).
    placed = {p["normalized"]: p for p in infer_role_skills(snap, req, semantic)}

    items: list[dict[str, Any]] = []
    gate_like: list[str] = []
    skill_items, gate_like = job_skill_items(req)
    for item in skill_items:
        key = item["normalized"]
        answer = answers.get(key)
        row = rows.get(key)
        line = find_skill_in_text(key, role_text)
        entry: dict[str, Any] = {
            **item,
            "answer": answer.decision.value if answer else None,
            "role_ids": [r.id for r in resolve_refs(profile, answer.role_ids_json)] if answer else [],
            "match": "missing",
            "evidence": None,
            "employer": None,  # where the candidate's existing experience shows it, when the evidence search found it
            "pending_suggestion": any(key in {normalize_skill(s) for s in r.skills_json or []} for r in pending),
        }
        if (answer and answer.decision == SkillDecision.NOT_USED) or (row and row.verification_status == VerificationStatus.REJECTED):
            status: GapStatus = "rejected"
        elif line:
            literal = re.search(rf"(?<![\w+#.]){re.escape(key)}(?![\w+#])", line, re.IGNORECASE)
            status, entry["match"], entry["evidence"] = "matched", ("exact" if literal else "equivalent"), line
        elif key in placed:
            status, entry["match"] = "matched", "equivalent"
            entry["employer"] = placed[key]["employer"]
            entry["evidence"] = f"Shown at {placed[key]['employer']} — {placed[key]['evidence']}"
        elif key in snap_skills or any(skill_covers(s["name"], key) for s in snap_skills.values()):
            covering = snap_skills.get(key) or next(s for s in snap_skills.values() if skill_covers(s["name"], key))
            roles = covering.get("roles") or []
            status = "matched" if roles else "confirmed"
            entry["match"] = "exact" if covering["normalized"] == key else "equivalent"
            where = "Confirmed at " + "; ".join(role_names.get(r, "a role") for r in roles) if roles else "In the Skills list"
            entry["evidence"] = where if covering["normalized"] == key else f"{where}: {covering['name']}"
        elif key in semantic:
            status, entry["match"], entry["evidence"] = "matched", "equivalent", semantic[key]
        elif answer and answer.decision == SkillDecision.UNSURE:
            status = "unknown"
        else:
            partial = _partial_line(key, evidence_text)
            status = "plausible" if partial else "unknown"
            if partial:
                entry["match"], entry["evidence"] = "partial", partial
        entry["status"] = status
        entry["needs_answer"] = status in ("plausible", "unknown") and answer is None and not entry["pending_suggestion"]
        items.append(entry)

    gates: list[dict[str, Any]] = []
    if load_settings(db).hard_requirement_gates:
        gates = [g.__dict__ for g in evaluate_gates(req, snap) if g.status != "pass"]
        gates += [
            {"name": "Credential or requirement", "status": "unknown", "detail": f"{name}: add it to the profile (Education & credentials) if the candidate holds it."}
            for name in gate_like
        ]
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("matched", "confirmed", "plausible", "unknown", "rejected")}
    return {
        "job_id": job.id,
        "job_title": job.title,
        "company": job.company,
        "candidate_id": candidate.id,
        "candidate_name": candidate.full_name,
        "items": items,
        "hard_gates": gates,
        "counts": counts,
        "unanswered": sum(1 for i in items if i["needs_answer"]),
        "pending_suggestions": sum(1 for r in pending if job.id in (r.source_job_ids_json or [])),
        "score": match.score if match else None,
        "potential_score": match.potential_score if match else None,
        "previous_score": match.previous_score if match else None,
        "roles": [{"id": r.id, "label": _role_name(r)} for r in profile.employment],
    }


def batch_gaps(db: Session, job_ids: list[str] | None = None) -> dict[str, Any]:
    """JOB-11 / UX-04: missing skills across jobs (by default every job waiting for answers), per top candidate,
    most frequent required skills first, then by how much they would add to the jobs' scores."""
    if job_ids:
        jobs = [j for j in (db.get(Job, jid) for jid in dict.fromkeys(job_ids)) if j is not None]
    else:
        jobs = waiting_jobs(db)
    weights = load_settings(db).match_weights
    per_candidate: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for job in jobs:
        top = top_match(db, job)
        if top is None:
            skipped.append({"job_id": job.id, "reason": "No ranked candidate yet"})
            continue
        candidate = db.get(Candidate, top.candidate_id)
        report = analyze_gaps(db, job, candidate)
        req = requirements_of(job)
        required_n = max(1, len(req.mandatory_skills))
        preferred_n = max(1, len(req.preferred_skills))
        bucket = per_candidate.setdefault(
            candidate.id,
            {"candidate_id": candidate.id, "candidate_name": candidate.full_name, "roles": report["roles"], "job_ids": [], "skills": {}},
        )
        bucket["job_ids"].append(job.id)
        for item in report["items"]:
            if item["status"] not in ("plausible", "unknown"):
                continue
            entry = bucket["skills"].setdefault(
                item["normalized"],
                {
                    "skill": item["skill"],
                    "normalized": item["normalized"],
                    "required_count": 0,
                    "preferred_count": 0,
                    "impact": 0.0,
                    "jobs": [],
                    "answer": item["answer"],
                    "status": item["status"],
                    "evidence": item["evidence"],
                    "needs_answer": item["needs_answer"],
                },
            )
            if item["kind"] == "required":
                entry["required_count"] += 1
                entry["impact"] += weights.mandatory_skills / required_n
            else:
                entry["preferred_count"] += 1
                entry["impact"] += weights.preferred_skills / preferred_n
            entry["jobs"].append({"job_id": job.id, "title": job.title, "company": job.company, "kind": item["kind"]})
    candidates = []
    for bucket in per_candidate.values():
        skills = sorted(bucket["skills"].values(), key=lambda s: (-s["required_count"], -s["impact"], -s["preferred_count"], s["skill"].lower()))
        for s in skills:
            s["impact"] = round(s["impact"], 1)
        candidates.append({**bucket, "skills": skills})
    return {"jobs": len(jobs), "candidates": candidates, "skipped": skipped}


# ---------------------------------------------------------------- answers (CAN-14/17)


class SkillAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=200)
    decision: SkillDecision
    role_ids: list[str] = Field(default_factory=list, max_length=20)
    # What the candidate did with it at those roles (CAN-14): a bullet is written only from this.
    context: str | None = Field(default=None, max_length=1000)
    project: str | None = Field(default=None, max_length=300)
    outcome: str | None = Field(default=None, max_length=500)
    years: float | None = Field(default=None, ge=0, le=60)
    proficiency: Literal["beginner", "intermediate", "advanced", "expert"] | None = None
    last_used: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=1000)


def _confirm_skill_row(db: Session, candidate: Candidate, rows: dict[str, CandidateSkill], key: str, name: str, evidence: str) -> None:
    row = rows.get(key)
    if row is None:
        row = CandidateSkill(candidate_id=candidate.id, skill_name=name[:200], normalized_name=key, evidence=evidence, verification_status=VerificationStatus.VERIFIED)
        db.add(row)
        rows[key] = row
    else:
        row.verification_status = VerificationStatus.VERIFIED
        row.evidence = row.evidence or evidence


def _answer_issues(answer: SkillAnswer, roles: list[Role], profile: CandidateProfile) -> list[dict[str, str]]:
    issues = []
    if answer.years:
        spans = roles or profile.employment
        available = total_years([(r.start, r.end) for r in spans])
        if available and answer.years > available + 1:
            where = "the selected roles" if roles else "the whole work history"
            issues.append({"severity": "warning", "code": "years_conflict", "message": f"{answer.years:g} years of {answer.skill} is more than {where} cover ({available:g} years)."})
    for role in roles:
        release = RELEASE_YEAR.get(normalize_skill(answer.skill))
        end = _year(role.end) or date.today().year
        if release and end < release:
            issues.append({"severity": "warning", "code": "before_release", "message": f"{answer.skill} was released in {release}, but {_role_name(role)} ended in {end}."})
    return issues


def confirm_skills(
    db: Session, ai: AIService | None, queue: ProcessingQueue, candidate: Candidate, answers: list[SkillAnswer], source_job_ids: list[str]
) -> dict[str, Any]:
    """Records the operator's answers. "Have used" confirms the skill (Skills section); with a role and a description of
    the work, a bullet is drafted for that role and waits for approval. "Have not used" is never suggested again."""
    require_authorized(candidate)
    profile = profile_from_json(candidate.profile_json)
    roles = {r.id: r for r in profile.employment}
    existing = _answers(db, candidate.id)
    rows = _skill_rows(db, candidate.id)
    source_job_ids = [j for j in dict.fromkeys(source_job_ids) if db.get(Job, j) is not None]
    new_records: list[CandidateRoleExperience] = []
    views: list[dict[str, Any]] = []
    changed = False
    for answer in answers:
        key = normalize_skill(answer.skill)
        if not key:
            continue
        unknown = [rid for rid in answer.role_ids if rid not in roles]
        if unknown:
            raise AppError(422, "unknown_role", "Choose roles from this candidate's own work history.", {"role_ids": unknown})
        name = display_name(key, answer.skill)
        record = existing.get(key)
        if record is None:
            record = CandidateSkillEvidence(candidate_id=candidate.id, skill_name=name[:200], normalized_name=key, decision=answer.decision, role_ids_json=[], source_job_ids_json=[])
            db.add(record)
            existing[key] = record
        chosen = [roles[rid] for rid in dict.fromkeys(answer.role_ids)]
        record.decision = answer.decision
        record.role_ids_json = role_refs(profile, [r.id for r in chosen]) if answer.decision == SkillDecision.USED else []
        record.years, record.proficiency, record.last_used = answer.years, answer.proficiency, answer.last_used
        record.note = (answer.note or "").strip() or None
        record.source_job_ids_json = list(dict.fromkeys([*(record.source_job_ids_json or []), *source_job_ids]))
        record.decided_at = _now()
        row = rows.get(key)
        if answer.decision == SkillDecision.USED:
            where = f" — used at {_join([r.employer or r.title for r in chosen])}" if chosen else ""
            _confirm_skill_row(db, candidate, rows, key, name, f"Confirmed by the operator{where}")
            changed = True
            context = (answer.context or "").strip()
            for role in chosen if context else []:
                new_records.append(_upsert_record(db, candidate, role, [name], context, answer.project, answer.outcome, "gap", source_job_ids))
        elif answer.decision == SkillDecision.NOT_USED:
            if row is not None and row.verification_status != VerificationStatus.REJECTED:
                row.verification_status = VerificationStatus.REJECTED
                changed = True
            for pending in _pending_records(db, candidate.id):
                if key in {normalize_skill(s) for s in pending.skills_json or []}:
                    pending.status = AdditionStatus.REJECTED  # a suggestion built on a skill now answered "not used"
        elif row is not None and row.source_resume_version_id is None and (row.evidence or "").startswith("Confirmed by the operator"):
            row.verification_status = VerificationStatus.REJECTED  # "Unsure" withdraws an earlier confirmation
            changed = True
        views.append({"skill": name, "normalized": key, "decision": answer.decision.value, "issues": _answer_issues(answer, chosen, profile)})
    if changed:
        candidate.profile_revision += 1
    db.commit()
    drafted = draft_records(db, ai, new_records)
    approved = auto_approve(db, candidate, drafted) if drafted and load_settings(db).auto_approve_bullets else []
    follow_up = after_profile_change(db, queue, candidate.id, source_job_ids)
    profile = profile_from_json(candidate.profile_json)
    return {
        "answers": views,
        "approved": [record_view(r, profile) for r in approved],
        "suggestions": [record_view(r, profile) for r in drafted if r not in approved],
        **follow_up,
    }


# ---------------------------------------------------------------- role-level records and bullets (CAN-13/15, GEN-04)


def _pending_records(db: Session, candidate_id: str) -> list[CandidateRoleExperience]:
    return list(
        db.scalars(
            select(CandidateRoleExperience).where(
                CandidateRoleExperience.candidate_id == candidate_id, CandidateRoleExperience.status == AdditionStatus.SUGGESTED
            )
        )
    )


def _upsert_record(
    db: Session,
    candidate: Candidate,
    role: Role,
    skills: list[str],
    context: str,
    project: str | None,
    outcome: str | None,
    origin: str,
    source_job_ids: list[str],
) -> CandidateRoleExperience:
    """One description of work at one role = one record; more skills for the same description join it."""
    for record in _pending_records(db, candidate.id):
        if record.role_id == role.id and record.context.strip().lower() == context.strip().lower():
            record.skills_json = list(dict.fromkeys([*(record.skills_json or []), *skills]))
            record.project = (project or "").strip() or record.project
            record.outcome = (outcome or "").strip() or record.outcome
            record.source_job_ids_json = list(dict.fromkeys([*(record.source_job_ids_json or []), *source_job_ids]))
            record.narrative = None  # drafted again with the extra skills
            return record
    record = CandidateRoleExperience(
        candidate_id=candidate.id,
        role_id=role.id,
        role_label=role_label(role),
        skills_json=list(dict.fromkeys(skills)),
        context=context.strip()[:1000],
        project=(project or "").strip()[:300] or None,
        outcome=(outcome or "").strip()[:500] or None,
        status=AdditionStatus.SUGGESTED,
        origin=origin,
        issues_json=[],
        source_job_ids_json=list(source_job_ids),
    )
    db.add(record)
    db.flush()
    return record


class AIDraftedBullet(BaseModel):
    record_id: str
    text: str


class AIDraftedBullets(BaseModel):
    bullets: list[AIDraftedBullet]


DRAFT_INSTRUCTIONS = f"""You write resume bullets from work a person has confirmed they did.
{UNTRUSTED_NOTICE}

Each record in <records> is one piece of work at one employer: the role, the technologies the person confirmed using
there, and their own description of the work. Write exactly one bullet per record:
- Use only what the record states. Never add numbers, results, scale, team size, leadership, ownership or
  technologies that the record doesn't mention.
- Start with a strong, specific action verb (vary verbs across bullets); past tense for a past role, present tense
  for a current role; no first person.
- Name the confirmed technologies naturally and describe the concrete technical action and its purpose.
- One sentence of at most 30 words. Don't mention the employer's name or the record id in the bullet.
Return each bullet with its record_id."""


def _record_listing(record: CandidateRoleExperience, role: Role) -> str:
    lines = [f"[{record.id}] Role: {_role_name(role)}", f"Technologies confirmed: {', '.join(record.skills_json or [])}", f"Work (as described): {record.context}"]
    if record.project:
        lines.append(f"Project: {record.project}")
    if record.outcome:
        lines.append(f"Result or scale (as stated): {record.outcome}")
    return "\n".join(lines)


def offline_bullet(record: CandidateRoleExperience) -> str:
    """Without AI: the operator's own words, tidied, with the confirmed technologies named. Nothing is added."""
    text = re.sub(r"\s+", " ", record.context).strip().rstrip(".")
    text = text[:1].upper() + text[1:]
    missing = [s for s in record.skills_json or [] if not find_skill_in_text(normalize_skill(s), text, include_ambiguous=True) and s.lower() not in text.lower()]
    if missing:
        text += f" using {_join(missing)}"
    if record.project and record.project.lower() not in text.lower():
        text += f" for {record.project.strip()}"
    if record.outcome:
        text += f", {record.outcome.strip().rstrip('.')}"
    return text + "."


def draft_records(db: Session, ai: AIService | None, records: list[CandidateRoleExperience], *, force: bool = False) -> list[CandidateRoleExperience]:
    """Writes the bullet for each record (one AI call for all of them, OPS-09), then checks it (VAL-07)."""
    todo = [r for r in dict.fromkeys(records) if force or not r.narrative]
    if not todo:
        return list(dict.fromkeys(records))
    candidate = get_candidate(db, todo[0].candidate_id)
    profile = profile_from_json(candidate.profile_json)
    roles = {r.id: resolve_role(profile, r.role_id, r.role_label) for r in todo}
    drafted: dict[str, str] = {}
    if ai is not None and ai.available():
        listing = "\n\n".join(_record_listing(r, roles[r.id]) for r in todo if roles[r.id] is not None)
        job_id = next((j for r in todo for j in (r.source_job_ids_json or [])), None)
        try:
            result = ai.run(
                operation=GenerationOperation.EXPERIENCE_DRAFTING,
                role="writing",
                instructions=DRAFT_INSTRUCTIONS,
                input_text=wrap("records", listing),
                schema=AIDraftedBullets,
                job_id=job_id,
                max_output_tokens=4000,
            )
            drafted = {b.record_id: b.text.strip() for b in result.bullets if b.text.strip()}
        except (AIError, AIConfigError, BudgetExceeded):
            drafted = {}
    for record in todo:
        text = drafted.get(record.id)
        record.narrative = (text or offline_bullet(record))[:1000]
        record.drafted_by = "ai" if text else "offline"
        record.issues_json = check_record(db, record, profile)
    db.commit()
    return list(dict.fromkeys(records))


def _copied(text: str, source: str, size: int = 8) -> bool:
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    if len(words) < size:
        return False
    haystack = " ".join(re.findall(r"[a-z0-9+#.]+", source.lower()))
    return any(" ".join(words[i : i + size]) in haystack for i in range(len(words) - size + 1))


def check_record(db: Session, record: CandidateRoleExperience, profile: CandidateProfile) -> list[dict[str, str]]:
    """VAL-07: flag what the operator didn't state, wrong employers and impossible dates. Flags, never fixes."""
    text = record.narrative or ""
    said = " ".join(filter(None, [record.context, record.project, record.outcome]))
    role = resolve_role(profile, record.role_id, record.role_label)
    issues: list[dict[str, str]] = []

    def flag(severity: str, code: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "message": message})

    if role is None:
        flag("error", "role_missing", f"The role “{record.role_label}” is no longer in the profile; choose another role or delete this record.")
    invented = numbers_in(text) - numbers_in(said)
    if invented:
        flag("error", "unsupported_number", f"Numbers you didn't state: {', '.join(sorted(invented))}")
    allowed = {normalize_skill(s) for s in record.skills_json or []} | detect_tools(said)
    if role is not None:
        allowed |= detect_tools(" ".join(b.text for b in role.bullets))
    extra = detect_tools(text) - allowed
    if extra:
        flag("error", "unconfirmed_tool", f"Mentions {_join(sorted(display_name(e) for e in extra))}, not confirmed for this role")
    if _LEADERSHIP.search(text) and not _LEADERSHIP.search(said):
        flag("error", "unsupported_leadership", "Claims leadership or ownership that the description doesn't state")
    if role is not None:
        lowered = text.lower()
        for other in profile.employment:
            if other.id != role.id and len(other.employer.strip()) >= 4 and other.employer.strip().lower() not in (role.employer or "").lower():
                if re.search(rf"\b{re.escape(other.employer.strip().lower())}\b", lowered):
                    flag("warning", "wrong_employer", f"Mentions {other.employer}, a different employer")
        start, end = _year(role.start), _year(role.end) or date.today().year
        if start and end and start > end:
            flag("warning", "role_dates", f"{_role_name(role)} starts after it ends; check the dates")
        for skill in record.skills_json or []:
            release = RELEASE_YEAR.get(normalize_skill(skill))
            if release and end < release:
                flag("warning", "before_release", f"{skill} was released in {release}, but this role ended in {end}")
    for job_id in record.source_job_ids_json or []:
        job = db.get(Job, job_id)
        if job is not None and _copied(text, job.raw_jd):
            flag("warning", "copied_from_job_description", "Repeats wording from the job description; describe the actual work instead")
            break
    return issues


def record_view(record: CandidateRoleExperience, profile: CandidateProfile) -> dict[str, Any]:
    role = resolve_role(profile, record.role_id, record.role_label)
    issues = record.issues_json or []
    return {
        "id": record.id,
        "role_id": role.id if role else record.role_id,
        "role": _role_name(role) if role else record.role_label,
        "role_found": role is not None,
        "skills": record.skills_json or [],
        "context": record.context,
        "project": record.project,
        "outcome": record.outcome,
        "narrative": record.narrative,
        "status": record.status.value,
        "origin": record.origin,
        "drafted_by": record.drafted_by,
        "issues": issues,
        "issue_counts": {s: sum(1 for i in issues if i.get("severity") == s) for s in ("error", "warning")},
        "source_job_ids": record.source_job_ids_json or [],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "approved_at": record.approved_at,
    }


def get_record(db: Session, candidate_id: str, record_id: str) -> CandidateRoleExperience:
    record = db.scalar(select(CandidateRoleExperience).where(CandidateRoleExperience.id == record_id, CandidateRoleExperience.candidate_id == candidate_id))
    if record is None:
        raise AppError(404, "experience_not_found", "Experience record not found for this candidate")
    return record


def evidence_overview(db: Session, candidate: Candidate) -> dict[str, Any]:
    """UX-02: suggested bullets, approved additions and the confirmation history for one candidate."""
    profile = profile_from_json(candidate.profile_json)
    records = list(
        db.scalars(select(CandidateRoleExperience).where(CandidateRoleExperience.candidate_id == candidate.id).order_by(CandidateRoleExperience.created_at.desc()))
    )
    answers = sorted(_answers(db, candidate.id).values(), key=lambda a: a.decided_at, reverse=True)
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.id.in_({jid for a in answers for jid in a.source_job_ids_json or []})))}
    return {
        "profile_revision": candidate.profile_revision,
        "records": [record_view(r, profile) for r in records],
        "history": [
            {
                "skill": a.skill_name,
                "normalized": a.normalized_name,
                "decision": a.decision.value,
                "roles": [_role_name(r) for r in resolve_refs(profile, a.role_ids_json)],
                "years": a.years,
                "proficiency": a.proficiency,
                "last_used": a.last_used,
                "note": a.note,
                "decided_at": a.decided_at,
                "jobs": [{"id": jid, "title": jobs[jid].title, "company": jobs[jid].company} for jid in a.source_job_ids_json or [] if jid in jobs],
            }
            for a in answers
        ],
        "roles": [{"id": r.id, "label": _role_name(r)} for r in profile.employment],
    }


class RecordCreate(BaseModel):
    """The experience interview (CAN-15): one piece of work at one role, in the operator's words."""

    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1, max_length=40)
    skills: list[str] = Field(default_factory=list, max_length=20)
    context: str = Field(min_length=3, max_length=1000)
    project: str | None = Field(default=None, max_length=300)
    outcome: str | None = Field(default=None, max_length=500)
    origin: Literal["interview", "manual"] = "interview"


def create_record(db: Session, ai: AIService | None, candidate: Candidate, body: RecordCreate, queue: ProcessingQueue | None = None) -> CandidateRoleExperience:
    require_authorized(candidate)
    profile = profile_from_json(candidate.profile_json)
    role = next((r for r in profile.employment if r.id == body.role_id), None)
    if role is None:
        raise AppError(422, "unknown_role", "Choose a role from this candidate's own work history.")
    skills = [display_name(normalize_skill(s), s) for s in body.skills if normalize_skill(s)]
    record = _upsert_record(db, candidate, role, skills, body.context, body.project, body.outcome, body.origin, [])
    db.commit()
    draft_records(db, ai, [record])
    if load_settings(db).auto_approve_bullets and auto_approve(db, candidate, [record]) and queue is not None:
        after_profile_change(db, queue, candidate.id, [])
    return record


class RecordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: str | None = Field(default=None, max_length=1000)
    context: str | None = Field(default=None, max_length=1000)
    project: str | None = Field(default=None, max_length=300)
    outcome: str | None = Field(default=None, max_length=500)
    skills: list[str] | None = Field(default=None, max_length=20)
    role_id: str | None = Field(default=None, max_length=40)
    status: AdditionStatus | None = None
    redraft: bool = False
    acknowledge: bool = False


def update_record(db: Session, ai: AIService | None, queue: ProcessingQueue, candidate: Candidate, record: CandidateRoleExperience, body: RecordUpdate) -> dict[str, Any]:
    """Edit, re-draft, approve, reject or undo (CAN-18). Any change to approved content is a new profile revision."""
    require_authorized(candidate)
    profile = profile_from_json(candidate.profile_json)
    was_approved = record.status == AdditionStatus.APPROVED
    if body.role_id is not None:
        role = next((r for r in profile.employment if r.id == body.role_id), None)
        if role is None:
            raise AppError(422, "unknown_role", "Choose a role from this candidate's own work history.")
        record.role_id, record.role_label = role.id, role_label(role)
    if body.context is not None:
        record.context = body.context.strip()[:1000] or record.context
    if body.project is not None:
        record.project = body.project.strip() or None
    if body.outcome is not None:
        record.outcome = body.outcome.strip() or None
    if body.skills is not None:
        record.skills_json = [display_name(normalize_skill(s), s) for s in body.skills if normalize_skill(s)]
    if body.redraft:
        draft_records(db, ai, [record], force=True)
    if body.narrative is not None and body.narrative.strip() and body.narrative.strip() != (record.narrative or ""):
        record.narrative = body.narrative.strip()
        record.drafted_by = "operator"
    record.issues_json = check_record(db, record, profile)
    if body.status == AdditionStatus.APPROVED:
        return approve_records(db, queue, candidate, [record.id], acknowledge=body.acknowledge)
    if body.status is not None:
        record.status = body.status
        record.approved_at = None
    if was_approved or body.status is not None:
        candidate.profile_revision += 1
    db.commit()
    follow_up = after_profile_change(db, queue, candidate.id, record.source_job_ids_json or []) if was_approved else {"resumed": [], "rematched": []}
    return {"records": [record_view(record, profile)], **follow_up}


def delete_record(db: Session, queue: ProcessingQueue, candidate: Candidate, record: CandidateRoleExperience) -> dict[str, Any]:
    require_authorized(candidate)
    was_approved = record.status == AdditionStatus.APPROVED
    jobs = list(record.source_job_ids_json or [])
    db.delete(record)
    if was_approved:
        candidate.profile_revision += 1
    db.commit()
    return after_profile_change(db, queue, candidate.id, jobs) if was_approved else {"resumed": [], "rematched": []}


def approve_records(db: Session, queue: ProcessingQueue, candidate: Candidate, record_ids: list[str], *, acknowledge: bool = False, edits: dict[str, str] | None = None) -> dict[str, Any]:
    """Approval makes a record a profile fact: its bullet may appear under that employer, and its skills count as
    confirmed for that role. Records with errors need an explicit acknowledgement (VAL-07)."""
    require_authorized(candidate)
    profile = profile_from_json(candidate.profile_json)
    records = [get_record(db, candidate.id, rid) for rid in dict.fromkeys(record_ids)]
    if not records:
        raise AppError(422, "nothing_to_approve", "Choose at least one suggestion to approve.")
    for record in records:
        if edits and edits.get(record.id, "").strip() and edits[record.id].strip() != (record.narrative or ""):
            record.narrative = edits[record.id].strip()[:1000]
            record.drafted_by = "operator"
        if not (record.narrative or "").strip():
            raise AppError(422, "no_bullet", "This suggestion has no bullet yet; write or draft one first.")
        record.issues_json = check_record(db, record, profile)
    blocked = [r for r in records if any(i.get("severity") == "error" for i in r.issues_json or [])]
    if blocked and not acknowledge:
        raise AppError(
            409,
            "unresolved_issues",
            f"{len(blocked)} suggestion(s) say something the description doesn't support. Edit them, or approve anyway after checking.",
            {"records": [{"id": r.id, "issues": r.issues_json} for r in blocked]},
        )
    _mark_approved(db, candidate, records, profile)
    candidate.profile_revision += 1
    db.commit()
    jobs = [j for r in records for j in r.source_job_ids_json or []]
    follow_up = after_profile_change(db, queue, candidate.id, jobs)
    return {"records": [record_view(r, profile) for r in records], **follow_up}


def _has_errors(record: CandidateRoleExperience) -> bool:
    return any(i.get("severity") == "error" for i in record.issues_json or [])


def auto_approve(db: Session, candidate: Candidate, records: list[CandidateRoleExperience]) -> list[CandidateRoleExperience]:
    """Settings → auto_approve_bullets: a bullet written from the operator's own description is added at once. An AI draft
    that says more than the description (numbers, tools, leadership) is first replaced by the description's own wording."""
    profile = profile_from_json(candidate.profile_json)
    ready = []
    for record in records:
        if _has_errors(record):
            record.narrative = offline_bullet(record)[:1000]
            record.drafted_by = "offline"
            record.issues_json = check_record(db, record, profile)
        if (record.narrative or "").strip() and not _has_errors(record):
            ready.append(record)
    if ready:
        _mark_approved(db, candidate, ready, profile)
        candidate.profile_revision += 1
    db.commit()
    return ready


def _mark_approved(db: Session, candidate: Candidate, records: list[CandidateRoleExperience], profile: CandidateProfile) -> None:
    """Approval makes each record a profile fact and confirms its skills for that role."""
    rows = _skill_rows(db, candidate.id)
    answers = _answers(db, candidate.id)
    for record in records:
        record.status = AdditionStatus.APPROVED
        record.approved_at = _now()
        role = resolve_role(profile, record.role_id, record.role_label)
        for skill in record.skills_json or []:
            key = normalize_skill(skill)
            _confirm_skill_row(db, candidate, rows, key, skill, f"Confirmed by the operator — used at {role.employer if role else record.role_label}")
            answer = answers.get(key)
            if answer is None:
                answer = CandidateSkillEvidence(candidate_id=candidate.id, skill_name=skill[:200], normalized_name=key, decision=SkillDecision.USED, role_ids_json=[], source_job_ids_json=[])
                db.add(answer)
                answers[key] = answer
            answer.decision = SkillDecision.USED
            if role is not None and role.id not in {r.id for r in resolve_refs(profile, answer.role_ids_json)}:
                answer.role_ids_json = [*(answer.role_ids_json or []), {"id": role.id, "label": role_label(role)}]
            answer.source_job_ids_json = list(dict.fromkeys([*(answer.source_job_ids_json or []), *(record.source_job_ids_json or [])]))
            answer.decided_at = _now()


# ---------------------------------------------------------------- waiting jobs (UX-03/04, JOB-12, MAT-09)


def waiting_jobs(db: Session) -> list[Job]:
    return list(
        db.scalars(
            select(Job).where(Job.confirmation_requested_at.is_not(None), Job.confirmation_resolved_at.is_(None)).order_by(Job.confirmation_requested_at)
        )
    )


def needs_confirmation(db: Session, job: Job, candidate: Candidate) -> dict[str, Any] | None:
    """The gap report when the job should wait for answers (unanswered skills or unapproved suggestions), else None."""
    report = analyze_gaps(db, job, candidate)
    return report if report["unanswered"] or report["pending_suggestions"] else None


def pause_for_confirmation(job: Job, report: dict[str, Any]) -> None:
    if job.confirmation_requested_at is None or job.confirmation_resolved_at is not None:
        job.confirmation_requested_at = _now()  # a new wait; an ongoing one keeps its start time for the metrics
    job.confirmation_resolved_at = None
    skills = [i["skill"] for i in report["items"] if i["needs_answer"]]
    names = _join(skills[:6]) + ("…" if len(skills) > 6 else "") if skills else "the suggested bullets"
    message = f"Waiting for answers about {names} for {report['candidate_name']}. Nothing is generated until then."
    job.warnings_json = [
        *(w for w in (job.warnings_json or []) if w.get("code") != NEEDS_CONFIRMATION),
        {"code": NEEDS_CONFIRMATION, "message": message, "skills": skills, "candidate_id": report["candidate_id"]},
    ]


def resolve_confirmation(job: Job) -> None:
    if job.confirmation_requested_at is not None and job.confirmation_resolved_at is None:
        job.confirmation_resolved_at = _now()
    job.warnings_json = [w for w in (job.warnings_json or []) if w.get("code") != NEEDS_CONFIRMATION]


def after_profile_change(db: Session, queue: ProcessingQueue, candidate_id: str, job_ids: list[str]) -> dict[str, list[str]]:
    """Waiting jobs whose top candidate this is continue automatically once nothing is left to answer; the other jobs
    the answers came from are re-matched, so their scores show before and after (JOB-12, MAT-09)."""
    resumed: list[str] = []
    for job in waiting_jobs(db):
        top = top_match(db, job)
        if top is None or top.candidate_id != candidate_id:
            continue
        candidate = db.get(Candidate, candidate_id)
        if needs_confirmation(db, job, candidate) is None:
            resolve_confirmation(job)
            db.commit()
            queue.enqueue(db, ProcessingJobType.PROCESS_JOB, idempotency_key=f"process:{job.id}:{uuid4().hex[:12]}", job_id=job.id)
            resumed.append(job.id)
    rematched: list[str] = []
    for job_id in dict.fromkeys(job_ids):
        job = db.get(Job, job_id)
        if job is None or job.id in resumed or job.parsed_requirements_json is None:
            continue
        if job.confirmation_requested_at is not None and job.confirmation_resolved_at is None:
            continue  # still waiting for other answers; it is re-matched when it continues
        queue.enqueue(db, ProcessingJobType.MATCH_JOB, idempotency_key=f"match:{job.id}:{uuid4().hex[:12]}", job_id=job.id)
        rematched.append(job.id)
    db.commit()
    return {"resumed": resumed, "rematched": rematched}


def handle_match_job(ai: AIService):
    def run(ctx: JobContext) -> None:
        with ctx.session() as db:
            job = db.get(Job, ctx.job_id)
            if job is None or job.parsed_requirements_json is None:
                return
            run_matching(db, ai, job)

    return run


def register_experience_handlers(queue: ProcessingQueue, ai: AIService) -> None:
    queue.register(ProcessingJobType.MATCH_JOB, handle_match_job(ai))


# ---------------------------------------------------------------- coverage and changes (VAL-09/10, EXP-07)


def baseline_for(db: Session, candidate: Candidate) -> dict[str, Any]:
    """The candidate's imported resume (text and skills) as the "before" for coverage and change lists."""
    profile = profile_from_json(candidate.profile_json)
    version = db.get(ResumeVersion, profile.source_version_id) if profile.source_version_id else None
    if version is None:
        version = db.scalar(select(ResumeVersion).where(ResumeVersion.candidate_id == candidate.id).order_by(ResumeVersion.version.desc()).limit(1))
    parsed = (version.parsed_profile_json or {}) if version else {}
    return {
        "text": (version.extracted_text or "") if version else "",
        "skills": [s.get("name", "") for s in parsed.get("skills", []) if isinstance(s, dict)],
    }


def _found(key: str, text: str) -> bool:
    return bool(text) and bool(find_skill_in_text(key, text, include_ambiguous=True))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]{3,}", text.lower())}


def duplicate_bullets(resume: ResumeContent | None, threshold: float = 0.75) -> list[list[str]]:
    bullets = [b.text for role in (resume.experience if resume else []) for b in role.bullets]
    pairs = []
    for i, a in enumerate(bullets):
        for b in bullets[i + 1 :]:
            wa, wb = _words(a), _words(b)
            if wa and wb and len(wa & wb) / len(wa | wb) >= threshold:
                pairs.append([a, b])
    return pairs


_UNSUPPORTED_CODES = {"unsupported_bullet", "unsupported_number", "unverified_skill", "unsupported_tool", "ai_unsupported_claim", "unverified_skill_mention"}


def coverage(req: JobRequirements, resume: ResumeContent | None, snap: dict[str, Any], baseline: dict[str, Any] | None, issues: list[dict[str, Any]], unanswered: list[str] | None = None) -> dict[str, Any]:
    """VAL-10: how much of the job description's skill list the tailored resume covers, what is confirmed, what is
    missing, and what would improve it. Keyword coverage only — not an applicant tracking system score."""
    items, _ = job_skill_items(req)
    resume_text = resume_plain_text(resume, include_contact=False) if resume else ""
    resume_skills = {normalize_skill(s) for s in (resume.skills if resume else [])}
    profile_skills = {s["normalized"] for s in snap.get("skills", [])}
    profile_text = _evidence_text(snap)
    base_text = "\n".join([(baseline or {}).get("text", ""), ", ".join((baseline or {}).get("skills", []))]) if baseline else ""
    rows = []
    for item in items:
        key = item["normalized"]
        rows.append(
            {
                **item,
                "in_resume": key in resume_skills or _found(key, resume_text),
                "confirmed": key in profile_skills or _found(key, profile_text) or any(skill_covers(s["name"], key) for s in snap.get("skills", [])),
                "in_original_resume": _found(key, base_text) if baseline else None,
            }
        )

    def group(kind: str) -> dict[str, Any]:
        chosen = [r for r in rows if r["kind"] == kind]
        return {
            "total": len(chosen),
            "in_resume": sum(1 for r in chosen if r["in_resume"]),
            "confirmed": sum(1 for r in chosen if r["confirmed"]),
            "in_original_resume": sum(1 for r in chosen if r["in_original_resume"]) if baseline else None,
            "missing": [r["skill"] for r in chosen if not r["in_resume"]],
        }

    def pct(count: int | None) -> int | None:
        return round(100 * count / len(rows)) if rows and count is not None else None

    duplicates = duplicate_bullets(resume)
    unsupported = [i for i in issues if i.get("code") in _UNSUPPORTED_CODES and i.get("severity") in ("error", "warning")]
    required, preferred = group("required"), group("preferred")
    suggestions: list[str] = []
    if unanswered:
        suggestions.append(f"Answer whether the candidate has used {_join(unanswered[:5])}{'…' if len(unanswered) > 5 else ''}; confirmed skills can then be added.")
    confirmed_not_shown = [r["skill"] for r in rows if r["confirmed"] and not r["in_resume"]]
    if confirmed_not_shown:
        them = "it" if len(confirmed_not_shown) == 1 else "them"
        suggestions.append(f"Confirmed but not on this resume: {_join(confirmed_not_shown[:5])}. Regenerate to include {them}.")
    general_only = [s["name"] for s in snap.get("skills", []) if not s.get("roles") and any(r["normalized"] == s["normalized"] for r in rows) and not _found(s["normalized"], "\n".join(b["text"] for role in snap.get("employment", []) for b in role["bullets"]))]
    if general_only:
        one = len(general_only) == 1
        suggestions.append(
            f"{_join(general_only[:5])} {'appears' if one else 'appear'} only in Skills. Add where {'it was' if one else 'they were'} used (Evidence tab) for a stronger bullet."
        )
    if duplicates:
        suggestions.append(f"{len(duplicates)} pair(s) of bullets say nearly the same thing; merge or vary them.")
    if unsupported:
        suggestions.append(f"Fix {len(unsupported)} unsupported claim(s) before exporting.")
    if preferred["missing"]:
        suggestions.append(f"Preferred skills not covered: {_join(preferred['missing'][:5])}.")
    return {
        "rows": rows,
        "required": required,
        "preferred": preferred,
        "keyword_coverage_pct": pct(sum(1 for r in rows if r["in_resume"])),
        "confirmed_coverage_pct": pct(sum(1 for r in rows if r["confirmed"])),
        "original_coverage_pct": pct(sum(1 for r in rows if r["in_original_resume"])) if baseline else None,
        "duplicates": duplicates,
        "unsupported_claims": len(unsupported),
        "suggestions": suggestions,
        "skill_gap_report": snap.get("skill_gap_report") or [],
        "note": "Coverage of this job description's skills. It is not an applicant tracking system score.",
    }


def resume_changes(current: ResumeContent | None, previous: ResumeContent | None, snap: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """VAL-09: skills and bullets added, rewritten or left out, compared with the imported resume and the prior version."""
    # The candidate's own wording, before a bullet was reworded to name a job skill its work shows (v1.8.5).
    sources = {b["id"]: b.get("original_text") or b["text"] for role in snap.get("employment", []) for b in role["bullets"]}
    additions = set(snap.get("addition_ids") or [])
    base_skills = {normalize_skill(s): s for s in baseline.get("skills", []) if normalize_skill(s)}
    bullets = []
    cited: set[str] = set()
    for role in current.experience if current else []:
        for bullet in role.bullets:
            cited |= set(bullet.source_ids)
            originals = [sources[sid] for sid in bullet.source_ids if sid in sources]
            if any(sid in additions for sid in bullet.source_ids):
                kind = "added"
            elif any(o.strip() == bullet.text.strip() for o in originals):
                kind = "kept"
            else:
                kind = "rewritten"
            bullets.append(
                {
                    "role": f"{role.title} at {role.employer}",
                    "text": bullet.text,
                    "kind": kind,
                    "original": originals[0] if kind == "rewritten" and originals else None,
                    "addition_ids": [sid for sid in bullet.source_ids if sid in additions],
                }
            )
    not_used = [
        {"role": f"{role['title']} at {role['employer']}", "text": b["text"]}
        for role in snap.get("employment", [])
        for b in role["bullets"]
        if b["id"] not in cited and b["id"] not in additions
    ]
    current_skills = {normalize_skill(s): s for s in (current.skills if current else [])}
    result: dict[str, Any] = {
        "skills": {
            "added": [s for k, s in current_skills.items() if k not in base_skills],
            "kept": [s for k, s in current_skills.items() if k in base_skills],
            "not_used": [s for k, s in base_skills.items() if k not in current_skills],
        },
        "bullets": bullets,
        "not_used_bullets": not_used,
        "previous": None,
    }
    if previous is not None and current is not None:
        before = {b.text for r in previous.experience for b in r.bullets}
        after = {b.text for r in current.experience for b in r.bullets}
        previous_skills = {normalize_skill(s) for s in previous.skills}
        result["previous"] = {
            "bullets_added": sorted(after - before),
            "bullets_removed": sorted(before - after),
            "skills_added": [s for k, s in current_skills.items() if k not in previous_skills],
            "skills_removed": [s for s in previous.skills if normalize_skill(s) not in current_skills],
        }
    return result
