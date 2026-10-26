"""No-network coverage for trusted workspace image references."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.contracts import GatewayError, ImageReference
from gateway.local_references import LocalReferenceResolver
from gateway.reference_images import MAX_REFERENCE_IMAGE_BYTES


PNG = b"\x89PNG\r\n\x1a\nworkspace-image"


def reference(**values: object) -> ImageReference:
    return ImageReference(id="asset", **values)


def test_schema_accepts_url_or_local_path_but_not_both_or_neither() -> None:
    assert ImageReference(url="https://drive.google.com/image").url
    assert ImageReference(local_path="/files/asset/image.png").local_path
    with pytest.raises(ValidationError):
        ImageReference()
    with pytest.raises(ValidationError):
        ImageReference(url="https://drive.google.com/image", local_path="/files/image.png")


def test_schema_rejects_invalid_sha256() -> None:
    with pytest.raises(ValidationError):
        ImageReference(local_path="/files/image.png", sha256="not-a-digest")


def test_valid_workspace_image_is_copied_and_checksum_checked(tmp_path: Path) -> None:
    source = tmp_path / "smarter in 2d" / "episode" / "teacher.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(PNG)
    resolver = LocalReferenceResolver(root=str(tmp_path))
    workdir = tmp_path / "request"
    workdir.mkdir()
    digest = hashlib.sha256(PNG).hexdigest()

    copied = resolver.resolve(
        reference(local_path="/files/smarter in 2d/episode/teacher.png", sha256=digest),
        workdir,
        0,
    )
    assert copied.read_bytes() == PNG
    assert copied != source


@pytest.mark.parametrize("local_path", [
    "/files.evil/image.png", "/files/../secret.png", "/files/./image.png",
    "/files//image.png", r"C:\files\image.png", r"\\server\share\image.png",
    "/files/C:/image.png", "/files/image:stream.png", "/files",
])
def test_workspace_path_syntax_is_strict(tmp_path: Path, local_path: str) -> None:
    resolver = LocalReferenceResolver(root=str(tmp_path))
    with pytest.raises(GatewayError):
        resolver.resolve(reference(local_path=local_path), tmp_path, 0)


def test_missing_root_and_file_are_rejected_without_host_path(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    resolver = LocalReferenceResolver(root=str(missing_root))
    with pytest.raises(GatewayError) as raised:
        resolver.resolve(reference(local_path="/files/image.png"), tmp_path, 0)
    assert str(missing_root) not in str(raised.value)

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(GatewayError):
        LocalReferenceResolver(root=str(root)).resolve(
            reference(local_path="/files/missing.png"), tmp_path, 0,
        )


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside.png"
    root.mkdir()
    outside.write_bytes(PNG)
    link = root / "link.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    with pytest.raises(GatewayError):
        LocalReferenceResolver(root=str(root)).resolve(
            reference(local_path="/files/link.png"), tmp_path, 0,
        )


def test_size_signature_and_checksum_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    bad = root / "bad.png"
    bad.write_bytes(b"not an image")
    resolver = LocalReferenceResolver(root=str(root))
    with pytest.raises(GatewayError):
        resolver.resolve(reference(local_path="/files/bad.png"), tmp_path, 0)

    large = root / "large.png"
    with large.open("wb") as stream:
        stream.write(b"\x89PNG\r\n\x1a\n")
        stream.seek(MAX_REFERENCE_IMAGE_BYTES)
        stream.write(b"x")
    with pytest.raises(GatewayError):
        resolver.resolve(reference(local_path="/files/large.png"), tmp_path, 0)

    valid = root / "valid.png"
    valid.write_bytes(PNG)
    with pytest.raises(GatewayError):
        resolver.resolve(reference(local_path="/files/valid.png", sha256="0" * 64), tmp_path, 0)
