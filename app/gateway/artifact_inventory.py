"""9C.2: bounded, read-only metadata inventory of managed artifact storage.

Do not infer provenance from an unregistered filename, read file contents,
follow symlinks, return paths or filenames, or make anything cleanup eligible.
The separate retention service remains the sole owner of destructive cleanup.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from .artifact_health import MAX_DIRECTORY_ENTRIES, MAX_RECORDS, ORPHAN_GRACE
from .artifact_store import ArtifactStore
from .job_store import JobStore

MAX_EXAMPLES = 12
# These are *descriptive* file-name classifications, not deletion permissions.
STAGING_NAME = re.compile(
    r'\.art_[0-9a-f]{32}(?:_thumb)?\.(?:png|jpg|webp|bin)\.[0-9a-f]{32}\.staging'
)
AGE_BUCKETS = (
    ('under_5_minutes', timedelta(minutes=5)),
    ('5_minutes_to_1_hour', timedelta(hours=1)),
    ('1_to_24_hours', ORPHAN_GRACE),
)
CATEGORY_LABELS = {
    'registered_original': 'Registered originals',
    'registered_thumbnail': 'Registered thumbnails',
    'unregistered_original': 'Unregistered managed originals',
    'unregistered_thumbnail': 'Unregistered managed thumbnails',
    'unknown_ownership': 'Managed files with undetermined registration',
    'staging': 'Staging / interrupted import candidates',
    'other_regular': 'Unrecognized regular files',
    'symlink': 'Symlinks (not followed)',
    'directory': 'Subdirectories (not traversed)',
    'unreadable': 'Unreadable directory entries',
}


def _age_bucket(modified: datetime, now: datetime) -> str:
    age = now - modified
    for name, threshold in AGE_BUCKETS:
        if age < threshold:
            return name
    return 'older_than_24_hours'


def inspect_storage_inventory(store: JobStore, artifacts: ArtifactStore, *,
                              now: datetime | None = None) -> dict[str, Any]:
    """Bounded, observational scan; caller should run off the event loop.

    A truncated SQLite read must NEVER label an unrecognized managed file an
    orphan, because its registration row could be beyond our scan limit.
    """
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows, total = store.artifact_health_records(MAX_RECORDS)
    registered_complete = len(rows) == total
    originals: set[Path] = set()
    thumbnails: set[Path] = set()
    for row in rows:
        for field, paths in (('storage_path', originals), ('thumbnail_path', thumbnails)):
            value = row.get(field)
            path = artifacts._managed_path(value) if value else None
            if path is not None:
                paths.add(path)

    categories: dict[str, dict[str, Any]] = {
        key: {'label': label, 'count': 0, 'bytes': 0,
              'ages': {name: 0 for name in (
                  'under_5_minutes', '5_minutes_to_1_hour',
                  '1_to_24_hours', 'older_than_24_hours')}}
        for key, label in CATEGORY_LABELS.items()
    }
    examples: list[dict[str, Any]] = []
    count = 0
    bytes_total = 0
    truncated_directory = False
    # Unregistered candidates are defined ONLY for opaque managed names. The
    # other categories are never included in retention orphan selection.
    unregistered = {'recent': 0, 'eligible': 0, 'recent_bytes': 0,
                    'eligible_bytes': 0}
    with artifacts._lock:
        for path in artifacts.root.iterdir():
            if count >= MAX_DIRECTORY_ENTRIES:
                truncated_directory = True
                break
            count += 1
            category = 'unreadable'
            size = 0
            bucket = 'under_5_minutes'
            try:
                # lstat is intentional: a symlink must not be followed even to
                # obtain its size or modification time.
                info = path.lstat()
                modified = datetime.fromtimestamp(info.st_mtime, timezone.utc)
                bucket = _age_bucket(modified, instant)
                if path.is_symlink():
                    category = 'symlink'
                elif path.is_dir():
                    category = 'directory'
                elif not path.is_file():
                    category = 'unreadable'
                else:
                    size = info.st_size
                    # Registration checks go first: even a strange (legacy)
                    # registered filename should not be classed as unrecognized.
                    if path in originals:
                        category = 'registered_original'
                    elif path in thumbnails:
                        category = 'registered_thumbnail'
                    elif artifacts._managed_path(str(path)) is not None:
                        if registered_complete:
                            category = ('unregistered_thumbnail' if '_thumb.' in path.name
                                        else 'unregistered_original')
                        else:
                            category = 'unknown_ownership'
                    elif STAGING_NAME.fullmatch(path.name):
                        category = 'staging'
                    else:
                        category = 'other_regular'
            except OSError:
                category = 'unreadable'
                size = 0
            tally = categories[category]
            tally['count'] += 1
            tally['bytes'] += size
            tally['ages'][bucket] += 1
            bytes_total += size
            if category in ('unregistered_original', 'unregistered_thumbnail'):
                age_key = 'eligible' if bucket == 'older_than_24_hours' else 'recent'
                unregistered[age_key] += 1
                unregistered[age_key + '_bytes'] += size
            # Examples contain categorical metadata only: no path, name,
            # source URL, ID, prompt, filesystem path or token.
            if (category in ('unregistered_original', 'unregistered_thumbnail',
                             'staging', 'other_regular', 'symlink', 'unreadable')
                    and len(examples) < MAX_EXAMPLES):
                examples.append({'category': category, 'age': bucket,
                                 'size_bytes': size})

    complete = registered_complete and not truncated_directory
    return {
        'read_only': True, 'observed_at': instant.isoformat(),
        'scan': {'complete': complete, 'registered_complete': registered_complete,
                 'directory_complete': not truncated_directory,
                 'registered_records': len(rows), 'registered_total': total,
                 'scanned_directory_entries': count,
                 'max_records': MAX_RECORDS,
                 'max_directory_entries': MAX_DIRECTORY_ENTRIES},
        'summary': {'scanned_bytes': bytes_total, 'scanned_entries': count,
                    'recent_managed_unregistered': unregistered['recent'] if complete else None,
                    'recent_managed_unregistered_bytes': unregistered['recent_bytes'] if complete else None,
                    'eligible_managed_unregistered': unregistered['eligible'] if complete else None,
                    'eligible_managed_unregistered_bytes': unregistered['eligible_bytes'] if complete else None},
        'categories': categories, 'examples': examples,
        'note': ('Read-only directory metadata, not a frozen filesystem snapshot. '
                 'Unregistered does not prove that a file is unwanted; a running job '
                 'may still be registering it. Staging and unknown names are never '
                 'eligible for the gateway orphan cleanup. No file contents, names, '
                 'IDs or paths are included. Codex-owned images are outside this scan.'),
    }
