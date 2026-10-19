"""API credentials live in Windows Credential Manager (via keyring), never in SQLite."""

from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError

SERVICE_NAME = "ResumeFlowAI"
KNOWN_SECRETS = frozenset({"openai_api_key"})


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringSecretStore:
    def get(self, name: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(SERVICE_NAME, name, value)

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except PasswordDeleteError:
            pass


class MemorySecretStore:
    """For tests and environments without a credential vault."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)
