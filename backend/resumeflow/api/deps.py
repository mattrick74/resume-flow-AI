from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from resumeflow.config import AppConfig
from resumeflow.db.session import get_db
from resumeflow.services.ai.service import AIService

DbSession = Annotated[Session, Depends(get_db)]


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_ai(request: Request) -> AIService:
    return request.app.state.ai


Config = Annotated[AppConfig, Depends(get_config)]
AI = Annotated[AIService, Depends(get_ai)]
