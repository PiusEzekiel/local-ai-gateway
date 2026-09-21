"""9A.1: quota Settings API and real-monitor constructor must accept the same range.

All tests are non-destructive and isolated in tmp_path. In particular, real
QuotaMonitor construction is exercised without entering its app-server polling
lifespan, so tests never launch Codex or contact an external service.
"""
from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from gateway import app as root
from gateway.config import (
    MODELS, QUOTA_POLL_MIN_SECONDS, QUOTA_POLL_MAX_SECONDS,
    Settings, validate_quota_poll_seconds,
)
from gateway.settings_manager import FIELD_SPECS, SettingsManager, SettingsValidationError, apply_saved_settings

TOKEN = "quota-contract-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def configure_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(root, "DATA_DIR", tmp_path / "data")


def test_quota_settings_metadata_matches_supported_monitor_range(tmp_path):
    field = FIELD_SPECS["quota_poll_seconds"]
    assert (QUOTA_POLL_MIN_SECONDS, QUOTA_POLL_MAX_SECONDS) == (30, 60)
    assert (field.minimum, field.maximum, field.default) == (30, 60, 45)
    manager = SettingsManager(tmp_path / "settings.json", MODELS)
    metadata = manager.describe(Settings(api_token=TOKEN, codex_exe=sys.executable), MODELS[0])["fields"]["quota_poll_seconds"]
    assert (metadata["minimum"], metadata["maximum"], metadata["value"]) == (30, 60, 45)
    assert metadata["restart_required"] is True


@pytest.mark.parametrize("value", [15, 29, 61, 3600, "45", True])
def test_settings_patch_rejects_unsupported_quota_poll_without_writing(tmp_path, monkeypatch, value):
    configure_paths(monkeypatch, tmp_path)
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    app = root.create_app(settings=settings, runner=object())
    with TestClient(app) as client:
        response = client.patch("/dashboard/api/settings", headers=AUTH, json={"quota_poll_seconds": value})
    assert response.status_code == 422
    assert "30 to 60" in response.text
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("value", [30, 45, 60])
def test_every_accepted_ui_value_constructs_the_real_quota_monitor_on_restart(tmp_path, monkeypatch, value):
    """Do not replace the real QuotaMonitor; this catches constructor drift."""
    configure_paths(monkeypatch, tmp_path)
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    current_app = root.create_app(settings=settings, runner=object())
    with TestClient(current_app) as client:
        response = client.patch("/dashboard/api/settings", headers=AUTH, json={"quota_poll_seconds": value})
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["quota_poll_seconds"]
        assert field["saved"] == value
        assert field["value"] == 45
    # Next startup: use the actual application composition and actual monitor,
    # but DO NOT enter lifespan or start quota polling during a regression test.
    enabled = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=True)
    monkeypatch.setattr(root.Settings, "from_env", classmethod(lambda cls: enabled))
    next_app = root.create_app()
    try:
        assert next_app.state.effective_settings.quota_poll_seconds == value
        assert next_app.state.quota_monitor is not None
        assert isinstance(next_app.state.quota_monitor, root.QuotaMonitor)
    finally:
        if next_app.state.job_store is not None:
            next_app.state.job_store.close()
        if current_app.state.job_store is not None:
            current_app.state.job_store.close()


@pytest.mark.parametrize("legacy_value", [15, 3600])
def test_old_out_of_range_saved_value_is_ignored_at_boot(tmp_path, legacy_value):
    saved_path = tmp_path / "settings.json"
    saved_path.write_text(json.dumps({"quota_poll_seconds": legacy_value, "max_queue": 8}), encoding="utf-8")
    manager = SettingsManager(saved_path, MODELS)
    base = Settings(api_token=TOKEN, codex_exe=sys.executable)
    active = apply_saved_settings(base, manager.load(), MODELS, environ={})
    assert active.quota_poll_seconds == 45  # safe fallback; do not brick old installs
    assert active.max_queue == 8
    field = manager.describe(active, MODELS[0], environ={})["fields"]["quota_poll_seconds"]
    assert field["saved"] is None and field["pending_restart"] is False
    # User can repair this existing file by saving an allowed quota value.
    assert manager.update({"quota_poll_seconds": 30}, effective=active)["quota_poll_seconds"] == 30


@pytest.mark.parametrize("value", [15, 29, 61, 3600])
def test_invalid_quota_environment_override_fails_with_clear_boundary(monkeypatch, value):
    monkeypatch.setenv("AI_GATEWAY_QUOTA_POLL_SECONDS", str(value))
    with pytest.raises(ValueError, match="quota_poll_seconds must be an integer from 30 to 60"):
        Settings.from_env()


@pytest.mark.parametrize("value", [30, 45, 60])
def test_valid_quota_environment_override_is_accepted(monkeypatch, value):
    monkeypatch.setenv("AI_GATEWAY_QUOTA_POLL_SECONDS", str(value))
    assert Settings.from_env().quota_poll_seconds == value


@pytest.mark.parametrize("value", [15, 61])
def test_invalid_programmatically_injected_settings_fail_before_disk_init(tmp_path, monkeypatch, value):
    configure_paths(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="quota_poll_seconds must be an integer from 30 to 60"):
        root.create_app(settings=Settings(api_token=TOKEN, codex_exe=sys.executable, quota_poll_seconds=value), runner=object())
    assert not (tmp_path / "data").exists()


def test_environment_precedence_with_valid_saved_quota(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json", MODELS)
    manager.update({"quota_poll_seconds": 60})
    base = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_poll_seconds=30)
    active = apply_saved_settings(base, manager.load(), MODELS, environ={"AI_GATEWAY_QUOTA_POLL_SECONDS": "30"})
    assert active.quota_poll_seconds == 30
    field = manager.describe(active, MODELS[0], environ={"AI_GATEWAY_QUOTA_POLL_SECONDS": "30"})["fields"]["quota_poll_seconds"]
    assert field["environment_override"] is True
    assert field["pending_restart"] is False
    assert field["saved"] == 60


def test_shared_validator_uses_exact_integer_not_bool():
    assert [validate_quota_poll_seconds(i) for i in (30, 45, 60)] == [30, 45, 60]
    with pytest.raises(ValueError, match="integer from 30 to 60"):
        validate_quota_poll_seconds(True)
