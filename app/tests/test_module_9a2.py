"""9A.2: storage is atomic per file; original-image success requires DB registration.

All paths, images and SQLite files are disposable pytest tmp_path assets. No
Codex process, network access, or production cleanup is used.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gateway import app as root
from gateway.artifact_store import ArtifactStore
from gateway.contracts import ImageRunResult
from gateway.job_store import JobStore

TOKEN = "module-9a2-test-token-long-enough-1234"
AUTH = {"Authorization": "Bearer " + TOKEN}


def source(tmp_path):
    path = tmp_path / "codex-result.png"
    Image.new("RGB", (120, 90), "#224466").save(path)
    return path


def managed_files(store):
    return sorted(path.name for path in store.root.iterdir())


def assert_no_staging(store):
    assert not [p for p in store.root.iterdir() if p.name.endswith(".staging")]


def test_successful_atomic_publication_and_unmodified_source(tmp_path):
    src = source(tmp_path)
    initial = src.read_bytes()
    old = src.stat().st_mtime - 86400 * 100
    os.utime(src, (old, old))
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.create(job_id="test", source=src, mime_type="image/png")
    assert Path(record["storage_path"]).read_bytes() == initial
    assert Path(record["storage_path"]).stat().st_mtime > old + 86400
    assert src.read_bytes() == initial and src.stat().st_mtime == old
    assert Path(record["thumbnail_path"]).is_file()
    assert record["width"] == 120 and record["height"] == 90
    assert_no_staging(store)


def test_interrupted_copy_cannot_publish_partial_original(tmp_path, monkeypatch):
    import gateway.artifact_store as mod
    src = source(tmp_path)
    store = ArtifactStore(tmp_path / "artifacts")

    def partial_then_fail(_src, target):
        Path(target).write_bytes(b"PARTIAL PRIVATE COPY")
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod.shutil, "copyfile", partial_then_fail)
    with pytest.raises(OSError, match="disk full"):
        store.create(job_id="test", source=src, mime_type="image/png")
    assert managed_files(store) == []
    assert src.is_file()


def test_original_rename_failure_leaves_no_visible_files(tmp_path, monkeypatch):
    import gateway.artifact_store as mod
    store = ArtifactStore(tmp_path / "artifacts")
    actual = mod.os.replace

    def fail_original(src, dst):
        if str(dst).endswith(".png"):
            raise PermissionError("locked destination")
        return actual(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_original)
    with pytest.raises(PermissionError, match="locked"):
        store.create(job_id="test", source=source(tmp_path), mime_type="image/png")
    assert managed_files(store) == []


def test_thumbnail_rename_failure_keeps_original_without_broken_thumbnail_link(tmp_path, monkeypatch):
    import gateway.artifact_store as mod
    store = ArtifactStore(tmp_path / "artifacts")
    actual = mod.os.replace

    def fail_thumbnail(src, dst):
        if str(dst).endswith("_thumb.webp"):
            raise PermissionError("thumbnail locked")
        return actual(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_thumbnail)
    record = store.create(job_id="test", source=source(tmp_path), mime_type="image/png")
    assert record["thumbnail_path"] is None
    assert Path(record["storage_path"]).is_file()
    assert_no_staging(store)


def test_late_metadata_failure_rolls_back_both_published_files(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "artifacts")
    real_stat = Path.stat

    def fail_published_stat(self, *args, **kwargs):
        if self.parent == store.root and self.name.startswith("art_") and self.suffix == ".png":
            raise OSError("file stat failed")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_published_stat)
    with pytest.raises(OSError, match="file stat failed"):
        store.create(job_id="test", source=source(tmp_path), mime_type="image/png")
    assert managed_files(store) == []


def test_invalid_png_cannot_enter_gallery(tmp_path):
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not a real png")
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises((OSError, ValueError)):
        store.create(job_id="test", source=invalid, mime_type="image/png")
    assert managed_files(store) == []


def test_regular_resolve_and_delete_reject_symlink_even_into_artifact_root(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.create(job_id="test", source=source(tmp_path), mime_type="image/png")
    alias = store.root / ("art_" + "f" * 32 + ".png")
    try:
        alias.symlink_to(Path(record["storage_path"]))
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires Windows Developer Mode or privileges")
    assert store.resolve(str(alias)) is None
    assert store.delete({"storage_path": str(alias), "thumbnail_path": None}) is False
    assert alias.is_symlink() and Path(record["storage_path"]).exists()
    assert store.resolve(record["storage_path"]) is not None


class FakeRunner:
    def __init__(self, image):
        self.image = image
        self.on_image = None

    async def run_image(self, **kwargs):
        kwargs["progress"]("Launching Codex")
        if self.on_image:
            self.on_image(kwargs)
        return ImageRunResult(self.image, "image/png", {"input_tokens": 8, "output_tokens": 2}, "mock-thread")


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    db = JobStore(tmp_path / "db.sqlite3")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    runner = FakeRunner(source(tmp_path))
    cfg = root.Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    api = root.create_app(settings=cfg, runner=runner, job_store=db, artifact_store=artifacts)
    return api, db, artifacts, runner


def do_request(client, request_id):
    return client.post("/v1/images/generations", json={"prompt": "make an image", "request_id": request_id}, headers=AUTH)


def test_original_db_insert_failure_is_503_and_no_false_completed_job(tmp_path, monkeypatch):
    api, db, artifacts, _runner = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "save_artifact", lambda _: (_ for _ in ()).throw(OSError("DB disk full")))
    with TestClient(api) as client:
        response = do_request(client, "image_db_failed")
        assert response.status_code == 503
        assert response.json()["error"]["type"] == "artifact_persist_failed"
        assert "disk full" not in response.text.lower()
        row = db.list_jobs(search="image_db_failed")[0]
        assert row["status"] == "failed"
        assert row["error_type"] == "artifact_persist_failed"
        assert row["last_successful_stage"] == "Codex completed"
        assert db.get_usage(row["id"])["input_tokens"] == 8
        assert db.get_artifact_for_job(row["id"]) is None
        assert not [f for f in artifacts.root.iterdir() if f.is_file()]
        failures = client.get("/dashboard/api/diagnostics?range=24h", headers=AUTH)
        assert failures.status_code == 200
        assert "artifact_persist_failed" in failures.text
    db.close()


def test_db_commit_then_raise_compensates_db_and_files(tmp_path, monkeypatch):
    api, db, artifacts, _runner = make_app(tmp_path, monkeypatch)
    original = db.save_artifact

    def write_then_fail(record):
        original(record)
        raise OSError("commit succeeded but caller failed")

    monkeypatch.setattr(db, "save_artifact", write_then_fail)
    with TestClient(api) as client:
        resp = do_request(client, "image_committed_failure")
        assert resp.status_code == 503
        job = db.list_jobs(search="image_committed_failure")[0]
        assert job["status"] == "failed"
        assert db.get_artifact_for_job(job["id"]) is None
        assert managed_files(artifacts) == []
    db.close()


def test_file_store_failure_is_503_and_fails_job(tmp_path, monkeypatch):
    api, db, artifacts, _runner = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(artifacts, "create", lambda **kwargs: (_ for _ in ()).throw(OSError("blocked by OS")))
    with TestClient(api) as client:
        response = do_request(client, "image_copy_failed")
        assert response.status_code == 503
        row = db.list_jobs(search="image_copy_failed")[0]
        assert row["status"] == "failed"
        assert row["error_type"] == "artifact_persist_failed"
    db.close()


def test_success_binary_contract_and_gallery_link(tmp_path, monkeypatch):
    api, db, artifacts, _runner = make_app(tmp_path, monkeypatch)
    with TestClient(api) as client:
        response = do_request(client, "scene_image_success")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content == (tmp_path / "codex-result.png").read_bytes()
        assert response.headers["x-request-id"] == "scene_image_success"
        row = db.list_jobs(search="scene_image_success")[0]
        assert row["status"] == "completed" and row["artifact_id"]
        artifact = db.get_artifact(row["artifact_id"])
        assert artifact and Path(artifact["storage_path"]).is_file()
        gallery = client.get("/dashboard/api/gallery?limit=5", headers=AUTH)
        assert gallery.status_code == 200 and artifact["id"] in gallery.text
        returned = client.get(f"/dashboard/api/artifacts/{artifact['id']}", headers=AUTH)
        assert returned.status_code == 200 and returned.content == response.content
    db.close()


def test_reference_thumbnail_db_failure_is_optional_and_cleaned_up(tmp_path, monkeypatch):
    api, db, artifacts, runner = make_app(tmp_path, monkeypatch)
    ref_src = tmp_path / "reference.png"
    Image.new("RGB", (5, 7), "#335577").save(ref_src)
    original = db.save_artifact

    def fail_reference_only(record):
        if record["artifact_type"] == "reference":
            raise OSError("reference gallery unavailable")
        return original(record)

    monkeypatch.setattr(db, "save_artifact", fail_reference_only)
    def send_reference(kwargs):
        from gateway.contracts import ImageReference
        kwargs["reference_ready"](0, ImageReference(id="ref-1", url="https://example.org/a.png"), ref_src)
    runner.on_image = send_reference
    with TestClient(api) as client:
        resp = client.post("/v1/images/generations", headers=AUTH,
                           json={"request_id": "image_ref_optional", "prompt": "image", "reference_images": [
                               {"id": "ref-1", "url": "https://example.org/a.png"}]})
        assert resp.status_code == 200
        job = db.list_jobs(search="image_ref_optional")[0]
        assert job["references_downloaded"] == 1
        assert job["status"] == "completed"
        refs = db.get_references(job["id"])
        assert refs[0]["artifact_id"] is None
        assert len(db.retention_artifacts()) == 1  # output only
        assert not any("reference" in f.name for f in artifacts.root.iterdir())
    db.close()


def test_failed_db_rollback_keeps_files_as_recoverable_record(tmp_path, monkeypatch):
    api, db, artifacts, _runner = make_app(tmp_path, monkeypatch)
    real_save = db.save_artifact
    def save_then_raise(record):
        real_save(record)
        raise OSError("after commit")
    monkeypatch.setattr(db, "save_artifact", save_then_raise)
    monkeypatch.setattr(db, "rollback_artifact_record", lambda _id: (_ for _ in ()).throw(OSError("rollback unavailable")))
    with TestClient(api) as client:
        assert do_request(client, "image_rollback_unavailable").status_code == 503
        row = db.list_jobs(search="image_rollback_unavailable")[0]
        assert row["status"] == "failed"
        artifact = db.get_artifact_for_job(row["id"])
        assert artifact and Path(artifact["storage_path"]).exists()
    db.close()
