"""Conservative local data retention (Module 7C).

Never delete a running/queued job's data. A preview does not mutate files/DB.
Automatic cleanup is opt-in; the authenticated manual endpoint requires a
confirmation string. Metadata and files are removed in a single serialized
storage operation as far as filesystem + SQLite permit (not a distributed
transaction: a failed unlink leaves its DB record for a later retry).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from threading import RLock
from typing import Any

from .artifact_store import ArtifactStore
from .job_store import JobStore

LOG = logging.getLogger('uvicorn.error')
TERMINAL = ('completed', 'failed')
# Hard work limits keep a manual operation short; a repeat preview/run can
# remove the next batch without blocking generation for a long period.
MAX_JOBS_PER_RUN = 250
MAX_ARTIFACTS_PER_RUN = 500
MAX_ORPHANS_PER_RUN = 250
MIN_ORPHAN_AGE = timedelta(hours=24)
MIN_QUOTA_ARTIFACT_AGE = timedelta(minutes=5)


class RetentionService:
    def __init__(self, store: JobStore, artifacts: ArtifactStore) -> None:
        self.store = store
        self.artifacts = artifacts
        self._lock = RLock()

    def inspect(self, settings: Any, *, now: datetime | None = None, include_orphans: bool = False) -> dict[str, Any]:
        """Read-only preview. Must not delete, update or migrate anything."""
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        history_cutoff = (instant - timedelta(days=settings.history_retention_days)).isoformat()
        artifact_cutoff = (instant - timedelta(days=settings.artifact_retention_days)).isoformat()
        quota_cutoff = (instant - timedelta(days=settings.quota_snapshot_retention_days)).isoformat()
        limit_bytes = settings.max_artifact_storage_mb * 1024 * 1024

        # Select whole expired jobs first. Artifact expiry/over-budget must not
        # accidentally include artifacts of these same jobs twice.
        jobs = self.store.retention_expired_jobs(history_cutoff, MAX_JOBS_PER_RUN + 1)
        jobs_truncated = len(jobs) > MAX_JOBS_PER_RUN
        jobs = jobs[:MAX_JOBS_PER_RUN]
        expiring_ids = {job['id'] for job in jobs}
        all_artifacts = self.store.retention_artifacts()
        eligible = [a for a in all_artifacts if a['status'] in TERMINAL]
        selected_job_artifacts = [a for a in eligible if a['job_id'] in expiring_ids]
        selected_artifacts = {a['id']: a for a in selected_job_artifacts}

        # For the artifact age policy, use artifact created_at, NOT job created_at.
        # An artifact may have been created after the job was queued.
        for artifact in eligible:
            if artifact['id'] not in selected_artifacts and artifact['created_at'] < artifact_cutoff:
                selected_artifacts[artifact['id']] = artifact

        known_bytes = sum(self.artifacts.record_file_bytes(a) for a in all_artifacts)
        actual_usage = self.artifacts.disk_usage()
        freed = sum(self.artifacts.record_file_bytes(a) for a in selected_artifacts.values())
        # Storage quota is a best-effort target, not permission to remove data
        # from active jobs. Delete the oldest completed/failed artifacts first.
        overage = max(0, actual_usage['bytes'] - limit_bytes - freed)
        if overage:
            for artifact in sorted(eligible, key=lambda a: (a['created_at'], a['id'])):
                if not overage:
                    break
                if artifact['id'] in selected_artifacts:
                    continue
                # Protect just-produced image files while FastAPI may still
                # be streaming a FileResponse to n8n.
                if artifact['created_at'] >= (instant - MIN_QUOTA_ARTIFACT_AGE).isoformat():
                    continue
                selected_artifacts[artifact['id']] = artifact
                size = self.artifacts.record_file_bytes(artifact)
                freed += size
                overage = max(0, overage - size)

        selected_artifacts = list(selected_artifacts.values())
        truncated_artifacts = len(selected_artifacts) > MAX_ARTIFACTS_PER_RUN
        selected_artifacts = selected_artifacts[:MAX_ARTIFACTS_PER_RUN]
        quota_count = self.store.retention_quota_count(quota_cutoff)
        orphan_files: list[Path] = []
        orphans_truncated = False
        if include_orphans:
            registered = self.store.retention_registered_files()
            orphan_files = self.artifacts.list_orphans(registered, older_than=instant - MIN_ORPHAN_AGE,
                                                      limit=MAX_ORPHANS_PER_RUN + 1)
            orphans_truncated = len(orphan_files) > MAX_ORPHANS_PER_RUN
            orphan_files = orphan_files[:MAX_ORPHANS_PER_RUN]

        return {
            'dry_run': True,
            'observed_at': instant.isoformat(),
            'automatic_cleanup_enabled': settings.auto_cleanup_enabled,
            'configured_limits': {
                'history_retention_days': settings.history_retention_days,
                'artifact_retention_days': settings.artifact_retention_days,
                'quota_snapshot_retention_days': settings.quota_snapshot_retention_days,
                'max_artifact_storage_mb': settings.max_artifact_storage_mb,
            },
            'storage': {'bytes': actual_usage['bytes'], 'file_count': actual_usage['file_count'],
                        'limit_bytes': limit_bytes, 'registered_file_bytes': known_bytes},
            'candidates': {'job_count': len(jobs), 'artifact_count': len(selected_artifacts),
                           'quota_snapshot_count': quota_count, 'orphan_file_count': len(orphan_files),
                           'estimated_artifact_bytes': sum(self.artifacts.record_file_bytes(a) for a in selected_artifacts),
                           'orphan_bytes': sum(f.stat().st_size for f in orphan_files if f.exists())},
            'truncated': jobs_truncated or truncated_artifacts or orphans_truncated,
            # Internal-only plan entries. These are deliberately never exposed
            # to the browser: they contain filesystem paths and job IDs.
            '_job_ids': [job['id'] for job in jobs],
            '_artifact_ids': [a['id'] for a in selected_artifacts],
            '_orphan_paths': orphan_files,
            '_quota_cutoff': quota_cutoff,
        }

    def preview(self, settings: Any, *, include_orphans: bool = False) -> dict[str, Any]:
        with self._lock:
            plan = self.inspect(settings, include_orphans=include_orphans)
            return {key: value for key, value in plan.items() if not key.startswith('_')}

    def run(self, settings: Any, *, include_orphans: bool = False) -> dict[str, Any]:
        """Re-plan immediately before destructive work; never trust old previews."""
        with self._lock:
            plan = self.inspect(settings, include_orphans=include_orphans)
            deleted_ids: list[str] = []
            counters = {'deleted_jobs': 0, 'deleted_artifacts': 0,
                        'deleted_quota_snapshots': 0, 'deleted_orphan_files': 0,
                        'skipped_files_or_jobs': 0}
            # Entire expired jobs: files first, DB row last (ON DELETE CASCADE).
            for job_id in plan['_job_ids']:
                ok, removed = self.store.retention_delete_job(job_id, self.artifacts)
                counters['deleted_jobs'] += int(ok)
                if ok:
                    deleted_ids.append(job_id)
                counters['deleted_artifacts'] += removed
                counters['skipped_files_or_jobs'] += int(not ok)
            # Artifacts selected for expiry/quota. Job rows and telemetry remain.
            for artifact_id in plan['_artifact_ids']:
                ok = self.store.retention_delete_artifact(artifact_id, self.artifacts)
                counters['deleted_artifacts'] += int(ok)
                if not ok and self.store.get_artifact(artifact_id):
                    counters['skipped_files_or_jobs'] += 1
            counters['deleted_quota_snapshots'] = self.store.retention_delete_quota(plan['_quota_cutoff'])
            # Orphan cleanup is explicit opt-in for each manual execution.
            if include_orphans:
                for path in plan['_orphan_paths']:
                    # Verify again immediately before unlinking; a new request
                    # could have registered the file since the preview.
                    if path.resolve(strict=False) in self.store.retention_registered_files():
                        continue
                    try:
                        self.artifacts.remove_orphan(path)
                        counters['deleted_orphan_files'] += 1
                    except (OSError, ValueError):
                        counters['skipped_files_or_jobs'] += 1
            LOG.warning('retention_cleanup_complete jobs=%s artifacts=%s quota_snapshots=%s skipped=%s',
                        counters['deleted_jobs'], counters['deleted_artifacts'],
                        counters['deleted_quota_snapshots'], counters['skipped_files_or_jobs'])
            return {'dry_run': False, 'result': counters, 'preview_before_cleanup': {
                k: v for k, v in plan.items() if not k.startswith('_')},
                'storage_after': self.artifacts.disk_usage(),
                '_deleted_job_ids': deleted_ids}
