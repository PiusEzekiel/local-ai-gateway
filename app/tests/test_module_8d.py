"""8D production-readiness checks for the genuine storage-retention handoff.

Tests use a disposable tmp_path. They never run cleanup on user data or
launch Codex, and do not change n8n-facing contracts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from PIL import Image

from gateway.artifact_store import ArtifactStore
from gateway.codex_runner import CodexRunner
from gateway.config import Settings
from gateway.job_store import JobStore
from gateway.retention import RetentionService


def _old_source(tmp_path: Path, filename: str = 'aged-source.png') -> Path:
    file = tmp_path / filename
    Image.new('RGB', (20, 20), '#234567').save(file)
    ancient = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
    os.utime(file, (ancient, ancient))
    return file


def test_new_artifact_gets_creation_time_not_source_timestamp(tmp_path):
    store = ArtifactStore(tmp_path / 'artifacts')
    src = _old_source(tmp_path)
    original_timestamp = src.stat().st_mtime
    record = store.create(job_id='job', source=src, mime_type='image/png')
    destination = Path(record['storage_path'])
    assert destination.is_file()
    assert destination.read_bytes() == src.read_bytes()
    assert src.stat().st_mtime == original_timestamp
    assert abs(datetime.now(timezone.utc).timestamp() - destination.stat().st_mtime) < 30
    assert record['thumbnail_path'] and Path(record['thumbnail_path']).is_file()


def test_new_aged_source_is_not_listed_as_orphan_before_registration(tmp_path):
    store = ArtifactStore(tmp_path / 'artifacts')
    record = store.create(job_id='job', source=_old_source(tmp_path), mime_type='image/png')
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    assert store.list_orphans(set(), older_than=cutoff) == []
    assert Path(record['storage_path']).exists()


def test_manual_orphan_cleanup_cannot_delete_just_imported_aged_source(tmp_path):
    db = JobStore(tmp_path / 'db.sqlite3')
    artifacts = ArtifactStore(tmp_path / 'artifacts')
    service = RetentionService(db, artifacts)
    settings = Settings(api_token='A' * 26, codex_exe=sys.executable,
                        quota_monitor_enabled=False)
    try:
        record = artifacts.create(job_id='job', source=_old_source(tmp_path),
                                  mime_type='image/png')
        before = service.preview(settings, include_orphans=True)
        assert before['candidates']['orphan_file_count'] == 0
        after = service.run(settings, include_orphans=True)
        assert after['result']['deleted_orphan_files'] == 0
        assert Path(record['storage_path']).is_file()
        assert Path(record['thumbnail_path']).is_file()
    finally:
        db.close()


def test_genuine_old_opaque_orphan_is_eligible_but_unmanaged_file_is_not(tmp_path):
    store = ArtifactStore(tmp_path / 'artifacts')
    managed = store.root / ('art_' + uuid4().hex + '.png')
    unmanaged = store.root / 'user-important.png'
    managed.write_bytes(b'old generated artifact')
    unmanaged.write_bytes(b'important unrelated file')
    old = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(managed, (old, old))
    os.utime(unmanaged, (old, old))
    candidates = store.list_orphans(set(), older_than=datetime.now(timezone.utc)-timedelta(hours=24))
    assert candidates == [managed]
    assert unmanaged.exists()


def test_registered_old_files_are_not_orphans(tmp_path):
    store = ArtifactStore(tmp_path / 'artifacts')
    managed = store.root / ('art_' + uuid4().hex + '.webp')
    managed.write_bytes(b'old')
    old = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    os.utime(managed, (old, old))
    cutoff = datetime.now(timezone.utc)-timedelta(hours=24)
    assert store.list_orphans({managed.resolve()}, older_than=cutoff) == []


def test_real_codex_image_runner_returns_persistent_codex_home_output(tmp_path, monkeypatch):
    """Mock only the CLI; verify the actual runner's image path after tempdir exit."""
    codex_home = tmp_path / 'codex-home'
    thread_id = str(uuid4())
    generated = codex_home / 'generated_images' / thread_id / 'one.png'
    generated.parent.mkdir(parents=True)
    Image.new('RGB', (12, 12), '#234567').save(generated)
    monkeypatch.setenv('CODEX_HOME', str(codex_home))

    class FakeStdin:
        def write(self, _data):
            pass
        async def drain(self):
            pass
        def close(self):
            pass

    class FakeProcess:
        pid = 12345
        returncode = 0
        stdin = FakeStdin()

    async def create_process(*_args, **_kwargs):
        return FakeProcess()

    async def communicate(_process, _seconds, _progress):
        return (json.dumps({'type': 'thread.started', 'thread_id': thread_id}).encode(), b'')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create_process)
    runner = CodexRunner([sys.executable], model='gpt-5.6-luna')
    monkeypatch.setattr(runner, '_communicate', communicate)
    result = asyncio.run(runner.run_image(prompt='one image', timeout_seconds=10))
    assert result.path == generated
    assert result.path.is_file()
    assert result.mime_type == 'image/png'
    assert not any(tmp_path.glob('codex-image-gateway-*'))
