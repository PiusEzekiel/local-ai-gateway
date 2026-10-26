"""Secure resolution of request-local workspace image references."""
from __future__ import annotations

import os
from pathlib import Path

from .contracts import GatewayError, ImageReference
from .reference_images import MAX_REFERENCE_IMAGE_BYTES, detect_image_extension


class LocalReferenceResolver:
    """Validate workspace paths and copy their bytes into a request workdir.

    The request path is treated as an untrusted POSIX name.  The configured
    root is the only host filesystem location this class ever addresses.
    """

    def __init__(self, prefix: str = "/files", root: str | None = None):
        self.prefix = self._validate_prefix(prefix)
        self.root = Path(root).resolve() if root else None

    @staticmethod
    def _validate_prefix(prefix: str) -> str:
        if (not isinstance(prefix, str) or not prefix.startswith("/")
                or prefix == "/" or "\\" in prefix
                or any(part in {"", ".", ".."} for part in prefix.split("/")[1:])):
            raise ValueError("AI_GATEWAY_TRUSTED_WORKSPACE_PREFIX must be a canonical POSIX path.")
        return prefix.rstrip("/")

    def _error(self, message: str = "Invalid local reference image.") -> GatewayError:
        return GatewayError("invalid_reference_image", message, 422)

    def _relative_parts(self, local_path: str) -> tuple[str, ...]:
        if (not isinstance(local_path, str) or not local_path
                or len(local_path) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in local_path)
                or "\\" in local_path or not local_path.startswith("/")):
            raise self._error()

        # Reject ambiguous separators and dot segments before using any host
        # path APIs.  This also rejects drive-letter and UNC/device spellings.
        if "//" in local_path:
            raise self._error()
        parts = local_path.split("/")
        prefix_parts = self.prefix.split("/")[1:]
        if parts[:len(prefix_parts) + 1] != ["", *prefix_parts]:
            raise self._error()
        relative = parts[len(prefix_parts) + 1:]
        if not relative or any(not part or part in {".", ".."} for part in relative):
            raise self._error()
        if any(":" in part for part in relative):
            raise self._error()
        return tuple(relative)

    def resolve(self, reference: ImageReference, workdir: Path, index: int) -> Path:
        """Validate, read, and copy one image without exposing host paths."""
        try:
            parts = self._relative_parts(reference.local_path or "")
            if self.root is None or not self.root.is_dir():
                raise self._error("Local workspace references are not configured.")

            candidate = self.root.joinpath(*parts)
            resolved_root = self.root.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)

            # Open the validated target and read from that descriptor.  The
            # post-open realpath check narrows symlink/junction race exposure.
            descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            try:
                opened_path = Path(os.path.realpath(candidate))
                opened_path.relative_to(resolved_root)
                stat = os.fstat(descriptor)
                if not os.path.isfile(opened_path) or stat.st_size > MAX_REFERENCE_IMAGE_BYTES:
                    raise self._error("Local reference image is missing, not a file, or exceeds the 20 MB limit.")
                data = bytearray()
                while len(data) <= MAX_REFERENCE_IMAGE_BYTES:
                    chunk = os.read(descriptor, min(1024 * 1024, MAX_REFERENCE_IMAGE_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > MAX_REFERENCE_IMAGE_BYTES:
                    raise self._error("Local reference image exceeds the 20 MB limit.")
            finally:
                os.close(descriptor)

            extension = detect_image_extension(bytes(data))
            target = Path(workdir) / f"{index + 1:02d}_local_reference{extension}"
            target.write_bytes(data)
            return target
        except GatewayError:
            raise
        except (OSError, ValueError, RuntimeError):
            raise self._error() from None


def create_local_reference_resolver(prefix: str, root: str | None) -> LocalReferenceResolver:
    """Create the resolver from process configuration without logging its root."""
    return LocalReferenceResolver(prefix, root)
