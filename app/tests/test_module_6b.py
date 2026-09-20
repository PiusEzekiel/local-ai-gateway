"""Module 6B focused regression tests. No live Codex, Drive or network needed."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.app import (CodexRunner, GatewayError, ImageReference, JobHistory,
                         codex_failure_diagnostic)
from gateway.job_store import JobStore


def test_new_database_has_diagnostics_columns(tmp_path):
    store = JobStore(tmp_path / "gateway.sqlite3")
    cols = {r[1] for r in store._db.execute("PRAGMA table_info(jobs)")}
    assert {"exit_code", "diagnostic_preview", "last_successful_stage", "references_downloaded"} <= cols
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 6
    store.close()


def test_v4_upgrade_preserves_existing_data(tmp_path):
    db = tmp_path / "existing.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE jobs (id TEXT PRIMARY KEY, request_id TEXT, stage TEXT);
            INSERT INTO jobs (id,request_id,stage) VALUES ('old-job','scene_image_8_initial','Image ready');
            PRAGMA user_version=4;
        """)
    store = JobStore(db)
    row = store.get_job("old-job")
    assert row["request_id"] == "scene_image_8_initial"
    assert row["diagnostic_preview"] is None
    assert row["references_downloaded"] == 0
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 6
    store.close()
    store = JobStore(db)  # Re-opening an already-migrated database must be idempotent.
    assert store.get_job("old-job")["request_id"] == "scene_image_8_initial"
    store.close()


def test_persistence_redacts_untrusted_error_fields(tmp_path):
    store = JobStore(tmp_path / "data.sqlite3")
    job = JobHistory(store).add("scene_image_3_initial", "image", "gpt-5.6-luna", 2)
    job.update({
        "diagnostic_preview": "unexpected argument '--image' https://drive.google.com/uc?id=SECRET Authorization: Bearer tokenvalue1234567 C:\\Users\\piuse\\private.png",
        "last_successful_stage": "Reference images ready",
        "references_downloaded": 1,
        "exit_code": 2,
        "error": {"type": "codex_cli_argument_error", "message": "see https://drive.google.com/uc?id=SECRET"},
    })
    store.save_job(job)
    persisted = store.get_job(job["id"])
    assert persisted["exit_code"] == 2
    assert persisted["references_downloaded"] == 1
    assert persisted["last_successful_stage"] == "Reference images ready"
    assert "--image" in persisted["diagnostic_preview"]
    assert "SECRET" not in str(persisted)
    assert "tokenvalue1234567" not in str(persisted)
    assert "piuse" not in str(persisted)
    store.close()


def test_history_records_validated_reference_and_error_context(tmp_path):
    store = JobStore(tmp_path / "data.sqlite3")
    history = JobHistory(store)
    job = history.add("scene_image_1_initial", "image", "gpt-5.6-luna", 2)
    history.worker_acquired(job, 50)
    history.update(job, status="running", stage="Downloading reference image 1/2")
    assert job["last_successful_stage"] == "Worker acquired"
    history.reference_ready(job, 1)
    history.update(job, status="running", stage="Downloading reference image 2/2")
    assert job["last_successful_stage"] == "Reference image 1 ready"
    error = GatewayError("codex_cli_argument_error", "CLI rejected arguments", 502)
    error.exit_code = 2
    error.diagnostic = "unexpected argument '--image' with password=private1234"
    history.update(job, status="failed", stage="Image generation failed", error=error)
    row = store.get_job(job["id"])
    bundle = history.diagnostic_bundle(row)
    assert row["references_downloaded"] == 1
    assert row["last_successful_stage"] == "Reference image 1 ready"
    assert row["exit_code"] == 2
    assert "private1234" not in str(bundle)
    assert bundle["error_type"] == "codex_cli_argument_error"
    assert bundle["reference_count"] == 2
    assert bundle["references_downloaded"] == 1
    assert store.get_events(job["id"])[-1]["level"] == "error"
    store.close()


def test_diagnostic_does_not_capture_normal_model_output():
    stdout = b'{"type":"item.completed","item":{"type":"agent_message","text":"secret prompt"}}\n'
    stderr = b"Unexpected argument '--image'"
    preview = codex_failure_diagnostic(stderr, stdout)
    assert "--image" in preview
    assert "secret prompt" not in preview


def test_runner_image_failure_attaches_safe_context(tmp_path, monkeypatch):
    # CLI is replaced with a fake process. No Codex installation is required.
    class FakeStdin:
        def __init__(self): self.data = b""
        def write(self, data): self.data += data
        async def drain(self): return None
        def close(self): pass

    stdin = FakeStdin()
    process = SimpleNamespace(returncode=2, pid=1234, stdin=stdin)
    observed = {}

    async def fake_launch(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return process

    async def fake_communicate(*args):
        return b"", (
            b"error: unexpected argument '--image' "
            b"https://drive.google.com/uc?id=PRIVATEID "
            b"Authorization: Bearer myprivateapikey123456"
        )

    def fake_download(reference, workdir, index, request_id):
        path = Path(workdir) / "reference.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_launch)
    monkeypatch.setattr("gateway.app.download_reference_image", fake_download)
    runner = CodexRunner([sys.executable], model="gpt-5.6-luna")
    monkeypatch.setattr(runner, "_communicate", fake_communicate)

    async def go():
        return await runner.run_image(
            prompt="safe test prompt", timeout_seconds=30,
            reference_images=[ImageReference(id="ref", url="https://drive.google.com/example")],
            request_id="scene_image_1_initial",
        )

    with pytest.raises(GatewayError) as caught:
        asyncio.run(go())
    exc = caught.value
    assert exc.kind == "codex_cli_argument_error"
    assert exc.exit_code == 2
    assert "--image" in exc.diagnostic
    assert "PRIVATEID" not in exc.diagnostic
    assert "myprivateapikey123456" not in exc.diagnostic
    assert "exec" in observed["args"]
    assert observed["args"].index("exec") < observed["args"].index("--image")
    assert "safe test prompt" not in observed["args"]
    assert b"safe test prompt" in stdin.data


def test_text_api_persists_classified_failure_without_changing_response(tmp_path, monkeypatch):
    """The actual FastAPI handler must preserve the n8n error API contract."""
    from fastapi.testclient import TestClient
    from gateway.app import Settings, create_app

    class FailingRunner:
        async def run(self, **kwargs):
            kwargs["progress"]("Codex session started")
            exc = GatewayError("codex_cli_argument_error", "Codex CLI rejected arguments", 502)
            exc.exit_code = 2
            exc.diagnostic = "unexpected argument '--model'; https://drive.google.com/uc?id=private"
            raise exc

    store = JobStore(tmp_path / "gateway.sqlite3")
    token = "test-token-that-is-at-least-24-characters"
    settings = Settings(
        api_token=token,
        codex_exe=sys.executable,
        quota_monitor_enabled=False,
    )
    # Pass an explicit store so this test never touches production SQLite.
    app = create_app(settings=settings, runner=FailingRunner(), job_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/v1/generate",
            headers={"Authorization": f"Bearer {token}"},
            json={"request_id": "text_cli_failure", "prompt": "hello"},
        )
        assert response.status_code == 502
        assert response.json()["error"] == {
            "type": "codex_cli_argument_error",
            "message": "Codex CLI rejected arguments",
        }
        job = store.list_jobs(search="text_cli_failure")[0]
        assert job["exit_code"] == 2
        assert job["last_successful_stage"] == "Codex session started"
        assert "--model" in job["diagnostic_preview"]
        assert "private" not in job["diagnostic_preview"]
    store.close()
