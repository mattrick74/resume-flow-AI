"""GenerationService (PRD §8): tailored resume + cover letter from verified evidence only.

Two modes, same output shape:
- AI (OpenAI key present): the writing model rephrases/condenses and orders verified
  evidence, citing evidence ids; we then rebuild the document deterministically and
  drop anything that cites nothing real.
- Deterministic (no key, budget reached, or AI unavailable): selection and ordering of
  verified facts only — truthful by construction.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from resumeflow.domain.content import (
    AICoverLetter,
    AIResumeTailoring,
    Contact,
    CoverLetterContent,
    CoverParagraph,
    ResumeBullet,
    ResumeContent,
    ResumeRole,
    Section,
)
from resumeflow.domain.enums import GenerationOperation
from resumeflow.domain.requirements import JobRequirements
from resumeflow.domain.skills import detect_tools, display_name, find_skill_in_text, normalize_skill, skill_covers
from resumeflow.services.ai.provider import AIConfigError, AIError, BudgetExceeded
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.evidence import bullet_index, evidence_listing, years_phrase
from resumeflow.services.inference import (
    EXPANSION_INSTRUCTIONS,
    emphasised_bullets,
    ensure_placements,
    expand_snapshot,
    expansion_candidates,
    expansion_hints,
    expansion_prompt_sections,
)

_TRANSIENT_RETRY_DELAYS_SECONDS = (0.5, 1.5)
MAX_SKILLS = 24
_STOP = set("the and for with from that this into your our you are will have has using use team work".split())
# Bullets per role (Settings → Bullets per role): (recent roles), (older roles) as (minimum, maximum).
DENSITY: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "concise": ((2, 4), (1, 3)),
    "standard": ((3, 6), (2, 4)),
    "detailed": ((5, 8), (3, 5)),
}


def role_skills(snap: dict[str, Any], role_id: str) -> set[str]:
    """Skills the operator confirmed for this particular role (v1.8)."""
    return {s["normalized"] for s in snap.get("skills", []) if role_id in (s.get("roles") or [])}


MAX_ROLE_TECHNOLOGIES = 12


def role_technologies(snap: dict[str, Any], role: dict[str, Any], req: JobRequirements) -> list[str]:
    """An employer's "Technologies:" line: skills confirmed for this role (Have used + that employer, or an approved
    bullet), then job skills its own bullets already name. Job skills first; never a skill the role doesn't show."""
    names = {s["normalized"]: s["name"] for s in snap.get("skills", [])}
    keys = [s["normalized"] for s in snap.get("skills", []) if role["id"] in (s.get("roles") or [])]
    job = [normalize_skill(s) for s in [*req.mandatory_skills, *req.preferred_skills]]
    named = detect_tools(" ".join(b["text"] for b in role["bullets"]))
    keys += [k for k in job if k in named]
    keys = list(dict.fromkeys(keys))
    keys.sort(key=lambda k: job.index(k) if k in job else len(job))
    return [names.get(k) or display_name(k) for k in keys][:MAX_ROLE_TECHNOLOGIES]


def unsupported_tools(text: str, source_texts: list[str], confirmed_for_role: set[str]) -> set[str]:
    """Technologies a bullet names that neither its source bullets nor the role's confirmations support (GEN-04):
    a skill confirmed only in general never becomes a claim about a specific employer."""
    allowed = detect_tools(" ".join(source_texts)) | confirmed_for_role
    return detect_tools(text) - allowed


def _relevance_terms(req: JobRequirements) -> tuple[list[str], set[str]]:
    skills = [normalize_skill(s) for s in [*req.mandatory_skills, *req.preferred_skills]]
    words = {w for w in re.findall(r"[a-z][a-z0-9+#]{2,}", " ".join([req.title or "", *req.duties]).lower()) if w not in _STOP}
    return skills, words


def _bullet_score(text: str, skills: list[str], words: set[str]) -> float:
    score = sum(2.0 for s in skills if find_skill_in_text(s, text))
    score += len(words & set(re.findall(r"[a-z][a-z0-9+#]{2,}", text.lower()))) * 0.5
    return score


