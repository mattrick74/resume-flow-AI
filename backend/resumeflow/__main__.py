"""Backend entry point.

- Chrome extension (v2.0): ``resumeflow-backend`` runs on its own on 127.0.0.1:8765 with a saved token;
  ``resumeflow-backend pair`` shows a pairing code for the extension; ``show-token`` prints the token.
- Desktop shell: ``python -m resumeflow --port 8765 --parent-pid 1234`` with RESUMEFLOW_SESSION_TOKEN in the
  environment (never on the command line, where other processes can read it).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

import uvicorn

from resumeflow.app import create_app
from resumeflow.config import AppConfig
from resumeflow.logging_setup import configure_logging
from resumeflow.pairing import CODE_MINUTES, load_or_create_token, new_pairing_code

logger = logging.getLogger("resumeflow")


def _wait_for_parent_exit(pid: int) -> None:
    if sys.platform == "win32":
        import ctypes

        synchronize, infinite = 0x00100000, 0xFFFFFFFF
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return  # already gone
        kernel32.WaitForSingleObject(handle, infinite)
        kernel32.CloseHandle(handle)
    else:
        import time

        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(1)


def _watch_parent(pid: int, server: uvicorn.Server) -> None:
    def run() -> None:
        _wait_for_parent_exit(pid)
        logger.warning("Desktop shell (pid %s) exited; shutting down sidecar", pid)
        server.should_exit = True

    threading.Thread(target=run, name="parent-watch", daemon=True).start()


def _already_running(host: str, port: int) -> bool:
    """A ResumeFlow backend already answers on this port (single instance when run on its own)."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2) as response:
            return response.status == 200 and b'"version"' in response.read()
    except OSError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="resumeflow-backend")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "pair", "show-token"],
        help="serve (default); pair: show a 6-digit code for the Chrome extension; show-token: print the API token",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--parent-pid", type=int, help="Exit when this process exits (desktop shell)")
    args = parser.parse_args(argv)

    overrides = {key: value for key, value in (("host", args.host), ("port", args.port)) if value is not None}
    config = AppConfig(**overrides)
    config.ensure_directories()
    # v2.0: run on its own (no desktop shell passing a per-session token), the backend keeps one saved token so the
    # paired Chrome extension stays connected across restarts.
    standalone = not os.environ.get("RESUMEFLOW_SESSION_TOKEN")
    if standalone:
        config = config.model_copy(update={"session_token": load_or_create_token(config.data_dir)})

    if args.command == "pair":
        code = new_pairing_code(config.data_dir)
        print(f"\n  ResumeFlow pairing code:  {code}\n\n  Enter it in the extension (Options > Pair) within {CODE_MINUTES} minutes.\n")
        return
    if args.command == "show-token":
        print(config.session_token)
        return

    configure_logging(config)
    if standalone and not args.parent_pid and _already_running(config.host, config.port):
        logger.info("ResumeFlow backend is already running on %s:%s", config.host, config.port)
        print(f"ResumeFlow backend is already running on http://{config.host}:{config.port}")
        return

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host=config.host, port=config.port, log_config=None)
    )
    if args.parent_pid:
        _watch_parent(args.parent_pid, server)
    logger.info("Starting backend on %s:%s (data dir %s)", config.host, config.port, config.data_dir)
    server.run()


if __name__ == "__main__":
    main()
