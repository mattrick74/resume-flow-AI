"""The backend's saved token and pairing codes for the Chrome extension (v2.0, BE-02).

Run on its own (not under the desktop shell), the backend keeps one API token in the data folder so the extension
stays connected across restarts. The extension gets it by pairing: ``resumeflow-backend pair`` shows a 6-digit code
for 5 minutes, and the extension sends that code to ``POST /pair`` once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

TOKEN_FILE = "api_token"
PAIRING_FILE = "pairing.json"
CODE_MINUTES = 5
MAX_ATTEMPTS = 5


def load_or_create_token(data_dir: Path) -> str:
    """The saved API token, created (64 hex characters) on first use. The data folder is the user's own."""
    path = data_dir / TOKEN_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def new_pairing_code(data_dir: Path, minutes: int = CODE_MINUTES) -> str:
    """A fresh 6-digit code, valid for ``minutes``; any earlier code stops working. Only its hash is stored."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(8)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / PAIRING_FILE).write_text(
        json.dumps({"salt": salt, "hash": _hash(code, salt), "expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(), "attempts": 0}),
        encoding="utf-8",
    )
    return code


def redeem_pairing_code(data_dir: Path, code: str) -> bool:
    """True once for the current code. A wrong code counts as an attempt; after 5, or once used or expired, the code
    is gone and a new one is needed."""
    path = data_dir / PAIRING_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    if datetime.fromisoformat(state["expires_at"]) < datetime.now(UTC):
        path.unlink(missing_ok=True)
        return False
    if secrets.compare_digest(_hash((code or "").strip(), state["salt"]), state["hash"]):
        path.unlink(missing_ok=True)
        return True
    state["attempts"] = int(state.get("attempts", 0)) + 1
    if state["attempts"] >= MAX_ATTEMPTS:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps(state), encoding="utf-8")
    return False
