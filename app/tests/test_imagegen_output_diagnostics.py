"""Regression tests for the real Codex image-tool observability gap.

No Codex installation, image API requests, tokens, or network access needed.
A live Windows smoke test remains mandatory for model/tool availability.
"""
from __future__ import annotations

import asyncio
import json
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gateway.codex_runner import CodexRunner
from gateway.contracts import GatewayError, ImageRequest


def test_image_request_allows_opt_in_older_model_without_changing_text_defaults():
    image = ImageRequest(prompt='a single orange', codex_model='gpt-5.5')
    assert image.codex_model == 'gpt-5.5'
    with pytest.raises(ValidationError):
        ImageRequest(prompt='a single orange', codex_model='unknown-model')


@pytest.mark.parametrize(
    ('observe_tool', 'expected_kind'),
    [(False, 'image_tool_not_observed'), (True, 'image_output_not_found')],
)
def test_no_saved_image_distinguishes_missing_tool_from_missing_artifact(
    tmp_path, monkeypatch, observe_tool, expected_kind
):
    thread_id = str(uuid4())
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))

    class FakeStdin:
        def write(self, _data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class FakeProcess:
        pid = 42
        returncode = 0
        stdin = FakeStdin()

    async def create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    events = [{'type': 'thread.started', 'thread_id': thread_id}]
    if observe_tool:
        events.append({'type': 'item.started', 'item': {'type': 'image_generation_call'}})
    events.append({'type': 'turn.completed', 'usage': {}})

    async def communicate(_process, _seconds, _progress):
        return ('\n'.join(json.dumps(e) for e in events).encode('utf8'), b'')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create_subprocess_exec)
    runner = CodexRunner([sys.executable], model='gpt-5.5')
    monkeypatch.setattr(runner, '_communicate', communicate)

    with pytest.raises(GatewayError) as caught:
        asyncio.run(runner.run_image(prompt='one image', timeout_seconds=10))
    assert caught.value.kind == expected_kind
