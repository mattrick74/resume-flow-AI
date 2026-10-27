"""AI resume extraction: structured, verbatim, and always unverified."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from resumeflow.domain.enums import GenerationOperation
from resumeflow.domain.links import clean_links
from resumeflow.domain.profile import Bullet, CandidateProfile, Certification, Education, Role
from resumeflow.services.ai.service import UNTRUSTED_NOTICE, AIService, wrap
from resumeflow.services.resume_parser import ExtractedResume, ExtractedSkill


class AIRole(BaseModel):
    employer: str
    title: str
    location: str | None
    start: str | None = Field(description="YYYY-MM, or YYYY if no month is given")
    end: str | None = Field(description='YYYY-MM, YYYY, or "present"')
    bullets: list[str] = Field(description="Each achievement/responsibility copied verbatim")


class AIEducation(BaseModel):
    institution: str
    degree: str | None
    field: str | None
    end_year: int | None


class AICertification(BaseModel):
    name: str
    issuer: str | None
    year: int | None


class AISkill(BaseModel):
    name: str
    evidence: str = Field(description="Short verbatim quote from the resume that shows this skill")


class AIResumeExtraction(BaseModel):
    full_name: str | None
    email: str | None
    phone: str | None
    location: str | None
    headline: str | None
    summary: str | None
    links: list[str]
    employment: list[AIRole]
    education: list[AIEducation]
    certifications: list[AICertification]
    skills: list[AISkill]
    work_authorization: str | None = Field(description="Only if the resume explicitly states it, else null")


INSTRUCTIONS = f"""You extract structured data from one candidate's resume.
{UNTRUSTED_NOTICE}

Rules:
- Extract only what the resume explicitly states. Never infer, embellish, or add facts.
- Copy bullet points, titles, employers and the summary verbatim (fix only line-wrap breaks).
- Dates: use YYYY-MM when a month is given, YYYY otherwise, and "present" for current roles.
- links: full web addresses only (LinkedIn, GitHub, portfolio), e.g. "https://www.linkedin.com/in/name". A label's
  address may follow it in parentheses ("LinkedIn (https://...)"); use the address. Never return a label alone.
- Skills: list technologies, tools, methods and domain skills that appear in the resume. For each,
  give a short verbatim evidence quote from the resume text.
- Use null for anything not present. Order employment most recent first."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def ai_extract_resume(ai: AIService, resume_text: str) -> ExtractedResume:
    result = ai.run(
        operation=GenerationOperation.RESUME_PARSE,
        role="analysis",
        instructions=INSTRUCTIONS,
        input_text=wrap("resume", resume_text[:60_000]),
        schema=AIResumeExtraction,
    )
    haystack = _norm(resume_text)
    warnings: list[str] = []

    unmatched_bullets = 0
    roles: list[Role] = []
    for item in result.employment:
        bullets = []
        for text in item.bullets:
            text = text.strip()
            if not text:
                continue
            if _norm(text) not in haystack:
                unmatched_bullets += 1
            bullets.append(Bullet(text=text[:1000]))
        roles.append(
            Role(
                employer=item.employer[:200],
                title=item.title[:200],
                location=item.location,
                start=item.start,
                end=item.end,
                bullets=bullets,
            )
        )
    if unmatched_bullets:
        warnings.append(f"{unmatched_bullets} extracted bullet(s) don't match the original wording exactly; review them.")

    skills: list[ExtractedSkill] = []
    unsupported = 0
    for skill in result.skills:
        evidence = skill.evidence.strip()
        if evidence and _norm(evidence) not in haystack:
            unsupported += 1
            evidence = ""
        skills.append(ExtractedSkill(name=skill.name.strip()[:200], evidence=evidence[:500] or None))
    if unsupported:
        warnings.append(f"{unsupported} skill(s) had evidence quotes not found in the resume; evidence was cleared.")

    profile = CandidateProfile(
        headline=result.headline,
        summary=result.summary,
        employment=roles,
        education=[
            Education(institution=e.institution[:300], degree=e.degree, field=e.field, end_year=e.end_year)
            for e in result.education
            if 1900 <= (e.end_year or 2000) <= 2100
        ],
        certifications=[
            Certification(name=c.name[:300], issuer=c.issuer, year=c.year)
            for c in result.certifications
            if c.name.strip() and 1900 <= (c.year or 2000) <= 2100
        ],
        links=clean_links(result.links)[:10],  # web addresses only; a bare label such as "LinkedIn" is not a link
        work_authorization=result.work_authorization,
    )
    return ExtractedResume(
        full_name=result.full_name,
        email=result.email,
        phone=result.phone,
        location=result.location,
        profile=profile,
        skills=skills,
        source="ai",
        warnings=warnings,
    )
