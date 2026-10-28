from __future__ import annotations

import secrets

from fastapi import Request

from resumeflow.errors import AppError


def require_license(request: Request) -> None:
    """v2.0: the API works only with a valid license key (403 license_* otherwise; /license itself stays open)."""
    request.app.state.license.check()


def require_session_token(request: Request) -> None:
    """Every route except /health requires the per-session bearer token."""
    expected: str = request.app.state.config.session_token
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.encode(), expected.encode()):
        raise AppError(401, "unauthorized", "Missing or invalid session token")
