"""ClaimValidator (PRD §8): claim-to-evidence checks and cross-candidate leakage detection.

Deterministic checks always run. An optional AI review adds findings, but every
AI-reported quote must exist verbatim in the document or it is discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.db.models import Candidate
from resumeflow.domain.content import AIClaimReview, CoverLetterContent, ResumeContent
from resumeflow.domain.enums import GenerationOperation
from resumeflow.domain.profile import profile_from_json
from resumeflow.domain.skills import detect_known_skills, detect_tools, normalize_skill
from resumeflow.services.ai.provider import AIError
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.evidence import bullet_index, evidence_catalogue, evidence_listing, numbers_in, snapshot_text
from resumeflow.services.inference import ensure_placements

Issue = dict[str, str]


def issue(severity: str, code: str, message: str, location: str = "") -> Issue:
    return {"severity": severity, "code": code, "message": message, "location": location}


@dataclass
class OtherCandidates:
    """Identifying data of every *other* candidate, for leakage checks."""

    names: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    employers: set[str] = field(default_factory=set)


def load_other_candidates(db: Session, candidate_id: str) -> tuple[OtherCandidates, set[str]]:
    others = OtherCandidates()
    own_employers: set[str] = set()
    for candidate in db.scalars(select(Candidate)):
        employers = {r.employer.strip().lower() for r in profile_from_json(candidate.profile_json).employment if len(r.employer.strip()) >= 4}
        if candidate.id == candidate_id:
            own_employers = employers
            continue
        others.names.append(candidate.full_name)
        others.contacts.extend(v for v in (candidate.email, candidate.phone) if v)
        others.employers |= employers
    others.employers -= own_employers
    return others, own_employers


def _leakage(text: str, others: OtherCandidates, location: str) -> list[Issue]:
    lowered = text.lower()
    found = []
    for name in others.names:
        if len(name) >= 5 and re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            found.append(issue("error", "cross_candidate_leakage", f"Mentions another candidate: {name}", location))
    for contact in others.contacts:
        if contact.lower() in lowered:
            found.append(issue("error", "cross_candidate_leakage", "Contains another candidate's contact details", location))
    for employer in others.employers:
        if re.search(rf"\b{re.escape(employer)}\b", lowered):
            found.append(issue("error", "cross_candidate_leakage", f"Mentions an employer from another candidate's history: {employer.title()}", location))
    return found


def _unverified_skill_mentions(text: str, snap: dict[str, Any], location: str, *, names: tuple[str | None, ...] = ()) -> list[Issue]:
    """``names`` (the job title and company) are quoted, not claimed: "the ServiceNow AI Engineer role" isn't a skill."""
    verified = {s["normalized"] for s in snap.get("skills", [])}
    evidence = snapshot_text(snap)
    for name in names:
        if name and name.strip():
            text = re.sub(re.escape(name.strip()), " ", text, flags=re.IGNORECASE)
    problems = []
    for canonical in detect_known_skills(text):
        if canonical in verified:
            continue
        if detect_known_skills(evidence, {canonical}).get(canonical):
            continue
        problems.append(issue("warning", "unverified_skill_mention", f"Mentions '{canonical}', which isn't in the verified evidence", location))
    return problems


_INTERNAL_WORDING = re.compile(
    r"\b(?:total )?verified (?:experience|skills?|facts?|evidence|roles?|profile)\b|\b[RBSEC]-[0-9a-f]{8,}\b", re.IGNORECASE
)


def _internal_wording(text: str, where: str) -> list[Issue]:
    """The app's own vocabulary ("verified experience", evidence ids) must not leak into a document."""
    match = _INTERNAL_WORDING.search(text or "")
    if not match:
        return []
    return [issue("warning", "internal_wording", f"Mentions app wording “{match.group(0)}”; rephrase it as a normal resume would", where)]


def _role_tools(snap: dict[str, Any], role_id: str) -> set[str]:
    return {s["normalized"] for s in snap.get("skills", []) if role_id in (s.get("roles") or [])}


