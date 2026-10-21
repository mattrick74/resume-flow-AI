"""JobService / JDAnalyzer (PRD §6): intake, duplicate detection, requirement extraction."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.db.models import Candidate, CandidateMatch, CandidateSkill, Job
from resumeflow.domain.enums import GenerationOperation, PipelineStatus
from resumeflow.domain.requirements import JobRequirements, empty_requirements
from resumeflow.domain.skills import detect_known_skills, display_name, normalize_skill
from resumeflow.errors import AppError
from resumeflow.services.ai.provider import AIError
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.settings import load_settings

_TRACKING_PARAMS = {"gclid", "fbclid", "ref", "refid", "source", "src", "trk", "trackingid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "lipi"}
SIMILARITY_THRESHOLD = 0.85


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise AppError(422, "invalid_url", "Enter the full job posting URL, starting with http:// or https://")
    host = parts.netloc.lower().removeprefix("www.")
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or ""
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "")


def _normalize_jd(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def jd_fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize_jd(text).encode()).hexdigest()


def _shingles(text: str, size: int = 5) -> set[int]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < size:
        return {hash(" ".join(words))}
    return {hash(" ".join(words[i : i + size])) for i in range(len(words) - size + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


_INJECTION_PATTERNS = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)",
    r"disregard (?:all |any |the )?(?:previous|prior|above) ",
    r"you are (?:now )?(?:an? |the )?(?:ai|assistant|chatgpt|language model)",
    r"system prompt",
    r"(?:reveal|print|show) (?:your|the) (?:instructions|prompt)",
    r"(?:rate|rank|score) (?:this|the) candidate (?:as|at) ",
    r"respond only with",
]


def detect_prompt_injection(text: str) -> list[str]:
    lowered = text.lower()
    return [p for p in _INJECTION_PATTERNS if re.search(p, lowered)]


def find_duplicates(db: Session, normalized_url: str, fingerprint: str, raw_jd: str, exclude_id: str | None = None) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for job in db.scalars(select(Job).where((Job.normalized_url == normalized_url) | (Job.jd_hash == fingerprint))):
        if job.id == exclude_id:
            continue
        reason = "same_url" if job.normalized_url == normalized_url else "same_text"
        matches[job.id] = {"job_id": job.id, "title": job.title, "company": job.company, "created_at": job.created_at, "reason": reason, "similarity": 1.0}
    length = len(raw_jd)
    recent = db.scalars(
        select(Job).where(Job.created_at >= _now() - timedelta(days=120)).order_by(Job.created_at.desc()).limit(1000)
    )
    for job in recent:
        if job.id in matches or job.id == exclude_id or not (0.6 * length <= len(job.raw_jd) <= 1.6 * length):
            continue
        score = similarity(raw_jd, job.raw_jd)
        if score >= SIMILARITY_THRESHOLD:
            matches[job.id] = {"job_id": job.id, "title": job.title, "company": job.company, "created_at": job.created_at, "reason": "similar_text", "similarity": round(score, 3)}
    return list(matches.values())


def create_job(db: Session, payload: dict[str, Any], *, confirm_duplicate: bool = False) -> Job:
    raw_jd = (payload.get("raw_jd") or "").strip()
    if len(raw_jd) < 50:
        raise AppError(422, "jd_too_short", "Paste the full job description (at least a few sentences).")
    url = (payload.get("job_url") or "").strip()
    normalized = normalize_url(url)
    fingerprint = jd_fingerprint(raw_jd)
    duplicates = find_duplicates(db, normalized, fingerprint, raw_jd)
    same_url = [m for m in duplicates if m["reason"] == "same_url"]
    if same_url and load_settings(db).skip_known_urls:
        # The same posting is already in history: skip it (Settings → "Skip job URLs already in history").
        raise AppError(409, "url_in_history", "This job posting is already in your history, so it was skipped.", {"matches": same_url})
    if duplicates and not confirm_duplicate:
        raise AppError(409, "duplicate_job", "This job looks like one you've already entered.", {"matches": duplicates})
    warnings = []
    if detect_prompt_injection(raw_jd):
        warnings.append(
            {
                "code": "possible_prompt_injection",
                "message": "This job description contains text that looks like instructions to an AI. It is treated as data only; review the extracted requirements.",
            }
        )
    job = Job(
        job_url=url,
        normalized_url=normalized,
        raw_jd=raw_jd,
        jd_hash=fingerprint,
        warnings_json=warnings,
        **{k: (payload.get(k) or None) for k in ("company", "title", "recruiter", "rate", "location", "employment_type", "notes")},
    )
    db.add(job)
    db.commit()
    return job


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise AppError(404, "job_not_found", "Job not found")
    return job


# ---------------------------------------------------------------- analysis

_REQUIRED_HEADINGS = re.compile(
    r"^(?:requirements|required|qualifications|minimum qualifications|basic qualifications|required qualifications|"
    r"must[- ]haves?|what you(?:'ll)? (?:need|bring)|who you are|skills(?: and experience)?|experience|you have)\s*:?$",
    re.IGNORECASE,
)
_PREFERRED_HEADINGS = re.compile(
    r"^(?:preferred|preferred qualifications|nice[- ]to[- ]haves?|bonus(?: points)?|pluses|desired(?: skills| qualifications)?|"
    r"additional qualifications|it's a plus if)\s*:?$",
    re.IGNORECASE,
)
_DUTY_HEADINGS = re.compile(
    r"^(?:responsibilities|key responsibilities|what you(?:'ll)? do|duties|the role|role|your impact|day[- ]to[- ]day|about the role)\s*:?$",
    re.IGNORECASE,
)
_OTHER_HEADING = re.compile(r"^(?:about (?:us|the company)|benefits|perks|compensation|salary|equal opportunity|eeo|how to apply)\s*:?$", re.IGNORECASE)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.IGNORECASE)
_CITY_STATE = re.compile(r"\b([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3}),\s*([A-Z]{2})\b")
_AUTH = re.compile(r"authori[sz]ed to work|work authori[sz]ation|sponsorship|u\.?s\.? citizen|citizenship|green card|permanent resident", re.IGNORECASE)
_CLEARANCE = re.compile(r"\b(top secret(?:/sci)?|ts/sci|secret|public trust)\b[^.\n]*clearance|clearance[^.\n]*\b(top secret|ts/sci|secret|public trust)\b", re.IGNORECASE)
_CREDENTIAL = re.compile(r"\b(licen[sc]e[ds]?|certification required|must (?:hold|have) (?:an? |the )?(?:active )?\w[\w\s]*certif\w*)\b", re.IGNORECASE)
_BULLET = re.compile(r"^[•▪●◦\-*–—•]+\s*")


_COMPANY_LABEL = re.compile(r"^(?:company|employer|organi[sz]ation|client)\s*:\s*(.{2,80})$", re.IGNORECASE)
_COMPANY_BEFORE_PLACE = re.compile(r"^([A-Z][\w&.,'’ ]{1,60}?)\s+(?:-|–|—|\||•|·|at)\s+(?:.*,\s*[A-Z]{2}\b|remote\b|hybrid\b|on-?site\b)", re.IGNORECASE)


_LOGO_MARKER = re.compile(r"^(?:company logo|company branding image|employer logo)$", re.IGNORECASE)


def _guess_company(head: list[str], title: str | None, top: list[str] | None = None) -> str | None:
    """'Acme Data Co - Austin, TX (Hybrid)', 'Company: Acme', or the name under "Company Logo" on job-board pages."""
    for line in head:
        if match := _COMPANY_LABEL.match(line):
            return match.group(1).strip()
    lines = top or head
    for index, line in enumerate(lines[:-1]):
        if _LOGO_MARKER.match(line.strip()):
            following = next((x.strip() for x in lines[index + 1 :] if not _LOGO_MARKER.match(x.strip())), "")
            if 2 <= len(following) <= 120 and not following.lower().startswith(("fitment", "dice", "apply")):
                return following
    for line in head:
        if title and line.strip() == title.strip():
            continue
        if match := _COMPANY_BEFORE_PLACE.match(line.strip()):
            candidate = match.group(1).strip(" ,")
            if not _CITY_STATE.fullmatch(candidate) and candidate.lower() not in {"location", "remote", "hybrid"}:
                return candidate[:120]
    return None


def _seniority_from_title(title: str | None) -> str:
    t = (title or "").lower()
    for words, level in (
        (("intern", "internship"), "intern"),
        (("chief", "vp", "vice president", "head of", "director"), "executive"),
        (("principal", "staff", "distinguished"), "principal"),
        (("lead", "manager"), "lead"),
        (("senior", "sr.", "sr "), "senior"),
        (("junior", "jr.", "jr ", "entry", "associate", "graduate"), "junior"),
    ):
        if any(w in t for w in words):
            return level
    return "mid" if t else "unknown"


def heuristic_requirements(raw_jd: str, vocabulary: set[str], title: str | None = None, company: str | None = None) -> JobRequirements:
    lines = [line.strip() for line in raw_jd.split("\n")]
    nonempty = [line for line in lines if line]
    buckets: dict[str, list[str]] = {"required": [], "preferred": [], "duties": [], "general": []}
    current = "general"
    for line in nonempty:
        if len(line) <= 60 and _REQUIRED_HEADINGS.match(line):
            current = "required"
            continue
        if len(line) <= 60 and _PREFERRED_HEADINGS.match(line):
            current = "preferred"
            continue
        if len(line) <= 60 and _DUTY_HEADINGS.match(line):
            current = "duties"
            continue
        if len(line) <= 60 and _OTHER_HEADING.match(line):
            current = "other"
            buckets.setdefault("other", [])
            continue
        buckets.setdefault(current, []).append(_BULLET.sub("", line))

    def skills_in(text: str) -> list[str]:
        return sorted(detect_known_skills(text, vocabulary))

    required_text = "\n".join(buckets["required"])
    preferred_text = "\n".join(buckets["preferred"])
    if required_text:
        mandatory = skills_in(required_text)
    else:
        mandatory = skills_in("\n".join(buckets["general"] + buckets["duties"]))
    preferred = [s for s in skills_in(preferred_text) if s not in mandatory]

    years = [int(m.group(1)) for m in _YEARS.finditer(required_text or raw_jd) if 0 < int(m.group(1)) <= 30]
    lowered = raw_jd.lower()
    if "hybrid" in lowered:
        arrangement = "hybrid"
    elif re.search(r"\bon[- ]?site\b|\bin[- ]office\b|\bin the office\b", lowered):
        arrangement = "onsite"
    elif re.search(r"\bremote\b", lowered):
        arrangement = "remote"
    else:
        arrangement = "unknown"
    auth_lines = [_BULLET.sub("", line) for line in nonempty if _AUTH.search(line)]
    clearance = _CLEARANCE.search(raw_jd)
    credentials = [line for line in buckets["required"] if _CREDENTIAL.search(line)]
    location_match = next((m for m in (_CITY_STATE.search(line) for line in nonempty[:8]) if m), None)
    job_title = title or (nonempty[0][:120] if nonempty and len(nonempty[0]) <= 120 else None)

    return JobRequirements(
        title=job_title,
        employer=company or _guess_company(nonempty[:6], job_title, nonempty[:20]),
        seniority=_seniority_from_title(job_title),  # type: ignore[arg-type]
        min_years_experience=float(max(years)) if years else None,
        mandatory_skills=[display_name(s) for s in mandatory],
        preferred_skills=[display_name(s) for s in preferred],
        duties=[d[:200] for d in buckets["duties"][:15]],
        industry=None,
        location=location_match.group(0) if location_match else None,
        work_arrangement=arrangement,  # type: ignore[arg-type]
        work_authorization="; ".join(auth_lines)[:300] or None,
        security_clearance=clearance.group(0)[:120] if clearance else None,
        required_credentials=[c[:200] for c in credentials[:10]],
        employment_type=next((t for t in ("Full-time", "Part-time", "Contract", "Temporary", "Internship") if t.lower() in lowered), None),
        other_hard_requirements=[],
    )


JD_INSTRUCTIONS = f"""You extract structured hiring requirements from one job description.
{UNTRUSTED_NOTICE}

