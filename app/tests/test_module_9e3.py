
"""Module 9E.3 — Multimodal chat HTTP integration.

No actual Codex calls or external network requests.
"""

import base64
import sys
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from gateway.app import create_app
from gateway.config import Settings
from gateway.contracts import RunResult
from gateway.job_store import JobStore
from gateway.artifact_store import ArtifactStore


def make_image_data_url(color):
    stream = BytesIO()
    Image.new("RGB", (4, 4), color).save(
        stream, format="PNG"
    )
    data = stream.getvalue()

    return (
        "data:image/png;base64,"
        + base64.b64encode(data).decode("ascii"),
        data,
    )


class RecordingRunner:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)

        response = '{"visual_review":"success"}'

        return RunResult(
            response=response,
            raw_response=response,
            usage={
                "input_tokens": 12,
                "output_tokens": 4,
            },
        )


def test_multimodal_http_forwarding(tmp_path):
    token = "module-9e3-testing-token-abcdefghijk"
    runner = RecordingRunner()

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

    first_url, first_bytes = make_image_data_url("red")
    second_url, second_bytes = make_image_data_url("blue")

    body = {
        "request_id": "module_9e3_vision_test",
        "messages": [
            {
                "role": "system",
                "content": "Return JSON only.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Compare these images.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": first_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Now inspect the second image.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": second_url,
                        },
                    },
                ],
            },
        ],
        "response_format": {
            "type": "json_object"
        },
    }

    headers = {
        "Authorization": f"Bearer {token}"
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json=body,
        )

        assert response.status_code == 200, response.text

        result = response.json()

        assert result["id"] == (
            "chatcmpl-module_9e3_vision_test"
        )

        assert result["choices"][0]["message"][
            "content"
        ] == '{"visual_review":"success"}'

        assert result["usage"]["prompt_tokens"] == 12
        assert result["usage"]["completion_tokens"] == 4

    assert len(runner.calls) == 1

    forwarded = runner.calls[0]

    # Actual bytes, not encoded strings.
    assert forwarded["image_attachments"] == [
        ("image/png", first_bytes),
        ("image/png", second_bytes),
    ]

    prompt = forwarded["prompt"]

    assert "[system]" in prompt
    assert "Return JSON only." in prompt
    assert "Compare these images." in prompt

    assert "[Attached image 1]" in prompt
    assert "[Attached image 2]" in prompt

    # Image markers must preserve relative text order.
    assert (
        prompt.index("Compare these images.")
        < prompt.index("[Attached image 1]")
        < prompt.index("Now inspect the second image.")
        < prompt.index("[Attached image 2]")
    )

    # Never stringify image bytes into the prompt.
    assert first_url not in prompt
    assert second_url not in prompt
    assert "base64," not in prompt

    store.close()


def test_invalid_image_rejected_before_runner(tmp_path):
    token = "module-9e3-testing-token-abcdefghijk"
    runner = RecordingRunner()

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

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}"
            },
            json={
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,NOT_VALID!"
                        },
                    }],
                }],
            },
        )

        assert response.status_code == 422

    assert runner.calls == []
    store.close()
