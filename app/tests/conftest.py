"""Prevent the Gateway regression suite from touching the installed Gateway data.

Why this must run BEFORE collection: gateway.app creates its module-level FastAPI
app (and opens its SQLite store) at import time, before autouse fixtures run.
The sandbox covers that import as well as ordinary tmp_path-based tests.

This module is loaded by pytest only. It never changes the Pinokio process,
production defaults, real data, or the n8n-facing Gateway API.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from weakref import WeakSet

import pytest


APP_DIR = Path(__file__).resolve().parents[1]
REAL_DATA_DIR = APP_DIR / ".gateway-data"
REAL_SETTINGS_PATH = APP_DIR / ".gateway-settings.json"
_SANDBOX: tempfile.TemporaryDirectory[str] | None = None
_ORIGINAL_ENV_VALUE: str | None = None
_ORIGINAL_ENV_PRESENT = False
_ORIGINAL_CONFIG_DATA: Path | None = None
_ORIGINAL_CONFIG_SETTINGS: Path | None = None
_TEST_STORES: WeakSet = WeakSet()


def _is_beneath(target: Path, root: Path) -> bool:
    """Resolve symlinks before checking for a path within the live store."""
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _forbid_live_path(path: str | os.PathLike[str], label: str) -> None:
    """Fail BEFORE constructors or settings saves create live files."""
    candidate = Path(path)
    if _is_beneath(candidate, REAL_DATA_DIR) or candidate.resolve(strict=False) == REAL_SETTINGS_PATH.resolve(strict=False):
        raise RuntimeError(f"pytest storage isolation: refusing {label} in live Gateway storage")


def _verify_basetemp(config: pytest.Config) -> None:
    candidate = config.getoption("basetemp")
    if candidate is not None and (
        _is_beneath(Path(candidate), REAL_DATA_DIR)
        or Path(candidate).resolve(strict=False) == REAL_SETTINGS_PATH.resolve(strict=False)
    ):
        # pytest clears --basetemp itself; refuse to place it under real data.
        raise pytest.UsageError("pytest --basetemp must not point into .gateway-data or at the settings file")


def pytest_configure(config: pytest.Config) -> None:
    # Validate before pytest's tmp_path factory can clear a requested basetemp.
    _verify_basetemp(config)


def pytest_sessionstart(session: pytest.Session) -> None:
    global _SANDBOX, _ORIGINAL_ENV_VALUE, _ORIGINAL_ENV_PRESENT
    global _ORIGINAL_CONFIG_DATA, _ORIGINAL_CONFIG_SETTINGS
    _verify_basetemp(session.config)
    if "gateway.app" in sys.modules:
        raise pytest.UsageError("gateway.app was imported before the pytest storage sandbox")

    # TemporaryDirectory lives outside the app tree and is removed at session end.
    # Never use app/.gateway-data even when the command runs from app/.
    _SANDBOX = tempfile.TemporaryDirectory(prefix="local-ai-gateway-tests-", ignore_cleanup_errors=True)
    base = Path(_SANDBOX.name)
    isolated_data = base / "data"
    isolated_settings = base / "settings.json"

    _ORIGINAL_ENV_PRESENT = "AI_GATEWAY_DATA_DIR" in os.environ
    _ORIGINAL_ENV_VALUE = os.environ.get("AI_GATEWAY_DATA_DIR")
    os.environ["AI_GATEWAY_DATA_DIR"] = str(isolated_data)

    # Importing gateway.config is side-effect-free; importing gateway.app is NOT.
    import gateway.config as gateway_config
    _ORIGINAL_CONFIG_DATA = gateway_config.DATA_DIR
    _ORIGINAL_CONFIG_SETTINGS = gateway_config.SETTINGS_PATH
    gateway_config.DATA_DIR = isolated_data
    gateway_config.SETTINGS_PATH = isolated_settings
    session.config._gateway_test_storage = base


def pytest_collection_finish(session: pytest.Session) -> None:
    """Let ordinary test fixtures override DATA_DIR without an env precedence leak.

    gateway.app was imported during collection using the sandbox env value.
    Clearing it here allows existing tests' monkeypatched root.DATA_DIR to take
    effect independently, instead of all tests accidentally sharing one DB.
    """
    os.environ.pop("AI_GATEWAY_DATA_DIR", None)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _SANDBOX
    try:
        # Close any stores the existing tests did not close explicitly. This is
        # especially important for Windows, where open SQLite/WAL files cannot
        # be removed by TemporaryDirectory.cleanup().
        for store in list(_TEST_STORES):
            store.close()
        _TEST_STORES.clear()
        # Release the SQLite handle created by gateway.app at collection time.
        # Other test-scoped stores should have been closed by their tests.
        mod = sys.modules.get("gateway.app")
        if mod is not None:
            app = getattr(mod, "app", None)
            store = getattr(getattr(app, "state", None), "job_store", None)
            if store is not None:
                store.close()
    finally:
        if _SANDBOX is not None:
            _SANDBOX.cleanup()
            _SANDBOX = None
        if _ORIGINAL_ENV_PRESENT:
            os.environ["AI_GATEWAY_DATA_DIR"] = _ORIGINAL_ENV_VALUE or ""
        else:
            os.environ.pop("AI_GATEWAY_DATA_DIR", None)
        cfg = sys.modules.get("gateway.config")
        if cfg is not None:
            if _ORIGINAL_CONFIG_DATA is not None:
                cfg.DATA_DIR = _ORIGINAL_CONFIG_DATA
            if _ORIGINAL_CONFIG_SETTINGS is not None:
                cfg.SETTINGS_PATH = _ORIGINAL_CONFIG_SETTINGS


@pytest.fixture(autouse=True)
def _reject_live_gateway_storage(monkeypatch: pytest.MonkeyPatch):
    """Catch accidental explicitly constructed real JobStore/ArtifactStore/saves."""
    from gateway.artifact_store import ArtifactStore
    from gateway.job_store import JobStore
    from gateway.settings_manager import SettingsManager

    job_init = JobStore.__init__
    artifact_init = ArtifactStore.__init__
    settings_write = SettingsManager._write

    def job_guard(self, path, *args, **kwargs):
        _forbid_live_path(path, "JobStore")
        result = job_init(self, path, *args, **kwargs)
        _TEST_STORES.add(self)
        return result

    def artifact_guard(self, root, *args, **kwargs):
        _forbid_live_path(root, "ArtifactStore")
        return artifact_init(self, root, *args, **kwargs)

    def settings_guard(self, data):
        _forbid_live_path(self.path, "SettingsManager write")
        return settings_write(self, data)

    monkeypatch.setattr(JobStore, "__init__", job_guard)
    monkeypatch.setattr(ArtifactStore, "__init__", artifact_guard)
    monkeypatch.setattr(SettingsManager, "_write", settings_guard)
