"""Opaque, persistent artifact storage used by the control plane."""

from __future__ import annotations

import os
import shutil
import re
from threading import RLock
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


Image.MAX_IMAGE_PIXELS = 50_000_000


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def create(self, *, job_id: str, source: Path, mime_type: str, artifact_type: str = "image",
               thumbnail_quality: int = 78) -> dict[str, Any]:
        # Serialize creation with retention, so orphan scans cannot treat an
        # incompletely registered file as an old candidate mid-copy.
        with self._lock:
            return self._create_locked(job_id=job_id, source=source,
                                       mime_type=mime_type, artifact_type=artifact_type,
                                       thumbnail_quality=thumbnail_quality)

    def _create_locked(self, *, job_id: str, source: Path, mime_type: str,
                       artifact_type: str, thumbnail_quality: int) -> dict[str, Any]:
        """Publish complete files only; a failed import cannot leave a public partial image.

        The staging files have deliberately NON-managed names, so retention
        never mistakes an in-flight copy for a completed gallery artifact.
        Each rename is atomic on this filesystem; SQLite registration remains
        a separate step managed by the caller, with compensating rollback.
        """
        source = Path(source)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type, ".bin")
        artifact_id = "art_" + uuid4().hex
        destination = self.root / f"{artifact_id}{suffix}"
        thumbnail = self.root / f"{artifact_id}_thumb.webp"
        # Staging must be in the same directory for os.replace to be atomic.
        staging = self.root / f".{artifact_id}{suffix}.{uuid4().hex}.staging"
        thumb_staging = self.root / f".{artifact_id}_thumb.webp.{uuid4().hex}.staging"
        width = height = None
        thumbnail_path: str | None = None
        published = False
        try:
            # copyfile (not copy2) gives each newly created artifact a fresh
            # timestamp, including when the input came from a very old archive.
            shutil.copyfile(source, staging)
            # Verify the entire source before exposing the final managed path.
            # Unknown MIME types preserve the pre-existing .bin fallback.
            if mime_type in {"image/png", "image/jpeg", "image/webp"}:
                with Image.open(staging) as image:
                    image.verify()
                with Image.open(staging) as image:
                    width, height = image.size
                    try:
                        image.thumbnail((560, 420), Image.Resampling.LANCZOS)
                        if image.mode not in {"RGB", "RGBA"}:
                            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                        image.save(thumb_staging, format="WEBP",
                                   quality=min(max(thumbnail_quality, 30), 95), method=4)
                    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                        # Thumbnailing is optional; verified original is durable.
                        thumb_staging.unlink(missing_ok=True)

            os.replace(staging, destination)
            published = True
            if thumb_staging.is_file():
                try:
                    os.replace(thumb_staging, thumbnail)
                    thumbnail_path = str(thumbnail)
                except OSError:
                    # A thumbnail publish error cannot invalidate a ready original.
                    # Clean up even if a mocked/unstable replace committed first.
                    thumbnail.unlink(missing_ok=True)
                    thumb_staging.unlink(missing_ok=True)
            return {
                "id": artifact_id,
                "job_id": job_id,
                "artifact_type": artifact_type,
                "mime_type": mime_type,
                "width": width,
                "height": height,
                "size_bytes": destination.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "storage_path": str(destination),
                "thumbnail_path": thumbnail_path,
            }
        except BaseException:
            # If publishing or stat fails, do not expose a half-registered file.
            # BaseException includes cancellation/interrupt; never swallow it.
            if published:
                destination.unlink(missing_ok=True)
                thumbnail.unlink(missing_ok=True)
            raise
        finally:
            staging.unlink(missing_ok=True)
            thumb_staging.unlink(missing_ok=True)

    def resolve(self, storage_path: str) -> Path | None:
        """Serving and regular deletion obey the same opaque-path rules as retention."""
        candidate = self._managed_path(storage_path)
        return candidate if candidate and candidate.is_file() else None

    def delete(self, artifact: dict[str, Any]) -> bool:
        """Remove both managed files, refusing unsafe DB paths or symlinks."""
        target = self.resolve(str(artifact.get("storage_path", "")))
        if target is None:
            return False
        self.delete_for_retention(artifact)
        return True

    # ----------------------------------------------------------------------
    # Shared filesystem path rules. Serving, deletion and retention must all
    # reject symlinks and files outside this opaque, managed namespace.
    # ----------------------------------------------------------------------
    def _managed_path(self, value: str | None) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        # Check the physical parent as well as the final resolved target.
        if candidate.parent.resolve() != self.root or candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=False)
        if resolved.parent != self.root:
            return None
        if not re.fullmatch(r'art_[0-9a-f]{32}(?:_thumb)?\.(?:png|jpg|webp|bin)', candidate.name):
            return None
        return candidate

    def record_file_bytes(self, artifact: dict[str, Any]) -> int:
        size = 0
        for name in ('storage_path', 'thumbnail_path'):
            value = artifact.get(name)
            if not value:
                continue
            path = self._managed_path(value)
            if path and path.is_file():
                try:
                    size += path.stat().st_size
                except OSError:
                    continue
        return size

    def disk_usage(self) -> dict[str, int]:
        total = count = 0
        with self._lock:
            for path in self.root.iterdir():
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    pass
        return {'bytes': total, 'file_count': count}

    def delete_for_retention(self, artifact: dict[str, Any]) -> None:
        """Raise on unsafe or failed paths; missing in-root files are safe to prune."""
        paths = []
        for name in ('storage_path', 'thumbnail_path'):
            value = artifact.get(name)
            if value:
                path = self._managed_path(value)
                if path is None:
                    raise ValueError('Unsafe artifact path in database')
                paths.append(path)
        if not paths:
            raise ValueError('Artifact has no managed file path')
        with self._lock:
            for path in paths:
                path.unlink(missing_ok=True)

    def list_orphans(self, registered: set[Path], *, older_than: datetime,
                     limit: int = 251) -> list[Path]:
        """Only our opaque-name files older than one day; never arbitrary files."""
        found = []
        with self._lock:
            for path in sorted(self.root.iterdir()):
                if path.is_symlink() or not path.is_file() or self._managed_path(str(path)) is None:
                    continue
                if path.resolve() in registered:
                    continue
                try:
                    changed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                except OSError:
                    continue
                if changed > older_than:
                    continue
                found.append(path)
                if len(found) >= limit:
                    break
        return found

    def remove_orphan(self, path: Path) -> None:
        candidate = self._managed_path(str(path))
        if candidate is None:
            raise ValueError('Invalid orphan path')
        with self._lock:
            candidate.unlink(missing_ok=True)
