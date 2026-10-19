"""Structured job requirements (stored in ``jobs.parsed_requirements_json``).

Fields have no defaults so the same model can be used as an OpenAI strict
structured-output schema; the heuristic analyzer fills every field explicitly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Seniority = Literal["intern", "junior", "mid", "senior", "lead", "principal", "executive", "unknown"]
Arrangement = Literal["onsite", "hybrid", "remote", "unknown"]

SENIORITY_ORDER: dict[str, int] = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "principal": 5,
    "executive": 6,
}


class JobRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(description="Job title exactly as written, or null")
    employer: str | None = Field(description="Hiring company name if stated, or null")
    seniority: Seniority
    min_years_experience: float | None = Field(description="Minimum years of experience required, or null")
    mandatory_skills: list[str] = Field(description="Skills/technologies explicitly required")
    preferred_skills: list[str] = Field(description="Skills listed as preferred, nice-to-have or bonus")
    duties: list[str] = Field(description="Main responsibilities, short phrases")
    industry: str | None = Field(description="Industry or domain, or null")
    location: str | None = Field(description="Job location (city, state/country), or null")
    work_arrangement: Arrangement
    work_authorization: str | None = Field(description="Verbatim work authorization / citizenship / sponsorship requirement, or null")
    security_clearance: str | None = Field(description="Required security clearance, or null")
    required_credentials: list[str] = Field(description="Licenses or certifications that are explicitly required")
    employment_type: str | None = Field(description="Full-time, contract, etc., or null")
    other_hard_requirements: list[str] = Field(description="Other explicit pass/fail conditions")


def empty_requirements() -> JobRequirements:
    return JobRequirements(
        title=None,
        employer=None,
        seniority="unknown",
        min_years_experience=None,
        mandatory_skills=[],
        preferred_skills=[],
        duties=[],
        industry=None,
        location=None,
        work_arrangement="unknown",
        work_authorization=None,
        security_clearance=None,
        required_credentials=[],
        employment_type=None,
        other_hard_requirements=[],
    )
