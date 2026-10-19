from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from resumeflow.db.session import get_db
from resumeflow.errors import AppError
from resumeflow.services.secrets import KNOWN_SECRETS, SecretStore
from resumeflow.services.settings import AppSettings, AppSettingsUpdate, load_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"])

DbSession = Annotated[Session, Depends(get_db)]


class StoragePaths(BaseModel):
    data_dir: str
    database: str


class SettingsResponse(BaseModel):
    settings: AppSettings
    secrets_configured: dict[str, bool]
    paths: StoragePaths


class SecretValue(BaseModel):
    value: str = Field(min_length=8, max_length=512)

    @field_validator("value")
    @classmethod
    def _no_whitespace(cls, value: str) -> str:
        if any(ch.isspace() for ch in value):
            raise ValueError("Secret must not contain whitespace")
        return value


def _secret_store(request: Request) -> SecretStore:
    return request.app.state.secret_store


def _response(request: Request, settings: AppSettings) -> SettingsResponse:
    store = _secret_store(request)
    config = request.app.state.config
    return SettingsResponse(
        settings=settings,
        secrets_configured={name: bool(store.get(name)) for name in sorted(KNOWN_SECRETS)},
        paths=StoragePaths(data_dir=str(config.data_dir), database=str(config.database_path)),
    )


def _known_secret(name: str) -> str:
    if name not in KNOWN_SECRETS:
        raise AppError(404, "unknown_secret", f"Unknown secret '{name}'")
    return name


@router.get("", response_model=SettingsResponse)
def read_settings(request: Request, db: DbSession) -> SettingsResponse:
    return _response(request, load_settings(db))


@router.patch("", response_model=SettingsResponse)
def patch_settings(patch: AppSettingsUpdate, request: Request, db: DbSession) -> SettingsResponse:
    return _response(request, update_settings(db, patch))


@router.put("/secrets/{name}", status_code=204)
def store_secret(name: str, body: SecretValue, request: Request) -> Response:
    _secret_store(request).set(_known_secret(name), body.value)
    return Response(status_code=204)


@router.delete("/secrets/{name}", status_code=204)
def delete_secret(name: str, request: Request) -> Response:
    _secret_store(request).delete(_known_secret(name))
    return Response(status_code=204)
