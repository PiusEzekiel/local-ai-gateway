"""Opaque, persistent artifact storage used by the control plane."""

from __future__ import annotations

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
        source = Path(source)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type, ".bin")
        artifact_id = "art_" + uuid4().hex
        destination = (self.root / f"{artifact_id}{suffix}").resolve()
        if destination.parent != self.root:
            raise ValueError("Invalid artifact destination")
        # Copy file data, not the source mtime: downloaded/reference images may
        # carry a timestamp older than the 24-hour orphan-cleanup threshold.
        # A newly copied artifact must always look new until its DB registration
        # completes, even when the source file is years old.
        shutil.copyfile(source, destination)
        width = height = None
        thumbnail_path: str | None = None
        try:
            with Image.open(destination) as image:
                width, height = image.size
                image.thumbnail((560, 420), Image.Resampling.LANCZOS)
                thumbnail = (self.root / f"{artifact_id}_thumb.webp").resolve()
                if thumbnail.parent != self.root:
                    raise ValueError("Invalid thumbnail destination")
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(thumbnail, format="WEBP", quality=min(max(thumbnail_quality, 30), 95), method=4)
                thumbnail_path = str(thumbnail)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            # The original artifact remains usable if optional thumbnailing fails.
            pass
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

    def resolve(self, storage_path: str) -> Path | None:
        candidate = Path(storage_path).resolve()
        if candidate.parent != self.root or not candidate.is_file():
            return None
        return candidate

    def delete(self, artifact: dict[str, Any]) -> bool:
        target = self.resolve(str(artifact.get("storage_path", "")))
        if target is None:
            return False
        target.unlink()
        thumbnail = artifact.get("thumbnail_path")
        if thumbnail:
            thumb = self.resolve(str(thumbnail))
            if thumb:
                thumb.unlink()
        return True

    # ----------------------------------------------------------------------
    # Retention-only filesystem functions. Legacy create/resolve/delete stay
    # untouched. Symlinks/paths outside the opaque store are never followed.
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
