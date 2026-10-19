"""EligibilityEngine + MatchScorer (PRD §7).

A. Hard gates (pass/fail/unknown) — cannot be offset by score.
B. Transparent weighted score from *verified* evidence only.
C. AI only to resolve semantic skill matches, and only when it cites real verified evidence.
D. Rank eligible candidates; never fabricate a fit when nobody qualifies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from resumeflow.db.models import Candidate, CandidateMatch, CandidateSkill, CandidateSkillEvidence, Job
from resumeflow.domain.enums import CandidateStatus, EligibilityStatus, GenerationOperation, PipelineStatus, SkillDecision, VerificationStatus
from resumeflow.domain.requirements import SENIORITY_ORDER, JobRequirements
from resumeflow.domain.skills import find_skill_in_text, implied_skills, normalize_skill, skill_covers
from resumeflow.services.ai.provider import AIError
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.candidates import readiness, skill_counts, usable_skill_count, verified_snapshot
from resumeflow.services.jobs import _seniority_from_title, requirements_of
from resumeflow.services.settings import MatchWeights, load_settings

GateStatus = Literal["pass", "fail", "unknown"]
SEMANTIC_CANDIDATE_LIMIT = 5

_STOPWORDS = set(
    "the and for with you your our are will this that from have has into using use able work team teams across within "
    "about their they them who what when where which while also must should can may new other more all any each such "
    "years year experience strong excellent good great including include includes ability skills skill knowledge "
    "role job position candidate candidates we us an a of to in on at by as or be is it".split()
)


@dataclass
class Gate:
    name: str
    status: GateStatus
    detail: str


@dataclass
class Evaluation:
    eligibility: EligibilityStatus
    score: float
    gates: list[Gate]
    components: dict[str, Any]
    manual_checks: list[str] = field(default_factory=list)
    explanation: str = ""


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]{2,}", (text or "").lower()) if w not in _STOPWORDS}


# ---------------------------------------------------------------- gates


def classify_candidate_authorization(text: str | None) -> str:
    t = (text or "").lower()
    if not t:
        return "unknown"
    negated = re.search(r"(does not|doesn't|do not|don't|no|without|never) (?:\w+ ){0,2}(?:require|need)", t)
    if not negated and (("sponsor" in t and re.search(r"requir|need", t)) or re.search(r"\bh-?1b\b|\bopt\b|\bcpt\b", t)):
        return "requires_sponsorship"
    if "citizen" in t:
        return "citizen"
    if "permanent resident" in t or "green card" in t:
        return "permanent_resident"
    if negated or re.search(r"authori[sz]ed|\bead\b|eligible to work", t):
        return "authorized"
    return "unknown"


def classify_job_authorization(text: str | None) -> str | None:
    t = (text or "").lower()
    if not t:
        return None
    if re.search(r"sponsorship (?:is )?available|will sponsor|can sponsor|visa sponsorship provided", t):
        return None
    if "citizen" in t and not re.search(r"permanent resident|green card|authori[sz]ed", t):
        return "citizen_required"
    if re.search(r"without (?:visa )?sponsorship|not (?:be )?(?:able to )?sponsor|unable to sponsor|no (?:visa )?sponsorship|will not sponsor|won't sponsor|cannot sponsor|can't sponsor|does not sponsor|doesn't sponsor", t):
        return "no_sponsorship"
    if "citizen" in t or "permanent resident" in t or "green card" in t:
        return "no_sponsorship"
    return "authorized"


_CLEARANCE_LEVELS = [("public trust", 1), ("ts/sci", 4), ("top secret", 3), ("secret", 2)]


def _clearance_level(text: str | None) -> int | None:
    t = (text or "").lower()
    for word, level in _CLEARANCE_LEVELS:
        if word in t:
            return level
    return None


def _credential_tokens(text: str) -> set[str]:
    # Keep short acronyms (PE, RN, CPA) — they are what distinguish credentials.
    ignore = {"certified", "certification", "certifications", "certificate", "license", "licensed", "licence", "required",
              "must", "have", "hold", "active", "valid", "current", "preferred", "the", "and", "with", "or", "an", "in", "of"}
    return {t for t in re.findall(r"[a-z0-9][a-z0-9+#]*", (text or "").lower()) if len(t) >= 2 and t not in ignore}


def evaluate_gates(req: JobRequirements, snap: dict[str, Any]) -> list[Gate]:
    gates: list[Gate] = []
    prefs = snap.get("preferences") or {}

    job_auth = classify_job_authorization(req.work_authorization)
    if job_auth:
        cand = classify_candidate_authorization(snap.get("work_authorization"))
        stated = snap.get("work_authorization") or "not provided"
        if job_auth == "citizen_required":
            status: GateStatus = "pass" if cand == "citizen" else "unknown" if cand == "unknown" else "fail"
        elif job_auth == "no_sponsorship":
            status = "pass" if cand in {"citizen", "permanent_resident", "authorized"} else "fail" if cand == "requires_sponsorship" else "unknown"
        else:
            status = "pass" if cand in {"citizen", "permanent_resident", "authorized"} else "unknown"
        gates.append(Gate("Work authorization", status, f"Job: {req.work_authorization}. Candidate (verified): {stated}."))

    if req.security_clearance:
        needed = _clearance_level(req.security_clearance) or 2
        evidence = " ".join([snap.get("work_authorization") or "", *(c["name"] for c in snap.get("certifications", []))])
        held = _clearance_level(evidence) if "clearance" in evidence.lower() else None
        status = "unknown" if held is None else ("pass" if held >= needed else "fail")
        gates.append(Gate("Security clearance", status, f"Requires {req.security_clearance}; candidate: {'none stated' if held is None else evidence.strip()}"))

    arrangements = prefs.get("work_arrangements") or []
    if arrangements and req.work_arrangement != "unknown":
        ok = req.work_arrangement in arrangements
        gates.append(Gate("Work arrangement", "pass" if ok else "fail", f"Job is {req.work_arrangement}; candidate accepts {', '.join(arrangements)}."))

    if req.work_arrangement in {"onsite", "hybrid"} and req.location and "remote" not in req.location.lower():
        job_city = req.location.split(",")[0].strip().lower()
        state = re.search(r",\s*([A-Z]{2})\b", req.location)
        places = [snap.get("location") or "", *(prefs.get("preferred_locations") or [])]
        places_text = " | ".join(places).lower()
        near = bool(job_city and job_city in places_text) or bool(state and re.search(rf",\s*{state.group(1).lower()}\b", places_text))
        if near:
            gates.append(Gate("Location", "pass", f"{req.location} matches candidate location/preferences."))
        elif prefs.get("willing_to_relocate"):
            gates.append(Gate("Location", "pass", f"Candidate is willing to relocate to {req.location}."))
        elif not any(p.strip() for p in places):
            gates.append(Gate("Location", "unknown", f"Job is {req.work_arrangement} in {req.location}; candidate location unknown."))
        else:
            gates.append(Gate("Location", "fail", f"Job is {req.work_arrangement} in {req.location}; candidate is in {snap.get('location') or 'another area'}."))

    for credential in req.required_credentials:
        wanted = _credential_tokens(credential)
        if not wanted:
            continue
        found = None
        for cert in snap.get("certifications", []):
            have = _credential_tokens(" ".join(filter(None, [cert.get("name"), cert.get("issuer")])))
            if have and len(wanted & have) / len(wanted) >= 0.75:
                found = cert["name"]
                break
        gates.append(
            Gate("Required credential", "pass" if found else "fail", f"{credential}: {'held — ' + found if found else 'no verified matching credential'}")
        )
    return gates


# ---------------------------------------------------------------- scoring


def _evidence_text(snap: dict[str, Any]) -> str:
    parts = [snap.get("summary") or "", snap.get("headline") or ""]
    for role in snap.get("employment", []):
        parts.append(role["title"])
        parts.extend(b["text"] for b in role["bullets"])
    return "\n".join(p for p in parts if p)


def _stem(word: str) -> str:
    previous = None
    while previous != word:
        previous = word
        for suffix in ("ing", "ers", "er", "ed", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
    return word


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9+#]+", text.lower())}


# How much one matched skill counts (v1.8 MAT-07): shown in a role, or confirmed for a role, counts fully; a skill
# confirmed only in general counts slightly less; a related-wording or AI-judged equivalent less again.
EVIDENCE_WEIGHT = {"experience": 1.0, "role_confirmed": 1.0, "implied": 0.9, "verified_skill": 0.9, "related_wording": 0.8, "semantic": 0.8}


def match_skill(skill: str, snap: dict[str, Any], evidence_text: str) -> dict[str, Any]:
    key = normalize_skill(skill)
    line = find_skill_in_text(key, evidence_text)
    if line:
        return {"skill": skill, "matched": True, "method": "experience", "evidence": line}
    covering = [item for item in snap.get("skills", []) if item["normalized"] == key] or [
        item for item in snap.get("skills", []) if skill_covers(item["name"], key)
    ]
    for item in covering:
        if item.get("roles"):
            return {"skill": skill, "matched": True, "method": "role_confirmed", "evidence": item.get("evidence") or "Confirmed for a role"}
    if covering:
        item = covering[0]
        evidence = item.get("evidence") or ("Skill in profile" if item["normalized"] == key else f"Skill in profile: {item['name']}")
        return {"skill": skill, "matched": True, "method": "verified_skill", "evidence": evidence}
    # Experience with a technology that implies the skill (v1.8.5): "Built C# services" shows .NET.
    for candidate_line in evidence_text.split("\n"):
        tool = implied_skills(candidate_line).get(key)
        if tool:
            return {"skill": skill, "matched": True, "method": "implied", "evidence": candidate_line.strip()}
    # Word-stem fallback for phrases: "data engineering" is evidenced by "Senior Data Engineer".
    wanted = _stems(key)
    if len(wanted) >= 2:
        for candidate_line in evidence_text.split("\n"):
            if wanted <= _stems(candidate_line):
                return {"skill": skill, "matched": True, "method": "related_wording", "evidence": candidate_line.strip()}
    return {"skill": skill, "matched": False, "method": None, "evidence": None}


def _candidate_level(snap: dict[str, Any]) -> int | None:
    years = snap.get("total_years_experience") or 0
    by_years = 1 if years < 2 else 2 if years < 5 else 3 if years < 8 else 4
    titles = [SENIORITY_ORDER.get(_seniority_from_title(r["title"]), 0) for r in snap.get("employment", []) if r.get("title")]
    if not titles and not years:
        return None
    return max([by_years, *titles])


def score_candidate(req: JobRequirements, snap: dict[str, Any], weights: MatchWeights, skill_results: dict[str, dict[str, Any]] | None = None) -> tuple[float, dict[str, Any]]:
    evidence_text = _evidence_text(snap)
    results = skill_results or {}

    def skill_component(skills: list[str]) -> dict[str, Any]:
        checks = [results.get(normalize_skill(s)) or match_skill(s, snap, evidence_text) for s in skills]
        matched = [c for c in checks if c["matched"]]
        credit = sum(EVIDENCE_WEIGHT.get(c["method"], 1.0) for c in matched)
        return {
            "score": round(credit / len(checks), 3) if checks else 1.0,
            "matched": matched,
            "missing": [c["skill"] for c in checks if not c["matched"]],
        }

    mandatory = skill_component(req.mandatory_skills)
    preferred = skill_component(req.preferred_skills)

    job_tokens = _tokens(" ".join([req.title or "", *req.duties]))
    cand_tokens = _tokens(evidence_text)
    coverage = len(job_tokens & cand_tokens) / len(job_tokens) if job_tokens else None
    title_tokens = _tokens(req.title or "")
    title_similarity = max(
        (len(title_tokens & _tokens(r["title"])) / len(title_tokens | _tokens(r["title"])) for r in snap.get("employment", []) if r.get("title") and title_tokens),
        default=0.0,
    )
    if coverage is None and not title_tokens:
        experience_score = 0.5
    else:
        experience_score = 0.5 * min(1.0, (coverage or 0) / 0.4) + 0.5 * title_similarity
    if not snap.get("employment"):
        experience_score = 0.0

    years = snap.get("total_years_experience") or 0.0
    years_score = min(1.0, years / req.min_years_experience) if req.min_years_experience else 1.0
    required_level = SENIORITY_ORDER.get(req.seniority)
    candidate_level = _candidate_level(snap)
    if required_level is None or candidate_level is None:
        seniority_score = years_score
        level_score = None
    else:
        level_score = 1.0 if candidate_level >= required_level else 0.5 if candidate_level == required_level - 1 else 0.0
        seniority_score = 0.6 * years_score + 0.4 * level_score

    if req.industry:
        industry_tokens = _tokens(req.industry)
        employers = " ".join(r["employer"] for r in snap.get("employment", []))
        industry_score = 1.0 if industry_tokens & _tokens(evidence_text + " " + employers) else 0.0
    else:
        industry_score = 1.0

    prefs = snap.get("preferences") or {}
    other_score = 1.0
    employment_types = [t.lower() for t in prefs.get("employment_types") or []]
    if employment_types and req.employment_type and not any(t in req.employment_type.lower() for t in employment_types):
        other_score = 0.5

    components = {
        "mandatory_skills": {**mandatory, "weight": weights.mandatory_skills},
        "experience": {"score": round(experience_score, 3), "title_similarity": round(title_similarity, 3), "duty_coverage": round(coverage, 3) if coverage is not None else None, "weight": weights.experience},
        "seniority": {"score": round(seniority_score, 3), "years": years, "required_years": req.min_years_experience, "level_score": level_score, "weight": weights.seniority},
        "preferred_skills": {**preferred, "weight": weights.preferred_skills},
        "industry": {"score": industry_score, "industry": req.industry, "weight": weights.industry},
        "other": {"score": other_score, "weight": weights.other},
    }
    total = sum(component["score"] * component["weight"] for component in components.values())
    return round(total, 1), components


def _explain(evaluation: Evaluation) -> str:
    c = evaluation.components
    parts = []
    mandatory = c["mandatory_skills"]
    total_mandatory = len(mandatory["matched"]) + len(mandatory["missing"])
    if total_mandatory:
        parts.append(f"{len(mandatory['matched'])}/{total_mandatory} required skills" + (f" (missing {', '.join(mandatory['missing'][:4])})" if mandatory["missing"] else ""))
    seniority = c["seniority"]
    if seniority["required_years"]:
        parts.append(f"{seniority['years']:g} yrs vs {seniority['required_years']:g} required")
    else:
        parts.append(f"{seniority['years']:g} yrs verified experience")
    if c["experience"]["title_similarity"] >= 0.5:
        parts.append("closely matching past title")
    failed = [g.name for g in evaluation.gates if g.status == "fail"]
    unknown = [g.name for g in evaluation.gates if g.status == "unknown"]
    if failed:
        parts.append("fails: " + ", ".join(failed))
    elif unknown:
        parts.append("needs check: " + ", ".join(unknown))
    return "; ".join(parts) + "."


def evaluate(req: JobRequirements, snap: dict[str, Any], weights: MatchWeights, skill_results: dict | None = None, *, gates_on: bool = True) -> Evaluation:
    """``gates_on=False`` (Settings → hard requirement checks off): no pass/fail gates; every ready candidate is ranked by score."""
    gates = evaluate_gates(req, snap) if gates_on else []
    score, components = score_candidate(req, snap, weights, skill_results)
    if any(g.status == "fail" for g in gates):
        eligibility = EligibilityStatus.INELIGIBLE
    elif any(g.status == "unknown" for g in gates):
        eligibility = EligibilityStatus.NEEDS_REVIEW
    else:
        eligibility = EligibilityStatus.ELIGIBLE
    evaluation = Evaluation(eligibility, score, gates, components, manual_checks=list(req.other_hard_requirements) if gates_on else [])
    evaluation.explanation = _explain(evaluation)
    return evaluation


# ---------------------------------------------------------------- semantic step (AI)


class SemanticMatch(BaseModel):
    skill: str
    evidence_id: str | None
    explanation: str


class SemanticMatches(BaseModel):
    matches: list[SemanticMatch]


SEMANTIC_INSTRUCTIONS = f"""You decide whether a candidate's verified evidence demonstrates job skills that
didn't match by exact name (for example "Postgres" vs "relational databases").
{UNTRUSTED_NOTICE}