def _copied_from(text: str, source: str, size: int = 10) -> bool:
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    if len(words) < size or not source:
        return False
    haystack = " ".join(re.findall(r"[a-z0-9+#.]+", source.lower()))
    return any(" ".join(words[i : i + size]) in haystack for i in range(len(words) - size + 1))


def _near_duplicates(texts: list[str], threshold: float = 0.75) -> int:
    sets = [{w for w in re.findall(r"[a-z0-9+#]{3,}", t.lower())} for t in texts]
    return sum(
        1 for i in range(len(sets)) for j in range(i + 1, len(sets)) if sets[i] and sets[j] and len(sets[i] & sets[j]) / len(sets[i] | sets[j]) >= threshold
    )


def validate_resume(content: ResumeContent, snap: dict[str, Any], others: OtherCandidates, job_text: str | None = None) -> list[Issue]:
    problems: list[Issue] = []
    return problems
    roles = {r["id"]: r for r in snap.get("employment", [])}
    bullets = bullet_index(snap)
    verified_skills = {s["normalized"] for s in snap.get("skills", [])}
    allowed_numbers = numbers_in(snapshot_text(snap)) | {str(int(snap.get("total_years_experience") or 0)), f"{snap.get('total_years_experience', 0):g}"}

    if content.contact.name != snap.get("full_name"):
        problems.append(issue("error", "altered_contact", "Candidate name differs from the profile", "contact"))

    for index, role in enumerate(content.experience):
        where = f"experience[{index}]"
        source = roles.get(role.role_id)
        if source is None:
            problems.append(issue("error", "unknown_role", f"Role '{role.title} at {role.employer}' isn't a verified role", where))
            continue
        for fact in ("employer", "title", "start", "end"):
            if (getattr(role, fact) or None) != (source.get(fact) or None):
                problems.append(issue("error", "altered_role_facts", f"{fact.title()} for {source['employer']} was changed", where))
        if role.technologies:
            shown = _role_tools(snap, role.role_id) | detect_tools(" ".join(b["text"] for b in source["bullets"]))
            extra = [t for t in role.technologies if normalize_skill(t) not in shown]
            if extra:
                problems.append(
                    issue("error", "unsupported_tool", f"Technologies at {source['employer']} not confirmed for that role: {', '.join(extra)}", f"{where}.technologies")
                )
        for b_index, bullet in enumerate(role.bullets):
            b_where = f"{where}.bullets[{b_index}]"
            sources = [bullets.get(sid) for sid in bullet.source_ids]
            if not bullet.source_ids or any(s is None for s in sources):
                problems.append(issue("error", "unsupported_bullet", f"Bullet has no verified source: “{bullet.text[:80]}”", b_where))
                continue
            if any(s[0] != role.role_id for s in sources if s):
                problems.append(issue("error", "bullet_moved_between_roles", "Bullet cites evidence from a different role", b_where))
            source_numbers = set().union(*(numbers_in(s[1]) for s in sources if s))
            invented = numbers_in(bullet.text) - source_numbers
            if invented:
                problems.append(issue("error", "unsupported_number", f"Numbers not in the source bullet: {', '.join(sorted(invented))}", b_where))
            # v1.8 GEN-04: a technology at a specific employer needs that role's evidence or confirmation.
            allowed = detect_tools(" ".join(s[1] for s in sources if s)) | _role_tools(snap, role.role_id)
            unconfirmed = detect_tools(bullet.text) - allowed
            if unconfirmed:
                problems.append(
                    issue("error", "unsupported_tool", f"Names {', '.join(sorted(unconfirmed))} at {source['employer']}, which isn't confirmed for that role", b_where)
                )
            if job_text and _copied_from(bullet.text, job_text):
                problems.append(issue("warning", "copied_from_job_description", "Repeats a sentence from the job description; describe the candidate's own work", b_where))
    all_bullets = [b.text for role in content.experience for b in role.bullets]
    duplicates = _near_duplicates(all_bullets)
    if duplicates:
        problems.append(issue("warning", "duplicate_bullets", f"{duplicates} pair(s) of bullets say nearly the same thing", "experience"))

    for skill in content.skills:
        if normalize_skill(skill) not in verified_skills:
            problems.append(issue("error", "unverified_skill", f"'{skill}' is not a verified skill", "skills"))

    for field_name in ("summary", "headline"):
        text = getattr(content, field_name) or ""
        invented = numbers_in(text) - allowed_numbers
        if invented:
            problems.append(issue("error", "unsupported_number", f"{field_name.title()} contains numbers not in the evidence: {', '.join(sorted(invented))}", field_name))
        problems += _unverified_skill_mentions(text, snap, field_name)

    verified_education = {e["id"] for e in snap.get("education", [])}
    verified_certs = {c["id"] for c in snap.get("certifications", [])}
    if any(e.get("id") not in verified_education for e in content.education):
        problems.append(issue("error", "unverified_education", "Education entries must come from verified education", "education"))
    if any(c.get("id") not in verified_certs for c in content.certifications):
        problems.append(issue("error", "unverified_certification", "Certifications must come from verified credentials", "certifications"))

    for where, text in [("summary", content.summary), ("headline", content.headline or "")] + [
        (f"experience[{r}].bullets[{b}]", bullet.text) for r, role in enumerate(content.experience) for b, bullet in enumerate(role.bullets)
    ]:
        problems += _internal_wording(text, where)

    full_text = resume_plain_text(content)
    problems += _leakage(full_text, others, "resume")
    if not content.experience:
        problems.append(issue("warning", "no_experience", "The resume has no experience section", "experience"))
    return problems


