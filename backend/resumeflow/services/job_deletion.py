"""Destructive job-history cleanup with database and filesystem safety checks."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from resumeflow.db.models import (
    Application,
    CandidateRoleExperience,
    CandidateSkillEvidence,
    Document,
    GenerationRun,
    Job,
    ProcessingJob,
)
from resumeflow.domain.enums import PipelineStatus, ProcessingStatus
from resumeflow.errors import AppError
from resumeflow.services.settings import load_settings

logger = logging.getLogger(__name__)

_ACTIVE = (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING)


def _safe_application_folder(
    output_root: Path,
    raw_path: str,
    application_date: date,
    sequence_number: int,
) -> Path | None:
    """Accept current folders, plus recognizable legacy folders under the configured root."""
    original = Path(raw_path)
    try:
        is_link = original.is_symlink() or (hasattr(original, "is_junction") and original.is_junction())
        folder = original.resolve()
    except (OSError, RuntimeError):
        return None
    if is_link:
        return None
    # Reject a junction or symlink at either generated directory layer.
    parent = original.parent
    try:
        parent_is_link = parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())
    except OSError:
        return None
    if parent_is_link:
        return None
    # Current layout. Its date + sequence makes it safe even when the output
    # root setting has changed since these files were exported.
    if folder.parent.name == application_date.isoformat() and folder.name.startswith(f"{sequence_number:03d} "):
        return folder

    # Legacy layouts are accepted only under today's configured output root:
    #   root/YYYY-MM-DD/Candidate/site/job
    #   root/instant resumes/MM-DD-YYYY/title
    root = output_root.resolve()
    if folder == root or not folder.is_relative_to(root):
        return None
    parts = folder.relative_to(root).parts
    legacy_url = len(parts) >= 4 and parts[0] == application_date.isoformat()
    legacy_instant = (
        len(parts) >= 3
        and parts[0].casefold() == "instant resumes"
        and parts[1] == application_date.strftime("%m-%d-%Y")
    )
    return folder if legacy_url or legacy_instant else None


def _safe_generated_file(
    output_root: Path,
    raw_path: str,
    raw_folder: str | None,
    application_date: date,
    sequence_number: int,
) -> tuple[Path, Path] | None:
    """Resolves one recorded output file without trusting its stored path."""
    original = Path(raw_path)
    try:
        is_link = original.is_symlink() or (hasattr(original, "is_junction") and original.is_junction())
        path = original.resolve()
    except (OSError, RuntimeError):
        return None
    if is_link:
        return None
    folder = _safe_application_folder(
        output_root,
        raw_folder or str(original.parent),
        application_date,
        sequence_number,
    )
    if folder is None or (path != folder and not path.is_relative_to(folder)):
        return None
    return path, folder


def purge_all_jobs(db: Session) -> dict:
    """Remove every job and application, then their generated output folders.

    Candidate profiles and their imported source resumes are intentionally kept.
    Database changes commit before filesystem cleanup, so a filesystem error
    cannot leave application rows pointing at files that were already removed.
    """
    active = db.scalar(
        select(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.status.in_(_ACTIVE),
            or_(ProcessingJob.job_id.is_not(None), ProcessingJob.application_id.is_not(None)),
        )
    )
    analyzing = db.scalar(select(func.count()).select_from(Job).where(Job.status == PipelineStatus.ANALYZING))
    if active or analyzing:
        raise AppError(409, "jobs_are_processing", "Wait for all background work to finish before deleting all jobs.")

    job_count = db.scalar(select(func.count()).select_from(Job)) or 0
    application_count = db.scalar(select(func.count()).select_from(Application)) or 0
    document_count = db.scalar(select(func.count()).select_from(Document)) or 0
    run_count = db.scalar(
        select(func.count())
        .select_from(GenerationRun)
        .where(or_(GenerationRun.job_id.is_not(None), GenerationRun.application_id.is_not(None)))
    ) or 0

    output_root = Path(load_settings(db).output_root).resolve()
    safe_folders: set[Path] = set()
    skipped_folders: list[str] = []
    application_rows = list(
        db.execute(
            select(Application.id, Application.output_directory, Application.application_date, Application.sequence_number)
        )
    )
    for _app_id, raw_path, application_date, sequence_number in application_rows:
        if raw_path is None:
            continue
        folder = _safe_application_folder(output_root, raw_path, application_date, sequence_number)
        if folder is None:
            skipped_folders.append(raw_path)
        else:
            safe_folders.add(folder)

    safe_files: set[Path] = set()
    skipped_files: list[str] = []
    file_rows = db.execute(
        select(
            Document.file_path,
            Application.output_directory,
            Application.application_date,
            Application.sequence_number,
        )
        .join(Application, Application.id == Document.application_id)
        .where(Document.file_path.is_not(None))
    )
    for raw_path, raw_folder, application_date, sequence_number in file_rows:
        result = _safe_generated_file(output_root, raw_path, raw_folder, application_date, sequence_number)
        if result is None:
            skipped_files.append(raw_path)
            continue
        path, folder = result
        safe_files.add(path)
        safe_folders.add(folder)

    if run_count:
        db.execute(
            delete(GenerationRun).where(
                or_(GenerationRun.job_id.is_not(None), GenerationRun.application_id.is_not(None))
            )
        )
    # Keep candidate-confirmed skills and bullets, but remove references to job
    # records that are about to disappear.
    db.execute(update(CandidateSkillEvidence).values(source_job_ids_json=[]))
    db.execute(update(CandidateRoleExperience).values(source_job_ids_json=[]))
    db.execute(delete(Application))
    db.execute(delete(Job))
    db.commit()

    files_deleted = 0
    for path in sorted(safe_files):
        if not path.exists():
            continue
        try:
            if not path.is_file():
                skipped_files.append(str(path))
                continue
            path.unlink()
            files_deleted += 1
        except OSError:
            logger.exception("Could not remove generated application file %s", path)
            skipped_files.append(str(path))

    # Only remove directories once every tracked file is gone and the folder is
    # otherwise empty. User-added files in an application folder are preserved.
    folders_deleted = 0
    for folder in sorted(safe_folders, key=lambda path: len(path.parts), reverse=True):
        if not folder.exists():
            continue
        try:
            folder.rmdir()
            folders_deleted += 1
        except OSError:
            skipped_folders.append(str(folder))
    for folder in sorted(safe_folders, key=lambda path: len(path.parts), reverse=True):
        parent = folder.parent
        # For current-root layouts, remove every now-empty legacy shell. For a
        # current-layout folder under an old root, remove only its date folder.
        while parent != output_root and parent.is_relative_to(output_root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        if not folder.is_relative_to(output_root):
            try:
                folder.parent.rmdir()
            except OSError:
                pass

    return {
        "jobs": job_count,
        "applications": application_count,
        "documents": document_count,
        "generation_runs": run_count,
        "files_deleted": files_deleted,
        "files_not_deleted": sorted(set(skipped_files)),
        "folders_deleted": folders_deleted,
        "folders_not_deleted": sorted(set(skipped_folders)),
    }
