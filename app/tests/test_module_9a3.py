"""9A.3: no-network tests for redirect SSRF defense and reference compatibility."""
from __future__ import annotations

import io
import logging
import socket
import ssl
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from gateway.contracts import GatewayError, ImageReference
from gateway import reference_images as ref


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-test-image-payload"
PUBLIC = ("142.250.190.46",)


class Response:
    status = 200

    def __init__(self, data=PNG, content_type="image/png"):
        self.data = io.BytesIO(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, count):
        return self.data.read(count)


def redirect(source, location, status=302):
    headers = Message()
    headers["Location"] = location
    return HTTPError(source, status, "Moved", headers, io.BytesIO(b""))


@pytest.mark.parametrize("url", [
    "http://drive.google.com/file", "https://127.0.0.1/file",
    "https://10.0.0.1/file", "https://evil.googleusercontent.com.evil.net/file",
    "https://googleusercontent.com/file", "https://drive.google.com:8443/file",
    "https://name:pw@drive.google.com/file", "https://drive.google.com\\@evil.test/file",
    "https://drive.google.com/hello\r\nX-Evil: true", "https://drive..google.com/file",
    "https://drive.google.com:bad/file", "https://drive.google.com/" + "x" * 4097,
])
def test_invalid_initial_url_is_never_resolved_or_opened(monkeypatch, url):
    monkeypatch.setattr(ref, "_public_addresses", lambda host: pytest.fail("should not resolve DNS"))
    monkeypatch.setattr(ref, "_open_validated_hop", lambda *a: pytest.fail("should not connect"))
    with pytest.raises(GatewayError) as raised:
        ref._fetch_reference_bytes(url)
    assert raised.value.kind == "invalid_reference_image"
    assert raised.value.status_code == 422


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.2.15", "169.254.169.254", "192.168.1.5",
    "100.64.0.1", "192.0.2.1", "::1", "fd00::42", "fe80::1", "2001:db8::1",
])
def test_private_reserved_ip_rejected_before_http(monkeypatch, ip):
    def fake_resolve(*a, **kw):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                 socket.SOCK_STREAM, 6, "", (ip, 443))]
    monkeypatch.setattr(ref.socket, "getaddrinfo", fake_resolve)
    monkeypatch.setattr(ref, "_open_validated_hop", lambda *a: pytest.fail("should not connect"))
    with pytest.raises(GatewayError, match="disallowed address"):
        ref._fetch_reference_bytes("https://drive.google.com/file")


def test_mixed_public_and_private_dns_rejects_whole_host(monkeypatch):
    monkeypatch.setattr(ref.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC[0], 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr(ref, "_open_validated_hop", lambda *a: pytest.fail("should not connect"))
    with pytest.raises(GatewayError, match="disallowed address"):
        ref._fetch_reference_bytes("https://drive.google.com/file")


def test_public_ipv4_and_ipv6_addresses_remain_pinned(monkeypatch):
    monkeypatch.setattr(ref.socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC[0], 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2607:f8b0:4004:800::200e", 443, 0, 0)),
    ])
    seen = []
    def fake_open(url, host, addresses):
        seen.append((url, host, addresses))
        return Response()
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    content, host, status, mime = ref._fetch_reference_bytes("https://drive.google.com/file")
    assert content == PNG
    assert (host, status, mime) == ("drive.google.com", 200, "image/png")
    assert seen[0][2] == (PUBLIC[0], "2607:f8b0:4004:800::200e")


@pytest.mark.parametrize("target", [
    "http://drive.google.com/file", "https://127.0.0.1/admin",
    "https://evil.test/redirect", "https://drive.google.com:8080/file",
    "https://u:p@drive.google.com/file", "//127.0.0.1/secret",
])
def test_disallowed_redirect_is_blocked_before_connecting(monkeypatch, target):
    requests = []
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    def fake_open(url, host, ips):
        requests.append(url)
        raise redirect(url, target)
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    with pytest.raises(GatewayError) as raised:
        ref._fetch_reference_bytes("https://drive.google.com/file")
    assert raised.value.kind == "invalid_reference_image"
    assert requests == ["https://drive.google.com/file"]


def test_unsafe_redirect_dns_is_blocked_before_connecting(monkeypatch):
    visited = []
    def fake_ips(host):
        if host == "lh3.googleusercontent.com":
            raise GatewayError("invalid_reference_image", "private DNS", 422)
        return PUBLIC
    def fake_open(url, host, ips):
        visited.append(host)
        raise redirect(url, "https://lh3.googleusercontent.com/image")
    monkeypatch.setattr(ref, "_public_addresses", fake_ips)
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    with pytest.raises(GatewayError, match="private DNS"):
        ref._fetch_reference_bytes("https://drive.google.com/file")
    assert visited == ["drive.google.com"]


def test_normal_google_drive_redirect_chain_and_filename_compatibility(monkeypatch, tmp_path):
    first = "https://drive.google.com/uc?id=TOPSECRET"
    second = "https://drive.usercontent.google.com/download?id=TOPSECRET"
    third = "https://lh3.googleusercontent.com/image?id=TOPSECRET"
    urls = []
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    def fake_open(url, host, ips):
        urls.append(url)
        if url == first:
            raise redirect(url, second)
        if url == second:
            raise redirect(url, third, status=307)
        assert url == third
        return Response()
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    path = ref.download_reference_image(
        ImageReference(id="my_asset", url=first), tmp_path, 0, "request_1"
    )
    assert urls == [first, second, third]
    assert path.name == "01_my_asset.png"
    assert path.read_bytes() == PNG


