"""9C.4: test collection and storage constructors never touch live Gateway data."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
from PIL import Image

from gateway import app as root
from gateway.artifact_store import ArtifactStore
from gateway.config import Settings
from gateway.job_store import JobStore
from gateway.settings_manager import SettingsManager

from conftest import REAL_DATA_DIR, REAL_SETTINGS_PATH, _verify_basetemp


def test_collection_time_default_app_uses_disposable_database(pytestconfig):
    sandbox = pytestconfig._gateway_test_storage
    path = root.app.state.job_store.path
    assert path.is_relative_to(sandbox)
    assert path == sandbox / "data" / "gateway.sqlite3"
    assert path.is_file()
    assert not path.is_relative_to(REAL_DATA_DIR)


def test_collection_time_settings_path_is_disposable(pytestconfig):
    sandbox = pytestconfig._gateway_test_storage
    assert root.app.state.settings_manager.path == sandbox / "settings.json"
    assert root.app.state.settings_manager.path != REAL_SETTINGS_PATH


def test_environment_collection_override_does_not_mask_test_fixtures(pytestconfig):
    base = pytestconfig._gateway_test_storage
    assert "AI_GATEWAY_DATA_DIR" not in os.environ
    assert root.DATA_DIR == base / "data"


def test_explicit_live_sqlite_open_is_refused_before_creation():
    with pytest.raises(RuntimeError, match="refusing JobStore"):
        JobStore(REAL_DATA_DIR / "gateway.sqlite3")


def test_explicit_live_artifact_store_open_is_refused_before_creation():
    with pytest.raises(RuntimeError, match="refusing ArtifactStore"):
        ArtifactStore(REAL_DATA_DIR / "artifacts")


def test_real_settings_save_is_refused_before_creation():
    manager = SettingsManager(REAL_SETTINGS_PATH, ("gpt-5.6-luna",))
    with pytest.raises(RuntimeError, match="refusing SettingsManager"):
        manager.update({"max_queue": 2})


def test_unrelated_tmp_stores_are_still_allowed_and_disposable(tmp_path):
    db = JobStore(tmp_path / "gateway.sqlite3")
    store = ArtifactStore(tmp_path / "artifacts")
    source = tmp_path / "image.png"
    Image.new("RGB", (32, 32), "red").save(source)
    record = store.create(job_id="isolated", source=source, mime_type="image/png")
    assert Path(record["storage_path"]).is_file()
    assert db.path.is_relative_to(tmp_path)
    db.close()


def test_refuse_symlink_into_production_if_available(tmp_path):
    link = tmp_path / "link"
    try:
        link.symlink_to(REAL_DATA_DIR, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks requires privileges on this system")
    with pytest.raises(RuntimeError, match="refusing ArtifactStore"):
        ArtifactStore(link / "artifacts")
    with pytest.raises(RuntimeError, match="refusing JobStore"):
        JobStore(link / "gateway.sqlite3")


def test_reject_test_basetemp_inside_real_data():
    class FakeConfig:
        def getoption(self, name):
            assert name == "basetemp"
            return str(REAL_DATA_DIR / "pytest-cache")
    with pytest.raises(pytest.UsageError, match="must not point"):
        _verify_basetemp(FakeConfig())


def test_new_default_app_has_isolated_disk_state(pytestconfig):
    new_app = root.create_app(
        settings=Settings(api_token="sandbox-token-long-enough-12345", codex_exe=sys.executable,
                          quota_monitor_enabled=False),
        runner=object(),
    )
    try:
        assert new_app.state.job_store.path.is_relative_to(pytestconfig._gateway_test_storage)
        assert new_app.state.artifact_store.root.is_relative_to(pytestconfig._gateway_test_storage)
    finally:
        new_app.state.job_store.close()
