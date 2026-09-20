"""Module 7B — privacy and opt-in bounded text retention."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway import app as root
from gateway.config import MODELS, Settings
from gateway.contracts import GatewayError, ImageRunResult, RunResult
from gateway.job_store import JobStore
from gateway.settings_manager import SettingsManager, SettingsValidationError, apply_saved_settings

TOKEN = "local-ai-gateway-7b-test-token-123456"
HEADERS = {"Authorization": "Bearer " + TOKEN}


class FakeRunner:
    def __init__(self, image=None, *, fail=False):
        self.image = image
        self.fail = fail

    async def run(self, **kwargs):
        kwargs["progress"]("Codex is working")
        if self.fail:
            error = GatewayError("codex_cli_argument_error", "Codex rejected an argument.", 502)
            error.diagnostic = "unexpected argument --model Authorization: Bearer SENSITIVEEXAMPLE123456"
            error.exit_code = 2
            raise error
        return RunResult("answer secret phrase", "answer secret phrase", {"input_tokens": 11, "output_tokens": 4})

    async def run_image(self, **kwargs):
        kwargs["progress"]("Launching Codex")
        return ImageRunResult(self.image, "image/png", {"input_tokens": 10}, "fake-thread")


def make_app(tmp_path, monkeypatch, *, settings=None, runner=None):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(root, "DATA_DIR", tmp_path / "data")
    settings = settings or Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    runner = runner or FakeRunner()
    api = root.create_app(settings=settings, runner=runner)
    return api, api.state.job_store


def test_defaults_prevent_plaintext_storage_and_keep_diagnostics(tmp_path, monkeypatch):
    api, db = make_app(tmp_path, monkeypatch)
    assert db._db.execute("PRAGMA user_version").fetchone()[0] == 6
    with TestClient(api) as c:
        result = c.post("/v1/generate", json={"prompt": "secret user prompt"}, headers=HEADERS)
        assert result.status_code == 200
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert job["usage"]["input_tokens"] == 11
        assert db.get_content(job["id"]) is None
        assert "secret user prompt" not in str(job)
        assert "answer secret phrase" not in str(job)
        assert c.get("/dashboard/api/jobs", headers=HEADERS).status_code == 200
        content = c.get(f"/dashboard/api/jobs/{job['id']}/content", headers=HEADERS)
        assert content.status_code == 200 and content.json()["content"] is None
        snap = c.get("/dashboard/api/settings", headers=HEADERS).json()["fields"]
        assert snap["store_prompts"]["value"] is False
        assert snap["store_outputs"]["value"] is False
        assert snap["store_diagnostics"]["value"] is True


def test_prompt_output_opt_in_and_no_content_in_existing_apis(tmp_path, monkeypatch):
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False,
                        store_prompts=True, store_outputs=True)
    api, db = make_app(tmp_path, monkeypatch, settings=settings)
    with TestClient(api) as c:
        resp = c.post("/v1/generate", headers=HEADERS, json={"prompt": "private prompt"})
        assert resp.status_code == 200 and resp.json()["response"] == "answer secret phrase"
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        saved = db.get_content(job["id"])
        assert saved["prompt_text"] == "private prompt"
        assert saved["output_text"] == "answer secret phrase"
        for route in ("/v1/jobs", "/dashboard/api/jobs", f"/dashboard/api/jobs/{job['id']}"):
            assert "private prompt" not in c.get(route, headers=HEADERS).text
            assert "answer secret phrase" not in c.get(route, headers=HEADERS).text
        assert c.get(f"/dashboard/api/jobs/{job['id']}/content").status_code == 401
        protected = c.get(f"/dashboard/api/jobs/{job['id']}/content", headers=HEADERS)
        assert protected.json()["content"]["prompt_text"] == "private prompt"
        assert protected.headers["cache-control"] == "no-store"
        assert db.content_stats()["prompts"] == db.content_stats()["outputs"] == 1


def test_only_prompt_and_only_output_modes(tmp_path, monkeypatch):
    for name, flags in (("prompts", dict(store_prompts=True)), ("outputs", dict(store_outputs=True))):
        subdir = tmp_path / name
        subdir.mkdir()
        settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False, **flags)
        api, db = make_app(subdir, monkeypatch, settings=settings)
        with TestClient(api) as c:
            assert c.post("/v1/generate", headers=HEADERS, json={"prompt": "private prompt"}).status_code == 200
            row = db._db.execute("SELECT prompt_text,output_text FROM job_payloads").fetchone()
            assert (row["prompt_text"] is not None) == flags.get("store_prompts", False)
            assert (row["output_text"] is not None) == flags.get("store_outputs", False)


def test_content_is_bounded_and_upsert_keeps_other_field(tmp_path):
    store = JobStore(tmp_path / "gateway.sqlite3")
    job = {"id": "j1", "request_id": "j1", "task": "generate", "model": "luna", "status": "completed", "stage": "Done", "created_at": "2026-09-20T00:00:00+00:00", "reference_count": 0, "elapsed_ms": 0}
    store.save_job(job)
    store.save_prompt("j1", "X" * 17000)
    store.save_output("j1", "Y" * 33000)
    row = store.get_content("j1")
    assert len(row["prompt_text"]) == 16000 and row["prompt_truncated"]
    assert len(row["output_text"]) == 32000 and row["output_truncated"]
    store.save_prompt("j1", "short")
    row = store.get_content("j1")
    assert row["prompt_text"] == "short" and not row["prompt_truncated"]
    assert len(row["output_text"]) == 32000
    with pytest.raises(ValueError):
        store._save_text_field("j1", "error", "never", 10)


def test_migrate_v5_preserves_jobs_and_statistics(tmp_path):
    path = tmp_path / "gateway.sqlite3"
    store = JobStore(path)
    store.save_job({"id": "old", "request_id": "old", "task": "image", "model": "luna", "status": "completed", "stage": "Image ready", "created_at": "2026-09-20T00:00:00+00:00", "reference_count": 0, "elapsed_ms": 0})
    store.save_usage("old", {"input_tokens": 10, "output_tokens": 2})
    with store._lock, store._db:
        store._db.execute("DROP TABLE job_payloads")
        store._db.execute("PRAGMA user_version=5")
    store.close()
    reopened = JobStore(path)
    assert reopened._db.execute("PRAGMA user_version").fetchone()[0] == 6
    assert reopened.get_job("old")["request_id"] == "old"
    assert reopened.get_usage("old")["input_tokens"] == 10
    assert reopened.get_content("old") is None
    reopened.save_prompt("old", "newly opted in")
    assert reopened.get_content("old")["prompt_text"] == "newly opted in"


def test_new_privacy_settings_validated_saved_and_restart_required(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    mgr = SettingsManager(path, MODELS)
    assert mgr.update({"store_prompts": True, "store_outputs": True, "store_diagnostics": False})["store_prompts"] is True
    prior = path.read_bytes()
    for field in ("store_prompts", "store_outputs", "store_diagnostics"):
        for bad in ("true", 1, 0, None):
            with pytest.raises(SettingsValidationError):
                mgr.update({field: bad})
    assert path.read_bytes() == prior
    active = Settings(api_token=TOKEN, codex_exe=sys.executable)
    info = mgr.describe(active, active.model, environ={})
    assert info["pending_restart"] is True
    assert all(info["fields"][key]["pending_restart"] for key in ("store_prompts", "store_outputs", "store_diagnostics"))
    restarted = apply_saved_settings(active, mgr.load(), MODELS, environ={})
    assert restarted.store_prompts and restarted.store_outputs and not restarted.store_diagnostics
    env_restarted = apply_saved_settings(active, mgr.load(), MODELS,
                                         environ={"AI_GATEWAY_STORE_PROMPTS": "0"})
    assert env_restarted.store_prompts is False


def test_api_patch_privacy_does_not_change_running_app(tmp_path, monkeypatch):
    api, db = make_app(tmp_path, monkeypatch)
    with TestClient(api) as c:
        resp = c.patch("/dashboard/api/settings", headers=HEADERS,
                       json={"store_prompts": True, "store_outputs": True})
        assert resp.status_code == 200
        assert resp.json()["pending_restart"] is True
        assert resp.json()["fields"]["store_prompts"]["value"] is False
        assert resp.json()["fields"]["store_prompts"]["saved"] is True
        assert c.post("/v1/generate", headers=HEADERS, json={"prompt": "not retained yet"}).status_code == 200
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert db.get_content(job["id"]) is None


def test_diagnostic_preview_can_be_disabled_without_losing_metadata(tmp_path, monkeypatch):
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False,
                        store_diagnostics=False)
    api, db = make_app(tmp_path, monkeypatch, settings=settings, runner=FakeRunner(fail=True))
    with TestClient(api) as c:
        r = c.post("/v1/generate", headers=HEADERS, json={"prompt": "secret"})
        assert r.status_code == 502
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert job["diagnostic_preview"] is None
        saved = db.get_job(job["id"])
        assert saved["diagnostic_preview"] is None
        assert saved["exit_code"] == 2
        assert saved["error_type"] == "codex_cli_argument_error"
        detail = c.get(f"/dashboard/api/diagnostics/{job['id']}", headers=HEADERS)
        assert detail.status_code == 200
        assert "SENSITIVEEXAMPLE" not in detail.text


def test_purge_requires_auth_exact_confirmation_and_scope(tmp_path, monkeypatch):
    api, db = make_app(tmp_path, monkeypatch)
    with TestClient(api) as c:
        assert c.post("/dashboard/api/privacy/purge", json={"confirmation": "DELETE_RETAINED_DATA", "scope": "both"}).status_code == 401
        for bad in ({}, {"confirmation": "DELETE_RETAINED_DATA", "scope": "all"},
                    {"confirmation": "DELETE_RETAINED_DATA", "scope": "both", "hidden": 1}):
            assert c.post("/dashboard/api/privacy/purge", headers=HEADERS, json=bad).status_code == 422
        assert c.get("/dashboard/api/privacy/storage").status_code == 401
        assert c.get("/dashboard/api/privacy/storage", headers=HEADERS).json()["retained_text"]["rows"] == 0


def test_purge_text_preserves_stats_and_purge_diag_clears_hot_cache(tmp_path, monkeypatch):
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False,
                        store_prompts=True, store_diagnostics=True)
    api, db = make_app(tmp_path, monkeypatch, settings=settings, runner=FakeRunner(fail=True))
    with TestClient(api) as c:
        r = c.post("/v1/generate", headers=HEADERS, json={"prompt": "private"})
        assert r.status_code == 502
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert db.get_content(job["id"])["prompt_text"] == "private"
        assert db.get_job(job["id"])["diagnostic_preview"] is not None
        purged = c.post("/dashboard/api/privacy/purge", headers=HEADERS,
                        json={"confirmation": "DELETE_RETAINED_DATA", "scope": "both"})
        assert purged.status_code == 200
        assert purged.json()["retained_text_rows_deleted"] == 1
        assert purged.json()["diagnostic_previews_cleared"] == 1
        assert db.get_content(job["id"]) is None
        assert db.get_job(job["id"])["exit_code"] == 2
        assert db.get_job(job["id"])["diagnostic_preview"] is None
        assert c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]["diagnostic_preview"] is None
        assert c.get("/dashboard/api/usage", headers=HEADERS).status_code == 200


def test_content_endpoint_missing_job_and_no_db(tmp_path, monkeypatch):
    api, db = make_app(tmp_path, monkeypatch)
    with TestClient(api) as c:
        assert c.get("/dashboard/api/jobs/not-real/content", headers=HEADERS).status_code == 404
    assert db.get_content("not-real") is None


def test_image_prompt_is_opt_in_and_never_saves_reference_url(tmp_path, monkeypatch):
    from PIL import Image
    image = tmp_path / "image.png"
    Image.new("RGB", (24, 24)).save(image)
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False,
                        store_prompts=True, store_outputs=True)
    api, db = make_app(tmp_path, monkeypatch, settings=settings, runner=FakeRunner(image=image))
    with TestClient(api) as c:
        r = c.post("/v1/images/generations", headers=HEADERS, json={"prompt": "my scene", "reference_images": [{"id": "asset", "url": "https://drive.google.com/secret"}]})
        assert r.status_code == 200
        job = c.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        content = db.get_content(job["id"])
        assert content["prompt_text"] == "my scene" and content["output_text"] is None
        assert "https://drive.google.com/secret" not in str(content)
        # ArtifactStore in this focused harness is a stub; the image route and
        # opt-in privacy assertions above remain valid.
