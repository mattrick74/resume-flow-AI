from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from resumeflow.config import AppConfig

_REDACTIONS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
]


class RedactingFilter(logging.Filter):
    """Masks bearer tokens, API keys and the session token in log output."""

    def __init__(self, session_token: str) -> None:
        super().__init__()
        self._patterns = [*_REDACTIONS, re.compile(re.escape(session_token))]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self._patterns:
            message = pattern.sub("[REDACTED]", message)
        record.msg, record.args = message, None
        return True


class _SkipHealthChecks(logging.Filter):
    """The shell polls /health every few seconds; keep it out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return '"GET /health ' not in record.getMessage()


def configure_logging(config: AppConfig) -> None:
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = RedactingFilter(config.session_token)

    file_handler = RotatingFileHandler(
        config.logs_dir / "backend.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        handler.addFilter(redactor)

    root = logging.getLogger()
    root.handlers[:] = [file_handler, console_handler]
    root.setLevel(config.log_level)
    logging.getLogger("uvicorn.access").addFilter(_SkipHealthChecks())
