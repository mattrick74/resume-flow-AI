from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.config import default_output_root
from resumeflow.db.models import ResumeTemplate, Setting
from resumeflow.domain.templates import DEFAULT_TEMPLATE, is_template_key
from resumeflow.errors import AppError, safe_validation_details

ThemePreference = Literal["system", "light", "dark"]
PdfConverter = Literal["auto", "libreoffice", "word", "none"]
# review: wait for Approve. auto: export right after generating unless the checks found an error. always: export regardless.
ExportMode = Literal["review", "auto", "always"]
# pdf: the Word file is only an intermediate step (kept if no PDF can be made). pdf_docx: keep both.
OutputFormats = Literal["pdf", "pdf_docx"]
# off (default): background generation runs straight through with what the profile already has. ask: it pauses a job until
# the top candidate's missing job skills are answered.
GapConfirmation = Literal["ask", "off"]
# Bullets per role on resumes: concise 2-4 / 1-3, standard 3-6 / 2-4, detailed 5-8 / 3-5 (recent / older roles).
BulletDensity = Literal["concise", "standard", "detailed"]


def _require_absolute(value: str) -> str:
    if not PureWindowsPath(value).is_absolute():
        raise ValueError("Output root must be an absolute path such as D:\\Applications")
    return value


class MatchWeights(BaseModel):
    """PRD §7 initial weights. Heuristics, not hiring probabilities; must total 100."""

    model_config = ConfigDict(extra="forbid")

    mandatory_skills: float = Field(default=35, ge=0, le=100)
    experience: float = Field(default=25, ge=0, le=100)
    seniority: float = Field(default=15, ge=0, le=100)
    preferred_skills: float = Field(default=10, ge=0, le=100)
    industry: float = Field(default=10, ge=0, le=100)
    other: float = Field(default=5, ge=0, le=100)

    @model_validator(mode="after")
    def _total_100(self) -> MatchWeights:
        total = sum(self.model_dump().values())
        if abs(total - 100) > 0.01:
            raise ValueError(f"Match weights must add up to 100 (currently {total:g})")
        return self


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0, description="USD per 1M input tokens")
    output: float = Field(ge=0, description="USD per 1M output tokens")


class AppSettings(BaseModel):
    """Operator preferences. Secret values are deliberately not representable here."""

    model_config = ConfigDict(extra="forbid")

    output_root: str = Field(default_factory=lambda: str(default_output_root()))
    daily_goal: int = Field(default=100, ge=1, le=1000)
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    ai_provider: Literal["openai"] = "openai"
    analysis_model: str = Field(default="gpt-6-luna", min_length=1, max_length=100)
    writing_model: str = Field(default="gpt-6.1-sol", min_length=1, max_length=100)
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    daily_budget_usd: float | None = Field(default=None, ge=0)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    ai_extract_on_import: bool = True
    semantic_matching: bool = True
    match_weights: MatchWeights = Field(default_factory=MatchWeights)
    resume_template: str = Field(default=DEFAULT_TEMPLATE, max_length=60, description="A built-in template name or custom:<id>")
    pdf_converter: PdfConverter = "auto"
    export_mode: ExportMode = "auto"
    output_formats: OutputFormats = "pdf"
    # A job posting URL already in history (saved or generated before) is skipped instead of saved or generated again.
    skip_known_urls: bool = True
    # Off: every fact in a candidate's profile is used as entered (rejected skills excepted); no ticks or review step.
    require_verification: bool = False
    gap_confirmation: GapConfirmation = "off"
    # Bullets drafted from the operator's own description of the work are added at once (no approval step); an AI draft that
    # says more than the description is replaced by the description's own wording first.
    auto_approve_bullets: bool = True
    # Off (default): work authorization, clearance, licenses, location and arrangement don't block or rank candidates;
    # every ready candidate is ranked by score. On: they are pass/fail gates that scores can't offset.
    hard_requirement_gates: bool = False
    bullet_density: BulletDensity = "standard"
    writing_instructions: str = Field(default="", max_length=2000, description="Operator notes the AI writer follows for every application")
    libreoffice_path: str | None = Field(default=None, max_length=400)
    auto_fetch_job_urls: bool = False
    backup_retention_count: int = Field(default=14, ge=1, le=365)
    theme: ThemePreference = "system"

    _check_output_root = field_validator("output_root")(_require_absolute)

    @field_validator("resume_template")
    @classmethod
    def _known_template(cls, value: str) -> str:
        if not is_template_key(value):
            raise ValueError("Unknown document template")
        return value


class AppSettingsUpdate(BaseModel):
    """PATCH body: any subset of AppSettings. Unknown keys (e.g. secrets) are rejected."""

    model_config = ConfigDict(extra="forbid")

    output_root: str | None = None
    daily_goal: int | None = None
    worker_concurrency: int | None = None
    ai_provider: Literal["openai"] | None = None
    analysis_model: str | None = None
    writing_model: str | None = None
    model_prices: dict[str, ModelPrice] | None = None
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    ai_extract_on_import: bool | None = None
    semantic_matching: bool | None = None
    match_weights: MatchWeights | None = None
    resume_template: str | None = None
    pdf_converter: PdfConverter | None = None
    export_mode: ExportMode | None = None
    output_formats: OutputFormats | None = None
    skip_known_urls: bool | None = None
    require_verification: bool | None = None
    gap_confirmation: GapConfirmation | None = None
    auto_approve_bullets: bool | None = None
    hard_requirement_gates: bool | None = None
    bullet_density: BulletDensity | None = None
    writing_instructions: str | None = None
    libreoffice_path: str | None = None
    auto_fetch_job_urls: bool | None = None
    backup_retention_count: int | None = None
    theme: ThemePreference | None = None


def load_settings(db: Session) -> AppSettings:
    stored = {row.key: row.value_json for row in db.scalars(select(Setting))}
    known = {key: value for key, value in stored.items() if key in AppSettings.model_fields}
    return AppSettings.model_validate(known)


def update_settings(db: Session, patch: AppSettingsUpdate) -> AppSettings:
    changes = patch.model_dump(exclude_unset=True)
    try:
        updated = AppSettings.model_validate({**load_settings(db).model_dump(), **changes})
    except ValidationError as exc:
        raise AppError(422, "invalid_settings", "Settings are invalid", safe_validation_details(exc.errors()))
    template = changes.get("resume_template")
    if template and template.startswith("custom:") and db.get(ResumeTemplate, template.removeprefix("custom:")) is None:
        raise AppError(422, "template_not_found", "That template no longer exists.")

    serialized = updated.model_dump(mode="json")
    for key in changes:
        db.merge(Setting(key=key, value_json=serialized[key]))
    db.commit()
    return updated
