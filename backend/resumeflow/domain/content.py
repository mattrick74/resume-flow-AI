"""Structured document content (``documents.content_json``) and AI output schemas.

The AI never controls layout (PRD §8): it returns these structures, and the
deterministic DOCX renderer lays them out. Employers, titles, dates, education
and credentials are always copied from the verified snapshot, never from the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResumeBullet(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(default_factory=list)


class ResumeRole(BaseModel):
    role_id: str
    employer: str
    title: str
    location: str | None = None
    start: str | None = None
    end: str | None = None
    bullets: list[ResumeBullet] = Field(default_factory=list)
    # The employer's "Technologies:" line: only technologies confirmed for this role or named in its own bullets.
    technologies: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class ResumeContent(BaseModel):
    contact: Contact
    headline: str | None = Field(default=None, max_length=300)
    summary: str = Field(default="", max_length=3000)
    skills: list[str] = Field(default_factory=list)
    experience: list[ResumeRole] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    certifications: list[dict] = Field(default_factory=list)


class CoverParagraph(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list)


class CoverLetterContent(BaseModel):
    date: str
    company: str | None = None
    job_title: str | None = None
    greeting: str = "Dear Hiring Manager,"
    paragraphs: list[CoverParagraph] = Field(default_factory=list)
    closing: str = "Sincerely,"
    signature: str
    contact: Contact | None = None


# ---------------------------------------------------------------- AI schemas (strict: no defaults)


class AITailoredBullet(BaseModel):
    text: str
    source_bullet_ids: list[str] = Field(description="Ids (B-...) of the evidence bullets this bullet is based on")


class AITailoredRole(BaseModel):
    role_id: str = Field(description="The R-... id of the role")
    bullets: list[AITailoredBullet]


class AIResumeTailoring(BaseModel):
    headline: str | None
    summary: str
    skill_order: list[str] = Field(description="Verified skills only, most relevant first")
    roles: list[AITailoredRole]


class AICoverParagraph(BaseModel):
    text: str
    evidence_ids: list[str]


class AICoverLetter(BaseModel):
    greeting: str
    paragraphs: list[AICoverParagraph]
    closing: str


class AIClaimIssue(BaseModel):
    quote: str = Field(description="Exact text from the document")
    reason: str
    severity: Literal["error", "warning"]


class AIClaimReview(BaseModel):
    issues: list[AIClaimIssue]


class AISkillPlacement(BaseModel):
    """One missing job skill: where the candidate's experience shows it and the bullet that says so (v1.8.5)."""

    skill: str = Field(description="The job skill, exactly as listed in <missing_job_skills>")
    employer_id: str | None = Field(description="The R-... id of the employer whose bullets show it best, or null")
    evidence_ids: list[str] = Field(description="B-... ids of that employer's bullets that show it; the first is the one rewritten")
    classification: Literal["equivalent", "confirmed", "related", "none"]
    bullet: str = Field(description="The first cited bullet rewritten to name the skill; empty unless equivalent or confirmed")
    reason: str


class AISkillExpansion(BaseModel):
    placements: list[AISkillPlacement]


Section = Literal["all", "summary", "skills", "experience"]
DocumentChoice = Literal["both", "resume", "cover_letter"]
