"""9C.2: read-only bounded inventory, disjoint from destructive retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys

from fastapi.testclient import TestClient
from PIL import Image

from gateway import app as root
from gateway.artifact_inventory import inspect_storage_inventory
from gateway.artifact_store import ArtifactStore
from gateway.job_store import JobStore

TOKEN = 'inventory-token-at-least-24-characters'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def make_store(tmp_path):
    db = JobStore(tmp_path / 'db.sqlite3')
    files = ArtifactStore(tmp_path / 'artifacts')
    image = tmp_path / 'original.png'
    Image.new('RGB', (32, 32), 'blue').save(image)
    when = datetime.now(timezone.utc).isoformat()
    db.save_job(dict(id='inventory-job', request_id='inventory-job', task='image',
                     operation='image', model='gpt-5.6-luna', status='completed',
                     stage='Image ready', created_at=when, reference_count=0, elapsed_ms=0))
    art = files.create(job_id='inventory-job', source=image, mime_type='image/png')
    db.save_artifact(art)
    return db, files, art


def put(files, name, *, minutes_old=1, data=b'example'):
    dest = files.root / name
    dest.write_bytes(data)
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_old)).timestamp()
    os.utime(dest, (stamp, stamp))
    return dest


def test_registered_original_and_thumbnail_are_not_unregistered(tmp_path):
    db, files, art = make_store(tmp_path)
    try:
        r = inspect_storage_inventory(db, files)
        c = r['categories']
        assert c['registered_original']['count'] == 1
        assert c['registered_thumbnail']['count'] == 1
        assert r['summary']['recent_managed_unregistered'] == 0
        assert r['summary']['eligible_managed_unregistered'] == 0
        assert r['read_only'] is True
        assert Path(art['storage_path']).exists()
    finally: db.close()


def test_fifty_recent_managed_files_split_originals_and_thumbnails(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        for i in range(25):
            put(files, f'art_{i:032x}.png', minutes_old=30)
            put(files, f'art_{i:032x}_thumb.webp', minutes_old=30)
        r = inspect_storage_inventory(db, files)
        c = r['categories']
        assert r['summary']['recent_managed_unregistered'] == 50
        assert r['summary']['eligible_managed_unregistered'] == 0
        assert r['summary']['recent_managed_unregistered_bytes'] == 50 * len(b'example')
        assert c['unregistered_original']['count'] == 25
        assert c['unregistered_thumbnail']['count'] == 25
        assert c['unregistered_original']['ages']['5_minutes_to_1_hour'] == 25
    finally: db.close()


def test_age_buckets_and_eligible_24_hour_boundary(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        for i, minutes in enumerate((1, 15, 120, 1500)):
            put(files, f'art_{i:032x}.png', minutes_old=minutes)
        c = inspect_storage_inventory(db, files)['categories']['unregistered_original']
        assert c['ages'] == {
            'under_5_minutes': 1, '5_minutes_to_1_hour': 1,
            '1_to_24_hours': 1, 'older_than_24_hours': 1}
    finally: db.close()


def test_staging_unknown_and_subdirectories_not_cleanup_candidates(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        put(files, f'.art_{"a" * 32}.png.{"b" * 32}.staging', minutes_old=3000)
        put(files, 'local-note.txt', minutes_old=3000)
        (files.root / 'nested').mkdir()
        r = inspect_storage_inventory(db, files)
        assert r['categories']['staging']['count'] == 1
        assert r['categories']['other_regular']['count'] == 1
        assert r['categories']['directory']['count'] == 1
        assert r['summary']['eligible_managed_unregistered'] == 0
    finally: db.close()


def test_symlinks_are_not_followed_and_names_not_returned(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        outside = tmp_path / 'secret-external.txt'
        outside.write_text('never return this secret!')
        alias = files.root / 'secret-link.txt'
        try: alias.symlink_to(outside)
        except (OSError, NotImplementedError): return
        r = inspect_storage_inventory(db, files)
        assert r['categories']['symlink']['count'] == 1
        assert r['summary']['eligible_managed_unregistered'] == 0
        text = json.dumps(r)
        for secret in ('secret-external.txt', 'secret-link.txt', 'never return this secret!', str(tmp_path)):
            assert secret not in text
    finally: db.close()


def test_scan_bound_directory_counts_partial_only(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        for i in range(4): put(files, f'art_{i:032x}.png', minutes_old=90)
        with patch('gateway.artifact_inventory.MAX_DIRECTORY_ENTRIES', 2):
            r = inspect_storage_inventory(db, files)
        assert r['scan']['directory_complete'] is False
        assert r['scan']['complete'] is False
        assert r['summary']['recent_managed_unregistered'] is None
        assert r['scan']['scanned_directory_entries'] == 2
    finally: db.close()


def test_truncated_database_never_calls_unseen_rows_orphans(tmp_path):
    db, files, art = make_store(tmp_path)
    try:
        another = files.create(job_id='inventory-job', source=tmp_path/'original.png', mime_type='image/png')
        db.save_artifact(another)
        with patch('gateway.artifact_inventory.MAX_RECORDS', 1):
            r = inspect_storage_inventory(db, files)
        assert r['scan']['registered_complete'] is False
        assert r['summary']['eligible_managed_unregistered'] is None
        assert r['categories']['unknown_ownership']['count'] >= 1
    finally: db.close()


def test_no_mutation_and_no_cleanup_side_effect(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        old = put(files, f'art_{"f" * 32}.png', minutes_old=4000)
        before = [(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in files.root.iterdir()]
        r = inspect_storage_inventory(db, files)
        assert r['summary']['eligible_managed_unregistered'] == 1
        assert old.exists()
        assert [(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                for p in files.root.iterdir()] == before
        assert db.get_artifact('no-record-here') is None
    finally: db.close()


def test_authenticated_api_and_safe_metadata(tmp_path):
    db, files, _ = make_store(tmp_path)
    try:
        put(files, f'art_{"a" * 32}_thumb.webp')
        app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                       quota_monitor_enabled=False),
                              runner=object(), job_store=db, artifact_store=files)
        client = TestClient(app)
        assert client.get('/dashboard/api/storage/inventory').status_code == 401
        response = client.get('/dashboard/api/storage/inventory', headers=AUTH)
        assert response.status_code == 200
        assert response.json()['summary']['recent_managed_unregistered'] == 1
        assert str(tmp_path) not in response.text
        assert 'storage_path' not in response.text
        assert 'art_' not in response.text
        assert client.post('/dashboard/api/storage/inventory', headers=AUTH).status_code == 405
    finally: db.close()


def test_frontend_has_separate_readonly_button_and_cache_bust():
    base = Path(__file__).resolve().parents[1] / 'gateway'
    html = (base/'dashboard.html').read_text()
    js = (base/'dashboard/settings.js').read_text()
    routes = (base/'retention_routes.py').read_text()
    assert 'id="settingsInventoryRefresh"' in html
    assert 'id="inventoryRows"' in html
    assert 'getStorageInventory' in js
    assert 'refreshStorageInventory' in js
    assert '@router.get(\'/dashboard/api/storage/inventory\'' in routes
    assert 'DELETE_EXPIRED_DATA' not in (base/'artifact_inventory.py').read_text()
    assert '9c2-20260920' in html