Rules:
- Only include what the job description explicitly states. Do not guess or add requirements.
- mandatory_skills: skills/technologies stated as required. preferred_skills: stated as preferred/nice-to-have.
- Use short canonical skill names (e.g. "Python", "AWS", "Kubernetes").
- work_authorization, security_clearance and required_credentials are pass/fail conditions: copy the
  requirement wording; use null/empty when the posting doesn't state one.
- seniority: infer only from the title or stated level; otherwise "unknown"."""


def _clean_requirements(req: JobRequirements) -> JobRequirements:
    def dedupe(items: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for item in items:
            key = normalize_skill(item)
            if key and key not in seen:
                seen[key] = item.strip()[:100]
        return list(seen.values())

    mandatory = dedupe(req.mandatory_skills)[:40]
    mandatory_keys = {normalize_skill(s) for s in mandatory}
    preferred = [s for s in dedupe(req.preferred_skills) if normalize_skill(s) not in mandatory_keys][:40]
    return req.model_copy(
        update={
            "mandatory_skills": mandatory,
            "preferred_skills": preferred,
            # "No", "None", "N/A"… mean nothing is required, not a requirement called "No".
            "work_authorization": _stated(req.work_authorization),
            "security_clearance": _stated(req.security_clearance),
            "required_credentials": [c for c in req.required_credentials if _stated(c)],
            "other_hard_requirements": [c for c in req.other_hard_requirements if _stated(c)],
        }
    )


_NOT_STATED = re.compile(
    r"^\s*(?:no|none|nil|null|n/?a|-+|not (?:stated|specified|required|applicable|mentioned)|no (?:clearance|requirement)s?(?: required)?)\s*\.?\s*$",
    re.IGNORECASE,
)


def _stated(value: str | None) -> str | None:
    return None if value is None or _NOT_STATED.match(value) else value.strip() or None


def analyze_job(db: Session, ai: AIService | None, job: Job, *, use_cache: bool = True) -> Job:
    job.status = PipelineStatus.ANALYZING
    db.commit()
    try:
        cached = (
            db.scalar(
                select(Job).where(Job.jd_hash == job.jd_hash, Job.id != job.id, Job.parsed_requirements_json.is_not(None)).limit(1)
            )
            if use_cache
            else None
        )
        if cached is not None:
            requirements = JobRequirements.model_validate(cached.parsed_requirements_json)
            source = "cache"
        elif ai is not None and ai.available():
            try:
                requirements = ai.run(
                    operation=GenerationOperation.JD_ANALYSIS,
                    role="analysis",
                    instructions=JD_INSTRUCTIONS,
                    input_text=wrap("job_description", job.raw_jd[:40_000]),
                    schema=JobRequirements,
                    job_id=job.id,
                )
                source = "ai"
            except AIError as exc:
                requirements = heuristic_requirements(job.raw_jd, _vocabulary(db), job.title, job.company)
                source = "heuristic"
                job.warnings_json = [*(job.warnings_json or []), {"code": "ai_analysis_failed", "message": f"AI analysis failed, used offline rules: {exc}"}]
        else:
            requirements = heuristic_requirements(job.raw_jd, _vocabulary(db), job.title, job.company)
            source = "heuristic"
        requirements = _clean_requirements(requirements)
        job.parsed_requirements_json = requirements.model_dump(mode="json")
        job.requirements_source = source
        job.title = job.title or requirements.title
        job.company = job.company or requirements.employer
        job.location = job.location or requirements.location
        job.employment_type = job.employment_type or requirements.employment_type
        job.analyzed_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        job.status = PipelineStatus.FAILED
        db.commit()
        raise
    return job


def update_requirements(db: Session, job: Job, requirements: JobRequirements) -> Job:
    job.parsed_requirements_json = _clean_requirements(requirements).model_dump(mode="json")
    job.requirements_source = "manual"
    db.commit()
    return job


def _vocabulary(db: Session) -> set[str]:
    """Skills of all candidates help detect the same terms in job descriptions."""
    return set(db.scalars(select(CandidateSkill.normalized_name).distinct()))


def requirements_of(job: Job) -> JobRequirements:
    if job.parsed_requirements_json is None:
        return empty_requirements()
    # Cleaned on read too, so jobs analyzed before a cleaning rule existed benefit from it.
    return _clean_requirements(JobRequirements.model_validate(job.parsed_requirements_json))


def guess_details(raw_jd: str) -> dict[str, str | None]:
    """Offline best guess at title, company and location from the top of a posting (no AI)."""
    top = [line.strip() for line in raw_jd.split("\n") if line.strip()][:20]
    head = top[:8]
    title = head[0][:120] if head and len(head[0]) <= 120 else None
    location = next((m.group(0) for m in (_CITY_STATE.search(line) for line in head) if m), None)
    return {"title": title, "company": _guess_company(head[:6], title, top), "location": location}


def job_summary(job: Job) -> dict[str, Any]:
    # A saved job isn't analyzed yet; show a guess so the list is readable. Analysis sets the real values.
    guess = guess_details(job.raw_jd) if job.parsed_requirements_json is None else {}
    return {
        "id": job.id,
        "job_url": job.job_url,
        "company": job.company or guess.get("company"),
        "title": job.title or guess.get("title"),
        "location": job.location or guess.get("location"),
        "status": job.status,
        "created_at": job.created_at,
        "analyzed_at": job.analyzed_at,
        # Background processing paused until the top candidate's missing job skills are answered (v1.8 UX-04).
        "needs_confirmation": job.confirmation_requested_at is not None and job.confirmation_resolved_at is None,
    }


def job_detail(db: Session, job: Job) -> dict[str, Any]:
    from resumeflow.db.models import Application

    matches = db.execute(
        select(CandidateMatch, Candidate.full_name)
        .join(Candidate, Candidate.id == CandidateMatch.candidate_id)
        .where(CandidateMatch.job_id == job.id)
        .order_by(CandidateMatch.rank.is_(None), CandidateMatch.rank, CandidateMatch.score.desc())
    ).all()
    applications = db.scalars(select(Application).where(Application.job_id == job.id).order_by(Application.created_at.desc()))
    return {
        **job_summary(job),
        "recruiter": job.recruiter,
        "rate": job.rate,
        "employment_type": job.employment_type,
        "notes": job.notes,
        "raw_jd": job.raw_jd,
        "normalized_url": job.normalized_url,
        "requirements": job.parsed_requirements_json,
        "requirements_source": job.requirements_source,
        "warnings": job.warnings_json or [],
        "matches": [
            {
                "candidate_id": m.candidate_id,
                "candidate_name": name,
                "score": m.score,
                "potential_score": m.potential_score,
                "previous_score": m.previous_score,
                "rank": m.rank,
                "eligibility_status": m.eligibility_status,
                "details": m.matching_details_json,
            }
            for m, name in matches
        ],
        "applications": [
            {"id": a.id, "candidate_id": a.candidate_id, "status": a.status, "created_at": a.created_at} for a in applications
        ],
    }
