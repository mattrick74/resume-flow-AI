"""Evidence-based skill expansion (v1.8.5).

Missing JD skill → search every employment record → rank the employers by evidence → a bullet of the best-supported
employer names the skill → validated in Python → inserted deterministically → exported. All missing skills are
handled in one pass and, with AI, returned as part of the resume-writing call rather than a separate request.

What counts as evidence that an employer's work shows a skill the resume never names (classification ``equivalent``,
or ``confirmed`` when the operator also confirmed the skill for that employer):
- implied: a bullet names a more specific technology ("Next.js" is React work, "Amazon EKS" is Kubernetes);
- described: a bullet describes it unambiguously ("serverless functions on AWS" are AWS Lambda);
- semantic: the strict AI semantic check during matching cited that exact bullet as showing the skill.
A bullet that already names the skill or an alias ("Postgres") counts as named; the job's spelling is used.
``related``: similar but different work ("Docker on Amazon ECS" for Kubernetes). It is emphasised, never claimed.
The resume AI response writes the bullet for a placement (checked in Python, else the standard wording is used) and
can point out related work; its judgement alone never puts a skill in the candidate's experience.

A placed skill becomes a skill of that employer in the application's snapshot (its evidence record), and the strongest
supporting bullet is reworded to name it, so every later step (writing, validation, automatic fixes, the profile-only
fallback) treats it as that employer's evidence, and ``ensure_placements`` inserts it into the final resume whatever
the writing step did. Skills with no evidence are not added; they are listed in the gap report kept with the
application.
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from resumeflow.domain.content import AISkillPlacement, ResumeBullet, ResumeContent
from resumeflow.domain.requirements import JobRequirements
from resumeflow.domain.skills import (
    ALIASES,
    RELATED,
    SPELLINGS,
    detect_tools,
    display_name,
    find_skill_in_text,
    implied_closure,
    implied_skills,
    normalize_skill,
    skill_covers,
    term_span,
)
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, wrap
from resumeflow.services.evidence import numbers_in

STRENGTH = {"implied": 1.0, "described": 1.0, "semantic": 0.8}
MAX_ROLE_TECHNOLOGIES = 12
MAX_SKILLS = 24
_STOP = set("the and for with from that this into your our you are will have has using use team work".split())


def job_skills(req: JobRequirements) -> dict[str, str]:
    """normalized -> display name, required first, then preferred."""
    found: dict[str, str] = {}
    for name in [*req.mandatory_skills, *req.preferred_skills]:
        key = normalize_skill(name)
        if key and key not in found:
            found[key] = display_name(key, name)
    return found


def semantic_evidence(details: dict[str, Any] | None) -> dict[str, str]:
    """normalized skill -> the evidence text the AI semantic check cited for it (from a match's details)."""
    components = (details or {}).get("components") or {}
    return {
        normalize_skill(m["skill"]): m.get("evidence") or ""
        for name in ("mandatory_skills", "preferred_skills")
        for m in (components.get(name) or {}).get("matched", [])
        if m.get("method") == "semantic" and m.get("evidence")
    }


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^\[[^\]]*\]\s*", "", text or "")).strip(" .;").lower()


def bullet_evidence(key: str, text: str, cited: str | None = None) -> dict[str, Any] | None:
    """How a bullet shows skill ``key`` without naming it: {method, term, label}, or None."""
    term = implied_skills(text).get(key)
    if term:
        if term in ALIASES:
            return {"method": "implied", "term": term, "label": f"{display_name(term)} work"}
        return {"method": "described", "term": term, "label": f"described as “{term}”"}
    if cited and _plain(cited) and _plain(cited) == _plain(text):
        return {"method": "semantic", "term": None, "label": "AI evidence check"}
    return None


def _related_terms(key: str, text: str) -> list[str]:
    return [t for t in RELATED.get(key, ()) if t != key and find_skill_in_text(t, text)]


