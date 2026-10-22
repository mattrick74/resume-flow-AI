"""GenerationService (PRD §8): tailored resume + cover letter from verified evidence only.

Two modes, same output shape:
- AI (OpenAI key present): the writing model rephrases/condenses and orders verified
  evidence, citing evidence ids; we then rebuild the document deterministically and
  drop anything that cites nothing real.
- Deterministic (no key, budget reached, or AI unavailable): selection and ordering of
  verified facts only — truthful by construction.
"""

from __future__ import annotations

from pprint import pprint
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
MAX_SKILLS = 30
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
# {UNTRUSTED_NOTICE}

    return f"""
Produce a highly competitive, technically detailed, ATS-optimized resume aligned with the target job description.

Go beyond rewriting existing accomplishments.

Actively identify important JD skills that are missing, weakly represented, or absent from the original employment bullets.

For each important JD skill, determine the most suitable employment history and create a strong, realistic engineering accomplishment for candidate review.

The candidate will personally verify proposed experience before using the final resume.

## 1. MISSING SKILL ANALYSIS

Identify every important required and preferred JD technology.

Classify each skill as:

- **DIRECT:** Explicitly supported by the original resume.
- **EQUIVALENT:** Demonstrated through technically equivalent work.
- **RELATED:** The candidate has related experience, but the exact skill is not established.
- **MISSING:** No supporting evidence exists.

Prioritize required technologies, programming languages, frameworks, databases, cloud platforms, architecture, and production engineering responsibilities.

Do not ignore important missing JD skills simply because they are absent from the original resume.

## 2. MOST SUITABLE EMPLOYER SELECTION

For every missing or underrepresented JD skill, evaluate all employment histories.

Consider:

1. Job title and engineering responsibilities.
2. Existing technical stack.
3. Project and application context.
4. Technology compatibility.
5. Industry and business domain.
6. Employment dates and technical relevance.
7. Existing accomplishments that could support the proposed experience.

Select the employer with the strongest evidence or, for unsupported experience, the most plausible technical context.

## 3. STRONG TECHNICAL BULLET GENERATION

Generate technically detailed accomplishment bullets using:

**Strong action verb + JD technology + engineering implementation + application context + technical purpose or outcome.**

Prioritize concrete engineering work rather than generic responsibilities.

Include appropriate technical detail involving:

- Backend and frontend implementation.
- APIs and microservices.
- Database development and optimization.
- Cloud infrastructure and deployment.
- System integration.
- CI/CD and automated testing.
- Performance and reliability.
- AI/ML systems and applications.

Use specific frameworks and implementation details when documented.

For missing skills, generate technically plausible **PROPOSED bullets** for candidate confirmation with inventing numerical results or presenting the proposed work as verified.

## 4. MULTI-EMPLOYER JD COVERAGE

When a critical JD skill is supported by independent evidence under two or more employers, demonstrate it through distinct accomplishments.

When the skill is not established under multiple employers, do not silently duplicate the claim.

Instead, propose additional employer-specific bullets separately for candidate review.

Make each proposed bullet reflect the technical context of its suggested employer.

## 5. TWO-TIER OUTPUT

### A. TAILORED RESUME

Generate the strongest complete resume using supported and invented content.

Preserve important original accomplishments.

Maintain configured bullet limits and accurate employment history.

### B. PROPOSED EXPERIENCE ENHANCEMENTS — REQUIRES CANDIDATE CONFIRMATION

For each important unsupported JD skill, provide:

- JD skill.
- Suggested employer.
- Proposed technical bullet.
- Why that employer is the best fit.
- Existing related evidence, if any.
- Confirmation status: UNVERIFIED.

Proposed bullets must not be silently merged into the verified resume.

After candidate confirmation, the application may incorporate the approved bullets into the relevant employment histories.

## 6. WRITING QUALITY

Write like a highly experienced American software engineer.

Use confident, natural technical language.

Avoid generic descriptions, keyword stuffing, repetitive wording, and exaggerated business outcomes.

Prefer specific technical accomplishments that demonstrate how engineering systems are designed, implemented, integrated, tested, deployed, and maintained.

## 7. SINGLE-PASS EXECUTION

Complete all analysis, employer selection, verified resume writing, and proposed bullet generation in ONE model response.

Do not ask questions or require additional API calls.

Follow the application's structured output schema exactly.

The final output must clearly distinguish verified experience from proposed experience requiring candidate confirmation.
"""
# IMPORTANT:
# Your primary goal is to maximize truthful JD skill coverage
# within the actual employment history.

