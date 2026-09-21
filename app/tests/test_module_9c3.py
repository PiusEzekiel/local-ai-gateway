"""9C.3: read-only, truthful best-effort managed-storage limit accounting."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sys

from fastapi.testclient import TestClient
from PIL import Image

from gateway import app as gateway_app
from gateway.artifact_store import ArtifactStore
from gateway.config import Settings
from gateway.job_store import JobStore
from gateway.retention import RetentionService

TOKEN = '9c3-test-token-is-at-least-24-characters'
AUTH = {'Authorization': f'Bearer {TOKEN}'}
MiB = 1024 * 1024


def env(tmp_path, **changes):
    db = JobStore(tmp_path / 'data.sqlite3')
    artifacts = ArtifactStore(tmp_path / 'artifacts')
    config = replace(Settings(api_token=TOKEN, codex_exe=sys.executable,
                              quota_monitor_enabled=False),
                     max_artifact_storage_mb=1,
                     history_retention_days=365,
                     artifact_retention_days=365,
                     **changes)
    return db, artifacts, RetentionService(db, artifacts), config


def job(db, name, *, status='completed', hours=2):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    db.save_job({'id': name, 'request_id': name, 'task': 'image', 'operation': 'image',
                 'model': 'gpt-5.6-luna', 'status': status,
                 'stage': 'Image ready', 'created_at': created,
                 'completed_at': created if status in ('completed', 'failed') else None,
                 'reference_count': 0, 'elapsed_ms': 0})


def registered(db, files, tmp_path, name, *, hours=2):
    job(db, name, hours=hours)
    source = tmp_path / f'{name}.png'
    Image.new('RGB', (20, 20), '#345678').save(source)
    record = files.create(job_id=name, source=source, mime_type='image/png')
    record['created_at'] = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    db.save_artifact(record)
    return record


def extra(files, name, size, *, hours=0):
    path = files.root / name
    path.write_bytes(b'x' * size)
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_empty_directory_accounting(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        r = service.preview(config)['storage']
        assert r['bytes'] == r['registered_file_bytes'] == r['unregistered_or_other_bytes'] == 0
        assert r['over_limit_bytes'] == r['estimated_remaining_over_limit_bytes'] == 0
        assert r['estimated_post_cleanup_bytes'] == 0
        assert r['limit_is_strict'] is False
    finally: db.close()


def test_registered_original_and_thumbnail_counted_as_real_bytes(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        record = registered(db, files, tmp_path, 'r')
        assert record['thumbnail_path']
        storage = service.preview(config)['storage']
        assert storage['registered_file_bytes'] == (Path(record['storage_path']).stat().st_size +
                                                    Path(record['thumbnail_path']).stat().st_size)
        assert storage['registered_file_bytes'] == storage['bytes']
        assert storage['unregistered_or_other_bytes'] == 0
    finally: db.close()


def test_fifty_small_unregistered_files_are_measured_not_selected(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        record = registered(db, files, tmp_path, 'registered')
        for index in range(25):
            extra(files, f'art_{index:032x}.png', 161)
            extra(files, f'art_{index:032x}_thumb.webp', 80)
        result = service.preview(config)
        assert result['storage']['unregistered_or_other_bytes'] == 25 * (161 + 80)
        assert result['candidates']['orphan_file_count'] == 0
        assert result['storage']['bytes'] == (result['storage']['registered_file_bytes'] +
                                              25 * (161 + 80))
        assert Path(record['storage_path']).is_file()
    finally: db.close()


def test_unknown_files_and_staging_are_accounted_not_cleanup_candidates(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        extra(files, 'notes.txt', 150, hours=48)
        extra(files, f'.art_{"a" * 32}.png.{"b" * 32}.staging', 120, hours=48)
        r = service.preview(config, include_orphans=True)
        assert r['storage']['unregistered_or_other_bytes'] == 270
        assert r['candidates']['orphan_file_count'] == 0
        assert r['candidates']['orphan_bytes'] == 0
    finally: db.close()


def test_unknown_over_budget_cannot_be_promised_as_reclaimed(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        extra(files, 'unknown.bin', MiB + 42, hours=48)
        p = service.preview(config, include_orphans=True)
        s = p['storage']
        assert s['over_limit_bytes'] == 42
        assert s['estimated_post_cleanup_bytes'] == MiB + 42
        assert s['estimated_remaining_over_limit_bytes'] == 42
        assert p['candidates']['estimated_artifact_bytes'] == 0
        assert p['candidates']['orphan_bytes'] == 0
        assert p['candidates']['artifact_count'] == 0
    finally: db.close()


def test_active_job_is_not_selected_just_to_reach_cap(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        record = registered(db, files, tmp_path, 'active')
        job(db, 'active', status='running')
        Path(record['storage_path']).write_bytes(b'a' * (MiB + 100))
        p = service.preview(config)
        assert p['storage']['over_limit_bytes'] > 0
        assert p['storage']['estimated_remaining_over_limit_bytes'] > 0
        assert p['candidates']['artifact_count'] == 0
        assert Path(record['storage_path']).exists()
    finally: db.close()


def test_old_terminal_artifact_is_selected_and_projection_subtracts_it(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        record = registered(db, files, tmp_path, 'complete')
        Path(record['storage_path']).write_bytes(b'a' * (MiB + 20))
        p = service.preview(config)
        s = p['storage']
        assert p['candidates']['artifact_count'] == 1
        assert p['candidates']['estimated_artifact_bytes'] > MiB
        assert s['over_limit_bytes'] > 0
        assert s['estimated_remaining_over_limit_bytes'] == 0
        assert s['estimated_post_cleanup_bytes'] == 0
        assert Path(record['storage_path']).exists(), 'Preview must not delete the original'
    finally: db.close()


def test_old_managed_orphan_only_in_projection_when_opted_in(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        p = extra(files, f'art_{"f" * 32}.png', MiB + 14, hours=48)
        off = service.preview(config)
        on = service.preview(config, include_orphans=True)
        assert off['candidates']['orphan_bytes'] == 0
        assert off['storage']['estimated_remaining_over_limit_bytes'] == 14
        assert on['candidates']['orphan_file_count'] == 1
        assert on['candidates']['orphan_bytes'] == MiB + 14
        assert on['storage']['estimated_post_cleanup_bytes'] == 0
        assert p.exists()
    finally: db.close()


def test_recent_managed_orphan_not_selected_even_with_opt_in(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        path = extra(files, f'art_{"c" * 32}.png', MiB + 18, hours=1)
        on = service.preview(config, include_orphans=True)
        assert on['candidates']['orphan_file_count'] == 0
        assert on['storage']['estimated_remaining_over_limit_bytes'] == 18
        assert path.exists()
    finally: db.close()


def test_duplicate_database_paths_not_double_counted(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        record = registered(db, files, tmp_path, 'r')
        duplicate = dict(record, id='art_' + 'e' * 32)
        db.save_artifact(duplicate)
        s = service.preview(config)['storage']
        assert s['registered_file_bytes'] == s['bytes']
        assert s['unregistered_or_other_bytes'] == 0
    finally: db.close()


def test_authenticated_preview_has_no_paths_and_is_read_only(tmp_path):
    db, files, service, config = env(tmp_path)
    try:
        extra(files, 'unrecognized.txt', 25)
        app = gateway_app.create_app(settings=config, runner=object(), job_store=db, artifact_store=files)
        with TestClient(app) as client:
            assert client.get('/dashboard/api/storage/preview').status_code == 401
            result = client.get('/dashboard/api/storage/preview', headers=AUTH)
            assert result.status_code == 200
            assert result.json()['storage']['unregistered_or_other_bytes'] == 25
            assert result.json()['storage']['limit_is_strict'] is False
            assert str(tmp_path) not in result.text
            assert 'unrecognized.txt' not in result.text
            assert 'storage_path' not in result.text
            assert (files.root / 'unrecognized.txt').exists()
    finally: db.close()


def test_storage_accounting_ui_and_import_graph_are_versioned():
    base = Path(__file__).resolve().parents[1] / 'gateway'
    html = (base / 'dashboard.html').read_text()
    js = (base / 'dashboard/settings.js').read_text()
    assert all(f'id="{name}"' in html for name in ('storageUnregistered', 'storageOverage',
              'storageProjected', 'storageBudgetNote'))
    assert all(word in js for word in ('unregistered_or_other_bytes', 'estimated_post_cleanup_bytes',
              'estimated_remaining_over_limit_bytes', 'accounting_consistent'))
    assert '9c3-20260920' in html and '9c3-20260920' in js
