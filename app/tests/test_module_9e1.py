
"""Module 9E.1: multimodal chat contract tests.

No Codex calls, network requests or live image generation.
"""

import base64
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.contracts import (
    ChatCompletionRequest,
    ChatImagePart,
    ChatTextPart,
    RunResult,
    MAX_CHAT_IMAGE_BYTES,
)


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"test"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test"
WEBP_BYTES = b"RIFF1234WEBP" + b"test"


def data_url(mime, content):
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_part(mime="image/jpeg", content=JPEG_BYTES):
    return {
        "type": "image_url",
        "image_url": {
            "url": data_url(mime, content),
        },
    }


def request_with(parts, role="user"):
    return ChatCompletionRequest.model_validate({
        "request_id": "module_9e1_test",
        "messages": [
            {
                "role": role,
                "content": parts,
            }
        ],
    })


def test_existing_text_chat_contract():
    request = ChatCompletionRequest.model_validate({
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Return JSON."},
        ]
    })

    assert isinstance(request.messages[0].content, str)
    assert request.messages[1].content == "Return JSON."


@pytest.mark.parametrize(
    "mime,data",
    [
        ("image/jpeg", JPEG_BYTES),
        ("image/png", PNG_BYTES),
        ("image/webp", WEBP_BYTES),
    ],
)
def test_supported_multimodal_images(mime, data):
    request = request_with([
        {"type": "text", "text": "Review this image."},
        image_part(mime, data),
    ])

    content = request.messages[0].content

    assert isinstance(content[0], ChatTextPart)
    assert isinstance(content[1], ChatImagePart)
    assert content[0].text == "Review this image."


def test_image_only_user_message():
    request = request_with([
        image_part(),
    ])

    assert len(request.messages[0].content) == 1


@pytest.mark.parametrize(
    "mime,data",
    [
        ("image/gif", JPEG_BYTES),
        ("image/jpeg", PNG_BYTES),
        ("image/png", JPEG_BYTES),
        ("image/webp", JPEG_BYTES),
    ],
)
def test_unsupported_or_mismatched_mime(mime, data):
    with pytest.raises(ValidationError):
        request_with([
            image_part(mime, data),
        ])


def test_invalid_base64_rejected():
    with pytest.raises(ValidationError):
        request_with([
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,###",
                },
            }
        ])


def test_remote_image_urls_rejected():
    with pytest.raises(ValidationError):
        request_with([
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/photo.jpg",
                },
            }
        ])


def test_oversized_image_rejected():
    oversized = JPEG_BYTES + (
        b"x" * (MAX_CHAT_IMAGE_BYTES + 1)
    )

    with pytest.raises(ValidationError):
        request_with([
            image_part("image/jpeg", oversized),
        ])


def test_request_image_count_limit():
    with pytest.raises(ValidationError):
        request_with([
            image_part()
            for _ in range(5)
        ])


def test_unknown_content_type_rejected():
    with pytest.raises(ValidationError):
        request_with([
            {
                "type": "file",
                "file_id": "example",
            }
        ])


def test_extra_image_fields_rejected():
    part = image_part()
    part["unexpected"] = "ignored?"

    with pytest.raises(ValidationError):
        request_with([part])


def test_non_user_image_messages_rejected():
    with pytest.raises(ValidationError):
        request_with([
            image_part(),
        ], role="system")


def test_text_only_array_rejected():
    with pytest.raises(ValidationError):
        request_with([
            {"type": "text", "text": "Hello"},
        ])


def test_existing_empty_text_restriction():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate({
            "messages": [
                {"role": "user", "content": ""}
            ]
        })



def test_endpoint_forwards_valid_images_after_9e3(
    tmp_path,
):
    """Text stays compatible and images reach the runner."""

    from gateway.app import create_app
    from gateway.config import Settings
    from gateway.job_store import JobStore
    from gateway.artifact_store import ArtifactStore

    class FakeRunner:
        def __init__(self):
            self.calls = []

        async def run(self, **kwargs):
            self.calls.append(kwargs)

            return RunResult(
                response='{"ok":true}',
                raw_response='{"ok":true}',
                usage={
                    "input_tokens": 10,
                    "output_tokens": 3,
                },
            )

    runner = FakeRunner()
    token = "module-9e1-testing-token-abcdefghijk"

    store = JobStore(tmp_path / "gateway.sqlite3")

    app = create_app(
        settings=Settings(
            api_token=token,
            codex_exe=sys.executable,
            quota_monitor_enabled=False,
        ),
        runner=runner,
        job_store=store,
        artifact_store=ArtifactStore(
            tmp_path / "artifacts"
        ),
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    with TestClient(app) as client:

        # Existing text-only chat must still work.
        text_response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Return JSON.",
                    }
                ],
                "response_format": {
                    "type": "json_object"
                },
            },
        )

        assert text_response.status_code == 200
        assert "image_attachments" not in runner.calls[0]

        # A valid multimodal message now reaches the runner.
        image_response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Describe the image.",
                            },
                            image_part(),
                        ],
                    }
                ],
                "response_format": {
                    "type": "json_object"
                },
            },
        )

        
        assert image_response.status_code == 200, (
            f"HTTP {image_response.status_code}: "
            f"{image_response.text}"
        )

        assert image_response.json()["choices"][0][
            "message"
        ]["content"] == '{"ok":true}'

    assert len(runner.calls) == 2

    image_call = runner.calls[1]

    assert image_call["image_attachments"] == [
        ("image/jpeg", JPEG_BYTES),
    ]

    assert "[Attached image 1]" in image_call["prompt"]

    # Image data must never become prompt text.
    assert "data:image/jpeg;base64," not in (
        image_call["prompt"]
    )

    store.close()