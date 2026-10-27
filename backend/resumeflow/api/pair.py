from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from resumeflow.errors import AppError
from resumeflow.pairing import redeem_pairing_code

router = APIRouter(tags=["pairing"])


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/pair")
def pair(body: PairRequest, request: Request) -> dict[str, str]:
    """Unauthenticated: exchanges the 6-digit code shown by ``resumeflow-backend pair`` for the API token (v2.0)."""
    config = request.app.state.config
    if not redeem_pairing_code(config.data_dir, body.code):
        raise AppError(401, "pairing_failed", "That pairing code is wrong or has expired. Run the Pair command again for a new code.")
    return {"token": config.session_token}
