"""Module 7A settings contract, security, persistence and restart semantics."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway import app as root
from gateway.config import MODELS, Settings
from gateway.settings_manager import (
    SettingsManager, SettingsValidationError, apply_saved_settings,
)

TOKEN = "module7a-test-token-0123456789012345"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeRunner:
    def __init__(self):
        self.seen = []

    async def run(self, **kwargs):
        from gateway.contracts import RunResult
        self.seen.append(kwargs)
        return RunResult("ok", "ok", {"input_tokens": 1, "output_tokens": 1})

    async def run_image(self, **kwargs):
        from gateway.contracts import ImageRunResult
        self.seen.append(kwargs)
        image = Path(kwargs.pop("_test_image_path", ""))
        # Set by per-test monkeypatch below when using image request.
        return ImageRunResult(self.image, "image/png", None, None)


def create_test_app(tmp_path, monkeypatch, *, injected=True, runner=None, initial_settings=None):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(root, "DATA_DIR", tmp_path / "data")
    runner = runner or FakeRunner()
    settings = initial_settings or Settings(api_token=TOKEN, codex_exe=sys.executable,
                                            quota_monitor_enabled=False)
    if not injected:
        monkeypatch.setattr(root.Settings, "from_env", classmethod(lambda cls: settings))
    api = root.create_app(settings=settings if injected else None, runner=runner)
    return api, runner


def test_existing_model_api_preserves_unrelated_json_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"theme":"dark","other":{"keep":1}}', encoding="utf-8")
    manager = SettingsManager(path, MODELS)
    saved = manager.save_model("gpt-5.6-terra")
    assert saved["theme"] == "dark"
    assert saved["other"] == {"keep": 1}
    assert saved["model"] == "gpt-5.6-terra"


def test_manager_validates_types_and_limits_and_does_not_write_on_failure(tmp_path):
    path = tmp_path / "settings.json"
    mgr = SettingsManager(path, MODELS)
    for value in (True, "4", 0, 17, 1.2):
        with pytest.raises(SettingsValidationError):
            mgr.update({"max_concurrency": value})
    for key, value in (("api_token", "unsafe"), ("codex_exe", "unsafe"),
                       ("max_queue", -1), ("image_timeout_seconds", 901),
                       ("quota_monitor_enabled", "false")):
        with pytest.raises(SettingsValidationError):
            mgr.update({key: value})
    assert not path.exists()


def test_manager_rejects_invalid_relationship_and_stays_atomic(tmp_path):
    path = tmp_path / "settings.json"
    mgr = SettingsManager(path, MODELS)
    assert mgr.update({"max_queue": 8})["max_queue"] == 8
    previous = path.read_bytes()
    for change in ({"default_timeout_seconds": 300, "max_timeout_seconds": 100},
                   {"quota_warning_remaining_percent": 5, "quota_critical_remaining_percent": 10}):
        with pytest.raises(SettingsValidationError):
            mgr.update(change)
        assert path.read_bytes() == previous


def test_invalid_json_refuses_overwrite_but_startup_can_fallback(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"model":', encoding="utf-8")
    mgr = SettingsManager(path, MODELS)
    assert mgr.load() == {}
    with pytest.raises(SettingsValidationError, match="valid JSON"):
        mgr.save_model("gpt-5.6-luna")
    assert path.read_text(encoding="utf-8") == '{"model":'


def test_apply_saved_settings_honors_env_and_preserves_model_precedence():
    base = Settings(api_token=TOKEN, codex_exe=sys.executable, max_queue=12)
    saved = {"model": "gpt-5.6-terra", "max_concurrency": 3, "max_queue": 7,
             "image_timeout_seconds": 720, "quota_warning_remaining_percent": 35}
    env = {"AI_GATEWAY_MAX_QUEUE": "12", "AI_GATEWAY_MODEL": "gpt-5.6-sol"}
    effective = apply_saved_settings(base, saved, MODELS, environ=env)
    assert effective.max_queue == 12
    assert effective.max_concurrency == 3
    assert effective.image_timeout_seconds == 720
    assert effective.quota_warning_remaining_percent == 35
    assert effective.model == "gpt-5.6-terra"  # historical behavior
    assert effective.api_token == TOKEN and effective.codex_exe == sys.executable


def test_get_requires_auth_and_does_not_expose_token_or_executable(tmp_path, monkeypatch):
    app, _ = create_test_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/dashboard/api/settings").status_code == 401
        response = client.get("/dashboard/api/settings", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "api_token" not in str(data) and TOKEN not in str(data)
        assert "codex_exe" not in str(data) and sys.executable not in str(data)
        assert data["fields"]["max_concurrency"]["restart_required"] is True
        assert data["fields"]["model"]["restart_required"] is False


def test_patch_validated_and_rejected_inputs_do_not_change_file(tmp_path, monkeypatch):
    app, _ = create_test_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.patch("/dashboard/api/settings", json={"max_queue": 8}).status_code == 401
        assert client.patch("/dashboard/api/settings", headers=HEADERS,
                            json={"max_queue": 8}).status_code == 200
        assert (tmp_path / "settings.json").exists()
        prior = (tmp_path / "settings.json").read_bytes()
        for patch in ({"max_concurrency": "4"}, {"api_token": "leak"},
                      {"max_queue": -1}, {"max_queue": 3, "model": "nonsense"}, {}):
            result = client.patch("/dashboard/api/settings", headers=HEADERS, json=patch)
            assert result.status_code == 422, (patch, result.text)
            assert (tmp_path / "settings.json").read_bytes() == prior


def test_patch_model_is_live_and_updates_existing_model_endpoint(tmp_path, monkeypatch):
    app, runner = create_test_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        result = client.patch("/dashboard/api/settings", headers=HEADERS,
                              json={"model": "gpt-5.6-terra"})
        assert result.status_code == 200
        assert result.json()["fields"]["model"]["value"] == "gpt-5.6-terra"
        assert result.json()["pending_restart"] is False
        assert client.get("/v1/models", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"
        assert client.get("/health", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"
        assert client.get("/dashboard/api/summary", headers=HEADERS).json()["gateway"]["model"] == "gpt-5.6-terra"
        assert client.post("/v1/generate", headers=HEADERS, json={"prompt": "hi"}).json()["model"] == "gpt-5.6-terra"
        assert runner.seen[-1]["model"] == "gpt-5.6-terra"
        assert json.loads((tmp_path / "settings.json").read_text())["model"] == "gpt-5.6-terra"


def test_restart_required_update_does_not_change_running_pool(tmp_path, monkeypatch):
    app, runner = create_test_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        changed = client.patch("/dashboard/api/settings", headers=HEADERS, json={
            "max_concurrency": 3, "max_queue": 8, "default_timeout_seconds": 145,
            "image_timeout_seconds": 720,
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["pending_restart"] is True
        assert changed.json()["fields"]["max_concurrency"]["value"] == 1
        assert changed.json()["fields"]["max_concurrency"]["saved"] == 3
        assert client.get("/dashboard/api/summary", headers=HEADERS).json()["workers"]["concurrency"] == 1
        assert client.post("/v1/generate", headers=HEADERS,
                           json={"prompt": "hi"}).status_code == 200
        assert runner.seen[-1]["timeout_seconds"] <= 120


def test_saved_settings_apply_on_next_boot(tmp_path, monkeypatch):
    initial = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    app, _ = create_test_app(tmp_path, monkeypatch, initial_settings=initial)
    with TestClient(app) as client:
        assert client.patch("/dashboard/api/settings", headers=HEADERS, json={
            "max_concurrency": 3, "max_queue": 8, "default_timeout_seconds": 145,
            "max_timeout_seconds": 250, "image_timeout_seconds": 720,
        }).status_code == 200
    app2, runner2 = create_test_app(tmp_path, monkeypatch, injected=False, initial_settings=initial)
    with TestClient(app2) as client:
        assert client.get("/dashboard/api/summary", headers=HEADERS).json()["workers"]["concurrency"] == 3
        assert client.get("/dashboard/api/summary", headers=HEADERS).json()["workers"]["max_queue"] == 8
        assert client.get("/dashboard/api/settings", headers=HEADERS).json()["pending_restart"] is False
        assert client.post("/v1/generate", headers=HEADERS, json={"prompt": "hello"}).status_code == 200
        assert runner2.seen[-1]["timeout_seconds"] <= 145
        assert runner2.seen[-1]["timeout_seconds"] > 130


def test_image_default_timeout_is_configurable_without_losing_explicit_override(tmp_path, monkeypatch):
    runner = FakeRunner()
    from PIL import Image
    source = tmp_path / "image.png"
    Image.new("RGB", (16, 16)).save(source)
    runner.image = source
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable,
                        quota_monitor_enabled=False, image_timeout_seconds=720)
    app, _ = create_test_app(tmp_path, monkeypatch, runner=runner, initial_settings=settings)
    with TestClient(app) as client:
        assert client.post("/v1/images/generations", headers=HEADERS,
                           json={"prompt": "hello"}).status_code == 200
        assert 710 < runner.seen[-1]["timeout_seconds"] <= 720
        assert client.post("/v1/images/generations", headers=HEADERS,
                           json={"prompt": "hello", "timeout_seconds": 40}).status_code == 200
        assert 30 < runner.seen[-1]["timeout_seconds"] <= 40


def test_describe_marks_env_override_but_not_pending_restart(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json", MODELS)
    manager.update({"max_queue": 8})
    data = manager.describe(Settings(api_token=TOKEN, codex_exe=sys.executable), "gpt-5.6-luna",
                            environ={"AI_GATEWAY_MAX_QUEUE": "4"})
    field = data["fields"]["max_queue"]
    assert field["environment_override"] is True
    assert field["pending_restart"] is False
    assert field["saved"] == 8 and field["value"] == 4


def test_invalid_saved_settings_dont_abort_boot(tmp_path, monkeypatch):
    base = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_concurrency": "6", "model": "gpt-5.6-terra"}), encoding="utf-8")
    app, _ = create_test_app(tmp_path, monkeypatch, injected=False, initial_settings=base)
    with TestClient(app) as client:
        assert client.get("/dashboard/api/summary", headers=HEADERS).json()["workers"]["concurrency"] == 1
        assert client.get("/v1/models", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"


def test_quota_monitor_settings_apply_on_restart(tmp_path, monkeypatch):
    """Monitor is initialized with saved values; PATCH must not restart it."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(root, "SETTINGS_PATH", path)
    monkeypatch.setattr(root, "DATA_DIR", tmp_path / "data")
    base = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=True)
    monkeypatch.setattr(root.Settings, "from_env", classmethod(lambda cls: base))
    created = []

    class Monitor:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        async def start(self):
            pass

        async def stop(self):
            pass

        def snapshot(self):
            return {"status": "unavailable", "buckets": []}

    monkeypatch.setattr(root, "QuotaMonitor", Monitor)
    app = root.create_app()
    with TestClient(app) as client:
        assert client.patch("/dashboard/api/settings", headers=HEADERS, json={
            "quota_poll_seconds": 55,
            "quota_warning_remaining_percent": 30,
            "quota_critical_remaining_percent": 7,
        }).status_code == 200
        assert len(created) == 1
        assert created[0].kwargs["poll_seconds"] == 45  # live monitor untouched
    next_app = root.create_app()
    with TestClient(next_app) as client:
        assert client.get("/health", headers=HEADERS).status_code == 200
        assert len(created) == 2
        assert created[1].kwargs["poll_seconds"] == 55
        assert created[1].kwargs["warning_remaining"] == 30
        assert created[1].kwargs["critical_remaining"] == 7
