from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/license", tags=["license"])


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=10, max_length=4000)


@router.get("")
def license_status(request: Request) -> dict[str, Any]:
    """The license and this PC's machine code. Needs the API token, but not a license (v2.0)."""
    return request.app.state.license.status()


@router.post("")
def activate_license(body: ActivateRequest, request: Request) -> dict[str, Any]:
    """Checks a key (signature, expiry, this PC, the clock) and keeps it; a bad key leaves the current one in place."""
    return request.app.state.license.activate(body.key)