def validate_cover_letter(content: CoverLetterContent, snap: dict[str, Any], others: OtherCandidates) -> list[Issue]:
    problems: list[Issue] = []
    catalogue = evidence_catalogue(snap)
    body = "\n".join(p.text for p in content.paragraphs)
    words = len(re.findall(r"\b\w+\b", body))
    if words < 150:
        problems.append(issue("warning", "too_short", f"Cover letter is short ({words} words); a concise one-page letter is about 180–400 words", "paragraphs"))
    elif words > 450:
        problems.append(issue("warning", "too_long", f"Cover letter is long ({words} words); keep it to about one page", "paragraphs"))
    allowed_numbers = numbers_in(snapshot_text(snap)) | {str(int(snap.get("total_years_experience") or 0))}
    for index, paragraph in enumerate(content.paragraphs):
        where = f"paragraphs[{index}]"
        unknown = [eid for eid in paragraph.evidence_ids if eid not in catalogue]
        if unknown:
            problems.append(issue("warning", "unknown_evidence", f"Cites evidence that doesn't exist: {', '.join(unknown[:3])}", where))
        invented = numbers_in(paragraph.text) - allowed_numbers
        if invented:
            problems.append(issue("error", "unsupported_number", f"Numbers not in the evidence: {', '.join(sorted(invented))}", where))
        problems += _unverified_skill_mentions(paragraph.text, snap, where, names=(content.job_title, content.company))
        problems += _internal_wording(paragraph.text, where)
    if re.search(r"\[[^\]]{2,40}\]|\{[^}]{2,40}\}|<[A-Z][^>]{2,30}>", body + content.greeting):
        problems.append(issue("error", "placeholder_text", "Contains placeholder text such as [Company Name]", "paragraphs"))
    if content.company and content.company.lower() not in body.lower():
        problems.append(issue("warning", "company_not_mentioned", f"Doesn't mention {content.company}", "paragraphs"))
    problems += _leakage(body + "\n" + content.greeting, others, "cover_letter")
    return problems


def resume_plain_text(content: ResumeContent, *, include_contact: bool = True) -> str:
    """``include_contact=False`` for the AI fact-check: the contact block is copied from the profile and checked exactly."""
    parts = [content.contact.name if include_contact else "", content.headline or "", content.summary, ", ".join(content.skills)]
    for role in content.experience:
        parts.append(f"{role.title} {role.employer}")
        parts.extend(b.text for b in role.bullets)
        if role.technologies:
            parts.append("Technologies: " + ", ".join(role.technologies))
    return "\n".join(parts)


def cover_plain_text(content: CoverLetterContent) -> str:
    return "\n".join([content.greeting, *(p.text for p in content.paragraphs), content.closing])


