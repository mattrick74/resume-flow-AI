"""Sidecar entry point: ``python -m resumeflow --port 8765 --parent-pid 1234``.

The desktop shell passes RESUMEFLOW_SESSION_TOKEN in the environment (never on
the command line, where other processes can read it).
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="resumeflow-backend")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--parent-pid", type=int, help="Exit when this process exits")
    args = parser.parse_args(argv)

    overrides = {key: value for key, value in (("host", args.host), ("port", args.port)) if value is not None}
    config = AppConfig(**overrides)
    config.ensure_directories()
    configure_logging(config)

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host=config.host, port=config.port, log_config=None)
    )
    if args.parent_pid:
        _watch_parent(args.parent_pid, server)
    logger.info("Starting sidecar on %s:%s (data dir %s)", config.host, config.port, config.data_dir)
    server.run()


if __name__ == "__main__":
    main()