For every skill in <job_skills>, return one entry. Set evidence_id to the id of the single evidence item
that clearly demonstrates the skill, or null if none does. Be strict: related-but-different technologies
do not count, and never assume skills that aren't evidenced."""


def semantic_matches(ai: AIService, job_id: str, skills: list[str], snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, str] = {}
    for item in snap.get("skills", []):
        evidence[f"S-{item['id'][:8]}"] = f"Skill: {item['name']}" + (f" — {item['evidence']}" if item.get("evidence") else "")
    for role in snap.get("employment", []):
        evidence[f"R-{role['id']}"] = f"Title: {role['title']} at {role['employer']}"
        for bullet in role["bullets"]:
            evidence[f"B-{bullet['id']}"] = bullet["text"]
    if not evidence or not skills:
        return {}
    listing = "\n".join(f"[{key}] {text}" for key, text in evidence.items())
    result = ai.run(
        operation=GenerationOperation.SEMANTIC_MATCH,
        role="analysis",
        instructions=SEMANTIC_INSTRUCTIONS,
        input_text=wrap("job_skills", "\n".join(skills)) + "\n\n" + wrap("candidate_evidence", listing),
        schema=SemanticMatches,
        job_id=job_id,
        max_output_tokens=4000,
    )
    wanted = {normalize_skill(s): s for s in skills}
    accepted: dict[str, dict[str, Any]] = {}
    for match in result.matches:
        key = normalize_skill(match.skill)
        if key in wanted and match.evidence_id in evidence:
            accepted[key] = {
                "skill": wanted[key],
                "matched": True,
                "method": "semantic",
                "evidence": evidence[match.evidence_id],
                "explanation": match.explanation[:300],
            }
    return accepted


