"""ResumeFlow license keygen: makes the keys that unlock the backend (v2.0). For the issuer only; never ship this or the
signing key.

    backend\\.venv\\Scripts\\python scripts\\license\\keygen.py init
        Once: creates the signing key in %USERPROFILE%\\.resumeflow-keygen and writes its public half into
        backend\\resumeflow\\license_key.py. Rebuild the backend after this. Back up signing_key.pem: without it no new
        keys can be made for builds that carry this public key.

    backend\\.venv\\Scripts\\python scripts\\license\\keygen.py issue --name "Alex Doe" [--days 7] [--machine 4F2A-9C1E-77B0-D3E8]
        A key valid for --days (default 7) from now. With --machine (the code the user sees in ResumeFlow) it works only
        on that PC, so it can't be passed on. Every key issued is listed in issued.csv next to the signing key.

    backend\\.venv\\Scripts\\python scripts\\license\\keygen.py show KEY
        Who a key is for, when it expires and which PC it is for.

The signing key's folder can be changed with RESUMEFLOW_KEYGEN_DIR.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, load_pem_private_key  # noqa: E402

from resumeflow.licensing import LicenseError, issue_key, normalize_machine, public_key_text, read_key  # noqa: E402

PUBLIC_KEY_MODULE = ROOT / "backend" / "resumeflow" / "license_key.py"


def keygen_dir() -> Path:
    return Path(os.environ.get("RESUMEFLOW_KEYGEN_DIR") or Path.home() / ".resumeflow-keygen")


def signing_key_path() -> Path:
    return keygen_dir() / "signing_key.pem"


def load_signing_key() -> Ed25519PrivateKey:
    path = signing_key_path()
    if not path.exists():
        raise SystemExit(f"No signing key at {path}. Run: keygen.py init")
    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} isn't a ResumeFlow signing key.")
    return key


def configured_public_key() -> str:
    match = re.search(r'^PUBLIC_KEY = "([^"]*)"', PUBLIC_KEY_MODULE.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else ""


def write_public_key(public_key: str) -> None:
    text = PUBLIC_KEY_MODULE.read_text(encoding="utf-8")
    PUBLIC_KEY_MODULE.write_text(re.sub(r'^PUBLIC_KEY = "[^"]*"', f'PUBLIC_KEY = "{public_key}"', text, count=1, flags=re.MULTILINE), encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> None:
    path = signing_key_path()
    if path.exists() and not args.force:
        key = load_signing_key()
        if configured_public_key() != public_key_text(key):
            write_public_key(public_key_text(key))
            print(f"The signing key exists ({path}); wrote its public key into {PUBLIC_KEY_MODULE}. Rebuild the backend.")
        else:
            print(f"Already set up: {path}")
        return
    if path.exists():
        print("Replacing the signing key: keys issued before stop working once the backend is rebuilt.")
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    write_public_key(public_key_text(key))
    print(f"Signing key: {path}  (keep it private and back it up)")
    print(f"Public key written into {PUBLIC_KEY_MODULE}. Rebuild the backend (npm run release) so builds check keys.")


def cmd_issue(args: argparse.Namespace) -> None:
    key = load_signing_key()
    if configured_public_key() != public_key_text(key):
        raise SystemExit("The backend's public key doesn't match this signing key. Run: keygen.py init (then rebuild).")
    try:
        machine = normalize_machine(args.machine)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.days <= 0:
        raise SystemExit("--days must be more than 0")
    license_key = issue_key(key, name=args.name, days=args.days, machine=machine)
    lic = read_key(license_key, public_key_text(key))
    if not args.no_log:
        log = keygen_dir() / "issued.csv"
        new = not log.exists()
        with log.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new:
                writer.writerow(["issued_at", "key_id", "name", "expires_at", "machine"])
            writer.writerow([lic.issued.isoformat(), lic.id, lic.name, lic.expires.isoformat(), lic.machine or "any PC"])
    if args.quiet:
        print(license_key)
        return
    where = f"the PC with machine code {lic.machine}" if lic.machine else "any PC (add --machine to tie it to one PC)"
    print(f"\nLicense for {lic.name or 'unnamed'}, valid until {lic.expires.astimezone():%b %d, %Y %H:%M} on {where}:\n")
    print(license_key)
    print()


def cmd_show(args: argparse.Namespace) -> None:
    public_key = configured_public_key()
    try:
        lic = read_key(args.key, public_key)
    except LicenseError as exc:
        raise SystemExit(exc.message) from exc
    state = "expired" if datetime.now(UTC) >= lic.expires else "valid"
    print(f"Key {lic.id} for {lic.name or 'unnamed'}: issued {lic.issued.astimezone():%b %d, %Y %H:%M}, {state} until {lic.expires.astimezone():%b %d, %Y %H:%M}, PC: {lic.machine or 'any'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="keygen.py", description="Make ResumeFlow license keys.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create the signing key (once) and put its public key in the backend")
    init.add_argument("--force", action="store_true", help="replace an existing signing key (old keys stop working after a rebuild)")
    issue = commands.add_parser("issue", help="make a license key")
    issue.add_argument("--name", required=True, help="who the key is for")
    issue.add_argument("--days", type=float, default=7, help="how long it works, from now (default 7)")
    issue.add_argument("--machine", help="the user's machine code: the key works on that PC only")
    issue.add_argument("--quiet", action="store_true", help="print only the key")
    issue.add_argument("--no-log", action="store_true", help="don't add it to issued.csv")
    show = commands.add_parser("show", help="read a key")
    show.add_argument("key")
    args = parser.parse_args(argv)
    {"init": cmd_init, "issue": cmd_issue, "show": cmd_show}[args.command](args)


if __name__ == "__main__":
    main()
