"""Redirect-safe, DNS-pinned retrieval of Google-hosted visual references."""
from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request as UrlRequest,
    build_opener,
)

from .contracts import GatewayError, ImageReference

LOG = logging.getLogger("uvicorn.error")
MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_REDIRECTS = 5
MAX_REFERENCE_URL_LENGTH = 4096
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def allowed_reference_host(hostname: str) -> bool:
    """Keep the existing narrow Google domain allowlist, rejecting bad labels."""
    host = str(hostname or "").lower().rstrip(".")
    if not host or len(host) > 253 or any(
        not label or len(label) > 63 or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in label)
        for label in host.split(".")
    ):
        return False
    return (host == "drive.google.com" or host == "drive.usercontent.google.com"
            or host.endswith(".googleusercontent.com"))


def detect_image_extension(data: bytes) -> str:
    """Determine the extension from bytes rather than from a remote file name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise GatewayError("invalid_reference_image",
                       "Downloaded reference was not a supported PNG, JPEG, or WebP image.", 422)


def _validate_destination(url: str) -> str:
    """Reject unsafe URL before making any network request or DNS query."""
    if (not isinstance(url, str) or len(url) > MAX_REFERENCE_URL_LENGTH
        or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url):
        raise GatewayError("invalid_reference_image", "Invalid reference image URL.", 422)
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise GatewayError("invalid_reference_image", "Invalid reference image URL.", 422) from exc
    if parsed.scheme != "https":
        raise GatewayError("invalid_reference_image", "Reference image URLs must use HTTPS.", 422)
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise GatewayError("invalid_reference_image", "Reference URL credentials or port are not allowed.", 422)
    if not allowed_reference_host(host):
        raise GatewayError("invalid_reference_image", "Reference image host is not allowed.", 422)
    return host


def _public_addresses(host: str) -> tuple[str, ...]:
    """Check ALL DNS answers; pin the numeric public IPs for the actual TLS hop."""
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    ):
        raw = sockaddr[0]
        if "%" in raw:
            raise GatewayError("invalid_reference_image", "Reference host resolves to a disallowed address.", 422)
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise GatewayError("invalid_reference_image", "Reference host resolves to an invalid address.", 422) from exc
        if not address.is_global:
            raise GatewayError("invalid_reference_image", "Reference host resolves to a disallowed address.", 422)
        numeric = str(address)
        if numeric not in addresses:
            addresses.append(numeric)
    if not addresses:
        raise GatewayError("reference_download_failed", "Could not resolve a reference image host.", 502)
    return tuple(addresses)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Verify TLS against allowed domain but connect to a previously checked IP."""
    def __init__(self, host: str, *, validated_host: str,
                 addresses: tuple[str, ...], **kwargs):
        super().__init__(host, **kwargs)
        self._reference_host = validated_host
        self._reference_addresses = addresses

    def connect(self):
        if self._tunnel_host:
            raise OSError("Reference downloader does not support proxy tunneling")
        last_error: OSError | None = None
        for address in self._reference_addresses:
            try:
                sock = socket.create_connection((address, 443), self.timeout, self.source_address)
            except OSError as exc:
                last_error = exc
                continue
            try:
                self.sock = self._context.wrap_socket(sock, server_hostname=self._reference_host)
                return
            except Exception:
                sock.close()
                raise
        raise last_error or OSError("No validated reference address reachable")


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, host: str, addresses: tuple[str, ...]):
        super().__init__(context=ssl.create_default_context())
        self._host = host
        self._addresses = addresses

    def https_open(self, request):
        def make_connection(host: str, **kwargs):
            return _PinnedHTTPSConnection(host, validated_host=self._host,
                                          addresses=self._addresses, **kwargs)
        return self.do_open(make_connection, request, context=self._context)


def _open_validated_hop(url: str, host: str, addresses: tuple[str, ...]):
    """Exactly one HTTPS request: no environment proxy, no automatic redirect."""
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler(),
                          _PinnedHTTPSHandler(host, addresses))
    req = UrlRequest(url, headers={
        "User-Agent": "Local-AI-Gateway/1.0", "Accept": "image/*,*/*;q=0.8",
    })
    return opener.open(req, timeout=30)


def _fetch_reference_bytes(url: str) -> tuple[bytes, str, int | str, str]:
    """Follow no more than five allowlisted, public-DNS, pinned HTTPS redirects."""
    current_url = url
    for hop in range(MAX_REFERENCE_REDIRECTS + 1):
        host = _validate_destination(current_url)
        addresses = _public_addresses(host)
        try:
            response = _open_validated_hop(current_url, host, addresses)
        except HTTPError as exc:
            try:
                if exc.code not in REDIRECT_STATUSES:
                    raise GatewayError("reference_download_failed",
                                       "Reference image server rejected the download.", 502) from exc
                location = exc.headers.get("Location") if exc.headers else None
                if not location or hop >= MAX_REFERENCE_REDIRECTS:
                    raise GatewayError("invalid_reference_image",
                                       "Reference image redirect was missing or exceeded the limit.", 422) from exc
                next_url = urljoin(current_url, location)
                _validate_destination(next_url)  # Before contacting next hop.
                current_url = next_url
                continue
            finally:
                exc.close()
        with response:
            status = getattr(response, "status", 200)
            if not 200 <= status < 300:
                raise GatewayError("reference_download_failed",
                                   "Reference image server rejected the download.", 502)
            content_type = str(response.headers.get("Content-Type") or "")
            data = response.read(MAX_REFERENCE_IMAGE_BYTES + 1)
            return data, host, status, content_type
    raise GatewayError("invalid_reference_image", "Too many reference image redirects.", 422)


def download_reference_image(reference: ImageReference, workdir: Path, index: int,
                             request_id: str | None = None) -> Path:
    """Download one reference; never log Google URL query strings or Drive IDs."""
    log_request_id = request_id or "unknown"
    reference_id = str(reference.id or f"reference_{index + 1}")[:128]
    LOG.info("reference_download_start request_id=%s index=%s reference_id=%s",
             log_request_id, index + 1, reference_id)
    try:
        data, final_host, http_status, content_type = _fetch_reference_bytes(reference.url)
        if len(data) > MAX_REFERENCE_IMAGE_BYTES:
            raise GatewayError("invalid_reference_image", "Reference image exceeds the 20 MB limit.", 422)
        extension = detect_image_extension(data)
    except GatewayError as exc:
        LOG.warning("reference_download_rejected request_id=%s reference_id=%s error=%s",
                    log_request_id, reference_id, exc.kind)
        raise
    except Exception as exc:
        LOG.warning("reference_download_failed request_id=%s reference_id=%s exception=%s",
                    log_request_id, reference_id, type(exc).__name__)
        raise GatewayError("reference_download_failed", "Could not download a reference image.", 502) from exc
    LOG.info("reference_download_response request_id=%s reference_id=%s status=%s final_host=%s "
             "content_type=%s bytes=%s", log_request_id, reference_id, http_status,
             final_host, content_type or "unknown", len(data))
    safe_id = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_"
                      for c in reference_id)[:128] or f"reference_{index + 1}"
    path = workdir / f"{index + 1:02d}_{safe_id}{extension}"
    path.write_bytes(data)
    LOG.info("reference_download_ready request_id=%s reference_id=%s local_file=%s bytes=%s",
             log_request_id, reference_id, path.name, path.stat().st_size)
    return path