# ---------------------------------------------------------------- orchestration


def _matched_skills(details: dict[str, Any] | None) -> set[str]:
    components = (details or {}).get("components") or {}
    return {
        normalize_skill(m["skill"])
        for name in ("mandatory_skills", "preferred_skills")
        for m in (components.get(name) or {}).get("matched", [])
    }


def rejected_skills(db: Session, candidate_ids: list[str]) -> dict[str, set[str]]:
    """Skills each candidate answered "Have not used" or marked Don't use: never counted, never suggested again."""
    found: dict[str, set[str]] = {cid: set() for cid in candidate_ids}
    rows = db.execute(
        select(CandidateSkillEvidence.candidate_id, CandidateSkillEvidence.normalized_name).where(
            CandidateSkillEvidence.candidate_id.in_(candidate_ids), CandidateSkillEvidence.decision == SkillDecision.NOT_USED
        )
    ).all()
    rows += db.execute(
        select(CandidateSkill.candidate_id, CandidateSkill.normalized_name).where(
            CandidateSkill.candidate_id.in_(candidate_ids), CandidateSkill.verification_status == VerificationStatus.REJECTED
        )
    ).all()
    for cid, key in rows:
        found.setdefault(cid, set()).add(key)
    return found


def potential_score(req: JobRequirements, snap: dict[str, Any], weights: MatchWeights, evaluation: Evaluation, rejected: set[str]) -> float:
    """The score if every unanswered job skill were confirmed for a role (MAT-06). Shown only; never used to choose."""
    components = evaluation.components
    missing = [s for s in components["mandatory_skills"]["missing"] + components["preferred_skills"]["missing"] if normalize_skill(s) not in rejected]
    if not missing:
        return evaluation.score
    hypothetical = {
        **snap,
        "skills": [
            *snap.get("skills", []),
            *({"id": f"potential-{i}", "name": s, "normalized": normalize_skill(s), "evidence": None, "roles": ["potential"]} for i, s in enumerate(missing)),
        ],
    }
    return max(evaluation.score, score_candidate(req, hypothetical, weights)[0])