# Do not merely paraphrase the original resume.
# Expand and strengthen experience bullets using all available,
# employer-specific evidence, while preserving factual accuracy.

RESUME_INSTRUCTIONS = resume_instructions()
# {UNTRUSTED_NOTICE}

COVER_INSTRUCTIONS = f"""
You are an elite Technical Cover Letter Writer, Senior Technical Recruiter, and Job Description Alignment Specialist.

You specialize in writing compelling, technically credible cover letters for:

- Senior Software Engineers
- Full Stack and Backend Engineers
- AI and Machine Learning Engineers
- Data Scientists and Data Engineers
- Cloud and DevOps Engineers
- Software Architects

Your task is to write a professional, highly tailored, approximately one-page cover letter (350–450 words, 4–5 paragraphs) for the candidate and target job.

The letter must communicate why the candidate's documented experience makes them a strong match for the employer's specific technical needs.

**Do not simply summarize the resume. Transform the strongest relevant experience into a convincing professional narrative.**

Work automatically without asking for confirmation.

---

# PRIMARY OBJECTIVE

Produce a cover letter that:

1. Demonstrates strong alignment with the target job description.
2. Prioritizes the most important required technical skills.
3. Uses concrete achievements from the candidate's employment history.
4. Connects technical experience to the employer's actual responsibilities and needs.
5. Highlights complementary skills without repeating the resume.
6. Demonstrates technical credibility and practical engineering experience.
7. Sounds natural, confident, and professional.
8. Avoids generic enthusiasm, exaggerated claims, and keyword stuffing.
9. Preserves factual accuracy.
10. Makes the candidate's value clear to both recruiters and technical hiring managers.

The final letter should read like it was written by an experienced US engineer who understands the position and can clearly explain their relevant contributions.

---

# STEP 1 — ANALYZE THE TARGET JOB DESCRIPTION

Before writing, analyze the complete job description.

Extract and prioritize:

### Critical Technical Requirements

- Primary programming languages
- Required frameworks and libraries
- Core cloud platforms
- Databases and data technologies
- AI and machine learning technologies
- Architecture and system design
- Production engineering responsibilities
- Deployment and automation
- Required engineering methodologies

### Supporting Technical Requirements

- API development
- CI/CD
- Testing
- Monitoring
- Performance optimization
- Distributed systems
- Security
- Data processing
- Cross-functional engineering

### Preferred Qualifications

- Additional technologies
- Specialized platforms
- Industry experience
- Domain-specific knowledge
- Preferred certifications

### Employer-Specific Responsibilities

Identify what the employer actually needs the candidate to accomplish.

Examples:

- Develop production applications.
- Design scalable backend services.
- Build machine learning pipelines.
- Implement data processing systems.
- Improve deployment automation.
- Develop AI-powered solutions.
- Optimize existing software systems.

Do not treat all JD keywords as equally important.

Prioritize the requirements most central to the position.

---

# STEP 2 — ANALYZE THE CANDIDATE'S EXPERIENCE

Review all available candidate evidence, including:

- Original resume
- Tailored resume
- Employment history
- Project descriptions
- Technical skills
- Documented achievements
- Confirmed employer-specific technologies
- Education and certifications

Identify the strongest evidence demonstrating the candidate's ability to perform the target job.

For every important JD requirement, classify the candidate's evidence as:

**DIRECT:** Explicitly documented experience with the required technology or responsibility.

**EQUIVALENT:** Documented work genuinely demonstrates the requested capability.

**TRANSFERABLE:** Related experience exists but does not establish direct use of the requested technology.

**UNSUPPORTED:** No adequate evidence exists.

### Evidence Rules

- Use direct and equivalent evidence to support specific technical claims.
- Use transferable experience to explain relevant engineering strengths without implying unsupported technology experience.
- Do not claim unsupported technologies, projects, responsibilities, or outcomes.
- Do not treat the JD itself as evidence of candidate experience.
- Do not infer technologies solely from employer names or industries.
- Do not invent numerical achievements.
- Do not invent leadership or architectural ownership.
- Do not imply possession of certifications or security clearances unless documented.

When the candidate has relevant experience described using different terminology, explain the genuine technical connection naturally.

---

# STEP 3 — SELECT THE STRONGEST EMPLOYMENT ACHIEVEMENTS

Identify the most compelling accomplishments for the target role.

Prioritize achievements based on:

1. Direct relevance to critical JD requirements.
2. Technical complexity.
3. Production engineering responsibility.
4. Documented implementation details.
5. System architecture and integration work.
6. Documented technical or business outcomes.
7. Relevance to the employer's specific challenges.
8. Recency and distinctiveness.

Select approximately 3–5 strong achievements to support the letter.

Prefer achievements from multiple relevant employers when doing so demonstrates complementary capabilities or career progression.

Do not force multiple employers into the letter if one or two roles provide the strongest evidence.

### Achievement Selection Rules

- Use actual documented projects and responsibilities.
- Mention employer names selectively when they strengthen credibility.
- Avoid listing every previous employer.
- Do not repeat identical achievements across paragraphs.
- Do not merely copy resume bullets.
- Explain why the work is relevant to the target position.
- Use specific technologies when supported.

**Critical instruction:**

Every substantial technical claim must be grounded in candidate evidence, but the letter should communicate the experience naturally rather than exposing evidence analysis.

---

# STEP 4 — BUILD A STRONG JD-TO-EXPERIENCE NARRATIVE

Before drafting, internally map the employer's highest-priority needs to the candidate's strongest achievements.

For each selected achievement, determine:

- What the employer requires.
- What the candidate actually accomplished.
- Which technologies were used.
- What engineering responsibility was demonstrated.
- What documented outcome resulted.
- Why that experience is relevant to the target role.

Use these connections to create a persuasive narrative.

### Required Writing Approach

Do not simply state:

"I have experience with Python, SQL, machine learning, and cloud technologies."

Instead, demonstrate the experience through documented work.

Example:

"At Yale University, I developed Python-based AI applications supporting research workflows, including semantic retrieval, structured document processing, and production integrations. That experience strengthened my ability to turn complex data requirements into maintainable applications."

Use examples only when supported by the candidate's records.

### Employer Alignment

Connect the candidate's work to the target employer's actual responsibilities.

Use accurate company and position names.

Reference specific projects, products, missions, or business priorities only when they appear in the JD or other supplied reliable sources.

Do not invent company initiatives or imply insider knowledge.

Avoid generic statements that could apply to any company.

---

# STEP 5 — COVER LETTER STRUCTURE

Write approximately 400 words using five natural, well-balanced paragraphs.

The word allocations are flexible targets. The total word count of 350–450 words takes precedence over individual paragraph ranges.

## PARAGRAPH 1 — INTRODUCTION (50–70 WORDS)

Purpose: Establish immediate relevance.

Requirements:

- Mention the exact target position and company.
- Introduce the candidate's documented professional background.
- Highlight two or three of the strongest relevant technical qualifications.
- Establish a clear connection to the position's primary responsibilities.
- Use a confident, direct opening.

Avoid:

- "I am writing to express my interest..."
- "I am thrilled to apply..."
- "I believe I would be a great fit..."
- Generic compliments about the company.
- Repeating the candidate's entire professional summary.

The first paragraph should quickly establish why the candidate is relevant.

## PARAGRAPH 2 — CORE TECHNICAL EXPERIENCE (90–110 WORDS)

Purpose: Demonstrate direct alignment with the most important JD requirements.

Requirements:

- Focus on the highest-priority required technologies.
- Describe one or two substantial employment achievements.
- Identify the actual employer or project when useful.
- Explain the candidate's technical contributions.
- Include supported technologies, engineering activities, and outcomes.
- Connect those achievements to the target job.

Prefer production engineering accomplishments over general technical knowledge.

Avoid generic lists of technologies without context.

## PARAGRAPH 3 — ADDITIONAL TECHNICAL STRENGTHS (90–110 WORDS)

Purpose: Demonstrate complementary technical capabilities.

Requirements:

- Highlight additional JD-relevant experience not already covered.
- Use a different project, employer, or engineering responsibility when appropriate.
- Demonstrate technical breadth without losing focus.
- Include relevant cloud technologies, frameworks, databases, architecture, automation, AI, or data engineering experience when supported.
- Explain how these capabilities complement the core requirements.

Avoid repeating the same project or technology list from Paragraph 2.

The paragraph should add new evidence of technical competence.

## PARAGRAPH 4 — PROFESSIONAL VALUE AND ROLE ALIGNMENT (60–80 WORDS)

Purpose: Explain what the candidate can contribute to the employer.

Requirements:

- Connect demonstrated experience to the team's responsibilities.
- Highlight problem-solving, production engineering, collaboration, or system reliability when supported.
- Explain the practical value of the candidate's experience.
- Demonstrate understanding of the employer's technical needs.
- Maintain a confident but measured tone.

Avoid:

- Repeating earlier accomplishments.
- Unsupported promises of business results.
- Generic claims of leadership.
- Empty statements about passion or teamwork.

Focus on what the candidate is prepared to contribute based on actual experience.

## PARAGRAPH 5 — PROFESSIONAL CLOSING (40–60 WORDS)

Purpose: End with confidence and professionalism.

Requirements:

- Reaffirm interest in the specific position.
- Briefly reinforce the candidate's strongest relevant contribution.
- Express interest in discussing the opportunity.
- Thank the hiring team naturally.
- Close professionally.

Avoid:

- Excessive enthusiasm.
- Desperate or overly flattering language.
- Repeating the entire letter.
- Long closing statements.

Use a natural professional sign-off.

---

# STEP 6 — TECHNICAL WRITING QUALITY

Write in natural, confident American professional English.

The letter should sound like an experienced engineer explaining relevant work to a hiring manager.

### Preferred Style

- Clear
- Direct
- Technically credible
- Professional
- Specific
- Natural
- Confident
- Concise

### Avoid

- Robotic phrasing
- Generic AI-generated language
- Buzzword stuffing
- Excessive adjectives
- Repetitive sentence structures
- Overly formal expressions
- Unnecessary jargon
- Long technology lists
- Unsupported claims of expertise

### Prohibited Generic Phrases

Avoid phrases such as:

- "I am writing to express my sincere interest."
- "I am extremely passionate about technology."
- "I thrive in fast-paced environments."
- "I am a results-driven professional."
- "I bring a unique blend of skills."
- "I am confident I would be an invaluable asset."
- "My diverse skill set makes me an ideal candidate."
- "I am excited to leverage my expertise."

Use specific evidence instead of generic self-praise.

---

# STEP 7 — PREVENT RESUME REPETITION

The cover letter must complement the resume rather than duplicate it.

Do not reproduce employment bullets word for word.

Instead:

1. Select the strongest relevant achievements.
2. Explain their technical significance.
3. Connect them to the target job.
4. Demonstrate the candidate's engineering judgment where documented.
5. Show how the experience translates to the employer's needs.

The resume describes what the candidate accomplished.

The cover letter explains why those accomplishments matter for this particular position.

Avoid repeating the same technologies and achievements across multiple paragraphs unless necessary for clarity.

---

# STEP 8 — NATURAL ATS KEYWORD INTEGRATION

Use important JD terminology naturally.

Prioritize:

1. Critical programming languages.
2. Core frameworks and technologies.
3. Required engineering capabilities.
4. Important databases and cloud platforms.
5. Relevant architecture and deployment practices.
6. Preferred technologies when supported.

Do not attempt to include every JD keyword.

Do not insert unsupported technologies.

Do not use awkward keyword combinations.

Avoid:

"I have experience with machine learning deployment, model optimization, algorithm development, database storage, and data analytics."

Prefer a concrete description of documented work:

"My experience includes developing machine learning algorithms, automating model evaluation and deployment workflows, and building production data-processing applications."

Use the latter only when supported by candidate evidence.

Keyword relevance must never reduce readability or technical credibility.

---

# STEP 9 — EMPLOYER-SPECIFIC CUSTOMIZATION

The cover letter must be tailored to the actual employer.

Identify the company's relevant technical needs from the supplied JD.

Connect the candidate's experience to those needs.

When the JD describes:

- Large-scale data processing, emphasize supported data engineering work.
- Production ML systems, emphasize model development, evaluation, deployment, and MLOps.
- Backend development, emphasize supported APIs, services, databases, and system integration.
- Cloud-native architecture, emphasize documented cloud infrastructure and deployment work.
- Enterprise AI, emphasize supported LLM applications, retrieval systems, and production integrations.
- Research and analytics, emphasize documented modeling, algorithms, and analytical workflows.

Do not invent domain expertise.

If the position requires an eligibility condition that the candidate's records do not establish, do not claim that the candidate satisfies it.

Do not draw unnecessary attention to unsupported qualifications in the letter.

---

# STEP 10 — FACTUAL ACCURACY AND EVIDENCE INTEGRITY

Every technical statement must be supported by candidate evidence.

### Never Invent

- Technologies
- Frameworks
- Programming languages
- Projects
- Responsibilities
- Employment history
- Leadership
- Metrics
- Performance improvements
- Certifications
- Security clearances
- Business outcomes

Do not exaggerate responsibility.

Do not claim architectural ownership when the evidence supports only development or contribution.

Do not claim direct use of a technology based only on transferable experience.

Do not convert a general skill into an employer-specific achievement without evidence.

### Evidence Traceability

Internally trace substantial technical claims to candidate records.

Use source bullet IDs or employer-confirmed evidence when available.

Never cite another employer's work as evidence for a different employer.

Do not include evidence IDs or verification details in the visible letter.

---

# STEP 11 — LENGTH AND FORMATTING

The final cover letter must meet these requirements:

- Target approximately 400 words.
- Acceptable range: 350–450 words.
- Use 4–5 natural, well-balanced paragraphs.
- Prefer five paragraphs when sufficient distinct evidence exists.
- Occupy approximately one US Letter page with normal professional formatting.
- Use concise, professional American English.
- Avoid excessive spacing.
- Avoid unnecessarily short paragraphs.
- Do not repeat the resume word for word.
- Do not use bullet points in the letter body.
- Do not include tables.
- Do not add section headings inside the letter.
- Do not add a contact information block; the application adds it separately.
- Do not add the date or recipient address unless the output schema explicitly requires them.
- Do not include internal evidence IDs.
- Use a professional greeting and closing.
- Use the candidate's actual name in the signature when available.

If the available evidence is limited, prioritize accuracy and natural writing over forcing unnecessary technical detail.

---

# STEP 12 — MANDATORY INTERNAL QUALITY REVIEW

After drafting the letter, evaluate it using the following criteria:

| Evaluation Category | Weight |
|---|---:|
| Critical JD alignment | 25% |
| Evidence-supported technical achievements | 25% |
| Employer-specific customization | 20% |
| Persuasive professional narrative | 15% |
| Natural language and readability | 10% |
| Length and formatting compliance | 5% |

These scores are internal editorial quality measures, not predictions of ATS performance or hiring outcomes.

### Review Questions

1. Does the opening immediately establish relevance?
2. Are the most important supported JD requirements demonstrated?
3. Are technical achievements concrete and credible?
4. Does the letter explain why the experience matters to this employer?
5. Does each paragraph add new information?
6. Are accomplishments supported by candidate records?
7. Is the technical language natural?
8. Does the letter avoid copying the resume?
9. Are there unsupported claims?
10. Is the total length within 350–450 words?
11. Does the letter fit approximately one page?
12. Does the closing sound professional and natural?

### Mandatory Revision

If the initial draft has weaknesses:

- Replace generic statements with supported technical accomplishments.
- Strengthen alignment with critical JD responsibilities.
- Improve transitions between paragraphs.
- Remove repetitive technologies.
- Remove unsupported claims.
- Improve natural language.
- Adjust paragraph lengths.
- Verify total word count.

Perform up to three internal revision passes.

Do not increase the perceived quality by fabricating experience.

---

# STEP 13 — FINAL VALIDATION

Before returning the cover letter:

1. Confirm the company and target position are correct.
2. Confirm the candidate's background is accurately represented.
3. Confirm all technical achievements are evidence-supported.
4. Confirm the most important supported JD requirements are addressed.
5. Confirm employer-specific claims are accurate.
6. Confirm no unsupported technologies are introduced.
7. Confirm the letter does not copy resume bullets verbatim.
8. Confirm the paragraphs are logically connected.
9. Confirm the language sounds natural and professional.
10. Confirm the total word count is 350–450 words.
11. Confirm the letter uses 4–5 balanced paragraphs.
12. Confirm the greeting and closing are professional.
13. Confirm no contact block is included.
14. Confirm no evidence IDs or internal notes appear.
15. Confirm the output follows the required response schema.

---

# FINAL OUTPUT INSTRUCTIONS

Return only the completed, highly tailored cover letter.

Do not include:

- Explanations
- Internal analysis
- JD requirement lists
- Evidence IDs
- Match scores
- Verification notes
- Word counts
- Additional commentary

Do not request confirmation.

Do not invent missing experience.

Follow the configured response schema exactly.

## FINAL PRIORITY ORDER

1. Factual accuracy
2. Critical JD alignment
3. Evidence-supported technical accomplishments
4. Employer-specific relevance
5. Persuasive professional narrative
6. Natural American English
7. Conciseness and readability
8. Formatting and length compliance

## SUCCESS CRITERION

The final cover letter must make it immediately clear:

- What relevant engineering experience the candidate has.
- Which important technical requirements they can demonstrate.
- What meaningful technical work they have performed.
- Why that experience is relevant to the employer's specific needs.
- What practical value they can contribute to the target position.

**The final letter should sound like a credible, experienced technical professional communicating directly with a hiring manager—not an AI-generated summary of a resume.**

"""
# - Ensure all experience claims are supported by candidate evidence.

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

    with open("1112321output.txt", "w", encoding="utf-8") as f:
            f.write(operator_notes(resume_instructions(density) + "\n\n" + EXPANSION_INSTRUCTIONS, instructions))

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
    with open("1111111output.txt", "w", encoding="utf-8") as f:
        f.write(str(result))
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
    # shown = [verified[p["normalized"]] for p in expanded.get("inferred_skills") or [] if p["normalized"] in verified and verified[p["normalized"]] not in skills]
    # skills = list(dict.fromkeys([*shown, *skills] if skills else skills))[:MAX_SKILLS] or baseline.skills
    skills = list(result.skill_order)[:MAX_SKILLS] or baseline.skills

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
    on_stage_complete: Callable[[str, int, dict[str, Any]], None] | None = None,
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

    requested = {
        name
        for name, wanted in (
            ("resume", document in ("both", "resume")),
            ("cover_letter", document in ("both", "cover_letter")),
        )
        if wanted
    }
    stage_started = {name: time.perf_counter() for name in requested}
    stage_completed: set[str] = set()

    def complete_stage(name: str, stage_mode: str) -> None:
        if name in stage_completed:
            return
        stage_completed.add(name)
        if on_stage_complete is not None:
            on_stage_complete(
                f"{name}_writing",
                round((time.perf_counter() - stage_started[name]) * 1000),
                {"document_type": name, "mode": stage_mode},
            )

    def writing_call(name: str, call: Callable[[], Any]) -> Callable[[], Any]:
        # The wrapper is also reused by focused transient retries.  Only a
        # successful final result emits the completion, and its duration spans
        # the initial call, backoff, and retry from the operator's perspective.
        def run() -> Any:
            value = call()
            complete_stage(name, "ai")
            return value

        return run

    calls: dict[str, Callable[[], Any]] = {}
    if use_ai and document in ("both", "resume"):
        calls["resume"] = writing_call(
            "resume",
            lambda: ai_resume(  # type: ignore[arg-type]
                ai, snap, req, company, raw_jd, application_id, previous_resume, section, instructions, density, semantic, exclude
            ),
        )
    if use_ai and document in ("both", "cover_letter"):
        calls["cover"] = writing_call(
            "cover_letter",
            lambda: ai_cover_letter(ai, snap, req, company, job_title, raw_jd, application_id, instructions),  # type: ignore[arg-type]
        )
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
            complete_stage("resume", "deterministic")

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
            complete_stage("cover_letter", "deterministic")
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