def order_skills(snap: dict[str, Any], req: JobRequirements) -> list[str]:
    """Required skills first, then preferred, then ones named in the job's title or duties, then the
    candidate's own order (how their resume lists them), never alphabetical."""
    mandatory = [normalize_skill(s) for s in req.mandatory_skills]
    preferred = [normalize_skill(s) for s in req.preferred_skills]
    _, job_words = _relevance_terms(req)
    skills = snap.get("skills", [])

    def rank(skill: dict[str, Any], wanted: list[str]) -> int | None:
        if skill["normalized"] in wanted:
            return wanted.index(skill["normalized"])
        return next((i for i, w in enumerate(wanted) if skill_covers(skill["name"], w)), None)  # "CI/CD Pipelines" → CI/CD

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, skill = item
        n = skill["normalized"]
        if (position := rank(skill, mandatory)) is not None:
            return (0, position)
        if (position := rank(skill, preferred)) is not None:
            return (1, position)
        if set(re.findall(r"[a-z][a-z0-9+#]{2,}", n)) & job_words:
            return (2, index)
        return (3, index)

    return [s["name"] for _, s in sorted(enumerate(skills), key=key)][:MAX_SKILLS]


def _contact(snap: dict[str, Any]) -> Contact:
    return Contact(
        name=snap["full_name"],
        email=snap.get("email"),
        phone=snap.get("phone"),
        location=snap.get("location"),
        links=snap.get("links") or [],
    )


def _role_shell(role: dict[str, Any]) -> ResumeRole:
    return ResumeRole(
        role_id=role["id"],
        employer=role["employer"],
        title=role["title"],
        location=role.get("location"),
        start=role.get("start"),
        end=role.get("end"),
    )


def _baseline_bullets(
    role: dict[str, Any], index: int, skills: list[str], words: set[str], density: str = "standard", emphasis: set[str] | frozenset[str] = frozenset()
) -> list[ResumeBullet]:
    recent, older = DENSITY.get(density, DENSITY["standard"])
    limit = (recent if index < 2 else older)[1]
    # Bullets that show a job skill through evidence, or transferable work for a missing one, are kept and lead.
    ranked = sorted(role["bullets"], key=lambda b: _bullet_score(b["text"], skills, words) + (10 if b["id"] in emphasis else 0), reverse=True)
    chosen = {b["id"] for b in ranked[:limit]}
    # Keep the original order among the chosen bullets except put the strongest first.
    ordered = [b for b in ranked[:limit] if b["id"] in chosen]
    return [ResumeBullet(text=b["text"], source_ids=[b["id"]]) for b in ordered]


def _baseline_summary(snap: dict[str, Any], skills: list[str]) -> tuple[str | None, str]:
    if snap.get("summary"):
        return snap.get("headline"), snap["summary"]
    roles = snap.get("employment", [])
    title = roles[0]["title"] if roles else None
    years = years_phrase(snap.get("total_years_experience") or 0) if (snap.get("total_years_experience") or 0) >= 1 else ""
    parts = []
    if title and years:
        parts.append(f"{title} with {years} years of experience")
    elif title:
        parts.append(title)
    if skills:
        parts.append(("skilled in " if parts else "Skilled in ") + ", ".join(skills[:4]))
    summary = (" ".join(parts) + ".") if parts else ""
    return title, summary[:1].upper() + summary[1:]


def deterministic_resume(snap: dict[str, Any], req: JobRequirements, density: str = "standard") -> ResumeContent:
    skill_terms, words = _relevance_terms(req)
    skills = order_skills(snap, req)
    headline, summary = _baseline_summary(snap, skills)
    emphasis = emphasised_bullets(snap)
    experience = []
    for index, role in enumerate(snap.get("employment", [])):
        shell = _role_shell(role)
        shell.bullets = _baseline_bullets(role, index, skill_terms, words, density, emphasis)
        shell.technologies = role_technologies(snap, role, req)
        experience.append(shell)
    content = ResumeContent(
        contact=_contact(snap),
        headline=headline,
        summary=summary,
        skills=skills,
        experience=experience,
        education=snap.get("education", []),
        certifications=snap.get("certifications", []),
    )
    return ensure_placements(content, snap)[0]


def _job_brief(req: JobRequirements, company: str | None, raw_jd: str) -> str:
    lines = [
        f"Title: {req.title or 'unknown'}",
        f"Company: {company or req.employer or 'unknown'}",
        f"Required skills: {', '.join(req.mandatory_skills) or 'none listed'}",
        f"Preferred skills: {', '.join(req.preferred_skills) or 'none listed'}",
        f"Duties: {'; '.join(req.duties[:10]) or 'not listed'}",
    ]
    return wrap("job_requirements", "\n".join(lines)) + "\n\n" + wrap("job_description", raw_jd[:8000])