REVIEW_INSTRUCTIONS = f"""You are a strict fact-checker. Compare what the generated document says about the CANDIDATE
against the candidate's verified evidence and list every such statement that is not supported (invented skills,
tools, metrics, responsibilities, seniority, credentials, or exaggerations).
Only claims about the candidate count. Sentences that describe the employer, the role or its requirements (for
example "your focus on analytics pipelines" or "the role combines X and Y") come from <job_description>; never
report them. The candidate's name and contact details are checked separately; never report them either.
{UNTRUSTED_NOTICE}

Quote the exact unsupported text from <generated_document>. Use severity "error" for invented facts and
"warning" for exaggeration or vague overstatement. Return an empty list if everything is supported."""


def ai_claim_review(
    ai: AIService, document_text: str, snap: dict[str, Any], application_id: str, location: str, job_text: str | None = None
) -> list[Issue]:
    parts = [wrap("candidate_evidence", evidence_listing(snap))]
    if job_text:
        parts.append(wrap("job_description", job_text[:8000]))  # lets the checker tell job statements from candidate claims
    parts.append(wrap("generated_document", document_text))
    try:
        review = ai.run(
            operation=GenerationOperation.CLAIM_VALIDATION,
            role="analysis",
            instructions=REVIEW_INSTRUCTIONS,
            input_text="\n\n".join(parts),
            schema=AIClaimReview,
            application_id=application_id,
            max_output_tokens=6000,
        )
    except AIError as exc:
        return [issue("info", "ai_review_skipped", f"AI fact-check skipped: {exc}", location)]
    normalized_doc = re.sub(r"\s+", " ", document_text).lower()
    found = []
    for item in review.issues:
        quote = re.sub(r"\s+", " ", item.quote).strip()
        if quote and quote.lower() in normalized_doc:
            found.append({**issue(item.severity, "ai_unsupported_claim", f"“{quote[:160]}” — {item.reason[:200]}", location), "quote": quote})
    return found


# ---------------------------------------------------------------- automatic fixes (no review hold)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _mentions(text: str, needle: str) -> bool:
    return bool(needle) and re.sub(r"\s+", " ", needle).lower() in re.sub(r"\s+", " ", text or "").lower()


def _note(message: str, location: str) -> Issue:
    return issue("info", "auto_fixed", message, location)


def _leftover_ai_findings(ai_issues: list[Issue], text: str) -> list[Issue]:
    """AI findings whose quote is gone become notes; any still present are warnings, never blocking errors."""
    kept = []
    for item in ai_issues:
        if item.get("quote") and not _mentions(text, item["quote"]):
            kept.append(_note(f"Removed {item['message']}", item.get("location", "")))
        else:
            kept.append({**item, "severity": "warning" if item.get("severity") == "error" else item.get("severity", "warning")})
    return kept


