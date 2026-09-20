"""Bounded, read-only operational information for the local dashboard.

Never expose environment variables, executable paths, usernames, hostnames,
credentials, SQLite paths, subprocess stderr, or command-line arguments.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def codex_version(executable: str) -> str | None:
    """Ask the installed CLI for its version without a shell or secret output."""
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            [executable, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=3, creationflags=flags, check=False,
        )
        if result.returncode != 0:
            return None
        value = result.stdout[:128].decode("utf-8", errors="replace").strip()
        # Some tools use `codex-cli 0.XX`, others `codex 0.XX`.
        match = re.fullmatch(r"codex(?:-cli)?\s+v?[0-9][A-Za-z0-9.+-]{0,48}", value, re.IGNORECASE)
        return match.group(0) if match else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def system_snapshot(*, codex_exe: str, db_path: Path | None,
                    started_monotonic: float, gateway_version: str,
                    active: int, queued: int, concurrency: int, max_queue: int) -> dict[str, Any]:
    """Provide safe system-level metrics only. Storage detail lives in retention API."""
    db_bytes = None
    if db_path is not None:
        try:
            path = Path(db_path)
            db_bytes = sum(p.stat().st_size for p in (path, Path(str(path) + "-wal")) if p.is_file())
        except OSError:
            pass
    return {
        "gateway_version": gateway_version,
        "codex_version": codex_version(codex_exe),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.system(),
        "uptime_seconds": max(0, int(time.monotonic() - started_monotonic)),
        "database_bytes": db_bytes,
        "workers": {"active": max(0, active), "queued": max(0, queued),
                    "concurrency": concurrency, "max_queue": max_queue},
    }