def resume_instructions(density: str = "standard") -> str:
    (recent_min, recent_max), (older_min, older_max) = DENSITY.get(
        density, DENSITY["standard"]
    )

    return f"""You are an expert senior technical resume writer and
JD-driven employment experience expansion specialist.

Your task is to produce the strongest accurate, ATS-friendly resume
for the target job by identifying underrepresented JD skills and
integrating them into the most relevant existing employment history.

{UNTRUSTED_NOTICE}

PRIMARY OBJECTIVE:
Do not simply rearrange existing bullets or place skills only in
the Skills section.

Actively discover missing or underrepresented JD skills, search all
candidate evidence, select the correct employer, and generate strong
experience bullets demonstrating supported technical capabilities.

Work automatically without asking for confirmations.

STEP 1 — ANALYZE JOB REQUIREMENTS
- Extract all required and preferred technical skills.
- Identify important frameworks, programming languages, cloud
  services, databases, architectural patterns, and engineering practices.
- Compare these requirements against the candidate's existing skills
  and employment history.
- Prioritize required skills, especially those not clearly demonstrated
  in existing employment bullets.
- Distinguish skills genuinely absent from experience that is merely
  described using different terminology.

STEP 2 — SEARCH ALL AVAILABLE CANDIDATE EVIDENCE
For each JD skill:
- Search source bullets, role descriptions, projects, technologies,
  verified skills, and confirmed employer-specific experience.
- Recognize technical aliases, standard abbreviations, and
  genuine technology equivalences.
- Recognize skills logically demonstrated by documented use of
  a technology, such as Amazon EKS demonstrating Kubernetes.
- Do not equate merely related technologies: Docker alone does not
  demonstrate Kubernetes, and AWS alone does not demonstrate Lambda.
- Use employer-specific evidence even when the exact JD keyword
  is absent from that employer's original bullets.
- Never treat the JD itself as evidence of candidate experience.

STEP 3 — SELECT THE MOST APPROPRIATE EMPLOYER
For every supported missing or underrepresented JD skill:
- Examine every existing role independently.
- Find the employer with the strongest direct or equivalent evidence.
- Prefer work that most closely matches the JD's responsibilities.
- Consider technical compatibility, projects, role responsibilities,
  and actual employment dates.
- Do not automatically select the newest employer.
- Do not assign skills based only on company industry or the
  likelihood that a company used a particular technology.
- If multiple employers have independently supported experience,
  the skill may appear in relevant bullets for multiple roles.
- Never move achievements between employers.

STEP 4 — GENERATE STRONG EXPERIENCE BULLETS
For each adequately supported JD skill:
- Generate or expand an experience bullet under the correct employer.
- Include the exact JD skill name when it is an accurate description
  of the candidate's documented work.
- Combine evidence from multiple sources belonging to the same
  employer to produce a more complete technical accomplishment.
- Describe the engineering activity, tools, technical approach,
  responsibilities, and supported outcomes.
- Start with a powerful, natural action verb.
- Prefer accomplishment-focused language over generic statements.
- Use precise technical terminology.
- Preserve supported numbers and metrics exactly as sourced.
- Never invent a percentage, metric, project, technology, or outcome.
- Avoid repetitive openings and duplicate achievements.
- Ensure important supported JD skills appear in employment bullets,
  not only in the Skills or Technologies sections.

Suggested bullet structure:
Action verb + technical contribution + supported technology
+ project context + supported outcome.

STEP 5 — HANDLE DIFFERENT EVIDENCE TYPES

DIRECT EVIDENCE:
The candidate explicitly used the technology under an employer.
Generate a strong bullet that demonstrates that technology.

EQUIVALENT EVIDENCE:
The source documents a technology that genuinely demonstrates
the JD skill, even when the exact keyword is absent.
Use the JD terminology naturally when technically accurate.

EMPLOYER-CONFIRMED EVIDENCE:
The candidate's records confirm use of the technology for a
particular employer, even if the original bullets omit the skill.
Generate a bullet from the confirmed work details.
Do not invent implementation details or outcomes.

TRANSFERABLE EXPERIENCE:
The candidate worked with related tools or performed related tasks,
but direct use of the requested technology is not established.
Strengthen the transferable achievement using technologies the
candidate actually used. Do not claim direct use of the missing tool.

INSUFFICIENT EVIDENCE:
Do not fabricate direct experience, assign the skill to an employer,
or insert the unsupported technology into an employment bullet.
Continue generating the resume without requesting confirmation.

STEP 6 — DISTRIBUTE SKILLS ACROSS EMPLOYMENT HISTORY
- Process all existing roles, not just the most recent company.
- Strengthen the most relevant experience under each employer.
- Spread supported JD keywords naturally across the appropriate roles.
- Do not insert all missing skills into one company.
- Prioritize quality, relevance, technical specificity, and credibility.
- Maintain {recent_min}–{recent_max} bullets for recent roles and
  {older_min}–{older_max} bullets for older roles when sufficient
  independent evidence exists.
- If evidence is limited, use fewer bullets rather than padding
  with unsupported claims.
- Replace weaker bullets with stronger supported achievements
  when necessary to remain within the configured limits.

STEP 7 — EVIDENCE AND OUTPUT INTEGRITY
- Preserve all employer names, titles, locations, dates, degrees,
  certifications, and actual career history.
- Every experience bullet must reference source evidence.
- Use B-... source IDs when the bullet originates from source bullets.
- Multiple source IDs may be combined when they belong to the
  same employer.
- Use employer-specific confirmed evidence IDs when available
  and when supported by the output schema.
- Never cite another employer's work to support a role's bullet.
- Do not invent leadership, responsibilities, metrics, or achievements.
- Do not treat an unassigned general skill as proof of use
  at a particular employer.
- Do not mention evidence, IDs, verification, or internal logic
  in the visible resume.

STEP 8 — SKILLS SECTION
- Order supported technical skills by relevance to the JD.
- Prioritize required skills followed by preferred skills.
- Include only skills supported by the candidate's records.
- Normalize aliases and avoid duplicate names.
- Make sure skills demonstrated in employment bullets also
  appear in the Skills section when relevant.
- Do not add unsupported skills just to improve keyword matching.

STEP 9 — SUMMARY AND HEADLINE
- Write a concise, powerful, factual professional summary.
- Emphasize the candidate's strongest relevant technologies
  and accomplishments.
- Include important supported JD keywords naturally.
- Use 2–3 professional sentences with no first-person pronouns.
- Base the headline on actual experience and documented roles.
- Never invent years of experience or seniority.

STEP 10 — FINAL SELF-CHECK
Before returning the result:
1. Review all required JD skills.
2. Confirm that every supported, relevant JD skill appears naturally
   in at least one appropriate employment bullet where possible.
3. Confirm that each bullet belongs to the correct employer.
4. Check every technical claim against its supporting evidence.
5. Remove duplicate or generic bullets.
6. Preserve original employment chronology.
7. Ensure the output satisfies the configured response schema.
8. Return the completed resume without requesting user approval.

IMPORTANT:
Your primary goal is to maximize truthful JD skill coverage
within the actual employment history.

Do not merely paraphrase the original resume.
Expand and strengthen experience bullets using all available,
employer-specific evidence, while preserving factual accuracy.
"""


