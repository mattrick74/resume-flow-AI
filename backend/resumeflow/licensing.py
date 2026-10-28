"""License keys (v2.0): the backend serves its API only with a valid, unexpired license key.

Keys are made by the ResumeFlow keygen (scripts/license/keygen.py) with a private signing key that never leaves the
issuer's PC; the backend holds only the public key (license_key.PUBLIC_KEY), so it can check keys but not make them.

A key is ``RF1-<payload>.<signature>`` (base64url): the payload is JSON with the key id, the licensee's name, when it
was issued, when it expires, and optionally the machine code of the one PC it works on. The activated key is kept in
``<data dir>/license.key``. Setting the clock back doesn't extend a key: the latest time the backend has seen is kept in
``license_state.json`` and a clock earlier than that (beyond a small tolerance) is refused.

Source runs may turn the check off for development (RESUMEFLOW_LICENSE_REQUIRED=0); the built executable always checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from resumeflow.errors import AppError

PREFIX = "RF1-"
LICENSE_FILE = "license.key"
STATE_FILE = "license_state.json"
CLOCK_TOLERANCE = timedelta(hours=12)  # clock corrections and time-zone mistakes, not a day of extra use
STATE_WRITE_EVERY = timedelta(minutes=10)


class LicenseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class License:
    id: str
    name: str
    issued: datetime
    expires: datetime
    machine: str | None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------- this PC


def machine_code() -> str:
    """A stable code for this Windows installation (from its MachineGuid), e.g. ``4F2A-9C1E-77B0-D3E8``. The issuer
    puts it in a key to make the key work on this PC only."""
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                raw = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            raw = ""
    raw = raw or f"node-{uuid.getnode():012x}"
    digest = hashlib.sha256(f"resumeflow-machine:{raw}".encode()).hexdigest()[:16].upper()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


def normalize_machine(code: str | None) -> str | None:
    cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if not cleaned:
        return None
    if len(cleaned) != 16:
        raise ValueError("A machine code has 16 letters and digits, like 4F2A-9C1E-77B0-D3E8")
    return "-".join(cleaned[i : i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------- keys


def public_key_text(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return _b64(private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def issue_key(private_key: Ed25519PrivateKey, *, name: str, days: float, machine: str | None = None, now: datetime | None = None) -> str:
    """Signs a key (the keygen's job; the backend never has a private key)."""
    issued = (now or _now()).replace(microsecond=0)
    payload = {
        "v": 1,
        "id": secrets.token_hex(4),
        "name": name.strip()[:120],
        "iat": issued.isoformat(),
        "exp": (issued + timedelta(days=days)).isoformat(),
    }
    if machine := normalize_machine(machine):
        payload["m"] = machine
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{PREFIX}{_b64(body)}.{_b64(private_key.sign(body))}"


def read_key(key: str, public_key: str) -> License:
    """The key's contents when its signature is genuine; it isn't checked against the clock or this PC here."""
    text = "".join((key or "").split())
    if not public_key:
        raise LicenseError("license_not_configured", "This ResumeFlow build has no license key configured. Ask your provider for an updated build.")
    if not text.startswith(PREFIX) or "." not in text:
        raise LicenseError("license_invalid", "That isn't a ResumeFlow license key. Paste the whole key, starting with RF1-.")
    body_text, _, signature_text = text[len(PREFIX) :].partition(".")
    try:
        body, signature = _unb64(body_text), _unb64(signature_text)
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(signature, body)
        payload = json.loads(body)
        return License(
            id=str(payload["id"]),
            name=str(payload.get("name") or ""),
            issued=_parse_time(payload["iat"]),
            expires=_parse_time(payload["exp"]),
            machine=payload.get("m"),
        )
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise LicenseError("license_invalid", "This license key isn't valid. Check that it was copied completely.") from exc


def configured_public_key() -> str:
    from resumeflow import license_key

    return license_key.PUBLIC_KEY


# ---------------------------------------------------------------- the activated license of this backend


class LicenseManager:
    def __init__(self, data_dir: Path, *, required: bool, public_key: str | None = None) -> None:
        self.data_dir = data_dir
        self.required = required
        self.public_key = configured_public_key() if public_key is None else public_key
        self.machine = machine_code()
        self._key: str | None = None
        self._license: License | None = None
        self._error: LicenseError | None = None
        self._last_seen: datetime | None = None
        self._load()

    @property
    def _key_path(self) -> Path:
        return self.data_dir / LICENSE_FILE

    @property
    def _state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    def _load(self) -> None:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_seen = _parse_time(state["last_seen"])
        except (OSError, ValueError, KeyError, TypeError):
            self._last_seen = None
        try:
            self._key = self._key_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            self._key = None
        self._license, self._error = None, None
        if self._key:
            try:
                self._license = read_key(self._key, self.public_key)
            except LicenseError as exc:
                self._error = exc

    def _see(self, now: datetime) -> None:
        """Remembers the latest time seen, so setting the clock back can't make an expired key work again."""
        if self._last_seen is None or now - self._last_seen >= STATE_WRITE_EVERY:
            self._last_seen = max(now, self._last_seen or now)
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                self._state_path.write_text(json.dumps({"last_seen": self._last_seen.isoformat()}), encoding="utf-8")
            except OSError:
                pass

    def _validate(self, lic: License, now: datetime) -> None:
        if lic.machine and lic.machine != self.machine:
            raise LicenseError("license_other_machine", f"This license key is for another PC. This PC's machine code is {self.machine}.")
        latest = max(t for t in (self._last_seen, lic.issued) if t is not None)
        if now + CLOCK_TOLERANCE < latest:
            raise LicenseError("license_clock", "This PC's date and time are earlier than ResumeFlow last saw. Correct the clock, then try again.")
        if now >= lic.expires:
            raise LicenseError("license_expired", f"Your ResumeFlow license expired on {lic.expires.astimezone().strftime('%b %d, %Y %H:%M')}. Enter a new license key.")

    def problem(self, now: datetime | None = None) -> LicenseError | None:
        """Why the API is locked, or None when it may be used."""
        if not self.required:
            return None
        now = now or _now()
        if self._error is not None:
            return self._error
        if self._license is None:
            return LicenseError("license_required", "ResumeFlow needs a license key. Enter the key you were given.")
        try:
            self._validate(self._license, now)
        except LicenseError as exc:
            return exc
        self._see(now)
        return None

    def check(self) -> None:
        if (error := self.problem()) is not None:
            raise AppError(403, error.code, error.message, {"machine_code": self.machine})

    def activate(self, key: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or _now()
        try:
            lic = read_key(key, self.public_key)
            self._validate(lic, now)
        except LicenseError as exc:
            raise AppError(422, exc.code, exc.message, {"machine_code": self.machine}) from exc
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._key_path.write_text("".join(key.split()), encoding="utf-8")
        self._load()
        self._see(now)
        return self.status(now)

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or _now()
        error = self.problem(now)
        lic = self._license
        left = (lic.expires - now).total_seconds() / 86_400 if lic else None
        return {
            "required": self.required,
            "valid": error is None,
            "code": error.code if error else "ok",
            "message": error.message if error else None,
            "name": lic.name if lic else None,
            "key_id": lic.id if lic else None,
            "expires_at": lic.expires.isoformat() if lic else None,
            "days_left": round(max(left, 0), 1) if left is not None else None,
            "bound_to_this_pc": bool(lic and lic.machine),
            "machine_code": self.machine,
        }