def run_matching(db: Session, ai: AIService | None, job: Job) -> dict[str, Any]:
    req = requirements_of(job)
    settings = load_settings(db)
    weights = settings.match_weights

    candidates = list(db.scalars(select(Candidate).where(Candidate.status == CandidateStatus.ACTIVE)))
    counts = skill_counts(db, [c.id for c in candidates])
    rejected = rejected_skills(db, [c.id for c in candidates])
    # Previous results, so a re-match records the old score and what changed (MAT-09).
    previous = {
        m.candidate_id: (m.score, _matched_skills(m.matching_details_json))
        for m in db.scalars(select(CandidateMatch).where(CandidateMatch.job_id == job.id))
    }

    # Read and call the AI first; write everything at the end so no SQLite write
    # lock is held while waiting on the network.
    excluded: list[CandidateMatch] = []
    evaluated: list[tuple[Candidate, dict[str, Any], Evaluation]] = []
    for candidate in candidates:
        ready = readiness(candidate, usable_skill_count(counts.get(candidate.id, {}), settings.require_verification), require_verification=settings.require_verification)
        if not ready["ready_for_matching"]:
            excluded.append(
                CandidateMatch(
                    job_id=job.id,
                    candidate_id=candidate.id,
                    score=None,
                    eligibility_status=EligibilityStatus.INELIGIBLE,
                    matching_details_json={"excluded": True, "reasons": ready["blockers"]},
                )
            )
            continue
        snap = verified_snapshot(db, candidate)
        evaluated.append((candidate, snap, evaluate(req, snap, weights, gates_on=settings.hard_requirement_gates)))
    db.rollback()  # end the read transaction before any AI call

    warnings: list[str] = []
    if settings.semantic_matching and ai is not None and ai.available():
        shortlist = sorted(
            (e for e in evaluated if e[2].eligibility != EligibilityStatus.INELIGIBLE),
            key=lambda e: e[2].score,
            reverse=True,
        )[:SEMANTIC_CANDIDATE_LIMIT]
        shortlist_ids = {entry[0].id for entry in shortlist}
        for index, (candidate, snap, evaluation) in enumerate(list(evaluated)):
            if candidate.id not in shortlist_ids:
                continue
            missing = evaluation.components["mandatory_skills"]["missing"] + evaluation.components["preferred_skills"]["missing"]
            if not missing:
                continue
            try:
                extra = semantic_matches(ai, job.id, missing, snap)
            except AIError as exc:
                warnings.append(f"Semantic matching skipped for {candidate.full_name}: {exc}")
                continue
            if extra:
                evaluated[index] = (candidate, snap, evaluate(req, snap, weights, extra, gates_on=settings.hard_requirement_gates))

    eligible = sorted((e for e in evaluated if e[2].eligibility == EligibilityStatus.ELIGIBLE), key=lambda e: e[2].score, reverse=True)
    ranks = {e[0].id: i + 1 for i, e in enumerate(eligible)}
    db.execute(delete(CandidateMatch).where(CandidateMatch.job_id == job.id))
    db.add_all(excluded)
    for candidate, snap, evaluation in evaluated:
        old_score, old_matched = previous.get(candidate.id, (None, None))
        now_matched = _matched_skills({"components": evaluation.components})
        changes = (
            {"added": sorted(now_matched - old_matched), "removed": sorted(old_matched - now_matched)}
            if old_matched is not None and now_matched != old_matched
            else None
        )
        db.add(
            CandidateMatch(
                job_id=job.id,
                candidate_id=candidate.id,
                score=evaluation.score,
                potential_score=potential_score(req, snap, weights, evaluation, rejected.get(candidate.id, set())),
                previous_score=old_score,
                rank=ranks.get(candidate.id),
                eligibility_status=evaluation.eligibility,
                matching_details_json={
                    "gates": [g.__dict__ for g in evaluation.gates],
                    "components": evaluation.components,
                    "manual_checks": evaluation.manual_checks,
                    "explanation": evaluation.explanation,
                    "years_experience": snap.get("total_years_experience"),
                    "skill_changes": changes,
                },
            )
        )
    job.status = PipelineStatus.MATCHED if eligible else PipelineStatus.NO_ELIGIBLE_CANDIDATE
    if warnings:
        job.warnings_json = [*(job.warnings_json or []), *({"code": "semantic_skipped", "message": w} for w in warnings)]
    db.commit()
    return {"eligible": len(eligible), "evaluated": len(evaluated), "excluded": len(candidates) - len(evaluated)}