def named_at(snap: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Roles whose own bullets name the skill (or an alias)."""
    return [role for role in snap.get("employment", []) if any(find_skill_in_text(key, b["text"]) for b in role["bullets"])]


def confirmed_at(snap: dict[str, Any], key: str) -> list[str]:
    """Role ids the operator confirmed the skill for (not this module's own placements)."""
    return [r for s in snap.get("skills", []) if s["normalized"] == key and not s.get("inferred") for r in (s.get("roles") or [])]


def _shown_at(snap: dict[str, Any], key: str) -> list[dict[str, Any]]:
    confirmed = set(confirmed_at(snap, key))
    named = {r["id"] for r in named_at(snap, key)}
    return [role for role in snap.get("employment", []) if role["id"] in named | confirmed]


def infer_role_skills(
    snap: dict[str, Any], req: JobRequirements, semantic: dict[str, str] | None = None, exclude: set[str] | frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Job skills no employer's bullets name yet, each placed at the employer with the strongest evidence (most and
    strongest supporting bullets; the more recent employer on a tie). A skill the operator confirmed for particular
    employers is only placed at one of those; without such evidence there it stays with the confirmed-experience flow
    (its drafted bullet, approved automatically or by the operator per Settings). ``exclude``: skills the operator said
    the candidate has not used, never placed.

    Returns [{skill, normalized, role_id, employer, title, bullet_ids, evidence, method, classification, term, score,
    ranking}] where ``ranking`` lists every employer with evidence and its score.
    """
    roles = snap.get("employment", [])
    placements = []
    for key, name in job_skills(req).items():
        if key in exclude or named_at(snap, key):
            continue
        cited = (semantic or {}).get(key)
        confirmed = confirmed_at(snap, key)
        ranking: list[dict[str, Any]] = []
        for role in roles:  # most recent first, so a tie keeps the more recent employer
            if confirmed and role["id"] not in confirmed:
                continue
            support = [(b, ev) for b in role["bullets"] if (ev := bullet_evidence(key, b["text"], cited))]
            score = sum(STRENGTH[ev["method"]] for _, ev in support)
            if support:
                ranking.append({"role": role, "support": support, "score": score})
        if not ranking:
            continue
        best = max(ranking, key=lambda r: r["score"])  # max keeps the first (most recent) on a tie
        role = best["role"]
        support = sorted(best["support"], key=lambda item: -STRENGTH[item[1]["method"]])
        top_bullet, top = support[0]
        placements.append(
            {
                "skill": name,
                "normalized": key,
                "role_id": role["id"],
                "employer": role["employer"],
                "title": role.get("title"),
                "bullet_ids": [b["id"] for b, _ in support],
                "evidence": f"{top['label']}: “{top_bullet.get('original_text') or top_bullet['text']}”",
                "method": top["method"],
                "classification": "confirmed" if role["id"] in confirmed else "equivalent",
                "term": top["term"],
                "score": round(best["score"], 2),
                "ranking": [{"role_id": r["role"]["id"], "employer": r["role"]["employer"], "score": round(r["score"], 2)} for r in ranking],
            }
        )
    return placements


def related_experience(
    snap: dict[str, Any], req: JobRequirements, skip: set[str], exclude: set[str] | frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Job skills the candidate hasn't shown, with the employer whose related work is most transferable
    ("Docker on Amazon ECS" for Kubernetes). That work is emphasised; the missing skill is never claimed."""
    found = []
    for key, name in job_skills(req).items():
        if key in skip or key in exclude or not RELATED.get(key) or _shown_at(snap, key):
            continue
        best: dict[str, Any] | None = None
        for role in snap.get("employment", []):
            hits = [(b, terms) for b in role["bullets"] if (terms := _related_terms(key, b["text"]))]
            if hits and (best is None or len(hits) > len(best["hits"])):
                best = {"role": role, "hits": hits}
        if best is None:
            continue
        terms = list(dict.fromkeys(t for _, ts in best["hits"] for t in ts))
        found.append(
            {
                "skill": name,
                "normalized": key,
                "role_id": best["role"]["id"],
                "employer": best["role"]["employer"],
                "bullet_ids": [b["id"] for b, _ in best["hits"]],
                "related": [display_name(t) for t in terms],
            }
        )
    return found


def name_skill(text: str, skill: str, term: str | None) -> str:
    """``text`` naming ``skill`` beside the words that show it, in the candidate's own sentence:
    "Ran services on Amazon EKS" → "Ran services on Amazon EKS (Kubernetes)", "Built Lambda functions triggered by S3"
    → "Built AWS Lambda functions triggered by S3". Without such words it is added at the end in parentheses."""
    span = term_span(text, term) if term else None
    if span is None:
        body, end = text.rstrip(), ""
        if body.endswith("."):
            body, end = body[:-1], "."
        return f"{body} ({skill}){end}"
    start, stop = span
    found = text[start:stop]
    if skill.lower().endswith(" " + found.lower()):  # "Lambda" → "AWS Lambda"
        return text[:start] + skill + text[stop:]
    if text[:start].count("(") > text[:start].count(")"):  # already inside a list in parentheses
        return text[:stop] + f", {skill}" + text[stop:]
    opening = re.match(r"\s*\(", text[stop:])
    if opening:  # "AWS (EC2, S3)" → "AWS (Lambda, EC2, S3)"
        at = stop + opening.end()
        return text[:at] + f"{skill}, " + text[at:]
    return text[:stop] + f" ({skill})" + text[stop:]


def _deterministic_rewrite(text: str, place: dict[str, Any]) -> str:
    return name_skill(text, place["skill"], place.get("term"))


# ---------------------------------------------------------------- AI proposals (one batched call)

# doesn't name, and rewrite one bullet so it names the skill.
# {UNTRUSTED_NOTICE}

# Be strict: a related-but-different technology is "related", never "equivalent". Never assume.

# Keep every fact, number and technology of the original and add nothing else: no new tools, metrics,
# scope or responsibilities, and no sentence from the job description. One sentence starting with an action verb.
# <evidence_hints> is what an automatic search found; follow it unless the evidence clearly shows otherwise.
# EXPANSION_INSTRUCTIONS = f"""You find where a candidate's existing work experience shows job skills that their resume


# For every skill in <missing_job_skills>, return one entry with a classification:
# - "equivalent": an employer's bullet shows the candidate used this exact skill under another name, a more specific
#   form of it (Amazon EKS is Kubernetes, Next.js is React, C# is .NET) or an unambiguous description of it
#   ("serverless functions on AWS" are AWS Lambda).
# - "confirmed": <operator_confirmed> says the candidate used it at that employer; cite that employer's bullet where it
#   was used.
# - "related": similar but different work (Docker on ECS for Kubernetes, MySQL for PostgreSQL). That is NOT evidence of
#   the skill.
# - "none": nothing relevant.

# employer_id: the R-... id of the single employer whose bullets show it best (most and strongest evidence; the more
# recent employer on a tie). evidence_ids: that employer's B-... ids, strongest first.
# bullet (equivalent or confirmed only, else empty): the FIRST cited bullet rewritten to name the skill exactly as the
# job writes it. 


# Never mention verification, evidence, ids or this tool."""
EXPANSION_INSTRUCTIONS = f""

def expansion_candidates(snap: dict[str, Any], req: JobRequirements, exclude: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Job skills (display names) no employer's bullets name yet: what the AI expansion step is asked about."""
    return [name for key, name in job_skills(req).items() if key not in exclude and not named_at(snap, key)]


def expansion_prompt_sections(snap: dict[str, Any], skills: list[str], hints: list[str]) -> list[str]:
    """Extra sections for the resume request so skill placement needs no separate AI call."""
    roles = {r["id"]: r for r in snap.get("employment", [])}
    confirmed = [
        f"{s['name']}: " + ", ".join(f"R-{r} ({roles[r]['employer']})" for r in s.get("roles") or [] if r in roles)
        for s in snap.get("skills", [])
        if s.get("roles") and not s.get("inferred")
    ]
    return [
        wrap("missing_job_skills", "\n".join(skills) or "none"),
        wrap("candidate_skills", ", ".join(s["name"] for s in snap.get("skills", []) if not s.get("inferred")) or "none"),
        wrap("operator_confirmed", "\n".join(confirmed) or "none"),
        wrap("evidence_hints", "\n".join(hints) or "none"),
    ]


def expansion_hints(snap: dict[str, Any], req: JobRequirements, semantic: dict[str, str] | None = None, exclude: set[str] | frozenset[str] = frozenset()) -> list[str]:
    placements = infer_role_skills(snap, req, semantic, exclude)
    related = related_experience(snap, req, {p["normalized"] for p in placements}, exclude)
    lines = [
        f"{p['skill']}: {p['classification']} at R-{p['role_id']} ({p['employer']})"
        + (f", bullets {', '.join('B-' + b for b in p['bullet_ids'][:3])}" if p["bullet_ids"] else "")
        + f" — {p['evidence'][:160]}"
        for p in placements
    ]
    lines += [f"{r['skill']}: related only at R-{r['role_id']} ({', '.join(r['related'])}) — not evidence of the skill" for r in related]
    return lines


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z][a-z0-9+#]{3,}", (text or "").lower()) if w not in _STOP}


def _copied(text: str, source: str, size: int = 10) -> bool:
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    haystack = " ".join(re.findall(r"[a-z0-9+#.]+", (source or "").lower()))
    return bool(haystack) and len(words) >= size and any(" ".join(words[i : i + size]) in haystack for i in range(len(words) - size + 1))


_INTERNAL = re.compile(r"\bverified (?:experience|skills?|facts?|evidence|roles?|profile)\b|\b[RBSEC]-[0-9a-f]{6,}\b", re.IGNORECASE)


def check_proposed_bullet(text: str, source: str, key: str, skill: str, role_tools: set[str], job_text: str | None = None) -> str | None:
    """Why an AI-proposed bullet can't be used, or None when it can: it must name the skill, keep to its source
    bullet's facts (no new numbers or technologies besides the skill, most of the source's wording kept, not much
    longer) and not copy the job description."""
    text = (text or "").strip()
    if not text:
        return "empty"
    if not (find_skill_in_text(key, text) or term_span(text, skill)):
        return "doesn't name the skill"
    if numbers_in(text) - numbers_in(source):
        return "adds numbers"
    allowed = detect_tools(source) | role_tools | {key} | implied_closure(key) | detect_tools(skill)
    if detect_tools(text) - allowed:
        return f"adds {', '.join(sorted(detect_tools(text) - allowed))}"
    kept = _words(source)
    if kept and len(kept & _words(text)) / len(kept) < 0.5:
        return "drops most of the original bullet"
    if len(text) > len(source) + len(skill) + 90:
        return "much longer than the original"
    if job_text and _copied(text, job_text):
        return "copies the job description"
    if _INTERNAL.search(text):
        return "mentions app wording"
    return None


def merge_ai_proposals(
    snap: dict[str, Any],
    req: JobRequirements,
    placements: list[dict[str, Any]],
    related: list[dict[str, Any]],
    proposals: list[AISkillPlacement],
    exclude: set[str] | frozenset[str] = frozenset(),
    job_text: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Python decides: a deterministic placement keeps its employer and takes the AI's bullet only when that bullet
    passes the checks; an AI-only "equivalent" is placed when its citations and bullet pass the checks; "related"
    only adds emphasis. Returns (placements, related, notes about rejected proposals)."""
    wanted = job_skills(req)
    roles = {r["id"]: r for r in snap.get("employment", [])}
    by_key = {p["normalized"]: p for p in placements}
    near = {r["normalized"]: r for r in related}
    notes: list[str] = []
    for proposal in proposals:
        key = normalize_skill(proposal.skill)
        if key not in wanted or key in exclude or named_at(snap, key):
            continue
        role_id = (proposal.employer_id or "").removeprefix("R-")
        role = roles.get(role_id)
        ids = [i.removeprefix("B-") for i in proposal.evidence_ids]
        own = {b["id"]: b for b in role["bullets"]} if role else {}
        cited_ok = role is not None and bool(ids) and all(i in own for i in ids)
        name = wanted[key]
        if proposal.classification in ("equivalent", "confirmed") and key in by_key:
            place = by_key[key]
            if not cited_ok or role_id != place["role_id"] or ids[0] not in place["bullet_ids"]:
                continue  # the evidence ranking decides the employer and the supporting bullets
            problem = check_proposed_bullet(proposal.bullet, own[ids[0]]["text"], key, name, confirmed_tools(snap, role_id), job_text)
            if problem:
                notes.append(f"Used the standard wording for {name} at {place['employer']} (the suggested bullet {problem}).")
                continue
            place.update(ai_bullet=proposal.bullet.strip(), ai_bullet_id=ids[0], bullet_ids=list(dict.fromkeys([ids[0], *place["bullet_ids"]])))
        elif proposal.classification != "none" and cited_ok and key not in by_key and key not in near and not _shown_at(snap, key):
            # Without evidence the automatic search (aliases, more specific technologies, unambiguous descriptions, the
            # strict semantic check) can confirm, the AI's judgement alone never puts a skill in the candidate's
            # experience: the cited work is emphasised as related experience and the skill stays in the gap report.
            near[key] = {"skill": name, "normalized": key, "role_id": role_id, "employer": role["employer"], "bullet_ids": ids, "related": []}  # type: ignore[index]
            if proposal.classification in ("equivalent", "confirmed"):
                notes.append(f"{name} was not added at {role['employer']}: its evidence couldn't be confirmed, so the work is emphasised without naming it.")  # type: ignore[index]
    return placements, list(near.values()), notes


def confirmed_tools(snap: dict[str, Any], role_id: str) -> set[str]:
    return {s["normalized"] for s in snap.get("skills", []) if role_id in (s.get("roles") or [])}


# ---------------------------------------------------------------- gap report and the application's evidence record


def gap_report(
    snap: dict[str, Any],
    req: JobRequirements,
    placements: list[dict[str, Any]],
    related: list[dict[str, Any]],
    exclude: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Every job skill and what the tailored resume does with it. Unresolved skills (``resolved`` false) are the ones the
    candidate's experience doesn't show: they are not added."""
    required = {normalize_skill(s) for s in req.mandatory_skills}
    placed = {p["normalized"]: p for p in [*(snap.get("inferred_skills") or []), *placements]}
    near = {r["normalized"]: r for r in related}
    skills = snap.get("skills", [])
    summary = snap.get("summary") or ""
    report = []
    for key, name in job_skills(req).items():
        entry: dict[str, Any] = {"skill": name, "normalized": key, "required": key in required, "employer": None, "evidence": None, "related": [], "classification": None}
        if key in exclude:
            entry.update(status="not_used", classification="none", note="The operator said the candidate hasn't used it; not added.")
        elif key in placed:
            place = placed[key]
            entry.update(
                status="added_to_experience",
                employer=place["employer"],
                evidence=place["evidence"],
                classification=place.get("classification", "equivalent"),
                ranking=place.get("ranking", []),
                note=f"Named in a {place['employer']} bullet." if place.get("bullet_ids") else f"On the {place['employer']} Technologies line.",
            )
        elif named := named_at(snap, key):
            entry.update(status="in_experience", employer=named[0]["employer"], classification="named", note="Already in the candidate's experience.")
        elif confirmed := [r for r in snap.get("employment", []) if r["id"] in confirmed_at(snap, key)]:
            entry.update(
                status="in_experience",
                employer=confirmed[0]["employer"],
                classification="confirmed",
                note=f"Confirmed for {confirmed[0]['employer']}: on its Technologies line, and in a bullet once its drafted bullet is approved.",
            )
        elif any(s["normalized"] == key or skill_covers(s["name"], key) for s in skills) or find_skill_in_text(key, summary):
            entry.update(status="skills_section", classification="listed", note="In the candidate's Skills list or summary; no employer's bullets show it, so it stays there.")
        elif key in near:
            r = near[key]
            entry.update(
                status="transferable",
                employer=r["employer"],
                related=r["related"],
                classification="related",
                note=f"Not claimed. Related work at {r['employer']}" + (f" ({', '.join(r['related'])})" if r["related"] else "") + " is emphasised instead.",
            )
        else:
            entry.update(status="not_supported", classification="none", note="No evidence in the candidate's experience; not added.")
        if key in near and entry["status"] == "skills_section":
            entry.update(employer=near[key]["employer"], related=near[key]["related"])
        entry["resolved"] = entry["status"] in ("added_to_experience", "in_experience", "skills_section")
        report.append(entry)
    return report


def apply_inferences(
    snap: dict[str, Any],
    placements: list[dict[str, Any]],
    related: list[dict[str, Any]] | None = None,
    report: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A copy of the snapshot (the application's evidence record) where each placed skill is a skill of that employer
    (Skills section, Technologies line, allowed in that employer's bullets) and the strongest supporting bullet names
    it: the AI's checked rewrite, or the skill named beside the words that show it. The bullet keeps its original
    wording in ``original_text``. Earlier placements are kept for the audit trail."""
    if not placements and related is None and report is None:
        return snap
    result = copy.deepcopy(snap)
    skills = result.setdefault("skills", [])
    by_key = {s["normalized"]: s for s in skills}
    bullets = {b["id"]: b for role in result.get("employment", []) for b in role["bullets"]}
    # The most specific skill first, so "AWS Lambda" is named before the "AWS" it also shows (one mention covers both).
    keys = {p["normalized"] for p in placements}
    for place in sorted(placements, key=lambda p: -len(implied_closure(p["normalized"]) & keys)):
        evidence = f"Shown at {place['employer']} — {place['evidence']}"
        existing = by_key.get(place["normalized"])
        if existing is not None:
            existing["roles"] = list(dict.fromkeys([*(existing.get("roles") or []), place["role_id"]]))
            existing["evidence"] = existing.get("evidence") or evidence
        else:
            # A stable id that stays unique in its first 8 characters (evidence listings cite "S-" + 8 characters).
            stable = hashlib.sha1(place["normalized"].encode("utf-8")).hexdigest()[:12]
            item = {"id": stable, "name": place["skill"], "normalized": place["normalized"], "evidence": evidence, "roles": [place["role_id"]], "inferred": True}
            skills.append(item)
            by_key[place["normalized"]] = item
        target = place.get("ai_bullet_id") or (place["bullet_ids"][0] if place["bullet_ids"] else None)
        bullet = bullets.get(target) if target else None
        if bullet is None or find_skill_in_text(place["normalized"], bullet["text"]):
            continue
        rewritten = place.get("ai_bullet") if not bullet.get("original_text") else None  # a bullet is rewritten by the AI once
        bullet.setdefault("original_text", bullet["text"])
        bullet["text"] = rewritten or _deterministic_rewrite(bullet["text"], place)
        bullet["named_skills"] = [*bullet.get("named_skills", []), place["skill"]]
        place["bullet_ids"] = list(dict.fromkeys([target, *place["bullet_ids"]]))
        place["bullet_text"] = bullet["text"]
    known = {(p["normalized"], p["role_id"]) for p in result.get("inferred_skills", [])}
    result["inferred_skills"] = [*result.get("inferred_skills", []), *(p for p in placements if (p["normalized"], p["role_id"]) not in known)]
    if related is not None:
        result["transferable_skills"] = related
    if report is not None:
        result["skill_gap_report"] = report
    return result


def align_spellings(snap: dict[str, Any], req: JobRequirements) -> int:
    """In place: bullets that name a job skill by an informal spelling use the job's wording ("Postgres" → "PostgreSQL").
    Returns how many bullets changed."""
    wanted = job_skills(req)
    changed = 0
    for role in snap.get("employment", []):
        for bullet in role["bullets"]:
            for informal, canonical in SPELLINGS.items():
                name = wanted.get(canonical)
                if not name or term_span(bullet["text"], name):
                    continue
                pattern = re.compile(rf"(?<![\w+#.]){re.escape(informal)}(?![\w+#])", re.IGNORECASE)
                if pattern.search(bullet["text"]):
                    bullet.setdefault("original_text", bullet["text"])
                    bullet["text"] = pattern.sub(name, bullet["text"], count=1)
                    changed += 1
    return changed


def expand_snapshot(
    snap: dict[str, Any],
    req: JobRequirements,
    semantic: dict[str, str] | None = None,
    exclude: set[str] | frozenset[str] = frozenset(),
    proposals: list[AISkillPlacement] | None = None,
    job_text: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The whole pass for one application: place what the experience shows (taking the AI's bullets that pass the
    checks), find transferable work for the rest, and record the gap report. Returns (snapshot, notes). Running it
    again on its own result changes nothing."""
    placements = infer_role_skills(snap, req, semantic, exclude)
    placed = {p["normalized"] for p in [*placements, *(snap.get("inferred_skills") or [])]}
    related = related_experience(snap, req, placed, exclude)
    notes: list[str] = []
    if proposals:
        placements, related, notes = merge_ai_proposals(snap, req, placements, related, proposals, exclude, job_text)
    result = apply_inferences(snap, placements, related, gap_report(snap, req, placements, related, exclude))
    align_spellings(result, req)
    return result, notes


def emphasised_bullets(snap: dict[str, Any]) -> set[str]:
    """Bullets that show a job skill through evidence, or transferable work for a missing one: kept and put first."""
    return {i for p in (snap.get("inferred_skills") or []) for i in p["bullet_ids"][:1]} | {
        i for r in (snap.get("transferable_skills") or []) for i in r["bullet_ids"][:2]
    }


def ensure_placements(content: ResumeContent, snap: dict[str, Any]) -> tuple[ResumeContent, list[str]]:
    """Deterministic insertion: every placed skill appears in a bullet of its employer, whatever the writing step did.
    When a rewrite lost it, the skill is named again beside the words that show it, or the evidence-record bullet is
    used; when the bullet was left out, it is inserted first. The affected sections are repaired too: the skill is on
    that employer's Technologies line and in the Skills section."""
    placements = snap.get("inferred_skills") or []
    if not placements:
        return content, []
    sources = {b["id"]: b for role in snap.get("employment", []) for b in role["bullets"]}
    verified = {s["normalized"]: s["name"] for s in snap.get("skills", [])}
    index = {role.role_id: i for i, role in enumerate(content.experience)}
    fixed = content.model_copy(deep=True)
    notes: list[str] = []
    changed = False
    for place in placements:
        at = index.get(place["role_id"])
        if at is None:
            continue
        role = fixed.experience[at]
        key, name = place["normalized"], verified.get(place["normalized"], place["skill"])
        source = next((sources[i] for i in place["bullet_ids"] if i in sources and find_skill_in_text(key, sources[i]["text"])), None)
        if source is not None and not any(find_skill_in_text(key, b.text) for b in role.bullets):
            bullets = list(role.bullets)
            position = next((k for k, b in enumerate(bullets) if source["id"] in b.source_ids), None)
            if position is None:
                bullets.insert(0, ResumeBullet(text=source["text"], source_ids=[source["id"]]))
            elif place.get("term") and term_span(bullets[position].text, place["term"]):
                bullets[position] = bullets[position].model_copy(update={"text": _deterministic_rewrite(bullets[position].text, place)})
            else:
                bullets[position] = ResumeBullet(text=source["text"], source_ids=[source["id"]])
            role = role.model_copy(update={"bullets": bullets})
            notes.append(f"Named {place['skill']} in a {place['employer']} bullet ({place['evidence'][:120]}).")
            changed = True
        if role.technologies and not any(skill_covers(t, key) for t in role.technologies) and len(role.technologies) < MAX_ROLE_TECHNOLOGIES:
            role = role.model_copy(update={"technologies": [name, *role.technologies]})
            changed = True
        fixed.experience[at] = role
        if fixed.skills and not any(skill_covers(s, key) for s in fixed.skills):
            fixed.skills = [name, *fixed.skills][:MAX_SKILLS]
            changed = True
    return (fixed if changed else content), notes