RESUME_INSTRUCTIONS = resume_instructions()

COVER_INSTRUCTIONS = f"""Write a professional, highly tailored,
approximately one-page cover letter (350–450 words, 4–5 paragraphs)
for this candidate and job.

{UNTRUSTED_NOTICE}

COVER LETTER STRUCTURE:

PARAGRAPH 1 — INTRODUCTION (50–70 words)
- Mention the target position and company.
- Introduce the candidate's relevant professional background.
- Highlight the strongest qualifications.

PARAGRAPH 2 — TECHNICAL EXPERIENCE (90–110 words)
- Focus on the most important required JD skills.
- Describe relevant projects and technologies.
- Highlight supported achievements from employment history.

PARAGRAPH 3 — ADDITIONAL TECHNICAL STRENGTHS (90–110 words)
- Demonstrate additional JD-relevant technical capabilities.
- Mention relevant cloud technologies, frameworks, databases,
  architecture, automation, or AI experience where supported.
- Connect technical achievements to the employer's needs.

PARAGRAPH 4 — PROFESSIONAL VALUE (60–80 words)
- Explain how the candidate's experience aligns with the role.
- Emphasize supported engineering accomplishments.
- Avoid repeating information from previous paragraphs.

PARAGRAPH 5 — CLOSING (40–60 words)
- Reaffirm interest in the opportunity.
- Express enthusiasm about contributing to the team.
- Close professionally and naturally.


LENGTH AND FORMATTING:
- Target approximately 400 words.
- Acceptable range: 350–450 words.
- Use 4–5 natural, well-balanced paragraphs.
- The letter should occupy approximately one US Letter page.
- Use concise, professional American English.
- Avoid excessive spacing or unnecessarily short paragraphs.
- Do not repeat the resume word for word.
- Prioritize important JD requirements and relevant accomplishments.
- Ensure all experience claims are supported by candidate evidence.
- Do not add a contact block; the application adds it separately.
- Do not include internal evidence IDs in the visible letter.
- Do not request manual confirmation.
"""


