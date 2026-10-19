"""Safe filesystem helpers (PRD §9, §13): sanitized names, atomic no-overwrite
writes, and path containment checks."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from resumeflow.errors import AppError

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
# Windows MAX_PATH is 260; keep generous headroom for the temp-file suffix.
MAX_PATH_LENGTH = 240

_create_lock = threading.Lock()


def sanitize_component(value: str | None, *, max_length: int = 60, fallback: str = "Untitled") -> str:
    """Turns arbitrary text into a safe single path component like ``Joseph_Hein``."""
    cleaned = _INVALID_CHARS.sub(" ", value or "")
    cleaned = re.sub(r"[\s_]+", "_", cleaned.strip())
    cleaned = cleaned.strip("._ ")[:max_length].rstrip("._ ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_within(base: Path, candidate: Path) -> Path:
    """Resolves ``candidate`` and rejects anything outside ``base`` (path traversal)."""
    base_resolved = base.resolve()
    resolved = candidate.resolve()
    if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
        raise AppError(400, "unsafe_path", "Path is outside the allowed folder")
    return resolved


def _check_length(path: Path) -> None:
    if len(str(path)) > MAX_PATH_LENGTH:
        raise AppError(
            422,
            "path_too_long",
            f"The output path is too long for Windows ({len(str(path))} characters). Choose a shorter output folder in Settings.",
        )


def _write_temp(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return temp


def write_new_file(path: Path, data: bytes) -> Path:
    """Atomically creates ``path``; raises FileExistsError instead of overwriting."""
    _check_length(path)
    temp = _write_temp(path, data)
    try:
        with _create_lock:
            if path.exists():
                raise FileExistsError(path)
            os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return path


def replace_file(path: Path, data: bytes) -> Path:
    """Atomically writes ``path``, replacing a previous version written by us."""
    _check_length(path)
    temp = _write_temp(path, data)
    os.replace(temp, path)
    return path


def unique_path(path: Path) -> Path:
    """``name.ext`` if free, else ``name_v2.ext``, ``name_v3.ext``, ... (never overwrite)."""
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_v{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def write_unique_file(path: Path, data: bytes) -> Path:
    """Writes to ``path`` or the next free ``_vN`` name, retrying on races."""
    for _ in range(20):
        target = unique_path(path)
        try:
            return write_new_file(target, data)
        except FileExistsError:
            continue
    raise AppError(409, "file_collision", f"Could not find a free file name for {path.name}")
