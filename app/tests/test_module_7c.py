"""Module 7C — conservative history/artifact retention and protected API."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gateway import app as root
from gateway.artifact_store import ArtifactStore
from gateway.config import MODELS, Settings
from gateway.job_store import JobStore
from gateway.retention import RetentionService
from gateway.settings_manager import SettingsManager, SettingsValidationError, apply_saved_settings

TOKEN = 'gateway-7c-safe-token-at-least-24-characters'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


def hours_ago(n):
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()


def make_job(db, id, *, status='completed', hours=120):
    db.save_job({'id': id, 'request_id': id, 'task': 'image', 'model': 'luna',
                 'status': status, 'stage': 'Completed', 'created_at': hours_ago(hours),
                 'completed_at': hours_ago(hours - 1) if status in {'failed', 'completed'} else None,
                 'reference_count': 0, 'elapsed_ms': 20, 'artifact_id': None})
    db.add_event(id, hours_ago(hours), 20, 'Stage')
    db.save_usage(id, {'input_tokens': 12, 'output_tokens': 2})
    db.save_prompt(id, 'private prompt')


def add_artifact(db, artifacts, tmp_path, id, *, hours=72):
    src = tmp_path / f'{id}.png'
    Image.new('RGB', (80, 80), '#335577').save(src)
    record = artifacts.create(job_id=id, source=src, mime_type='image/png')
    record['created_at'] = hours_ago(hours)
    db.save_artifact(record)
    return record


def setup(tmp_path, **overrides):
    db = JobStore(tmp_path / 'db.sqlite3')
    artifacts = ArtifactStore(tmp_path / 'artifacts')
    settings = replace(Settings(api_token=TOKEN, codex_exe=sys.executable,
                 quota_monitor_enabled=False), **overrides)
    return db, artifacts, RetentionService(db, artifacts), settings


def test_defaults_disable_automatic_cleanup_and_persist_validated(tmp_path):
    mgr = SettingsManager(tmp_path / 'config.json', MODELS)
    active = Settings(api_token=TOKEN, codex_exe=sys.executable)
    assert active.auto_cleanup_enabled is False
    assert active.artifact_retention_days == 30
    assert active.history_retention_days == 90
    before = mgr.update({'artifact_retention_days': 5, 'max_artifact_storage_mb': 100})
    assert before['max_artifact_storage_mb'] == 100
    for key, bad in [('max_artifact_storage_mb', 0), ('artifact_retention_days', 0),
                     ('auto_cleanup_enabled', 'false'), ('cleanup_interval_hours', 0)]:
        with pytest.raises(SettingsValidationError):
            mgr.update({key: bad})
    assert mgr.load() == before
    assert mgr.describe(active, active.model, environ={})['pending_restart'] is True
    effective = apply_saved_settings(active, before, MODELS, environ={})
    assert effective.artifact_retention_days == 5 and effective.max_artifact_storage_mb == 100


def test_dry_run_has_no_side_effects_and_excludes_active(tmp_path):
    db, artifacts, service, config = setup(tmp_path, history_retention_days=1, artifact_retention_days=1)
    make_job(db, 'old', hours=96)
    old = add_artifact(db, artifacts, tmp_path, 'old', hours=72)
    make_job(db, 'active', status='running', hours=96)
    active = add_artifact(db, artifacts, tmp_path, 'active', hours=72)
    preview = service.preview(config)
    assert preview['dry_run'] and preview['candidates']['job_count'] == 1
    assert preview['candidates']['artifact_count'] == 1
    assert db.get_job('old') and db.get_artifact(old['id'])
    assert Path(old['storage_path']).exists()
    assert db.get_artifact(active['id'])
    assert 'storage_path' not in str(preview)
    assert str(tmp_path) not in str(preview)


def test_cleanup_cascades_expired_jobs_and_retains_active(tmp_path):
    db, artifacts, service, config = setup(tmp_path, history_retention_days=1, artifact_retention_days=1)
    make_job(db, 'old', hours=96)
    old = add_artifact(db, artifacts, tmp_path, 'old', hours=72)
    make_job(db, 'active', status='running', hours=96)
    active = add_artifact(db, artifacts, tmp_path, 'active', hours=72)
    result = service.run(config)
    assert result['result']['deleted_jobs'] == 1
    assert not db.get_job('old') and not db.get_artifact(old['id'])
    assert db.get_usage('old') is None and db.get_content('old') is None
    assert db.get_events('old') == []
    assert not Path(old['storage_path']).exists()
    assert not Path(old['thumbnail_path']).exists()
    assert db.get_job('active') and db.get_artifact(active['id'])
    assert Path(active['storage_path']).exists()


def test_artifact_expiry_preserves_job_and_token_metadata(tmp_path):
    db, artifacts, service, config = setup(tmp_path, history_retention_days=90, artifact_retention_days=1)
    make_job(db, 'older', hours=72)
    rec = add_artifact(db, artifacts, tmp_path, 'older', hours=72)
    db._db.execute('UPDATE jobs SET artifact_id=? WHERE id=?', (rec['id'], 'older'))
    db._db.commit()
    result = service.run(config)
    assert result['result']['deleted_jobs'] == 0
    assert result['result']['deleted_artifacts'] == 1
    assert db.get_job('older')['artifact_id'] is None
    assert db.get_artifact(rec['id']) is None
    assert db.get_usage('older')['input_tokens'] == 12
    assert db.get_content('older')['prompt_text'] == 'private prompt'


def test_invalid_path_is_never_unlinked_or_deleted_from_db(tmp_path):
    db, artifacts, service, config = setup(tmp_path, history_retention_days=1)
    make_job(db, 'old', hours=96)
    outside = tmp_path / 'important.png'
    outside.write_bytes(b'KEEP')
    db.save_artifact({'id': 'art_' + '1'*32, 'job_id': 'old', 'artifact_type': 'image',
                      'mime_type': 'image/png', 'size_bytes': 4, 'created_at': hours_ago(96),
                      'storage_path': str(outside), 'thumbnail_path': None})
    result = service.run(config)
    assert result['result']['deleted_jobs'] == 0
    assert result['result']['skipped_files_or_jobs'] > 0
    assert outside.read_bytes() == b'KEEP'
    assert db.get_job('old')


def test_quota_avoids_active_files_and_can_leave_overage(tmp_path):
    db, artifacts, service, config = setup(tmp_path, artifact_retention_days=365, max_artifact_storage_mb=1)
    make_job(db, 'old', hours=48)
    completed = add_artifact(db, artifacts, tmp_path, 'old', hours=48)
    make_job(db, 'active', status='running', hours=48)
    active = add_artifact(db, artifacts, tmp_path, 'active', hours=48)
    # Add real files to exceed 1 MB; only the terminal job can be reclaimed.
    Path(completed['storage_path']).write_bytes(b'a' * (2 * 1024 * 1024))
    Path(active['storage_path']).write_bytes(b'b' * (2 * 1024 * 1024))
    preview = service.preview(config)
    assert preview['candidates']['artifact_count'] == 1
    result = service.run(config)
    assert result['result']['deleted_artifacts'] == 1
    assert db.get_artifact(active['id']) and Path(active['storage_path']).exists()
    assert result['storage_after']['bytes'] > 1024 * 1024


def test_orphans_are_explicit_and_one_day_old(tmp_path):
    db, artifacts, service, config = setup(tmp_path)
    orphan = artifacts.root / ('art_' + 'f'*32 + '.webp')
    orphan.write_bytes(b'OLD')
    import os
    old = datetime.now(timezone.utc).timestamp() - 3 * 86400
    os.utime(orphan, (old, old))
    young = artifacts.root / ('art_' + 'e'*32 + '.png')
    young.write_bytes(b'YOUNG')
    assert service.preview(config)['candidates']['orphan_file_count'] == 0
    assert service.preview(config, include_orphans=True)['candidates']['orphan_file_count'] == 1
    result = service.run(config, include_orphans=False)
    assert result['result']['deleted_orphan_files'] == 0 and orphan.exists()
    result = service.run(config, include_orphans=True)
    assert result['result']['deleted_orphan_files'] == 1
    assert not orphan.exists() and young.exists()


def test_manual_endpoints_auth_confirmation_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(root, 'SETTINGS_PATH', tmp_path / 'settings.json')
    app = root.create_app(settings=Settings(api_token=TOKEN, codex_exe=sys.executable,
                                          quota_monitor_enabled=False), runner=object(),
                          job_store=JobStore(tmp_path / 'db.sqlite3'),
                          artifact_store=ArtifactStore(tmp_path / 'artifacts'))
    db = app.state.job_store
    make_job(db, 'old', hours=96)
    with TestClient(app) as client:
        assert client.get('/dashboard/api/storage/preview').status_code == 401
        assert client.post('/dashboard/api/storage/cleanup', json={}).status_code == 401
        p = client.get('/dashboard/api/storage/preview', headers=HEADERS)
        assert p.status_code == 200 and p.json()['dry_run'] is True
        assert client.post('/dashboard/api/storage/cleanup', headers=HEADERS,
                           json={'confirmation': 'DELETE_EXPIRED_DATA'}).status_code == 422
        assert client.post('/dashboard/api/storage/cleanup', headers=HEADERS,
                           json={'confirmation': 'DELETE_EXPIRED_DATA', 'include_orphans': 'false'}).status_code == 422
        assert client.post('/dashboard/api/storage/cleanup', headers=HEADERS,
                           json={'confirmation': 'DELETE_EXPIRED_DATA', 'include_orphans': False}).status_code == 200
        assert db.get_job('old')  # default 90-day retention: no deletion
        assert client.get('/dashboard/api/settings', headers=HEADERS).json()['fields']['auto_cleanup_enabled']['value'] is False


def test_failed_file_delete_keeps_metadata(tmp_path, monkeypatch):
    db, artifacts, service, config = setup(tmp_path, history_retention_days=1)
    make_job(db, 'old', hours=96)
    rec = add_artifact(db, artifacts, tmp_path, 'old', hours=72)
    def fail(record):
        raise PermissionError('locked')
    monkeypatch.setattr(artifacts, 'delete_for_retention', fail)
    result = service.run(config)
    assert result['result']['skipped_files_or_jobs'] > 0
    assert db.get_job('old') and db.get_artifact(rec['id'])


def test_quota_history_cleanup_isolated(tmp_path):
    db, artifacts, service, config = setup(tmp_path, quota_snapshot_retention_days=1)
    db.save_quota_snapshot({'observed_at': hours_ago(72), 'status': 'healthy'})
    db.save_quota_snapshot({'observed_at': hours_ago(1), 'status': 'healthy'})
    assert service.preview(config)['candidates']['quota_snapshot_count'] == 1
    assert len(db.list_quota_snapshots()) == 2
    assert service.run(config)['result']['deleted_quota_snapshots'] == 1
    assert len(db.list_quota_snapshots()) == 1


def test_manual_cleanup_prunes_hot_cache_and_keeps_original_api(tmp_path, monkeypatch):
    monkeypatch.setattr(root, 'SETTINGS_PATH', tmp_path / 'settings.json')
    config = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False,
                      history_retention_days=1, artifact_retention_days=365)
    api = root.create_app(settings=config, runner=object(),
                          job_store=JobStore(tmp_path / 'data.sqlite3'),
                          artifact_store=ArtifactStore(tmp_path / 'images'))
    db = api.state.job_store
    old = api.state.history.add('old', 'image', 'luna')
    old['status'] = 'completed'
    old['created_at'] = hours_ago(100)
    old['completed_at'] = hours_ago(96)
    db.save_job(old)
    with TestClient(api) as client:
        response = client.post('/dashboard/api/storage/cleanup', headers=HEADERS,
                               json={'confirmation': 'DELETE_EXPIRED_DATA', 'include_orphans': False})
        assert response.status_code == 200
        assert response.json()['result']['deleted_jobs'] == 1
        assert '_deleted_job_ids' not in response.text
        assert client.get('/v1/jobs', headers=HEADERS).json()['jobs'] == []
        assert db.get_job(old['id']) is None
        assert client.get('/v1/models', headers=HEADERS).status_code == 200


def test_retention_does_not_remove_recent_image_during_streaming_window(tmp_path):
    db, artifacts, service, config = setup(tmp_path, artifact_retention_days=365,
                                           max_artifact_storage_mb=1)
    make_job(db, 'recent', hours=2)
    image = add_artifact(db, artifacts, tmp_path, 'recent', hours=0)
    Path(image['storage_path']).write_bytes(b'A' * (2 * 1024 * 1024))
    preview = service.preview(config)
    assert preview['candidates']['artifact_count'] == 0
    assert service.run(config)['result']['deleted_artifacts'] == 0
    assert Path(image['storage_path']).exists()