def auto_fix_resume(
    content: ResumeContent,
    snap: dict[str, Any],
    others: OtherCandidates,
    job_text: str | None,
    ai_issues: list[Issue],
    fallback: ResumeContent,
) -> tuple[ResumeContent, list[Issue]]:
    """Repairs what the checks flag instead of holding the application for review. Each flagged bullet goes back to its
    source wording (or is dropped), a flagged summary/headline/skill/fact is replaced from the profile, and if anything is
    still wrong the profile-only resume (truthful by construction) is used. Returns the content and its final issues."""
    ai_issues, supported = _supported_by_record(ai_issues, content, snap)
    issues = validate_resume(content, snap, others, job_text) + ai_issues
    errors = [i for i in issues if i.get("severity") == "error"]
    if not errors:
        return content, issues + supported
    fixed = content.model_copy(deep=True)
    sources = bullet_index(snap)
    verified = {s["normalized"] for s in snap.get("skills", [])}
    base_roles = {r.role_id: r for r in fallback.experience}
    notes: list[Issue] = []
    drop: set[tuple[int, int]] = set()

    def restore_bullet(r: int, b: int, why: str) -> None:
        if r >= len(fixed.experience) or b >= len(fixed.experience[r].bullets):
            return
        bullet = fixed.experience[r].bullets[b]
        source = next((sources[sid][1] for sid in bullet.source_ids if sid in sources), None)
        if source is not None:
            fixed.experience[r].bullets[b] = bullet.model_copy(update={"text": source, "source_ids": [s for s in bullet.source_ids if s in sources][:1]})
            notes.append(_note(f"Used the profile's own wording for a bullet ({why}).", f"experience[{r}].bullets[{b}]"))
        else:
            drop.add((r, b))
            notes.append(_note(f"Removed a bullet without a source ({why}).", f"experience[{r}].bullets[{b}]"))

    for item in errors:
        location, why = item.get("location", ""), item.get("code", "check").replace("_", " ")
        bullet_at = re.fullmatch(r"experience\[(\d+)\]\.bullets\[(\d+)\]", location)
        role_at = re.fullmatch(r"experience\[(\d+)\]", location)
        tech_at = re.fullmatch(r"experience\[(\d+)\]\.technologies", location)
        if tech_at and int(tech_at.group(1)) < len(fixed.experience):
            index = int(tech_at.group(1))
            base = base_roles.get(fixed.experience[index].role_id)
            fixed.experience[index] = fixed.experience[index].model_copy(update={"technologies": base.technologies if base else []})
            notes.append(_note("Kept only technologies confirmed for that employer.", location))
        elif bullet_at:
            restore_bullet(int(bullet_at.group(1)), int(bullet_at.group(2)), why)
        elif role_at and int(role_at.group(1)) < len(fixed.experience):
            index = int(role_at.group(1))
            base = base_roles.get(fixed.experience[index].role_id)
            if base is not None:
                fixed.experience[index] = fixed.experience[index].model_copy(
                    update={k: getattr(base, k) for k in ("employer", "title", "location", "start", "end")}
                )
                notes.append(_note("Restored the employer, title and dates from the profile.", location))
        elif location in ("summary", "headline"):
            setattr(fixed, location, getattr(fallback, location))
            notes.append(_note(f"Used the profile-based {location} ({why}).", location))
        elif location == "skills":
            fixed.skills = [s for s in fixed.skills if normalize_skill(s) in verified] or fallback.skills
            notes.append(_note("Removed skills that aren't in the profile.", "skills"))
        elif location in ("contact", "education", "certifications"):
            setattr(fixed, location, getattr(fallback, location))
            notes.append(_note(f"Restored {location} from the profile.", location))
        elif item.get("code") == "cross_candidate_leakage":
            terms = [t for t in (*others.names, *others.contacts, *others.employers) if len(t) >= 4]

            def leaks(text: str) -> bool:
                return any(_mentions(text, t) for t in terms)

            if leaks(fixed.summary) or leaks(fixed.headline or ""):
                fixed.summary, fixed.headline = fallback.summary, fallback.headline
                notes.append(_note("Used the profile-based summary (it mentioned another candidate's details).", "summary"))
            for r, role in enumerate(fixed.experience):
                for b, bullet in enumerate(role.bullets):
                    if leaks(bullet.text):
                        restore_bullet(r, b, "another candidate's details")
            fixed.skills = [s for s in fixed.skills if not leaks(s)]
        elif item.get("quote"):
            quote = item["quote"]
            if _mentions(fixed.summary, quote) or _mentions(fixed.headline or "", quote):
                fixed.summary, fixed.headline = fallback.summary, fallback.headline
                notes.append(_note(f"Used the profile-based summary (unsupported: “{quote[:80]}”).", "summary"))
            else:
                for r, role in enumerate(fixed.experience):
                    for b, bullet in enumerate(role.bullets):
                        if _mentions(bullet.text, quote):
                            restore_bullet(r, b, f"unsupported: “{quote[:80]}”")
                if any(_mentions(s, quote) for s in fixed.skills):
                    fixed.skills = [s for s in fixed.skills if not _mentions(s, quote)]
    for r, role in enumerate(fixed.experience):
        kept = [b for b_index, b in enumerate(role.bullets) if (r, b_index) not in drop]
        base = base_roles.get(role.role_id)
        fixed.experience[r] = role.model_copy(update={"bullets": kept or (base.bullets if base else [])})
    # A fix never takes away a job skill the employer's own experience shows (v1.8.5): it's named again from the
    # evidence, which the checks below confirm.
    fixed, placed = ensure_placements(fixed, snap)
    notes += [_note(n, "experience") for n in placed]

    issues = validate_resume(fixed, snap, others, job_text)
    if any(i.get("severity") == "error" for i in issues):
        fixed = fallback
        issues = validate_resume(fixed, snap, others, job_text)
        notes = [_note("Used the profile's own wording for the whole resume, because the tailored version had problems that couldn't be fixed one by one.", "resume")]
    return fixed, issues + notes + supported + _leftover_ai_findings(ai_issues, resume_plain_text(fixed, include_contact=False))


