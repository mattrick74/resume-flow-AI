"""Curated candidate profile (stored in ``candidates.profile_json``).

Every fact has its own ``verified`` flag. Extraction (heuristic or AI) always
produces unverified facts; only the operator can verify them, and only verified
facts are used for matching and document generation (PRD §5).
"""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def short_id() -> str:
    return uuid4().hex[:10]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Bullet(_Model):
    id: str = Field(default_factory=short_id)
    text: str = Field(min_length=1, max_length=1000)
    verified: bool = False


class Role(_Model):
    id: str = Field(default_factory=short_id)
    employer: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start: str | None = Field(default=None, max_length=20)  # YYYY-MM or YYYY
    end: str | None = Field(default=None, max_length=20)  # YYYY-MM, YYYY or "present"
    bullets: list[Bullet] = Field(default_factory=list)
    verified: bool = False  # employer, title and dates confirmed


class Education(_Model):
    id: str = Field(default_factory=short_id)
    institution: str = Field(default="", max_length=300)
    degree: str | None = Field(default=None, max_length=200)
    field: str | None = Field(default=None, max_length=200)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    verified: bool = False


class Certification(_Model):
    id: str = Field(default_factory=short_id)
    name: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1900, le=2100)
    verified: bool = False


class CandidateProfile(_Model):
    headline: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=4000)
    summary_verified: bool = False
    employment: list[Role] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    # Only used for eligibility when supplied and verified (PRD §5).
    work_authorization: str | None = Field(default=None, max_length=300)
    work_authorization_verified: bool = False
    # Resume version this profile was extracted from.
    source_version_id: str | None = None


WorkArrangement = Literal["remote", "hybrid", "onsite"]


class CandidatePreferences(_Model):
    """Operator-supplied preferences (``candidates.preferences_json``)."""

    work_arrangements: list[WorkArrangement] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    willing_to_relocate: bool | None = None
    employment_types: list[str] = Field(default_factory=list)
    availability: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


def profile_from_json(value: dict | None) -> CandidateProfile:
    return CandidateProfile.model_validate(value or {})


def preferences_from_json(value: dict | None) -> CandidatePreferences:
    return CandidatePreferences.model_validate(value or {})
