"""Persistent, opt-in cache for validated remote reference images.

Cache files are inputs, not Gallery artifacts.  They have their own directory
and records, while job_references continues to preserve per-job history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import os
import shutil
from threading import RLock
from uuid import uuid4
from contextvars import ContextVar
from contextlib import contextmanager
from collections import OrderedDict
from typing import Iterator

from .contracts import GatewayError, ImageReference
from .job_store import JobStore
from .reference_images import (
    detect_image_extension, MAX_REFERENCE_IMAGE_BYTES, _public_addresses,
    _validate_destination,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_key(reference: ImageReference) -> str:
    # The caller's ID is request-local and must not split identical URL sources.
    # Preserve the complete URL, including signed query parameters.
    return hashlib.sha256(reference.url.encode("utf-8")).hexdigest()


class ReferenceCache:
    def __init__(self, root: Path, job_store: JobStore, *, ttl_seconds: int,
                 retention_days: int = 7, max_bytes: int):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.job_store = job_store
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.retention_days = max(1, int(retention_days))
        self.max_bytes = max(1, int(max_bytes))
        self._lock = RLock()
        self._leases: dict[Path, int] = {}
        # Module 10A.2: process-lifetime, thread-safe observations, deliberately
        # separate from the persistent file cache and job/usage accounting.
        self._analytics_since = _now().isoformat()
        self._analytics: dict[str, int] = {
            "cache_hits": 0,
            "cache_misses": 0,
            "remote_downloads": 0,
            "remote_download_failures": 0,
            "downloaded_bytes": 0,
            "estimated_bytes_avoided": 0,
            "cache_write_failures": 0,
        }
        # Only hashed URL identities leave this subsystem; never expose signed
        # source URLs or arbitrary episode-local reference IDs in analytics.
        self._reference_activity: OrderedDict[str, dict[str, int]] = OrderedDict()
        self._reference_activity_limit = 128
        self._lease_scope: ContextVar[list[Path] | None] = ContextVar(
            "reference_cache_lease_scope", default=None,
        )

    @contextmanager
    def lease_scope(self) -> Iterator[None]:
        leases: list[Path] = []
        token = self._lease_scope.set(leases)
        try:
            yield
        finally:
            self._lease_scope.reset(token)
            self.release_all(leases)

    def _record_source(self, source_key: str, metric: str, amount: int = 1) -> None:
        # Caller owns _lock. Bound this process-only diagnostic map even when
        # a long-lived gateway sees unlimited episode-specific URLs.
        activity = self._reference_activity.get(source_key)
        if activity is None:
            if len(self._reference_activity) >= self._reference_activity_limit:
                self._reference_activity.popitem(last=False)
            activity = {"hits": 0, "misses": 0, "bytes_avoided": 0}
            self._reference_activity[source_key] = activity
        self._reference_activity.move_to_end(source_key)
        activity[metric] += amount

    def record_remote_download(self, size_bytes: int) -> None:
        """Record successful, validated remote retrieval (even if cache writing fails)."""
        with self._lock:
            self._analytics["remote_downloads"] += 1
            self._analytics["downloaded_bytes"] += max(0, int(size_bytes))

    def record_remote_download_failure(self) -> None:
        """Download failures do not count as successful remote downloads."""
        with self._lock:
            self._analytics["remote_download_failures"] += 1

    def record_cache_write_failure(self) -> None:
        with self._lock:
            self._analytics["cache_write_failures"] += 1

    def analytics(self) -> dict:
        """A snapshot since this gateway process started, not SQLite history."""
        with self._lock:
            numbers = dict(self._analytics)
            attempts = numbers["cache_hits"] + numbers["cache_misses"]
            return {
                "since": self._analytics_since,
                **numbers,
                "hit_rate_percent": round(100 * numbers["cache_hits"] / attempts, 1)
                    if attempts else 0.0,
                "top_references": [
                    {"source_fingerprint": key[:12], **activity.copy()}
                    for key, activity in sorted(
                        self._reference_activity.items(),
                        key=lambda item: (-item[1]["hits"], -item[1]["misses"], item[0]),
                    )[:8]
                ],
            }

    def _path(self, cache_id: str, extension: str) -> Path:
        return self.root / f"ref_{cache_id}{extension}"

    def _safe_path(self, value: str) -> Path | None:
        candidate = Path(value)
        if candidate.is_symlink() or candidate.parent.resolve() != self.root:
            return None
        resolved = candidate.resolve(strict=False)
        if resolved.parent != self.root or candidate.name != resolved.name:
            return None
        if not candidate.name.startswith("ref_"):
            return None
        return candidate

    def _valid_local_file(self, record: dict) -> Path | None:
        path = self._safe_path(str(record.get("storage_path") or ""))
        if path is None or not path.is_file():
            return None
        try:
            if path.stat().st_size != int(record["size_bytes"]):
                return None
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError, KeyError):
            return None
        return path if digest == record.get("content_sha256") else None

    def resolve(self, reference: ImageReference, *, force_refresh: bool = False) -> Path | None:
        # A cache hit still validates the source URL and current DNS answers.
        # Cached bytes do not grant an unvalidated URL access to the filesystem.
        try:
            _public_addresses(_validate_destination(reference.url))
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                "reference_download_failed",
                "Could not validate the cached reference image source.",
                502,
            ) from exc
        key = _source_key(reference)
        with self._lock:
            record = self.job_store.get_reference_cache(key)
            if not record:
                self._analytics["cache_misses"] += 1
                self._record_source(key, "misses")
                return None
            validated_at = _parse_time(record.get("validated_at"))
            if force_refresh or validated_at is None or _now() - validated_at > timedelta(seconds=self.ttl_seconds):
                self._analytics["cache_misses"] += 1
                self._record_source(key, "misses")
                return None
            path = self._valid_local_file(record)
            if path is None:
                self.job_store.invalidate_reference_cache(record["cache_id"])
                self._analytics["cache_misses"] += 1
                self._record_source(key, "misses")
                return None
            self._acquire(path)
            self.job_store.touch_reference_cache(record["cache_id"], _now().isoformat())
            # Count only a successfully resolved and leased image as a hit.
            avoided = int(record["size_bytes"])
            self._analytics["cache_hits"] += 1
            self._analytics["estimated_bytes_avoided"] += avoided
            self._record_source(key, "hits")
            self._record_source(key, "bytes_avoided", avoided)
            return path

    def release(self, path: Path) -> None:
        with self._lock:
            resolved = Path(path).resolve(strict=False)
            count = self._leases.get(resolved, 0)
            if count <= 1:
                self._leases.pop(resolved, None)
            else:
                self._leases[resolved] = count - 1

    def _acquire(self, path: Path) -> None:
        resolved = Path(path).resolve(strict=False)
        with self._lock:
            self._leases[resolved] = self._leases.get(resolved, 0) + 1
        scope = self._lease_scope.get()
        if scope is not None:
            scope.append(resolved)

    def lease_count(self, path: Path) -> int:
        with self._lock:
            return self._leases.get(Path(path).resolve(strict=False), 0)

    def store(self, reference: ImageReference, source: Path, *, mime_type: str,
              width: int | None = None, height: int | None = None) -> Path:
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise GatewayError("invalid_reference_image", "Reference cache source is not a regular file.", 422)
        size = source.stat().st_size
        if size > MAX_REFERENCE_IMAGE_BYTES:
            raise GatewayError("invalid_reference_image", "Reference image exceeds the 20 MB limit.", 422)
        data = source.read_bytes()
        extension = detect_image_extension(data)
        digest = hashlib.sha256(data).hexdigest()
        # The source key selects the current version; the content digest makes
        # each cache ID immutable when a mutable remote source changes.
        cache_id = hashlib.sha256(f"{_source_key(reference)}\0{digest}".encode("ascii")).hexdigest()
        destination = self._path(cache_id, extension)
        staging = self.root / f".{cache_id}.{uuid4().hex}.staging"
        now = _now().isoformat()
        record = {
            "cache_id": cache_id,
            "source_key": _source_key(reference),
            "provider": "google",
            "provider_file_id": reference.id,
            "content_sha256": digest,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "size_bytes": size,
            "storage_path": str(destination),
            "created_at": now,
            "validated_at": now,
            "last_accessed_at": now,
        }
        with self._lock:
            existing = self.job_store.get_reference_cache(record["source_key"])
            if existing:
                existing_path = self._valid_local_file(existing)
                if existing_path is not None and existing.get("content_sha256") == digest:
                    self._acquire(existing_path)
                    self.job_store.refresh_reference_cache(existing["cache_id"], now)
                    self.job_store.touch_reference_cache(existing["cache_id"], now)
                    return existing_path
            try:
                shutil.copyfile(source, staging)
                if hashlib.sha256(staging.read_bytes()).hexdigest() != digest:
                    raise GatewayError("invalid_reference_image", "Reference cache content changed during copy.", 422)
                os.replace(staging, destination)
                try:
                    self.job_store.replace_reference_cache(
                        record, existing.get("cache_id") if existing else None,
                    )
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                self._acquire(destination)
                self._evict_locked(exclude={cache_id})
                return destination
            finally:
                staging.unlink(missing_ok=True)

    def _evict_locked(self, *, exclude: set[str]) -> None:
        records = self.job_store.list_reference_cache(include_retired=True)
        total = sum(int(row["size_bytes"]) for row in records)
        registered = {
            path.resolve(strict=False)
            for row in records
            if (path := self._safe_path(str(row.get("storage_path") or ""))) is not None
        }
        # Recover files left by an interrupted publication or an older failed
        # registration. Staging names are handled by the finally block.
        for orphan in self.root.glob("ref_*"):
            resolved = orphan.resolve(strict=False)
            if resolved not in registered and resolved not in self._leases:
                try:
                    orphan.unlink(missing_ok=True)
                except OSError:
                    pass
        for row in sorted(records, key=lambda item: (item.get("last_accessed_at") or "", item["cache_id"])):
            if total <= self.max_bytes or row["cache_id"] in exclude:
                continue
            path = self._safe_path(str(row.get("storage_path") or ""))
            if path is None:
                self.job_store.invalidate_reference_cache(row["cache_id"])
                continue
            if self.lease_count(path):
                continue
            try:
                path.unlink(missing_ok=True)
                total -= int(row["size_bytes"])
                self.job_store.invalidate_reference_cache(row["cache_id"])
            except OSError:
                continue

    def release_all(self, paths: list[Path]) -> None:
        for path in paths:
            self.release(path)

    def stats(self) -> dict[str, int]:
        """Return cache-only accounting; Gallery files are deliberately excluded."""
        with self._lock:
            records = self.job_store.list_reference_cache(include_retired=True)
            bytes_used = 0
            files = 0
            for row in records:
                path = self._safe_path(str(row.get("storage_path") or ""))
                if path is None:
                    continue
                try:
                    if path.is_file() and not path.is_symlink():
                        bytes_used += path.stat().st_size
                        files += 1
                except OSError:
                    continue
            return {
                "bytes": bytes_used,
                "file_count": files,
                "entry_count": len(records),
                "active_entry_count": sum(1 for row in records if row.get("status") == "ready"),
                "retired_entry_count": sum(1 for row in records if row.get("status") == "retired"),
            }

    def cleanup(self, *, clear_all_unused: bool = False,
                clear_expired: bool = False,
                now: datetime | None = None) -> dict[str, int]:
        """Remove unleased cache files without conflating freshness and retention.

        Scheduled cleanup calls the default mode, which considers only unused
        retention. Manual "expired" cleanup opts into freshness-expired
        entries as well; freshness alone never causes scheduled deletion.
        """
        instant = (now or _now()).astimezone(timezone.utc)
        cutoff = instant - timedelta(days=self.retention_days)
        removed = 0
        skipped_active = 0
        bytes_removed = 0
        with self._lock:
            records = self.job_store.list_reference_cache(include_retired=True)
            for row in records:
                path = self._safe_path(str(row.get("storage_path") or ""))
                if path is None:
                    self.job_store.invalidate_reference_cache(row["cache_id"])
                    continue
                if self.lease_count(path):
                    skipped_active += 1
                    continue
                last_accessed = _parse_time(row.get("last_accessed_at"))
                validated_at = _parse_time(row.get("validated_at"))
                freshness_expired = (
                    validated_at is None
                    or instant - validated_at > timedelta(seconds=self.ttl_seconds)
                )
                eligible = (
                    clear_all_unused
                    or (
                        clear_expired
                        and (
                            freshness_expired
                            or last_accessed is None
                            or last_accessed < cutoff
                        )
                    )
                    or (
                        not clear_all_unused
                        and not clear_expired
                        and (
                            last_accessed is None
                            or last_accessed < cutoff
                        )
                    )
                )
                if not eligible:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                self.job_store.invalidate_reference_cache(row["cache_id"])
                removed += 1
                bytes_removed += int(row.get("size_bytes") or 0)
            self._evict_locked(exclude=set())
        return {"removed": removed, "bytes_removed": bytes_removed, "skipped_active": skipped_active}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