def test_relative_redirect_is_supported(monkeypatch):
    initial = "https://drive.google.com/uc?id=one"
    target = "https://drive.google.com/download?id=one"
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    urls = []
    def fake_open(url, host, ips):
        urls.append(url)
        if url == initial:
            raise redirect(url, "/download?id=one")
        return Response()
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    assert ref._fetch_reference_bytes(initial)[0] == PNG
    assert urls == [initial, target]


def test_redirect_limit_is_enforced(monkeypatch):
    visited = []
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    def fake_open(url, host, ips):
        visited.append(url)
        raise redirect(url, f"/image{len(visited)}")
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    with pytest.raises(GatewayError, match="redirect") as raised:
        ref._fetch_reference_bytes("https://drive.google.com/file")
    assert raised.value.status_code == 422
    assert len(visited) == ref.MAX_REFERENCE_REDIRECTS + 1


def test_missing_redirect_location_does_not_retry(monkeypatch):
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    hits = []
    def fake_open(url, host, ips):
        hits.append(url)
        raise HTTPError(url, 302, "Moved", Message(), io.BytesIO(b""))
    monkeypatch.setattr(ref, "_open_validated_hop", fake_open)
    with pytest.raises(GatewayError, match="redirect"):
        ref._fetch_reference_bytes("https://drive.google.com/file")
    assert len(hits) == 1


def test_nonredirect_http_error_preserves_public_error_contract(monkeypatch):
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    monkeypatch.setattr(ref, "_open_validated_hop", lambda url, *_: (
        (_ for _ in ()).throw(HTTPError(url, 403, "Forbidden", Message(), io.BytesIO(b"")))
    ))
    with pytest.raises(GatewayError) as raised:
        ref._fetch_reference_bytes("https://drive.google.com/file")
    assert (raised.value.kind, raised.value.status_code) == ("reference_download_failed", 502)


def test_size_limit_and_magic_bytes_still_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    monkeypatch.setattr(ref, "_open_validated_hop", lambda *a: Response(b"x" * (ref.MAX_REFERENCE_IMAGE_BYTES + 1)))
    with pytest.raises(GatewayError, match="20 MB"):
        ref.download_reference_image(
            ImageReference(id="ref", url="https://drive.google.com/file"), tmp_path, 0
        )
    monkeypatch.setattr(ref, "_open_validated_hop", lambda *a: Response(b"not-image"))
    with pytest.raises(GatewayError, match="supported PNG"):
        ref.download_reference_image(
            ImageReference(id="ref", url="https://drive.google.com/file"), tmp_path, 0
        )
    assert list(tmp_path.iterdir()) == []


def test_full_google_url_not_logged_on_redirect_rejection(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ref, "_public_addresses", lambda host: PUBLIC)
    monkeypatch.setattr(ref, "_open_validated_hop", lambda url, *_: (
        (_ for _ in ()).throw(redirect(url, "https://127.0.0.1/secret?token=PRIVATE2"))
    ))
    with caplog.at_level(logging.INFO):
        with pytest.raises(GatewayError):
            ref.download_reference_image(
                ImageReference(id="asset", url="https://drive.google.com/file?token=PRIVATE1"),
                tmp_path, 0, "request_1"
            )
    assert "PRIVATE1" not in caplog.text
    assert "PRIVATE2" not in caplog.text


def test_pinned_tls_sni_uses_original_hostname_not_ip(monkeypatch):
    calls = []
    class FakeSocket:
        def close(self):
            calls.append("closed")
    class FakeContext:
        # Python's HTTPSConnection checks the SSLContext verification settings
        # before calling connect(). Model those settings in this test double.
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, sock, server_hostname):
            calls.append(("sni", server_hostname))
            return sock
    monkeypatch.setattr(ref.socket, "create_connection", lambda address, timeout, source: (
        calls.append(("connect", address)) or FakeSocket()
    ))
    conn = ref._PinnedHTTPSConnection(
        "drive.google.com", validated_host="drive.google.com", addresses=PUBLIC,
        context=FakeContext(), timeout=30
    )
    conn.connect()
    assert calls == [("connect", (PUBLIC[0], 443)), ("sni", "drive.google.com")]


def test_opener_has_redirect_blocker_and_no_environment_proxy(monkeypatch):
    handlers = []
    class FakeOpener:
        def open(self, request, timeout):
            return Response()
    def fake_build(*incoming):
        handlers.extend(incoming)
        return FakeOpener()
    monkeypatch.setattr(ref, "build_opener", fake_build)
    ref._open_validated_hop("https://drive.google.com/file", "drive.google.com", PUBLIC)
    assert any(isinstance(h, ref._NoRedirectHandler) for h in handlers)
    assert any(isinstance(h, ref._PinnedHTTPSHandler) for h in handlers)
    assert any(isinstance(h, ref.ProxyHandler) and h.proxies == {} for h in handlers)
    assert ref._NoRedirectHandler().redirect_request(None, None, 302, "", {}, "https://evil") is None
