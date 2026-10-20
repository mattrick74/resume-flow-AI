from __future__ import annotations

from enum import StrEnum


class PipelineStatus(StrEnum):
    """Status model from PRD section 14.

    DRAFT, ANALYZING and NO_ELIGIBLE_CANDIDATE describe a job before any
    candidate is chosen, so they are stored on ``jobs.status``. An application
    exists once a candidate is chosen and starts at MATCHED.
    """

    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    MATCHED = "MATCHED"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    APPROVED = "APPROVED"
    EXPORTED = "EXPORTED"
    NO_ELIGIBLE_CANDIDATE = "NO_ELIGIBLE_CANDIDATE"
    FAILED = "FAILED"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"


class CandidateStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ResumeReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


class SelectionMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL_OVERRIDE = "manual_override"


class DocumentType(StrEnum):
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    JOB_DESCRIPTION = "job_description"
    JOB_URL = "job_url"
    METADATA = "metadata"


class DocumentFormat(StrEnum):
    STRUCTURED = "structured"  # reviewed content before rendering
    DOCX = "docx"
    PDF = "pdf"
    TXT = "txt"
    JSON = "json"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ProcessingJobType(StrEnum):
    # Full pipeline for one saved job: analyze -> match -> application -> generate.
    PROCESS_JOB = "process_job"
    ANALYZE_JOB = "analyze_job"
    MATCH_JOB = "match_job"
    GENERATE_DOCUMENTS = "generate_documents"
    VALIDATE_DOCUMENTS = "validate_documents"
    EXPORT_APPLICATION = "export_application"
    CONVERT_PDF = "convert_pdf"


class ProcessingStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationOperation(StrEnum):
    JD_ANALYSIS = "jd_analysis"
    SEMANTIC_MATCH = "semantic_match"
    RESUME_GENERATION = "resume_generation"
    COVER_LETTER_GENERATION = "cover_letter_generation"
    CLAIM_VALIDATION = "claim_validation"
    RESUME_PARSE = "resume_parse"
    # Turning the operator's confirmed work context into a resume bullet (v1.8).
    EXPERIENCE_DRAFTING = "experience_drafting"
    # Historical operation value retained for existing GenerationRun analytics. New placement decisions are returned
    # by RESUME_GENERATION instead of using a separate request.
    SKILL_EXPANSION = "skill_expansion"


class SkillDecision(StrEnum):
    """The operator's answer about a job skill missing from a profile (CAN-14)."""

    USED = "used"
    NOT_USED = "not_used"
    UNSURE = "unsure"


class AdditionStatus(StrEnum):
    """A role-level experience record: a drafted bullet waits for approval before any document may use it."""

    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
