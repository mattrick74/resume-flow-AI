"""JobService / JDAnalyzer (PRD §6): intake, duplicate detection, requirement extraction."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = {"gclid", "fbclid", "ref", "refid", "source", "src", "trk", "trackingid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "lipi"}


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


def create_job(db: Session, payload: dict[str, Any], *, confirm_duplicate: bool = False) -> Job:
    raw_jd = (payload.get("raw_jd") or "").strip()
    if len(raw_jd) < 50:
        raise AppError(422, "jd_too_short", "Paste the full job description (at least a few sentences).")
    url = (payload.get("job_url") or "").strip()
    normalized = normalize_url(url) if url else ""
    fingerprint = jd_fingerprint(raw_jd)
    # ``confirm_duplicate`` remains in the API for compatibility. Repeated URLs
    # and descriptions are always saved as independent jobs and fully processed.
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
        **{k: (payload.get(k) or None) for k in ("company", "title", "recruiter", "rate", "location", "employment_type", "notes", "source", "site")},
    )
    if not job.title and payload.get("page_title"):
        # A captured page's title ("Senior Engineer - Acme | LinkedIn") until analysis finds the real one.
        job.title = re.split(r"\s+[|\-–—]\s+", payload["page_title"].strip())[0][:200] or None
    db.add(job)
    db.commit()
    return job


def same_url_history(db: Session, normalized_url: str, exclude_job_id: str | None = None) -> list[dict[str, Any]]:
    """Earlier jobs with the same posting URL and their applications (v2.0): shown when a posting is captured again.
    Saving it again stays allowed."""
    from resumeflow.db.models import Application

    if not normalized_url:
        return []
    jobs = db.scalars(select(Job).where(Job.normalized_url == normalized_url, Job.id != (exclude_job_id or "")).order_by(Job.created_at.desc()))
    history = []
    for job in jobs:
        applications = db.execute(
            select(Application, Candidate.full_name)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Application.job_id == job.id)
            .order_by(Application.created_at.desc())
        ).all()
        history.append(
            {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "status": job.status,
                "created_at": job.created_at,
                "applications": [
                    {
                        "application_id": app.id,
                        "candidate_name": name,
                        "status": app.status,
                        "application_date": app.application_date,
                        "submitted_at": app.submitted_at,
                    }
                    for app, name in applications
                ],
            }
        )
    return history


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise AppError(404, "job_not_found", "Job not found")
    return job


# ---------------------------------------------------------------- analysis

_REQUIRED_HEADINGS = re.compile(
    r"^(?:requirements|minimum requirements|required|required skills|skills required|required experience|qualifications|"
    r"minimum qualifications|basic qualifications|required qualifications|must[- ]haves?|"
    r"what you(?:['’]ll| will)? (?:need|bring)|"
    r"what we(?:['’]re| are) looking for|what we(?:['’]re| are) seeking|who you are|"
    r"about you|candidate profile|your profile|ideal candidate|skills(?: and experience)?|experience|you have)\s*:?$",
    re.IGNORECASE,
)
_PREFERRED_HEADINGS = re.compile(
    r"^(?:preferred|preferred skills|preferred qualifications|nice[- ]to[- ]haves?|bonus(?: points)?|pluses|desired(?: skills| qualifications)?|"
    r"additional qualifications|it's a plus if)\s*:?$",
    re.IGNORECASE,
)
_DUTY_HEADINGS = re.compile(
    r"^(?:responsibilities|job responsibilities|key responsibilities|responsibilities and duties|key duties|essential functions|"
    r"what you(?:['’]ll| will)? (?:do|be doing|work on)|duties|the role|role|your impact|"
    r"day[- ]to[- ]day|about the role)\s*:?$",
    re.IGNORECASE,
)
_OTHER_HEADING = re.compile(
    r"^(?:about (?:us|the company)|who we are|our company|company overview|our mission|our culture|culture|"
    r"our values|values|why (?:join|work (?:with|for)) us|what we offer|what you(?:['’]ll| will) get|"
    r"benefits|our benefits|benefits and perks|perks|compensation|salary|diversity|inclusion|"
    r"equal opportunity|equal employment opportunity|eeo|eeo statement|how to apply)\s*:?$",
    re.IGNORECASE,
)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.IGNORECASE)
_CITY_STATE = re.compile(r"\b([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,3}),\s*([A-Z]{2})\b")
_AUTH = re.compile(r"authori[sz]ed to work|work authori[sz]ation|sponsorship|u\.?s\.? citizen|citizenship|green card|permanent resident", re.IGNORECASE)
_CLEARANCE = re.compile(r"\b(top secret(?:/sci)?|ts/sci|secret|public trust)\b[^.\n]*clearance|clearance[^.\n]*\b(top secret|ts/sci|secret|public trust)\b", re.IGNORECASE)
_CREDENTIAL = re.compile(r"\b(licen[sc]e[ds]?|certification required|must (?:hold|have) (?:an? |the )?(?:active )?\w[\w\s]*certif\w*)\b", re.IGNORECASE)
_BULLET = re.compile(r"^[•▪●◦\-*–—•]+\s*")
_GENERIC_TITLE = re.compile(
    r"^(?:job|position|role)?\s*(?:description|overview|summary)?$|^(?:careers?|about us|the opportunity|who we are)$",
    re.IGNORECASE,
)
_PREFERENCE_CUE = re.compile(
    r"\b(?:preferred|nice[- ]to[- ]have|bonus (?:point|qualification|skill)s?|optional qualifications?|a plus)\b",
    re.IGNORECASE,
)
_DUTY_CUE = re.compile(r"\b(?:responsibilities|what you(?:'ll)? do|your impact|day[- ]to[- ]day|duties)\b", re.IGNORECASE)
_STRONG_REQUIREMENT_CUE = re.compile(r"\b(?:must|required|minimum|qualification|you have|you bring)\b", re.IGNORECASE)
_TECH_REQUIREMENT_CUE = re.compile(
    r"\b(?:experience with|experienced in|proficien(?:t|cy) (?:with|in)|knowledge of|hands[- ]on|expertise in|"
    r"familiar(?:ity)? with|using|worked? with)\b",
    re.IGNORECASE,
)
_TOOL_INTRO_CUE = re.compile(
    r"\b(?:experience with|experienced in|proficien(?:t|cy) (?:with|in)|knowledge of|expertise in|"
    r"familiar(?:ity)? with|using|worked? with)\b",
    re.IGNORECASE,
)
_UNCAPTURED_HARD_TOPIC = re.compile(
    r"\b(?:bachelor(?:'s)?|master(?:'s)?|degree|education|travel|shift|weekend|on[- ]call|bilingual|"
    r"language requirement|lift(?:ing)?|relocat(?:e|ion)|commut(?:e|ing)|driver(?:'s)? licen[sc]e)\b",
    re.IGNORECASE,
)
_CONDITIONAL_REQUIREMENT = re.compile(
    r"\b(?:either|one of|and/or|or equivalent|in lieu of|unless|except)\b",
    re.IGNORECASE,
)
_COMMON_NONTECH_REQUIREMENT = re.compile(
    r"\b(?:communication|written|verbal|leadership|teamwork|collaboration|collaborative|interpersonal|"
    r"problem[- ]solving|analytical|critical thinking|attention to detail|organi[sz]ational|time management|"
    r"self[- ]motivated|adaptability|mentoring|stakeholder management|fast learner|team player|work ethic|"
    r"results oriented|customer focus|ownership|initiative|curiosity|integrity|creativity)\b",
    re.IGNORECASE,
)
_RESPONSIBILITY_LINE = re.compile(
    r"^(?:(?:you\s+(?:will|['’]ll)\s+)?(?:build|create|design|develop|implement|architect|deploy|operate|maintain|manage|lead|own|"
    r"drive|deliver|analy[sz]e|support|collaborate|partner|write|test|monitor|optimi[sz]e|"
    r"coordinate|establish|define|provide|ensure)|(?:responsible|accountable) for|"
    r"(?:building|creating|designing|developing|implementing|architecting|deploying|operating|maintaining|managing|"
    r"leading|owning|driving|delivering|analy[sz]ing|supporting|collaborating|partnering|writing|testing|monitoring|"
    r"optimi[sz]ing|coordinating|establishing|defining|providing|ensuring))\b",
    re.IGNORECASE,
)
_DOMAIN_REQUIREMENT = re.compile(
    r"\b(?:(?:experience|background|knowledge|expertise|familiarity)\s+(?:working\s+)?(?:in|with|of)\s+(?:the\s+)?"
    r"(?:health(?:care)?|medical|pharma(?:ceutical)?|biotech|fintech|financial services|banking|insurance|"
    r"retail|e[- ]?commerce|government|public sector|defen[cs]e|telecommunications?|manufacturing|automotive|"
    r"energy|utilities|education|edtech|travel|hospitality|real estate|logistics|supply chain)|"
    r"(?:health(?:care)?|medical|pharma(?:ceutical)?|biotech|fintech|financial services|banking|insurance|"
    r"retail|e[- ]?commerce|government|public sector|defen[cs]e|telecommunications?|manufacturing|automotive|"
    r"energy|utilities|education|edtech|travel|hospitality|real estate|logistics|supply chain)\s+"
    r"(?:industry\s+|domain\s+)?(?:experience|background|knowledge|expertise|familiarity)|"
    r"(?:industry|domain|sector)\s+(?:experience|background|knowledge|expertise|familiarity)|"
    r"\d{1,2}\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience\s+)?(?:in|with)\s+(?:the\s+)?"
    r"(?:health(?:care)?|medical|pharma(?:ceutical)?|biotech|fintech|financial services|banking|insurance|"
    r"retail|e[- ]?commerce|government|public sector|defen[cs]e|telecommunications?|manufacturing|automotive|"
    r"energy|utilities|education|edtech|travel|hospitality|real estate|logistics|supply chain))\b",
    re.IGNORECASE,
)
_SKILL_ITEM_SPLIT = re.compile(
    r"\s*(?:,|;|\band/or\b|\band\b|\bor\b)\s*|\s+[|/]\s+",
    re.IGNORECASE,
)
_HEADING_TOPIC = re.compile(
    r"\b(?:overview|summary|requirements?|qualifications?|skills?|experience|responsibilities|duties|role|impact|"
    r"benefits?|perks?|compensation|company|team|culture|values?|opportunity|profile|application|location|schedule|"
    r"technology|stack)\b",
    re.IGNORECASE,
)
_EMPLOYMENT_TYPES = ("full-time", "part-time", "contract", "temporary", "internship")
_EMPLOYMENT_TYPE_DISPLAY = {
    "full-time": "Full-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Internship",
}


@dataclass(frozen=True)
class HeuristicConfidence:
    """Whether the fast local JD extraction is safe to use without an AI round trip."""

    score: float
    sufficient: bool
    reasons: tuple[str, ...]


def _looks_like_unrecognized_heading(line: str, index: int) -> bool:
    """Identify section boundaries we cannot safely classify without guessing."""
    if index < 2 or len(line) > 60 or _BULLET.match(line):
        return False
    core = line.rstrip(":").strip()
    words = re.findall(r"[A-Za-z][A-Za-z'’&/-]*", core)
    if not 1 <= len(words) <= 8 or re.search(r"[.!?]$", core):
        return False
    if line.rstrip().endswith(":"):
        return True
    if re.match(r"^(?:what|why|who|how|our|your|about)\b", core, re.IGNORECASE):
        return True
    significant = [word for word in words if word.lower() not in {"a", "an", "and", "for", "of", "the", "to"}]
    title_case_topic = _HEADING_TOPIC.search(core) and all(word[0].isupper() or word.isupper() for word in significant)
    return bool(significant) and bool(title_case_topic)


def _jd_sections(raw_jd: str) -> tuple[list[str], dict[str, list[str]], frozenset[str]]:
    """Split a line-oriented JD once so extraction and confidence use identical boundaries."""
    lines = [line.strip() for line in raw_jd.split("\n")]
    nonempty = [line for line in lines if line]
    buckets: dict[str, list[str]] = {"required": [], "preferred": [], "duties": [], "general": []}
    headings: set[str] = set()
    current = "general"
    for index, line in enumerate(nonempty):
        if len(line) <= 60 and _REQUIRED_HEADINGS.match(line):
            current = "required"
            headings.add(current)
            continue
        if len(line) <= 60 and _PREFERRED_HEADINGS.match(line):
            current = "preferred"
            headings.add(current)
            continue
        if len(line) <= 60 and _DUTY_HEADINGS.match(line):
            current = "duties"
            headings.add(current)
            continue
        if len(line) <= 60 and _OTHER_HEADING.match(line):
            current = "other"
            headings.add(current)
            buckets.setdefault(current, [])
            continue
        if _looks_like_unrecognized_heading(line, index):
            # Do not let benefits or another unknown section bleed into the
            # preceding requirement bucket. Confidence will route this JD to AI.
            current = "other"
            headings.add("unknown")
            buckets.setdefault(current, [])
            continue
        buckets.setdefault(current, []).append(_BULLET.sub("", line))
    return nonempty, buckets, frozenset(headings)


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


def heuristic_requirements(
    raw_jd: str,
    vocabulary: set[str],
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
) -> JobRequirements:
    nonempty, buckets, _headings = _jd_sections(raw_jd)

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
        location=location or (location_match.group(0) if location_match else None),
        work_arrangement=arrangement,  # type: ignore[arg-type]
        work_authorization="; ".join(auth_lines)[:300] or None,
        security_clearance=clearance.group(0)[:120] if clearance else None,
        required_credentials=[c[:200] for c in credentials[:10]],
        employment_type=employment_type or next((_EMPLOYMENT_TYPE_DISPLAY[t] for t in _EMPLOYMENT_TYPES if t in lowered), None),
        other_hard_requirements=[],
    )


def heuristic_confidence(
    raw_jd: str,
    requirements: JobRequirements,
    vocabulary: set[str],
    *,
    supplied_title: str | None = None,
    hard_requirements_matter: bool = False,
) -> HeuristicConfidence:
    """Score the local extraction and identify cases whose meaning needs AI.

    The fast path is intentionally conservative. A false low-confidence result
    only costs one AI request; a false high-confidence result could rank the
    wrong candidate. Prompt-injection text is not a blocker because keeping it
    in deterministic parsing is safer than sending it to the model.
    """
    nonempty, buckets, headings = _jd_sections(raw_jd)
    words = re.findall(r"\b\w+\b", raw_jd)
    title = (supplied_title or requirements.title or "").strip()
    credible_title = bool(title and len(title) <= 120 and len(title.split()) <= 20 and not _GENERIC_TITLE.fullmatch(title))
    skill_count = len(requirements.mandatory_skills) + len(requirements.preferred_skills)
    reasons: list[str] = []

    def line_covered(line: str) -> bool:
        lowered = line.lower()
        return bool(
            detect_known_skills(line, vocabulary)
            or _YEARS.search(line)
            or _AUTH.search(line)
            or _CLEARANCE.search(line)
            or _CREDENTIAL.search(line)
            or _COMMON_NONTECH_REQUIREMENT.search(line)
            or (not hard_requirements_matter and _UNCAPTURED_HARD_TOPIC.search(line))
            or any(term in lowered for term in (*_EMPLOYMENT_TYPES, "remote", "hybrid", "on-site", "onsite", "in-office"))
        )

    def coverage(lines: list[str]) -> float:
        meaningful = [line for line in lines if re.search(r"[A-Za-z0-9]", line)]
        return (sum(line_covered(line) for line in meaningful) / len(meaningful)) if meaningful else 1.0

    def mixed_technology_list_is_uncovered(line: str) -> bool:
        """Catch lists where one known skill used to hide unknown adjacent tools."""
        match = _TECH_REQUIREMENT_CUE.search(line)
        tail = line[match.end() :] if match else line
        parts = [part.strip(" .:()") for part in _SKILL_ITEM_SPLIT.split(tail)]
        meaningful = [part for part in parts if re.search(r"[A-Za-z]", part)]
        if len(meaningful) <= 1 or (not match and len(line.split()) > 14):
            return False
        detected = [bool(detect_known_skills(part, vocabulary)) for part in meaningful]
        return any(detected) and not all(detected)

    def technology_cue_tail_is_uncovered(line: str) -> bool:
        """A known skill before a later cue must not hide an unknown tool after it."""
        matches = list(_TOOL_INTRO_CUE.finditer(line))
        if not matches or not detect_known_skills(line, vocabulary):
            return False
        last = matches[-1]
        tail = line[last.end() :].strip(" .:()")
        if not detect_known_skills(line[: last.start()], vocabulary) or detect_known_skills(tail, vocabulary):
            return False
        tail_tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.'’/-]*", tail)
        return len(tail_tokens) == 1 or product_like_signal(tail)

    def short_unknown_requirement(line: str) -> bool:
        if line_covered(line) or len(line.split()) > 4:
            return False
        if _COMMON_NONTECH_REQUIREMENT.search(line) or _UNCAPTURED_HARD_TOPIC.search(line):
            return False
        return bool(re.search(r"[A-Za-z0-9]", line))

    def product_like_signal(line: str) -> bool:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.'’/-]*", line)
        significant = [
            token
            for token in tokens
            if token.lower() not in {"a", "an", "and", "for", "in", "of", "or", "the", "to", "with"}
        ]
        if not significant:
            return False
        if any(re.search(r"[A-Z].*[A-Z]|[a-z][A-Z]|\d|[+#./]", token) for token in significant):
            return True
        if all(token[0].isupper() for token in significant):
            return True
        generic_suffixes = {"api", "database", "framework", "library", "orchestration", "platform", "sdk", "service", "tool"}
        return significant[0][0].isupper() and any(token.lower() in generic_suffixes for token in significant[1:])

    def unknown_tool_cue_without_known_skill(line: str) -> bool:
        """Route a genuinely tool-like unknown cue to AI, not generic prose."""
        if detect_known_skills(line, vocabulary) or _COMMON_NONTECH_REQUIREMENT.search(line):
            return False
        matches = list(_TOOL_INTRO_CUE.finditer(line))
        if not matches:
            return False
        tail = line[matches[-1].end() :].strip(" .:()")
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.'’/-]*", tail)
        parts = [part for part in _SKILL_ITEM_SPLIT.split(tail) if re.search(r"[A-Za-z0-9]", part)]
        return bool(tokens) and (len(tokens) == 1 or product_like_signal(tail) or len(parts) > 1)

    required_lines = buckets["required"]
    preferred_lines = buckets["preferred"]
    if not required_lines:
        required_lines = [
            line
            for line in [*buckets["general"], *buckets["duties"]]
            if _STRONG_REQUIREMENT_CUE.search(line) or _TECH_REQUIREMENT_CUE.search(line)
        ]
    required_coverage = coverage(required_lines)
    preferred_coverage = coverage(preferred_lines)
    responsibility_lines = [
        line
        for line in buckets["general"]
        if _RESPONSIBILITY_LINE.search(line)
    ]
    domain_requirement_lines = [
        *buckets["required"],
        *buckets["preferred"],
        *[
            line
            for line in buckets["general"]
            if _STRONG_REQUIREMENT_CUE.search(line) or _PREFERENCE_CUE.search(line) or _YEARS.search(line)
        ],
    ]

    score = 0.0
    if credible_title:
        score += 0.18
    if "required" in headings:
        score += 0.22
    elif len(requirements.mandatory_skills) >= 4:
        score += 0.16
    elif len(requirements.mandatory_skills) >= 2:
        score += 0.10

    duty_cue_present = bool(_DUTY_CUE.search("\n".join(buckets["general"])))
    if len(requirements.duties) >= 2 or (not duty_cue_present and not responsibility_lines and "duties" not in headings):
        score += 0.12
    elif credible_title and len(requirements.mandatory_skills) >= 4:
        score += 0.06

    if required_lines:
        score += 0.28 * required_coverage
    elif len(requirements.mandatory_skills) >= 3:
        score += 0.28
    elif requirements.mandatory_skills:
        score += 0.14

    classified_text = "\n".join(
        [*buckets["general"], *buckets["required"], *buckets["preferred"], *buckets["duties"]]
    )
    preference_cue_present = bool(_PREFERENCE_CUE.search(classified_text))
    if preferred_lines:
        score += 0.10 * preferred_coverage
    elif not preference_cue_present:
        score += 0.10

    if requirements.mandatory_skills and (credible_title or requirements.duties):
        score += 0.10

    if not credible_title:
        reasons.append("job title was not clear")
    if len(words) < 25:
        reasons.append("job description was too short")
    if len(nonempty) < 4 and len(words) > 80:
        reasons.append("job description had no reliable section structure")
    if skill_count == 0:
        reasons.append("no known job skills were found")
    elif skill_count > 30:
        reasons.append("too many skills were detected to classify reliably")
    if "required" in headings and not requirements.mandatory_skills:
        reasons.append("the required section had no recognized skills")
    elif "required" in headings and required_coverage < 0.40:
        reasons.append("most required qualifications were outside the local vocabulary")
    if "preferred" in headings and not requirements.preferred_skills:
        reasons.append("the preferred section could not be classified")
    elif "preferred" in headings and preferred_coverage < 0.60:
        reasons.append("most preferred qualifications could not be classified locally")
    if duty_cue_present and "duties" not in headings and not requirements.duties:
        reasons.append("responsibilities were not in a recognized section")
    if preference_cue_present and "preferred" not in headings and not requirements.preferred_skills:
        reasons.append("preferred requirements were not in a recognized section")
    if "unknown" in headings:
        reasons.append("an unrecognized section heading could change requirement classification")
    if responsibility_lines and not requirements.duties:
        reasons.append("responsibilities were not classified locally")
    if requirements.industry is None and any(_DOMAIN_REQUIREMENT.search(line) for line in domain_requirement_lines):
        reasons.append("an industry or domain requirement was not classified locally")

    requirement_lines = [*buckets["required"], *buckets["preferred"]]
    if not requirement_lines:
        requirement_lines = [
            line for line in [*buckets["general"], *buckets["duties"]] if _STRONG_REQUIREMENT_CUE.search(line)
        ]
    for line in requirement_lines:
        if unknown_tool_cue_without_known_skill(line):
            reasons.append("a required technology was outside the local vocabulary")
            break
        if mixed_technology_list_is_uncovered(line):
            reasons.append("a technology list contained tools outside the local vocabulary")
            break
        if technology_cue_tail_is_uncovered(line):
            reasons.append("a required technology after a technology cue was outside the local vocabulary")
            break
    short_unknown = [line for line in buckets["required"] if short_unknown_requirement(line)]
    known_short = [line for line in buckets["required"] if len(line.split()) <= 4 and line_covered(line)]
    if any(product_like_signal(line) for line in short_unknown) or (
        known_short and any(len(re.findall(r"[A-Za-z0-9+#.'’/-]+", line)) == 1 for line in short_unknown)
    ):
        reasons.append("a short required qualification was outside the local vocabulary")
    if hard_requirements_matter and any(_UNCAPTURED_HARD_TOPIC.search(line) for line in requirement_lines):
        reasons.append("an unusual hard requirement needed interpretation")
    if any(_CONDITIONAL_REQUIREMENT.search(line) for line in requirement_lines):
        reasons.append("requirements contained alternatives or exceptions")
    if any(_PREFERENCE_CUE.search(line) for line in buckets["required"]) or any(
        _STRONG_REQUIREMENT_CUE.search(line) for line in buckets["preferred"]
    ):
        reasons.append("required and preferred sections overlapped")

    lowered = raw_jd.lower()
    arrangements = {
        name
        for name, found in (
            ("remote", bool(re.search(r"\bremote\b", lowered))),
            ("hybrid", "hybrid" in lowered),
            ("onsite", bool(re.search(r"\bon[- ]?site\b|\bin[- ]office\b|\bin the office\b", lowered))),
        )
        if found
    }
    if hard_requirements_matter and len(arrangements) > 1:
        reasons.append("multiple work arrangements were mentioned")
    if hard_requirements_matter and requirements.work_arrangement in {"hybrid", "onsite"} and not requirements.location:
        reasons.append("the required work location was unclear")

    final_score = round(min(1.0, score), 3)
    return HeuristicConfidence(score=final_score, sufficient=final_score >= 0.80 and not reasons, reasons=tuple(dict.fromkeys(reasons)))


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


_ANALYSIS_WARNING_CODES = {"ai_analysis_failed", "low_confidence_local_analysis"}


def _analysis_warning(job: Job, code: str, message: str) -> None:
    existing = [warning for warning in (job.warnings_json or []) if warning.get("code") not in _ANALYSIS_WARNING_CODES]
    job.warnings_json = [*existing, {"code": code, "message": message}]


def analyze_job(db: Session, ai: AIService | None, job: Job, *, use_cache: bool = True) -> Job:
    job.status = PipelineStatus.ANALYZING
    db.commit()
    try:
        job.warnings_json = [
            warning for warning in (job.warnings_json or []) if warning.get("code") not in _ANALYSIS_WARNING_CODES
        ]
        # A manual correction or a prior AI extraction is safe to reuse. A
        # heuristic result is intentionally cheap to recompute because the
        # candidate-derived skill vocabulary may have changed since it ran.
        cached = (
            db.scalar(
                select(Job)
                .where(
                    Job.jd_hash == job.jd_hash,
                    Job.id != job.id,
                    Job.parsed_requirements_json.is_not(None),
                    Job.requirements_source.in_(("manual", "ai")),
                )
                .order_by(Job.requirements_source.desc(), Job.updated_at.desc())
                .limit(1)
            )
            if use_cache
            else None
        )
        if cached is not None:
            requirements = JobRequirements.model_validate(cached.parsed_requirements_json)
            source = "cache"
        else:
            settings = load_settings(db)
            vocabulary = _vocabulary(db)
            local = _clean_requirements(
                heuristic_requirements(
                    job.raw_jd,
                    vocabulary,
                    job.title,
                    job.company,
                    job.location,
                    job.employment_type,
                )
            )
            confidence = heuristic_confidence(
                job.raw_jd,
                local,
                vocabulary,
                supplied_title=job.title,
                hard_requirements_matter=settings.hard_requirement_gates,
            )
            logger.info(
                "Local JD analysis for job %s: confidence=%.3f sufficient=%s reasons=%s",
                job.id,
                confidence.score,
                confidence.sufficient,
                "; ".join(confidence.reasons) or "none",
            )
            if confidence.sufficient:
                requirements = local
                source = "heuristic"
            elif not settings.local_analysis_only and ai is not None and ai.available():
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
                    requirements = local
                    source = "heuristic"
                    _analysis_warning(
                        job,
                        "ai_analysis_failed",
                        f"AI fallback failed; generation continued with fast local JD extraction: {exc}",
                    )
            else:
                requirements = local
                source = "heuristic"
                reason = "; ".join(confidence.reasons[:3]) or "the JD structure was ambiguous"
                why = "AI analysis is off (Settings: local analysis only)" if settings.local_analysis_only else "OpenAI was unavailable"
                _analysis_warning(
                    job,
                    "low_confidence_local_analysis",
                    f"Generation continued with fast local JD extraction; {why} for an ambiguous JD ({reason}). Check the requirements.",
                )
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
        "source": job.source,  # v2.0: "extension" for jobs captured in Chrome
        "site": job.site,
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
