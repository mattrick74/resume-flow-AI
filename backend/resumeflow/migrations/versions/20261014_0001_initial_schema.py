"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-14 11:35:14.565166
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('candidates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('preferences_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.Enum('active', 'archived', name='candidatestatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates'))
    )
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_candidates_status'), ['status'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_url', sa.Text(), nullable=False),
    sa.Column('normalized_url', sa.Text(), nullable=False),
    sa.Column('company', sa.String(length=200), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('recruiter', sa.String(length=200), nullable=True),
    sa.Column('rate', sa.String(length=100), nullable=True),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('employment_type', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('raw_jd', sa.Text(), nullable=False),
    sa.Column('jd_hash', sa.String(length=64), nullable=False),
    sa.Column('parsed_requirements_json', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'ANALYZING', 'MATCHED', 'GENERATING', 'VALIDATING', 'AWAITING_REVIEW', 'APPROVED', 'EXPORTED', 'NO_ELIGIBLE_CANDIDATE', 'FAILED', 'SUBMITTED', 'REJECTED', 'INTERVIEW', 'OFFER', name='pipelinestatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_jd_hash'), ['jd_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_normalized_url'), ['normalized_url'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)

    op.create_table('settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value_json', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('applications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('candidate_id', sa.String(length=36), nullable=False),
    sa.Column('application_date', sa.Date(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'ANALYZING', 'MATCHED', 'GENERATING', 'VALIDATING', 'AWAITING_REVIEW', 'APPROVED', 'EXPORTED', 'NO_ELIGIBLE_CANDIDATE', 'FAILED', 'SUBMITTED', 'REJECTED', 'INTERVIEW', 'OFFER', name='pipelinestatus', native_enum=False, length=32), nullable=False),
    sa.Column('selection_method', sa.Enum('automatic', 'manual_override', name='selectionmethod', native_enum=False, length=32), nullable=False),
    sa.Column('override_reason', sa.Text(), nullable=True),
    sa.Column('output_directory', sa.Text(), nullable=True),
    sa.Column('profile_snapshot_json', sa.JSON(), nullable=True),
    sa.Column('exported_at', sa.DateTime(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_applications_candidate_id_candidates'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_applications_job_id_jobs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applications')),
    sa.UniqueConstraint('application_date', 'sequence_number', name=op.f('uq_applications_application_date_sequence_number'))
    )
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_applications_application_date'), ['application_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index('ix_applications_date_status', ['application_date', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_applications_status'), ['status'], unique=False)

    op.create_table('candidate_matches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('candidate_id', sa.String(length=36), nullable=False),
    sa.Column('score', sa.Double(), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('eligibility_status', sa.Enum('eligible', 'ineligible', 'needs_review', name='eligibilitystatus', native_enum=False, length=32), nullable=False),
    sa.Column('matching_details_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_matches_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_candidate_matches_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_matches')),
    sa.UniqueConstraint('job_id', 'candidate_id', name=op.f('uq_candidate_matches_job_id_candidate_id'))
    )
    with op.batch_alter_table('candidate_matches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_candidate_matches_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidate_matches_job_id'), ['job_id'], unique=False)

    op.create_table('resume_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('candidate_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('original_file_path', sa.Text(), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=False),
    sa.Column('mime_type', sa.String(length=100), nullable=False),
    sa.Column('extracted_text', sa.Text(), nullable=True),
    sa.Column('parsed_profile_json', sa.JSON(), nullable=True),
    sa.Column('verification_status', sa.Enum('pending_review', 'partially_verified', 'verified', name='resumereviewstatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_resume_versions_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resume_versions')),
    sa.UniqueConstraint('candidate_id', 'version', name=op.f('uq_resume_versions_candidate_id_version'))
    )
    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resume_versions_candidate_id'), ['candidate_id'], unique=False)

    op.create_table('candidate_skills',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('candidate_id', sa.String(length=36), nullable=False),
    sa.Column('skill_name', sa.String(length=200), nullable=False),
    sa.Column('normalized_name', sa.String(length=200), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=True),
    sa.Column('source_resume_version_id', sa.String(length=36), nullable=True),
    sa.Column('verification_status', sa.Enum('unverified', 'verified', 'rejected', name='verificationstatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_skills_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_resume_version_id'], ['resume_versions.id'], name=op.f('fk_candidate_skills_source_resume_version_id_resume_versions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_skills')),
    sa.UniqueConstraint('candidate_id', 'normalized_name', name=op.f('uq_candidate_skills_candidate_id_normalized_name'))
    )
    with op.batch_alter_table('candidate_skills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_candidate_skills_candidate_id'), ['candidate_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=False),
    sa.Column('document_type', sa.Enum('resume', 'cover_letter', 'job_description', 'job_url', 'metadata', name='documenttype', native_enum=False, length=32), nullable=False),
    sa.Column('format', sa.Enum('structured', 'docx', 'pdf', 'txt', 'json', name='documentformat', native_enum=False, length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('draft', 'approved', 'superseded', name='documentstatus', native_enum=False, length=32), nullable=False),
    sa.Column('content_json', sa.JSON(), nullable=True),
    sa.Column('file_path', sa.Text(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_documents_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents')),
    sa.UniqueConstraint('application_id', 'document_type', 'format', 'version', name=op.f('uq_documents_application_id_document_type_format_version'))
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_application_id'), ['application_id'], unique=False)

    op.create_table('generation_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=True),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('operation', sa.Enum('jd_analysis', 'semantic_match', 'resume_generation', 'cover_letter_generation', 'claim_validation', 'resume_parse', name='generationoperation', native_enum=False, length=32), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('estimated_cost_micros', sa.BigInteger(), nullable=False),
    sa.Column('actual_cost_micros', sa.BigInteger(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('succeeded', 'failed', 'cancelled', name='runstatus', native_enum=False, length=32), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_generation_runs_application_id_applications'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_generation_runs_job_id_jobs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_generation_runs'))
    )
    with op.batch_alter_table('generation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_generation_runs_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_generation_runs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_generation_runs_job_id'), ['job_id'], unique=False)

    op.create_table('processing_jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('application_id', sa.String(length=36), nullable=True),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('job_type', sa.Enum('analyze_job', 'match_job', 'generate_documents', 'validate_documents', 'export_application', 'convert_pdf', name='processingjobtype', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', 'cancelled', name='processingstatus', native_enum=False, length=32), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=False),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('run_after', sa.DateTime(), nullable=False),
    sa.Column('locked_by', sa.String(length=100), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_processing_jobs_application_id_applications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_processing_jobs_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_processing_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_processing_jobs_idempotency_key'))
    )
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_processing_jobs_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_processing_jobs_job_id'), ['job_id'], unique=False)
        batch_op.create_index('ix_processing_jobs_status_run_after', ['status', 'run_after'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_processing_jobs_status_run_after')
        batch_op.drop_index(batch_op.f('ix_processing_jobs_job_id'))
        batch_op.drop_index(batch_op.f('ix_processing_jobs_application_id'))

    op.drop_table('processing_jobs')
    with op.batch_alter_table('generation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generation_runs_job_id'))
        batch_op.drop_index(batch_op.f('ix_generation_runs_created_at'))
        batch_op.drop_index(batch_op.f('ix_generation_runs_application_id'))

    op.drop_table('generation_runs')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_application_id'))

    op.drop_table('documents')
    with op.batch_alter_table('candidate_skills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_candidate_skills_candidate_id'))

    op.drop_table('candidate_skills')
    with op.batch_alter_table('resume_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resume_versions_candidate_id'))

    op.drop_table('resume_versions')
    with op.batch_alter_table('candidate_matches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_candidate_matches_job_id'))
        batch_op.drop_index(batch_op.f('ix_candidate_matches_candidate_id'))

    op.drop_table('candidate_matches')
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_applications_status'))
        batch_op.drop_index(batch_op.f('ix_applications_job_id'))
        batch_op.drop_index('ix_applications_date_status')
        batch_op.drop_index(batch_op.f('ix_applications_candidate_id'))
        batch_op.drop_index(batch_op.f('ix_applications_application_date'))

    op.drop_table('applications')
    op.drop_table('settings')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_jobs_normalized_url'))
        batch_op.drop_index(batch_op.f('ix_jobs_jd_hash'))

    op.drop_table('jobs')
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_candidates_status'))

    op.drop_table('candidates')
