
"""Module 9E.2 — Codex text runner image attachments.

All tests use a fake Codex subprocess.
No network, tokens, or live model calls.
"""

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from gateway.codex_runner import (
    CodexRunner,
    stage_chat_images,
)
from gateway.contracts import GatewayError


def make_image(fmt="PNG"):
    buffer = BytesIO()
    image = Image.new("RGB", (4, 4), "white")
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_image_validation_and_temporary_staging(tmp_path):
    data = make_image("PNG")

    paths = stage_chat_images(
        [("image/png", data)],
        tmp_path,
    )

    assert len(paths) == 1
    assert paths[0].name == "chat_input_01.png"
    assert paths[0].read_bytes() == data


def test_invalid_binary_rejected(tmp_path):
    with pytest.raises(GatewayError):
        stage_chat_images(
            [("image/png", b"not an image")],
            tmp_path,
        )


def test_mime_mismatch_rejected(tmp_path):
    with pytest.raises(GatewayError):
        stage_chat_images(
            [("image/jpeg", make_image("PNG"))],
            tmp_path,
        )


def test_image_count_limit(tmp_path):
    images = [
        ("image/png", make_image())
        for _ in range(5)
    ]

    with pytest.raises(GatewayError):
        stage_chat_images(images, tmp_path)


@pytest.mark.parametrize(
    "mime,fmt",
    [
        ("image/jpeg", "JPEG"),
        ("image/png", "PNG"),
        ("image/webp", "WEBP"),
    ],
)
def test_supported_image_formats(tmp_path, mime, fmt):
    paths = stage_chat_images(
        [(mime, make_image(fmt))],
        tmp_path,
    )
    assert len(paths) == 1


def test_runner_image_attachment_and_text_compatibility(
    monkeypatch,
):
    """Verify command structure without launching Codex."""

    captured = []

    class FakeStdin:
        def __init__(self):
            self.data = b""
            self.closed = False

        def write(self, data):
            self.data += data

        async def drain(self):
            return None

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.returncode = 0
            self.stdin = FakeStdin()

    async def fake_subprocess(*args, **kwargs):
        process = FakeProcess()

        captured.append({
            "args": list(args),
            "kwargs": kwargs,
            "process": process,
        })

        return process

    async def fake_communicate(
        process,
        timeout_seconds,
        progress=None,
    ):
        current = captured[-1]
        args = current["args"]

        # Simulate Codex writing its normal output file.
        output_index = args.index("-o") + 1
        output_path = Path(args[output_index])
        output_path.write_text(
            '{"ok":true}',
            encoding="utf-8",
        )

        return (
            b'{"type":"turn.completed","usage":'
            b'{"input_tokens":10,"output_tokens":3}}\n',
            b"",
        )

    monkeypatch.setattr(
        "gateway.codex_runner.asyncio.create_subprocess_exec",
        fake_subprocess,
    )

    runner = CodexRunner(["codex"])

    monkeypatch.setattr(
        runner,
        "_communicate",
        fake_communicate,
    )

    async def execute(images=None):
        return await runner.run(
            prompt="Return a JSON object.",
            schema=None,
            web_search=False,
            timeout_seconds=30,
            image_attachments=images,
        )

    # -------------------------------------------------------
    # Text-only request remains positional.
    # -------------------------------------------------------
    result = asyncio.run(execute())

    assert result.raw_response == '{"ok":true}'

    text_call = captured[-1]

    assert "--image" not in text_call["args"]
    assert text_call["args"][-1] == "Return a JSON object."
    assert (
        text_call["kwargs"]["stdin"]
        == asyncio.subprocess.DEVNULL
    )

    # -------------------------------------------------------
    # Image-enabled request attaches the image and uses stdin.
    # -------------------------------------------------------
    result = asyncio.run(
        execute([
            ("image/png", make_image("PNG")),
        ])
    )

    assert result.raw_response == '{"ok":true}'

    image_call = captured[-1]
    args = image_call["args"]

    assert "--image" in args
    assert args[-1].endswith(".png")
    assert "Return a JSON object." not in args

    assert (
        image_call["kwargs"]["stdin"]
        == asyncio.subprocess.PIPE
    )

    assert (
        image_call["process"].stdin.data
        == b"Return a JSON object."
    )

    assert image_call["process"].stdin.closed

    # The temporary attachment is removed after completion.
    image_path = Path(
        args[args.index("--image") + 1]
    )

    assert not image_path.exists()