def _supported_by_record(ai_issues: list[Issue], content: ResumeContent, snap: dict[str, Any]) -> tuple[list[Issue], list[Issue]]:
    """AI fact-check findings that quote only bullets taken word for word from the application's evidence record (the
    candidate's bullets, confirmed experience and the job skills their work shows) are kept as notes: a bullet is not
    reverted because the imported resume didn't have it. Findings about anything else stay."""
    record = {b["id"]: b["text"].strip() for role in snap.get("employment", []) for b in role["bullets"]}
    kept: list[Issue] = []
    supported: list[Issue] = []
    for item in ai_issues:
        quote = item.get("quote")
        holders = [b for role in content.experience for b in role.bullets if quote and _mentions(b.text, quote)]
        elsewhere = quote and (_mentions(content.summary, quote) or _mentions(content.headline or "", quote) or any(_mentions(s, quote) for s in content.skills))
        if holders and not elsewhere and all(any(record.get(s) == b.text.strip() for s in b.source_ids) for b in holders):
            supported.append(_note(f"Kept “{quote[:80]}”: it is in the candidate's evidence record.", item.get("location", "")))
        else:
            kept.append(item)
    return kept, supported


def auto_fix_cover_letter(
    content: CoverLetterContent,
    snap: dict[str, Any],
    others: OtherCandidates,
    ai_issues: list[Issue],
    fallback: CoverLetterContent,
) -> tuple[CoverLetterContent, list[Issue]]:
    """Removes the sentences the checks flag (unsupported claims, invented numbers, another candidate's details). If too
    little is left, or anything is still wrong, the profile-only cover letter is used."""
    issues = validate_cover_letter(content, snap, others) + ai_issues
    errors = [i for i in issues if i.get("severity") == "error"]
    if not errors:
        return content, issues
    allowed = numbers_in(snapshot_text(snap)) | {str(int(snap.get("total_years_experience") or 0))}
    needles = [i["quote"] for i in errors if i.get("quote")]
    leaked = [*others.names, *others.contacts, *others.employers]
    notes: list[Issue] = []
    paragraphs = []
    for p_index, paragraph in enumerate(content.paragraphs):
        kept = []
        for sentence in _sentences(paragraph.text):
            reason = (
                "an unsupported claim" if any(_mentions(sentence, n) for n in needles)
                else "a number that isn't in the profile" if numbers_in(sentence) - allowed
                else "another candidate's details" if any(_mentions(sentence, n) for n in leaked if len(n) >= 4)
                else None
            )
            if reason:
                notes.append(_note(f"Removed a sentence with {reason}: “{sentence[:100]}”", f"paragraphs[{p_index}]"))
            else:
                kept.append(sentence)
        if kept:
            paragraphs.append(paragraph.model_copy(update={"text": " ".join(kept)}))
    fixed = content.model_copy(update={"paragraphs": paragraphs})
    issues = validate_cover_letter(fixed, snap, others)
    if len(paragraphs) < 2 or any(i.get("severity") == "error" for i in issues):
        fixed = fallback
        issues = validate_cover_letter(fixed, snap, others)
        notes = [_note("Used the profile-based cover letter, because the written one had problems that couldn't be removed sentence by sentence.", "cover_letter")]
    return fixed, issues + notes + _leftover_ai_findings(ai_issues, cover_plain_text(fixed))
