"""SQLAlchemy models for PRD section 10.

Additions beyond the PRD table list, each for a stated requirement:
- ``jobs.status`` / ``normalized_url``: pre-match pipeline states and URL duplicate detection.
- ``applications.sequence_number``: the per-day ``001_`` file prefix; unique per date.
- ``applications.profile_snapshot_json``: reproducibility after later profile edits.
- ``documents.content_json`` / ``status``: reviewed structured content and approval state.
- ``processing_jobs.idempotency_key`` / ``run_after`` / ``locked_*``: restart-safe queue with backoff.
- Costs are integer micro-USD to stay exact in SQLite.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from resumeflow.db.types import UTCDateTime, new_id, utcnow
from resumeflow.domain.enums import (
    AdditionStatus,
    CandidateStatus,
    DocumentFormat,
    DocumentStatus,
    DocumentType,
    EligibilityStatus,
    GenerationOperation,
    PipelineStatus,
    ProcessingJobType,
    ProcessingStatus,
    ResumeReviewStatus,
    RunStatus,
    SelectionMethod,
    SkillDecision,
    VerificationStatus,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_column(enum_cls: type[PyEnum]) -> Enum:
    # Stored as plain strings without a CHECK constraint so new states can be
    # added without a SQLite table rebuild; validation happens in Python.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(200))
    preferences_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[CandidateStatus] = mapped_column(
        enum_column(CandidateStatus), default=CandidateStatus.ACTIVE, nullable=False, index=True
    )
    # Curated profile (domain.profile.CandidateProfile). Every fact carries its own
    # verified flag; only verified facts reach matching and generation.
    profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    profile_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # PRD §13: the candidate authorized use of their data and applications.
    consent_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Incremented whenever confirmed experience is approved, edited or removed (v1.8 provenance).
    profile_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # v2.0: free text the user keeps for answering application questions (preferences, availability, salary, the
    # stories behind projects). Read only by the question-answering request; never used in resumes or cover letters.
    answer_notes: Mapped[str | None] = mapped_column(Text)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"
    __table_args__ = (UniqueConstraint("candidate_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    parsed_profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verification_status: Mapped[ResumeReviewStatus] = mapped_column(
        enum_column(ResumeReviewStatus), default=ResumeReviewStatus.PENDING_REVIEW, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class CandidateSkill(TimestampMixin, Base):
    __tablename__ = "candidate_skills"
    __table_args__ = (UniqueConstraint("candidate_id", "normalized_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    source_resume_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="SET NULL")
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus), default=VerificationStatus.UNVERIFIED, nullable=False
    )


class CandidateSkillEvidence(TimestampMixin, Base):
    """The operator's answer about one skill a job asked for (v1.8 CAN-12/14): used, not used or unsure.

    "Used" also creates or confirms the candidate_skills row; "not used" marks it Don't use so it isn't suggested again.
    """

    __tablename__ = "candidate_skill_evidence"
    __table_args__ = (UniqueConstraint("candidate_id", "normalized_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    decision: Mapped[SkillDecision] = mapped_column(enum_column(SkillDecision), nullable=False)
    # Profile role ids where the skill was used; empty = confirmed generally, never a company-specific claim.
    role_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(20))
    years: Mapped[float | None] = mapped_column()
    last_used: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    source_job_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class CandidateRoleExperience(TimestampMixin, Base):
    """Work the operator confirmed for one role, and the bullet written from it (v1.8 CAN-13/15, GEN-04, VAL-06).

    Only ``approved`` records reach the profile snapshot; the imported resume is never changed.
    """

    __tablename__ = "candidate_role_experience"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[str] = mapped_column(String(40), nullable=False)
    # "Employer | Title | Start" at confirmation time, so the record finds its role again after a resume is re-read.
    role_label: Mapped[str] = mapped_column(String(500), nullable=False)
    skills_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)  # what the candidate did, in the operator's words
    project: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str | None] = mapped_column(Text)  # results or scale, only as stated by the operator
    narrative: Mapped[str | None] = mapped_column(Text)  # the resume bullet
    status: Mapped[AdditionStatus] = mapped_column(
        enum_column(AdditionStatus), default=AdditionStatus.SUGGESTED, nullable=False
    )
    origin: Mapped[str] = mapped_column(String(20), default="gap", nullable=False)  # gap | interview | manual
    drafted_by: Mapped[str | None] = mapped_column(String(20))  # ai | offline | operator
    issues_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    source_job_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    recruiter: Mapped[str | None] = mapped_column(String(200))
    rate: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(200))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    # v2.0: where the job came from ("manual", "batch" or "extension") and, for captured jobs, the job site.
    source: Mapped[str | None] = mapped_column(String(20))
    site: Mapped[str | None] = mapped_column(String(100))
    raw_jd: Mapped[str] = mapped_column(Text, nullable=False)
    jd_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parsed_requirements_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    requirements_source: Mapped[str | None] = mapped_column(String(20))  # ai | heuristic | manual | cache
    warnings_json: Mapped[list[Any] | None] = mapped_column(JSON)
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[PipelineStatus] = mapped_column(
        enum_column(PipelineStatus), default=PipelineStatus.DRAFT, nullable=False, index=True
    )
    # Background processing paused for skill confirmation (v1.8 UX-04), and when the operator answered.
    confirmation_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmation_resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CandidateMatch(Base):
    __tablename__ = "candidate_matches"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float | None] = mapped_column()  # verified match score: confirmed evidence only
    # Score if every unanswered skill gap were confirmed (v1.8 MAT-06); never used to choose a candidate.
    potential_score: Mapped[float | None] = mapped_column()
    previous_score: Mapped[float | None] = mapped_column()  # before the latest re-match (MAT-09)
    rank: Mapped[int | None] = mapped_column(Integer)
    eligibility_status: Mapped[EligibilityStatus] = mapped_column(
        enum_column(EligibilityStatus), nullable=False
    )
    matching_details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("application_date", "sequence_number"),
        Index("ix_applications_date_status", "application_date", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    application_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PipelineStatus] = mapped_column(
        enum_column(PipelineStatus), default=PipelineStatus.MATCHED, nullable=False, index=True
    )
    selection_method: Mapped[SelectionMethod] = mapped_column(
        enum_column(SelectionMethod), default=SelectionMethod.AUTOMATIC, nullable=False
    )
    override_reason: Mapped[str | None] = mapped_column(Text)
    output_directory: Mapped[str | None] = mapped_column(Text)
    profile_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    pdf_status: Mapped[str | None] = mapped_column(String(20))  # succeeded | failed | skipped
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # "operator" for legacy/manual API calls or "automatic" for the current pipeline; recorded in the metadata.
    approved_by: Mapped[str | None] = mapped_column(String(20))
    # The operator's own notes for the AI writer for this application (on top of the default in Settings).
    instructions: Mapped[str | None] = mapped_column(Text)
    # Provenance (v1.8 EXP-07): the candidate's profile revision in the snapshot, the approved additions it
    # contains, and the job-skill coverage of the latest resume.
    profile_revision: Mapped[int | None] = mapped_column(Integer)
    addition_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    coverage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    exported_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("application_id", "document_type", "format", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType), nullable=False)
    format: Mapped[DocumentFormat] = mapped_column(enum_column(DocumentFormat), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_column(DocumentStatus), default=DocumentStatus.DRAFT, nullable=False
    )
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    warnings_json: Mapped[list[Any] | None] = mapped_column(JSON)
    origin: Mapped[str | None] = mapped_column(String(20))  # generated | regenerated | edited | exported
    file_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    operation: Mapped[GenerationOperation] = mapped_column(
        enum_column(GenerationOperation), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False, index=True
    )


class ProcessingJob(TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[ProcessingJobType] = mapped_column(enum_column(ProcessingJobType), nullable=False)
    status: Mapped[ProcessingStatus] = mapped_column(
        enum_column(ProcessingStatus), default=ProcessingStatus.QUEUED, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class ProcessingStageEvent(Base):
    """One durable, cursor-addressable completion event for a slow processing stage.

    Events are separate rows rather than a JSON list on ``processing_jobs`` because
    resume and cover-letter writing may finish concurrently.  The integer primary
    key gives the UI a stable ordering and polling cursor.
    """

    __tablename__ = "processing_stage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processing_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False, index=True
    )


class ResumeTemplate(TimestampMixin, Base):
    """A document style copied from an existing resume (domain.templates.TemplateStyle).

    Only the look is stored, never the source resume's text.
    """

    __tablename__ = "resume_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    style_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    source_format: Mapped[str | None] = mapped_column(String(10))
    notes_json: Mapped[list[str] | None] = mapped_column(JSON)


class Setting(Base):
    """Non-secret operator preferences. Secrets live in Windows Credential Manager."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AnswerBankEntry(TimestampMixin, Base):
    """A saved answer to a common application question (v2.0): per candidate, or for everyone when candidate_id is
    null. Used to fill application forms without AI."""

    __tablename__ = "answer_bank"
    __table_args__ = (UniqueConstraint("candidate_id", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, nullable=False)


class ApplicationAnswer(Base):
    """An answer the extension filled in on an application form (v2.0), and where it came from."""

    __tablename__ = "application_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    page_url: Mapped[str | None] = mapped_column(Text)
    field_id: Mapped[str] = mapped_column(String(300), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    field_type: Mapped[str] = mapped_column(String(30), nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # profile | answer_bank | ai | needs_input | file
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
