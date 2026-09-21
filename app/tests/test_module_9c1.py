"""9C.1: read-only artifact health and precise, non-speculative statuses."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from gateway import app as root
from gateway.artifact_health import inspect_artifact_health, file_health, public_availability
from gateway.artifact_store import ArtifactStore
from gateway.job_store import JobStore

TOKEN = 'artifact-health-test-token-at-least-24'
AUTH = {'Authorization': 'Bearer ' + TOKEN}


def setup(tmp_path):
    db = JobStore(tmp_path / 'database.sqlite3')
    files = ArtifactStore(tmp_path / 'artifacts')
    src = tmp_path / 'original.png'
    Image.new('RGB', (70, 70), '#3672ae').save(src)
    job_id = 'artifact-health-job'
    db.save_job(dict(id=job_id, request_id='scene_artifact_health',
                     task='image', operation='image', model='gpt-5.6-luna',
                     status='completed', stage='Image ready',
                     created_at=datetime.now(timezone.utc).isoformat(),
                     reference_count=0, elapsed_ms=0))
    artifact = files.create(job_id=job_id, source=src, mime_type='image/png')
    db.save_artifact(artifact)
    db.save_job(dict(id=job_id, request_id='scene_artifact_health',
                     task='image', operation='image', model='gpt-5.6-luna',
                     status='completed', stage='Image ready',
                     created_at=datetime.now(timezone.utc).isoformat(),
                     artifact_id=artifact['id'], reference_count=0, elapsed_ms=0))
    return db, files, artifact


def test_all_files_present_do_not_report_false_missing_or_orphans(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        report = inspect_artifact_health(db, files)
        assert report['read_only'] is True
        assert report['records']['total'] == 1
        assert report['records']['originals_available'] == 1
        assert report['records']['thumbnails_available'] == 1
        assert report['records']['originals_missing'] == 0
        assert report['unregistered']['eligible_files'] == 0
        assert report['samples'] == []
        assert Path(art['storage_path']).exists()
    finally: db.close()


def test_missing_original_is_reported_but_not_deleted_or_mislabelled_expired(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        Path(art['storage_path']).unlink()
        report = inspect_artifact_health(db, files)
        assert report['records']['originals_missing'] == 1
        assert report['samples'][0] == {'artifact_id': art['id'], 'job_id': art['job_id'],
                                        'file': 'original', 'status': 'missing'}
        assert db.get_artifact(art['id']) is not None
        assert Path(art['thumbnail_path']).exists()
        assert 'expired without deletion history' in report['note']
    finally: db.close()


def test_optional_thumbnails_are_separated_from_missing_thumbnail_files(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        Path(art['thumbnail_path']).unlink()
        missing = inspect_artifact_health(db, files)
        assert missing['records']['thumbnails_missing'] == 1
        db._db.execute('UPDATE artifacts SET thumbnail_path=NULL WHERE id=?', (art['id'],))
        db._db.commit()
        absent = inspect_artifact_health(db, files)
        assert absent['records']['thumbnails_not_created'] == 1
        assert absent['records']['thumbnails_missing'] == 0
    finally: db.close()


def test_orphan_scan_ignores_staging_recent_and_nonmanaged_names(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        def put(name, age):
            path = files.root / name
            path.write_bytes(b'orphan')
            stamped = (now - age).timestamp()
            os.utime(path, (stamped, stamped))
            return path
        old = put('art_' + 'b'*32 + '.png', timedelta(days=2))
        put('art_' + 'c'*32 + '.png', timedelta(minutes=2))
        put('.art_' + 'd'*32 + '.png.staging', timedelta(days=2))
        put('private-key.txt', timedelta(days=2))
        before = sorted(p.name for p in files.root.iterdir())
        snapshot = inspect_artifact_health(db, files, now=now)
        assert snapshot['unregistered']['eligible_files'] == 1
        assert snapshot['unregistered']['eligible_bytes'] == len(b'orphan')
        assert snapshot['unregistered']['recent_unregistered_files'] == 1
        assert sorted(p.name for p in files.root.iterdir()) == before
        assert old.exists()
    finally: db.close()


def test_db_scan_limit_does_not_falsely_label_recorded_files_orphans(tmp_path):
    db, files, first = setup(tmp_path)
    try:
        src = tmp_path / 'source2.png'
        Image.new('RGB', (9, 9)).save(src)
        other = files.create(job_id=first['job_id'], source=src, mime_type='image/png')
        db.save_artifact(other)
        with patch('gateway.artifact_health.MAX_RECORDS', 1):
            report = inspect_artifact_health(db, files)
        assert report['scan']['records_complete'] is False
        assert report['unregistered']['eligible_files'] is None
        assert report['unregistered']['scan_complete'] is False
    finally: db.close()


def test_directory_limit_reports_unknown_counts_instead_of_partial_as_full(tmp_path):
    db, files, _ = setup(tmp_path)
    try:
        with patch('gateway.artifact_health.MAX_DIRECTORY_ENTRIES', 1):
            report = inspect_artifact_health(db, files)
        assert not report['scan']['directory_complete']
        assert report['unregistered']['eligible_files'] is None
    finally: db.close()


def test_unsafe_paths_and_symlinks_never_followed(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        assert file_health(files, str(tmp_path / 'outside.png')) == 'unsafe_path'
        assert public_availability(files, dict(storage_path=str(tmp_path / 'outside.png')))["original"] == 'unsafe_path'
        alias = files.root / ('art_' + 'f'*32 + '.png')
        try: alias.symlink_to(Path(art['storage_path']))
        except (OSError, NotImplementedError): return  # Windows without Developer Mode
        assert file_health(files, str(alias)) == 'unsafe_path'
    finally: db.close()


def test_authenticated_api_exposes_no_storage_paths_and_no_destructive_operations(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                       quota_monitor_enabled=False),
                              runner=object(), job_store=db, artifact_store=files)
        client = TestClient(app)
        assert client.get('/dashboard/api/storage/health').status_code == 401
        before = Path(art['storage_path']).read_bytes()
        response = client.get('/dashboard/api/storage/health', headers=AUTH)
        assert response.status_code == 200
        assert response.json()['read_only'] is True
        assert str(tmp_path) not in response.text
        assert 'storage_path' not in response.text
        assert Path(art['storage_path']).read_bytes() == before
    finally: db.close()


def test_job_gallery_and_references_use_explicit_artifact_availability(tmp_path):
    db, files, art = setup(tmp_path)
    try:
        app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                       quota_monitor_enabled=False),
                              runner=object(), job_store=db, artifact_store=files)
        client = TestClient(app)
        job = client.get('/dashboard/api/jobs/artifact-health-job', headers=AUTH).json()['job']
        assert job['artifact']['availability']['original'] == 'available'
        gallery = client.get('/dashboard/api/gallery', headers=AUTH).json()['items']
        assert gallery[0]['artifact']['availability']['original'] == 'available'
        Path(art['storage_path']).unlink()
        missing = client.get('/dashboard/api/jobs/artifact-health-job', headers=AUTH).json()['job']
        assert missing['artifact']['availability']['original'] == 'missing'
        assert client.get('/dashboard/api/artifacts/' + art['id'], headers=AUTH).status_code == 404
        assert str(tmp_path) not in str(missing)
    finally: db.close()


def test_frontend_explains_findings_and_keeps_deletion_separate():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / 'gateway'
    html = (base / 'dashboard.html').read_text('utf-8')
    settings = (base / 'dashboard/settings.js').read_text('utf-8')
    routes = (base / 'retention_routes.py').read_text('utf-8')
    assert 'id="settingsHealthRefresh"' in html
    assert 'id="healthOriginals"' in html
    assert 'id="healthOrphans"' in html
    assert 'getStorageHealth' in settings
    assert 'settingsHealthRefresh' in settings
    assert "@router.get('/dashboard/api/storage/health'" in routes
    assert "'DELETE_EXPIRED_DATA'" in routes
