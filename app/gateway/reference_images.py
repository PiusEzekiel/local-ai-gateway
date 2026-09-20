"""Allowlisted, size-limited image reference retrieval and validation."""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen
from .contracts import GatewayError, ImageReference

LOG = logging.getLogger("uvicorn.error")

MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB per reference.


def allowed_reference_host(hostname: str) -> bool:

    """Return True only for the Google hosts we intentionally allow."""

    host = str(hostname or "").lower().strip(".")

    return (

        host == "drive.google.com"

        or host == "drive.usercontent.google.com"

        or host.endswith(".googleusercontent.com")

    )


def detect_image_extension(data: bytes) -> str:

    """Validate image bytes using file signatures and return a safe extension."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):

        return ".png"

    if data.startswith(b"\xff\xd8\xff"):

        return ".jpg"

    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":

        return ".webp"



    raise GatewayError(

        "invalid_reference_image",

        "Downloaded reference was not a supported PNG, JPEG, or WebP image.",

        422,

    )


def download_reference_image(
    reference: ImageReference,
    workdir: Path,
    index: int,
    request_id: str | None = None,
) -> Path:
    """Download one allow-listed reference image into the ephemeral workdir.

    Operational logging intentionally records only the request ID, reference ID,
    host, response metadata, byte count, and local filename. The full source URL
    (including Google Drive file IDs/query parameters) is never written to logs.

    Security notes:
    - HTTPS is mandatory.
    - Both the original host and final redirect host are validated.
    - The download is size-limited before it is written to disk.
    - The extension comes from the image bytes, never from the remote filename.
    """
    log_request_id = request_id or "unknown"
    reference_id = str(reference.id or f"reference_{index + 1}")[:128]
    parsed = urlparse(reference.url)
    source_host = parsed.hostname or ""

    LOG.info(
        "reference_download_start request_id=%s index=%s reference_id=%s host=%s",
        log_request_id,
        index + 1,
        reference_id,
        source_host or "missing",
    )

    if parsed.scheme != "https":
        error = GatewayError(
            "invalid_reference_image",
            "Reference image URLs must use HTTPS.",
            422,
        )
        LOG.warning(
            "reference_download_rejected request_id=%s reference_id=%s reason=%s",
            log_request_id,
            reference_id,
            error.kind,
        )
        raise error

    if not allowed_reference_host(source_host):
        error = GatewayError(
            "invalid_reference_image",
            "Reference image host is not allowed.",
            422,
        )
        LOG.warning(
            "reference_download_rejected request_id=%s reference_id=%s host=%s reason=%s",
            log_request_id,
            reference_id,
            source_host or "missing",
            error.kind,
        )
        raise error

    request = UrlRequest(
        reference.url,
        headers={
            "User-Agent": "Local-AI-Gateway/1.0",
            "Accept": "image/*,*/*;q=0.8",
        },
    )

    final_host = ""
    content_type = ""
    http_status: int | str = "unknown"

    try:
        with urlopen(request, timeout=30) as response:
            # urllib follows redirects automatically, so validate the final host too.
            final_url = response.geturl()
            final_host = urlparse(final_url).hostname or ""
            http_status = getattr(response, "status", "unknown")
            content_type = str(response.headers.get("Content-Type") or "")

            if not allowed_reference_host(final_host):
                raise GatewayError(
                    "invalid_reference_image",
                    "Reference image redirected to a disallowed host.",
                    422,
                )

            # Read one byte beyond the limit so oversized files can be rejected.
            data = response.read(MAX_REFERENCE_IMAGE_BYTES + 1)

    except GatewayError as exc:
        LOG.warning(
            "reference_download_rejected request_id=%s reference_id=%s source_host=%s final_host=%s "
            "error=%s error_message=%s",
            log_request_id,
            reference_id,
            source_host or "missing",
            final_host or "unknown",
            exc.kind,
            exc.message,
        )
        raise
    except Exception as exc:
        LOG.warning(
            "reference_download_failed request_id=%s reference_id=%s host=%s exception=%s",
            log_request_id,
            reference_id,
            source_host or "missing",
            type(exc).__name__,
        )
        raise GatewayError(
            "reference_download_failed",
            "Could not download a reference image.",
            502,
        ) from exc

    LOG.info(
        "reference_download_response request_id=%s reference_id=%s status=%s final_host=%s "
        "content_type=%s bytes=%s",
        log_request_id,
        reference_id,
        http_status,
        final_host or "unknown",
        content_type or "unknown",
        len(data),
    )

    if len(data) > MAX_REFERENCE_IMAGE_BYTES:
        error = GatewayError(
            "invalid_reference_image",
            "Reference image exceeds the 20 MB limit.",
            422,
        )
        LOG.warning(
            "reference_download_rejected request_id=%s reference_id=%s reason=too_large bytes=%s",
            log_request_id,
            reference_id,
            len(data),
        )
        raise error

    try:
        extension = detect_image_extension(data)
    except GatewayError as exc:
        LOG.warning(
            "reference_download_rejected request_id=%s reference_id=%s reason=%s "
            "content_type=%s bytes=%s",
            log_request_id,
            reference_id,
            exc.kind,
            content_type or "unknown",
            len(data),
        )
        raise

    # Preserve the asset ID in the local filename so Codex has an extra visual/text
    # cue that connects each attached file to [asset_id] labels in the scene prompt.
    safe_id = "".join(
        char if char.isalnum() or char in "-_." else "_"
        for char in reference_id
    )
    safe_id = safe_id[:128] or f"reference_{index + 1}"

    path = workdir / f"{index + 1:02d}_{safe_id}{extension}"
    path.write_bytes(data)

    LOG.info(
        "reference_download_ready request_id=%s reference_id=%s local_file=%s bytes=%s",
        log_request_id,
        reference_id,
        path.name,
        path.stat().st_size,
    )

    return path


