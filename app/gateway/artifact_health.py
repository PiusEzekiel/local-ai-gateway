"""Read-only reconciliation between SQLite artifact rows and managed files.

The health snapshot intentionally does NOT repair records, read source URLs,
walk Codex-owned directories, or expose host filesystem paths to the browser.
This is an observed, bounded scan, not an atomic SQLite/filesystem snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore
from .job_store import JobStore

MAX_RECORDS = 5_000
MAX_DIRECTORY_ENTRIES = 10_000
MAX_SAMPLES = 8
ORPHAN_GRACE = timedelta(hours=24)


def file_health(artifacts: ArtifactStore, value: str | None) -> str:
    """Return an explicit status without trusting DB-supplied filesystem paths."""
    if not value:
        return 'not_created'
    path = artifacts._managed_path(value)
    if path is None:
        return 'unsafe_path'
    try:
        stat = path.stat()
        if not path.is_file():
            return 'missing'
        return 'empty' if stat.st_size == 0 else 'available'
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'unreadable'


def public_availability(artifacts: ArtifactStore | None, record: dict[str, Any]) -> dict[str, str]:
    if not artifacts:
        return {'original': 'unknown', 'thumbnail': 'unknown'}
    return {
        'original': file_health(artifacts, record.get('storage_path')),
        'thumbnail': file_health(artifacts, record.get('thumbnail_path')),
    }


def inspect_artifact_health(store: JobStore, artifacts: ArtifactStore, *,
                            now: datetime | None = None) -> dict[str, Any]:
    """Bounded metadata-only scan. Never delete, modify, or expose file paths."""
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows, total = store.artifact_health_records(MAX_RECORDS)
    truncated_records = len(rows) < total
    records = {
        'scanned': len(rows), 'total': total, 'originals_available': 0,
        'originals_missing': 0, 'originals_unsafe': 0, 'originals_unreadable': 0,
        'originals_empty': 0, 'thumbnails_available': 0,
        'thumbnails_missing': 0, 'thumbnails_not_created': 0,
        'thumbnails_unsafe': 0, 'thumbnails_unreadable': 0,
    }
    samples: list[dict[str, str]] = []
    # Registered set is complete only if we read all rows; never label registered
    # files as orphans when scanning a truncated database inventory.
    registered: set[Path] = set()
    for row in rows:
        original = file_health(artifacts, row.get('storage_path'))
        thumbnail = file_health(artifacts, row.get('thumbnail_path'))
        originals = {'available': 'originals_available', 'missing': 'originals_missing',
                     'unsafe_path': 'originals_unsafe', 'unreadable': 'originals_unreadable',
                     'empty': 'originals_empty'}
        thumbnails = {'available': 'thumbnails_available', 'missing': 'thumbnails_missing',
                      'not_created': 'thumbnails_not_created', 'unsafe_path': 'thumbnails_unsafe',
                      'unreadable': 'thumbnails_unreadable', 'empty': 'thumbnails_missing'}
        records[originals.get(original, 'originals_missing')] += 1
        records[thumbnails[thumbnail]] += 1
        if original != 'available' and len(samples) < MAX_SAMPLES:
            samples.append({'artifact_id': row['id'], 'job_id': row['job_id'],
                            'file': 'original', 'status': original})
        elif thumbnail not in ('available', 'not_created') and len(samples) < MAX_SAMPLES:
            samples.append({'artifact_id': row['id'], 'job_id': row['job_id'],
                            'file': 'thumbnail', 'status': thumbnail})
        for field in ('storage_path', 'thumbnail_path'):
            value = row.get(field)
            if value:
                path = artifacts._managed_path(value)
                if path is not None:
                    registered.add(path)

    # Only safe opaque managed files qualify; in-flight staging files and
    # arbitrary files in the directory are excluded from orphan counts.
    orphan = {'eligible_files': None, 'eligible_bytes': None,
              'recent_unregistered_files': None, 'scan_complete': False}
    if not truncated_records:
        cutoff = instant - ORPHAN_GRACE
        count = bytes_total = recent = entries = 0
        truncated_files = False
        with artifacts._lock:
            for path in artifacts.root.iterdir():
                entries += 1
                if entries > MAX_DIRECTORY_ENTRIES:
                    truncated_files = True
                    break
                if path in registered or path.is_symlink():
                    continue
                if artifacts._managed_path(str(path)) is None:
                    continue
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                changed = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                if changed <= cutoff:
                    count += 1
                    bytes_total += stat.st_size
                else:
                    recent += 1
        if not truncated_files:
            orphan = {'eligible_files': count, 'eligible_bytes': bytes_total,
                      'recent_unregistered_files': recent, 'scan_complete': True}

    return {
        'observed_at': instant.isoformat(), 'read_only': True,
        'records': records, 'unregistered': orphan,
        'scan': {'records_complete': not truncated_records,
                 'directory_complete': orphan['scan_complete'],
                 'max_records': MAX_RECORDS, 'max_directory_entries': MAX_DIRECTORY_ENTRIES},
        'samples': samples,
        'note': ('Observational scan; generation and cleanup may change files during inspection. '
                 'Missing files cannot be classified as expired without deletion history. '
                 'The managed artifact limit does not cover Codex-owned image files.'),
    }