def operator_notes(base: str, notes: str | None) -> str:
    """The operator's own guidance (Settings default + this application). The hard rules above it always win."""
    notes = (notes or "").strip()
    if not notes:
        return base
    return (
        f"{base}\n\nThe operator's preferences for this document. Follow them unless they conflict with the rules above,"
        f" which always win (never add facts that aren't in the evidence):\n{notes[:4000]}"
    )


def ai_resume(
    ai: AIService,
    snap: dict[str, Any],
    req: JobRequirements,
    company: str | None,
    raw_jd: str,
    application_id: str,
    previous: ResumeContent | None,
    section: Section,
    instructions: str = "",
    density: str = "standard",
    semantic: dict[str, str] | None = None,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> tuple[ResumeContent, list[str], dict[str, Any], list[str]]:
    missing_skills = expansion_candidates(snap, req, exclude)
    hints = expansion_hints(snap, req, semantic, exclude)
    candidate_prefix = "\n\n".join(
        [
            wrap("candidate_evidence", evidence_listing(snap)),
            wrap("verified_skills", "\n".join(s["name"] for s in snap.get("skills", []))),
        ]
    )
    result = ai.run(
        operation=GenerationOperation.RESUME_GENERATION,
        role="writing",
        instructions=operator_notes(resume_instructions(density) + "\n\n" + EXPANSION_INSTRUCTIONS, instructions),
        input_text="\n\n".join(
            [
                _job_brief(req, company, raw_jd),
                wrap(
                    "skills_shown_by_experience",
                    "\n".join(
                        f"R-{p['role_id']}: {p['skill']} — "
                        + (f"shown by {', '.join('B-' + b for b in p['bullet_ids'])}" if p["bullet_ids"] else "confirmed for this role")
                        + f" ({p['evidence']})"
                        for p in snap.get("inferred_skills") or []
                    )
                    or "none",
                ),
                wrap(
                    "transferable_experience",
                    "\n".join(
                        f"R-{r['role_id']}: {r['skill']} (not used) — related: {', '.join(r['related'])} in "
                        + ", ".join("B-" + b for b in r["bullet_ids"][:3])
                        for r in snap.get("transferable_skills") or []
                    )
                    or "none",
                ),
                *expansion_prompt_sections(snap, missing_skills, hints),
            ]
        ),
        schema=AIResumeTailoring,
        application_id=application_id,
        max_output_tokens=6000,
        cache_prefix=candidate_prefix,
    )
    expanded, expansion_notes = expand_snapshot(snap, req, semantic, exclude, result.placements, raw_jd)
    baseline = deterministic_resume(expanded, req, density)
    verified_skills = [s["name"] for s in expanded.get("skills", [])]
    notes: list[str] = []
    bullets = bullet_index(expanded)
    verified = {normalize_skill(s): s for s in verified_skills}

    skills = []
    # for name in result.skill_order:
    #     key = normalize_skill(name)
    #     if key in verified and verified[key] not in skills:
    #         skills.append(verified[key])
    dropped_skills = len(result.skill_order) - len(skills)
    if dropped_skills:
        notes.append(f"Removed {dropped_skills} skill(s) the model suggested that aren't verified.")
    # Job skills found in the candidate's experience are listed even if the model left them out.
    shown = [verified[p["normalized"]] for p in expanded.get("inferred_skills") or [] if p["normalized"] in verified and verified[p["normalized"]] not in skills]
    skills = list(dict.fromkeys([*shown, *skills] if skills else skills))[:MAX_SKILLS] or baseline.skills

    by_role = {r.role_id.removeprefix("R-"): r for r in result.roles}
    experience = []
    dropped_bullets = 0
    restored = 0
    for base_role in baseline.experience:
        ai_role = by_role.get(base_role.role_id)
        # confirmed_here = role_skills(snap, base_role.role_id)
        kept: list[ResumeBullet] = []
        for bullet in ai_role.bullets if ai_role else []:
            ids = [i.removeprefix("B-") for i in bullet.source_bullet_ids]  #### consider this part
            if ids and all(i in bullets and bullets[i][0] == base_role.role_id for i in ids) and bullet.text.strip():
                # originals = [bullets[i][1] for i in ids]
                # if unsupported_tools(bullet.text, originals, confirmed_here):
                #     # Names a technology this role's evidence doesn't show: keep the source wording instead.
                #     if all(k.text != originals[0] for k in kept):
                #         kept.append(ResumeBullet(text=originals[0], source_ids=[ids[0]]))
                #     restored += 1
                #     continue
                kept.append(ResumeBullet(text=bullet.text.strip()[:1000], source_ids=ids))
            else:
                dropped_bullets += 1
        role = base_role.model_copy(update={"bullets": kept or base_role.bullets})
        experience.append(role)
    if dropped_bullets:
        notes.append(f"Removed {dropped_bullets} bullet(s) that didn't cite verified evidence from the same role.")
    if restored:
        notes.append(f"Kept the original wording of {restored} bullet(s) that named a technology not confirmed for that role.")

    tailored = baseline.model_copy(
        update={
            "headline": (result.headline or baseline.headline),
            "summary": result.summary.strip() or baseline.summary,
            "skills": skills,
            "experience": experience,
        }
    )
    # A skill the employer's experience shows is in that employer's bullets even when the writing left it out.
    tailored, placed = ensure_placements(tailored, expanded)
    notes += placed
    if previous is not None and section != "all":
        tailored = merge_section(previous, tailored, section)
    return tailored, notes, expanded, expansion_notes


def merge_section(previous: ResumeContent, fresh: ResumeContent, section: Section) -> ResumeContent:
    """Section-only regeneration: take one section from ``fresh``, keep the rest as reviewed."""
    if section == "summary":
        return previous.model_copy(update={"summary": fresh.summary, "headline": fresh.headline})
    if section == "skills":
        return previous.model_copy(update={"skills": fresh.skills})
    if section == "experience":
        return previous.model_copy(update={"experience": fresh.experience})
    return fresh


_PAST_TENSE = {"built", "led", "ran", "wrote", "made", "drove", "grew", "won", "set", "cut", "owned", "rebuilt", "shipped", "kept", "taught", "brought", "began", "sold", "spent", "took"}


def _clause(text: str) -> str | None:
    """'Built X.' -> 'built X' when the bullet starts with a past-tense verb, else None."""
    first, _, rest = text.strip().rstrip(".").partition(" ")
    if first.lower().endswith("ed") or first.lower() in _PAST_TENSE:
        return first.lower() + (" " + rest if rest else "")
    return None


def _role_sentence(role: dict[str, Any], bullets: list[dict[str, Any]]) -> str:
    """One sentence per role so highlights don't repeat 'As X at Y' for every bullet."""
    clauses = [_clause(b["text"]) for b in bullets]
    if all(clauses):
        if len(clauses) <= 2:
            joined = " and ".join(clauses)  # type: ignore[arg-type]
        else:
            joined = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"  # type: ignore[arg-type]
        return f"As {role['title']} at {role['employer']}, I {joined}."
    listed = "; ".join(b["text"].strip().rstrip(".") for b in bullets)
    return f"As {role['title']} at {role['employer']}, my work included: {listed}."


def deterministic_cover_letter(snap: dict[str, Any], req: JobRequirements, company: str | None, job_title: str | None) -> CoverLetterContent:
    skill_terms, words = _relevance_terms(req)
    roles = snap.get("employment", [])
    years = years_phrase(snap.get("total_years_experience") or 0) if (snap.get("total_years_experience") or 0) >= 1 else ""
    target_company = company or "your organization"
    target_title = job_title or req.title or "this role"
    paragraphs: list[CoverParagraph] = []

    opening = f"I am writing to apply for the {target_title} position at {target_company}."
    opening_ids = ["YRS"]
    if roles:
        recent = roles[0]
        opening += (
            f" I bring {years} years of experience, most recently as {recent['title']} at {recent['employer']}."
            if years
            else f" Most recently I worked as {recent['title']} at {recent['employer']}."
        )
        opening_ids.append(f"R-{recent['id']}")
    paragraphs.append(CoverParagraph(text=opening, evidence_ids=opening_ids))

    # The most relevant verified bullets, grouped by role (at most three per role, two roles).
    scored = sorted(
        ((role, b) for role in roles for b in role["bullets"]),
        key=lambda rb: _bullet_score(rb[1]["text"], skill_terms, words),
        reverse=True,
    )
    by_role: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for role, bullet in scored:
        entry = by_role.setdefault(role["id"], (role, []))
        if len(entry[1]) < 3:
            entry[1].append(bullet)
    for role, bullets in list(by_role.values())[:2]:
        paragraphs.append(CoverParagraph(text=_role_sentence(role, bullets), evidence_ids=[f"B-{b['id']}" for b in bullets]))

    credentials = []
    education = snap.get("education", [])
    if education:
        e = education[0]
        degree = ", ".join(x for x in (e.get("degree"), e.get("field")) if x)
        if degree and e.get("institution"):
            credentials.append((f"a {degree} from {e['institution']}", f"E-{e['id']}"))
    for cert in snap.get("certifications", [])[:2]:
        credentials.append((f"the {cert['name']} certification", f"C-{cert['id']}"))
    if credentials:
        listed_credentials = " and ".join(c[0] for c in credentials) if len(credentials) <= 2 else ", ".join(c[0] for c in credentials[:-1]) + f", and {credentials[-1][0]}"
        credentials_sentence = f"I also hold {listed_credentials}."
    else:
        credentials_sentence = ""

    verified = {s["normalized"]: s for s in snap.get("skills", [])}
    matched = [verified[normalize_skill(s)] for s in req.mandatory_skills + req.preferred_skills if normalize_skill(s) in verified]
    if matched:
        names = [s["name"] for s in matched[:6]]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        paragraphs.append(
            CoverParagraph(
                text=f"My hands-on experience with {listed} lines up with what you are looking for in this role, and I would bring the same care to the work at {target_company}. {credentials_sentence}".strip(),
                evidence_ids=[f"S-{s['id'][:8]}" for s in matched[:6]] + [c[1] for c in credentials],
            )
        )
    elif credentials_sentence:
        paragraphs.append(CoverParagraph(text=credentials_sentence, evidence_ids=[c[1] for c in credentials]))
    paragraphs.append(
        CoverParagraph(
            text=f"Thank you for considering my application. I would welcome the opportunity to discuss how my experience can support the team at {target_company}.",
            evidence_ids=[],
        )
    )
    return CoverLetterContent(
        date=date.today().strftime("%B %d, %Y").replace(" 0", " "),
        company=company,
        job_title=job_title or req.title,
        paragraphs=paragraphs,
        signature=snap["full_name"],
        contact=_contact(snap),
    )


def ai_cover_letter(
    ai: AIService,
    snap: dict[str, Any],
    req: JobRequirements,
    company: str | None,
    job_title: str | None,
    raw_jd: str,
    application_id: str,
    instructions: str = "",
) -> CoverLetterContent:
    result = ai.run(
        operation=GenerationOperation.COVER_LETTER_GENERATION,
        role="writing",
        instructions=operator_notes(COVER_INSTRUCTIONS, instructions),
        input_text=_job_brief(req, company, raw_jd),
        schema=AICoverLetter,
        application_id=application_id,
        max_output_tokens=3000,
        cache_prefix=wrap("candidate_evidence", evidence_listing(snap)),
    )
    return CoverLetterContent(
        date=date.today().strftime("%B %d, %Y").replace(" 0", " "),
        company=company,
        job_title=job_title or req.title,
        greeting=result.greeting.strip() or "Dear Hiring Manager,",
        paragraphs=[CoverParagraph(text=p.text.strip(), evidence_ids=p.evidence_ids) for p in result.paragraphs if p.text.strip()],
        closing=result.closing.strip() or "Sincerely,",
        signature=snap["full_name"],
        contact=_contact(snap),
    )


def generate_documents(
    ai: AIService | None,
    snap: dict[str, Any],
    req: JobRequirements,
    *,
    company: str | None,
    job_title: str | None,
    raw_jd: str,
    application_id: str,
    document: str,
    section: Section,
    previous_resume: ResumeContent | None,
    previous_cover: CoverLetterContent | None,
    instructions: str = "",
    density: str = "standard",
    semantic: dict[str, str] | None = None,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> tuple[ResumeContent | None, CoverLetterContent | None, list[str], str, dict[str, Any], list[str]]:
    """Returns documents, notes, mode, expanded snapshot and expansion notes. Transient AI errors propagate.

    The resume call also returns skill-placement decisions. The independent cover-letter call starts at the same
    time, so it never waits for resume-specific skill placement.
    """
    notes: list[str] = []
    use_ai = ai is not None and ai.available()
    mode = "ai" if use_ai else "deterministic"
    resume: ResumeContent | None = None
    cover: CoverLetterContent | None = None
    expanded_snap = snap
    expansion_notes: list[str] = []

    calls: dict[str, Callable[[], Any]] = {}
    if use_ai and document in ("both", "resume"):
        calls["resume"] = lambda: ai_resume(  # type: ignore[arg-type]
            ai, snap, req, company, raw_jd, application_id, previous_resume, section, instructions, density, semantic, exclude
        )
    if use_ai and document in ("both", "cover_letter"):
        calls["cover"] = lambda: ai_cover_letter(ai, snap, req, company, job_title, raw_jd, application_id, instructions)  # type: ignore[arg-type]
    outcomes = run_concurrently(calls)
    outcomes = retry_transient_calls(calls, outcomes)
    for value, error in outcomes.values():
        if isinstance(error, AIError) and error.transient:
            raise error  # the queue retries the whole generation only after focused retries are exhausted
        if error is not None and not isinstance(error, AIError):
            raise error

    if document in ("both", "resume"):
        value, error = outcomes.get("resume", (None, None))
        if value is not None:
            resume, resume_notes, expanded_snap, expansion_notes = value
            notes += resume_notes
        elif error is not None:
            reason = "unavailable" if isinstance(error, (AIConfigError, BudgetExceeded)) else "failed"
            notes.append(f"AI writing {reason} ({error}); used deterministic tailoring.")
            mode = "deterministic"
        if resume is None:
            expanded_snap, expansion_notes = expand_snapshot(snap, req, semantic, exclude, job_text=raw_jd)
            fresh = deterministic_resume(expanded_snap, req, density)
            resume = merge_section(previous_resume, fresh, section) if previous_resume and section != "all" else fresh

    if document in ("both", "cover_letter"):
        value, error = outcomes.get("cover", (None, None))
        if value is not None:
            cover = value
        elif error is not None:
            if isinstance(error, (AIConfigError, BudgetExceeded)):
                notes.append(f"AI writing unavailable ({error}); used a deterministic cover letter.")
            else:
                notes.append(f"AI cover letter failed ({error}); used a deterministic cover letter.")
        if cover is None:
            cover = deterministic_cover_letter(snap, req, company, job_title)
    return resume, cover, notes, mode, expanded_snap, expansion_notes


def run_concurrently(calls: dict[str, Callable[[], Any]]) -> dict[str, tuple[Any, BaseException | None]]:
    """Runs independent calls (AI requests) at the same time; returns ``{name: (value, error)}``."""
    if len(calls) <= 1:
        results: dict[str, tuple[Any, BaseException | None]] = {}
        for name, call in calls.items():
            try:
                results[name] = (call(), None)
            except Exception as exc:
                results[name] = (None, exc)
        return results
    with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="ai") as pool:
        futures = {name: pool.submit(call) for name, call in calls.items()}
    return {name: (None, future.exception()) if future.exception() else (future.result(), None) for name, future in futures.items()}


def retry_transient_calls(
    calls: dict[str, Callable[[], Any]],
    outcomes: dict[str, tuple[Any, BaseException | None]],
    delays: tuple[float, ...] = _TRANSIENT_RETRY_DELAYS_SECONDS,
) -> dict[str, tuple[Any, BaseException | None]]:
    """Retry only failed transient requests, preserving successful concurrent results.

    Retries run sequentially to avoid recreating the burst that may have interrupted the
    original request. The processing queue remains the final, longer-backoff fallback.
    """
    for name, call in calls.items():
        _, error = outcomes[name]
        if not (isinstance(error, AIError) and error.transient):
            continue
        for delay in delays:
            time.sleep(delay)
            try:
                outcomes[name] = (call(), None)
                break
            except AIError as exc:
                outcomes[name] = (None, exc)
                if not exc.transient:
                    break
            except Exception as exc:
                outcomes[name] = (None, exc)
                break
    return outcomes
