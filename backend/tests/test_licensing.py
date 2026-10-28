"""v2.0 license keys: the API works only with a valid key from the keygen; keys expire, can be tied to one PC, can't be
forged or edited, and setting the clock back doesn't revive them."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from resumeflow import license_key, licensing
from resumeflow.app import create_app
from resumeflow.config import AppConfig
from resumeflow.licensing import LicenseManager, issue_key, public_key_text
from resumeflow.services.secrets import MemorySecretStore
from tests.conftest import TEST_TOKEN

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def signing_key(monkeypatch) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(license_key, "PUBLIC_KEY", public_key_text(key))
    return key


def licensed_app(data_dir: Path) -> TestClient:
    config = AppConfig(data_dir=data_dir, session_token=TEST_TOKEN, workers_enabled=False, auto_backup=False, license_required=True)
    client = TestClient(create_app(config, secret_store=MemorySecretStore()), base_url="http://127.0.0.1")
    client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
    return client


@pytest.fixture
def licensed(tmp_path, signing_key):
    with licensed_app(tmp_path / "data") as client:
        yield client


def test_without_a_key_the_api_is_locked_but_the_license_screen_works(licensed):
    response = licensed.get("/settings")
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "license_required" and len(error["details"]["machine_code"]) == 19
    assert licensed.get("/health").status_code == 200  # the front ends can still see the engine is running
    status = licensed.get("/license").json()
    assert status["valid"] is False and status["required"] is True and status["machine_code"] == error["details"]["machine_code"]
    assert TestClient(licensed.app, base_url="http://127.0.0.1").get("/license").status_code == 401  # still needs the token


def test_a_seven_day_key_unlocks_the_api_and_survives_a_restart(tmp_path, licensed, signing_key):
    key = issue_key(signing_key, name="Alex Doe", days=7)
    status = licensed.post("/license", json={"key": f"  {key[:40]}\n{key[40:]}  "}).json()  # pasted with line breaks
    assert status["valid"] is True and status["name"] == "Alex Doe" and 6.9 <= status["days_left"] <= 7
    assert licensed.get("/settings").status_code == 200
    with licensed_app(tmp_path / "data") as again:
        assert again.get("/settings").status_code == 200


def test_a_key_stops_working_after_seven_days(licensed, signing_key, monkeypatch):
    licensed.post("/license", json={"key": issue_key(signing_key, name="Alex", days=7)})
    later = datetime.now(UTC) + timedelta(days=7, minutes=1)
    monkeypatch.setattr(licensing, "_now", lambda: later)
    response = licensed.get("/settings")
    assert response.status_code == 403 and response.json()["error"]["code"] == "license_expired"
    assert licensed.get("/license").json()["code"] == "license_expired"
    # A new key for the next 7 days unlocks it again.
    renewed = licensed.post("/license", json={"key": issue_key(signing_key, name="Alex", days=7, now=later)})
    assert renewed.json()["valid"] is True and licensed.get("/settings").status_code == 200


def test_an_expired_or_foreign_key_is_refused_and_the_current_one_kept(licensed, signing_key):
    good = issue_key(signing_key, name="Alex", days=7)
    licensed.post("/license", json={"key": good})
    expired = issue_key(signing_key, name="Alex", days=7, now=datetime.now(UTC) - timedelta(days=8))
    response = licensed.post("/license", json={"key": expired})
    assert response.status_code == 422 and response.json()["error"]["code"] == "license_expired"
    other_pc = issue_key(signing_key, name="Alex", days=7, machine="AAAA-BBBB-CCCC-DDDD")
    response = licensed.post("/license", json={"key": other_pc})
    assert response.status_code == 422 and response.json()["error"]["code"] == "license_other_machine"
    assert licensed.get("/settings").status_code == 200  # the good key still applies


def test_a_key_for_this_pc_works_only_here(licensed, signing_key):
    machine = licensed.get("/license").json()["machine_code"]
    status = licensed.post("/license", json={"key": issue_key(signing_key, name="Alex", days=7, machine=machine.lower().replace("-", ""))}).json()
    assert status["valid"] is True and status["bound_to_this_pc"] is True


def test_keys_cant_be_edited_or_made_without_the_signing_key(licensed, signing_key):
    key = issue_key(signing_key, name="Alex", days=7)
    body, signature = key[len(licensing.PREFIX) :].split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["exp"] = (datetime.now(UTC) + timedelta(days=3650)).isoformat()
    edited = licensing.PREFIX + base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode() + "." + signature
    forged = issue_key(Ed25519PrivateKey.generate(), name="Alex", days=3650)
    for bad in (edited, forged, "RF1-nonsense", "hello, this is not a key"):
        response = licensed.post("/license", json={"key": bad})
        assert response.status_code == 422 and response.json()["error"]["code"] == "license_invalid", bad
    assert licensed.get("/settings").status_code == 403


def test_setting_the_clock_back_doesnt_revive_an_expired_key(tmp_path, signing_key):
    manager = LicenseManager(tmp_path, required=True)
    start = datetime.now(UTC)
    manager.activate(issue_key(signing_key, name="Alex", days=7, now=start), now=start)
    assert manager.problem(start + timedelta(days=6)) is None  # remembers day 6
    assert manager.problem(start + timedelta(days=8)).code == "license_expired"
    back = start + timedelta(days=3)  # clock set back to inside the key's week
    assert manager.problem(back).code == "license_clock"
    assert LicenseManager(tmp_path, required=True).problem(back).code == "license_clock"  # also after a restart
    assert manager.problem(start + timedelta(days=6, hours=-2)) is None  # small corrections are fine


def test_a_build_without_a_public_key_accepts_nothing(tmp_path, monkeypatch, signing_key):
    monkeypatch.setattr(license_key, "PUBLIC_KEY", "")
    manager = LicenseManager(tmp_path, required=True)
    with pytest.raises(Exception) as caught:
        manager.activate(issue_key(signing_key, name="Alex", days=7))
    assert getattr(caught.value, "code", "") == "license_not_configured"


def test_the_built_executable_always_requires_a_license(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert AppConfig(data_dir=tmp_path, license_required=False).license_required is True


def test_the_keygen_makes_keys_the_backend_accepts(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("keygen", ROOT / "scripts" / "license" / "keygen.py")
    keygen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keygen)
    module = tmp_path / "license_key.py"  # never the real backend/resumeflow/license_key.py
    module.write_text('"""test"""\n\nPUBLIC_KEY = ""\n', encoding="utf-8")
    monkeypatch.setattr(keygen, "PUBLIC_KEY_MODULE", module)
    monkeypatch.setenv("RESUMEFLOW_KEYGEN_DIR", str(tmp_path / "keygen"))

    keygen.main(["init"])
    public_key = keygen.configured_public_key()
    assert len(public_key) == 43 and (tmp_path / "keygen" / "signing_key.pem").exists()
    capsys.readouterr()
    keygen.main(["issue", "--name", "Alex Doe", "--days", "7", "--machine", "4f2a9c1e77b0d3e8", "--quiet"])
    key = capsys.readouterr().out.strip()
    lic = licensing.read_key(key, public_key)
    assert lic.name == "Alex Doe" and lic.machine == "4F2A-9C1E-77B0-D3E8"
    assert timedelta(days=6, hours=23) < lic.expires - datetime.now(UTC) <= timedelta(days=7)
    assert "Alex Doe" in (tmp_path / "keygen" / "issued.csv").read_text(encoding="utf-8")
