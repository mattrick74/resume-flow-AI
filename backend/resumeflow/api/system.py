"""Desktop integration: open files/folders in Explorer, PDF converter status."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from resumeflow.api.deps import Config, DbSession
from resumeflow.errors import AppError
from resumeflow.services.files import ensure_within
from resumeflow.services.pdf import converter_status
from resumeflow.services.settings import load_settings

router = APIRouter(prefix="/system", tags=["system"])


class OpenRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    reveal: bool = False  # select the file in Explorer instead of opening it


@router.post("/open", status_code=204)
def open_path(body: OpenRequest, db: DbSession, config: Config) -> Response:
    """Opens a file or folder, but only inside the output root or the app data folder."""
    target = Path(body.path)
    allowed_roots = [Path(load_settings(db).output_root), config.data_dir]
    resolved = None
    for root in allowed_roots:
        try:
            resolved = ensure_within(root, target)
            break
        except AppError:
            continue
    if resolved is None:
        raise AppError(403, "path_not_allowed", "Only files inside the output folder or app data can be opened")
    if not resolved.exists():
        raise AppError(404, "path_not_found", "That file or folder no longer exists")
    if sys.platform != "win32":
        raise AppError(501, "unsupported_platform", "Opening files is only supported on Windows")
    if body.reveal and resolved.is_file():
        subprocess.Popen(["explorer", "/select,", str(resolved)])
    else:
        os.startfile(str(resolved))  # type: ignore[attr-defined]
    return Response(status_code=204)


class OpenUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@router.post("/open-url", status_code=204)
def open_url(body: OpenUrlRequest) -> Response:
    """Opens a job posting in the default browser (http/https only; nothing is fetched by the app)."""
    from urllib.parse import urlsplit

    parts = urlsplit(body.url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise AppError(422, "invalid_url", "Only http(s) links can be opened")
    if sys.platform != "win32":
        raise AppError(501, "unsupported_platform", "Opening links is only supported on Windows")
    os.startfile(body.url)  # type: ignore[attr-defined]
    return Response(status_code=204)


@router.get("/pdf-converter")
def pdf_converter(db: DbSession) -> dict:
    return converter_status(load_settings(db))
