"""Module 6R.3: moved /v1 handlers preserve n8n contracts and per-app state."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from gateway import app as root
from gateway.contracts import GatewayError, ImageRunResult, RunResult
from gateway.generation_routes import GenerationRuntime, create_generation_router
from gateway.job_store import JobStore

TOKEN = "module-6r3-local-test-token-long-enough"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeRunner:
    def __init__(self, image: Path | None = None):
        self.image = image
        self.seen: list[dict] = []

    async def run(self, **kwargs):
        self.seen.append(kwargs)
        kwargs["progress"]("Codex is working")
        return RunResult("from fake Codex", "from fake Codex", {
            "input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 3, "total_tokens": 13,
        })

    async def run_image(self, **kwargs):
        self.seen.append(kwargs)
        kwargs["progress"]("Launching Codex")
        kwargs["progress"]("Generating image")
        assert self.image is not None
        return ImageRunResult(self.image, "image/png", {"input_tokens": 7, "output_tokens": 2}, None)


def make_app(tmp_path: Path, runner=None, model="gpt-5.6-luna", queue=4):
    store = JobStore(tmp_path / "db.sqlite3")
    settings = root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                             model=model, max_concurrency=1, max_queue=queue,
                             quota_monitor_enabled=False)
    app = root.create_app(settings=settings, runner=runner or FakeRunner(), job_store=store)
    return app, store


def test_factory_is_explicit_and_exports_runtime():
    assert callable(create_generation_router)
    assert GenerationRuntime("gpt-5.6-luna").pending == 0


def test_effective_v1_routes_and_dashboard_routes_unchanged(tmp_path):
    app, store = make_app(tmp_path)
    try:
        paths = {(path, method.upper())
                 for path, data in app.openapi()["paths"].items()
                 for method in data
                 if method.lower() in {"get", "post", "put", "patch", "delete"}}
        for method, path in [
            ("GET", "/health"), ("GET", "/v1/jobs"),
            ("GET", "/v1/models"), ("PUT", "/v1/models/default"),
            ("GET", "/v1/capabilities"), ("POST", "/v1/generate"),
            ("POST", "/v1/research"), ("POST", "/v1/chat/completions"),
            ("POST", "/v1/images/generations"),
            ("GET", "/dashboard/api/summary"),
            ("GET", "/dashboard/api/diagnostics"),
        ]:
            assert (path, method) in paths
    finally:
        store.close()


def test_auth_and_generate_research_chat_contracts(tmp_path):
    runner = FakeRunner()
    app, store = make_app(tmp_path, runner)
    try:
        with TestClient(app) as client:
            for path in ("/v1/generate", "/v1/research", "/v1/chat/completions", "/v1/images/generations"):
                assert client.post(path, json={"prompt": "hello"}).status_code == 401
            generate = client.post("/v1/generate", headers=HEADERS,
                                   json={"request_id": "gen_6r3", "prompt": "hello"})
            assert generate.status_code == 200
            assert generate.json()["response"] == "from fake Codex"
            assert generate.json()["usage"]["total_tokens"] == 13
            research = client.post("/v1/research", headers=HEADERS,
                                   json={"request_id": "res_6r3", "prompt": "hello"})
            assert research.status_code == 200
            assert research.json()["web_search_used"] is False
            assert runner.seen[-1]["web_search"] is True
            chat = client.post("/v1/chat/completions", headers=HEADERS,
                               json={"request_id": "chat_6r3", "messages": [
                                   {"role": "user", "content": "hello"}]})
            assert chat.status_code == 200
            assert chat.json()["choices"][0]["message"]["content"] == "from fake Codex"
            assert chat.json()["usage"]["total_tokens"] == 13
            assert client.get("/health", headers=HEADERS).json()["active_or_queued"] == 0
    finally:
        store.close()


def test_model_update_is_shared_across_health_dashboard_and_subsequent_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    if not hasattr(root.SettingsManager, "save_model"):
        monkeypatch.setattr(root.SettingsManager, "save_model", lambda self, model: {"model": model}, raising=False)
    runner = FakeRunner()
    app, store = make_app(tmp_path, runner)
    try:
        with TestClient(app) as client:
            assert client.put("/v1/models/default", headers=HEADERS,
                              json={"model": "gpt-5.6-terra"}).status_code == 200
            assert client.get("/v1/models", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"
            assert client.get("/health", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"
            assert client.get("/dashboard/api/summary", headers=HEADERS).json()["gateway"]["model"] == "gpt-5.6-terra"
            assert client.post("/v1/generate", headers=HEADERS,
                               json={"request_id": "new_default", "prompt": "hello"}).json()["model"] == "gpt-5.6-terra"
            assert runner.seen[-1]["model"] == "gpt-5.6-terra"
            assert client.post("/v1/generate", headers=HEADERS,
                               json={"request_id": "override", "prompt": "hello",
                                     "codex_model": "gpt-5.6-sol"}).json()["model"] == "gpt-5.6-sol"
            assert client.get("/v1/models", headers=HEADERS).json()["default_model"] == "gpt-5.6-terra"
    finally:
        store.close()


def test_generation_pool_is_independent_per_app(tmp_path):
    first, first_db = make_app(tmp_path / "first", model="gpt-5.6-luna")
    second, second_db = make_app(tmp_path / "second", model="gpt-5.6-sol")
    try:
        assert first.state.generation_runtime is not second.state.generation_runtime
        with TestClient(first) as a, TestClient(second) as b:
            assert a.get("/health", headers=HEADERS).json()["default_model"] == "gpt-5.6-luna"
            assert b.get("/health", headers=HEADERS).json()["default_model"] == "gpt-5.6-sol"
            assert a.post("/v1/generate", headers=HEADERS, json={"prompt": "hi"}).status_code == 200
            assert b.get("/v1/jobs", headers=HEADERS).json()["jobs"] == []
    finally:
        first_db.close()
        second_db.close()


def test_full_queue_returns_original_busy_contract_and_recovers(tmp_path):
    entered = asyncio.Event()
    resume = asyncio.Event()
    class SlowRunner:
        async def run(self, **kwargs):
            entered.set()
            await resume.wait()
            return RunResult("ok", "ok", None)
    app, store = make_app(tmp_path, SlowRunner(), queue=0)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            first = asyncio.create_task(client.post("/v1/generate", headers=HEADERS,
                                                    json={"request_id": "first", "prompt": "hello"}))
            await asyncio.wait_for(entered.wait(), 2)
            assert (await client.get("/health", headers=HEADERS)).json()["active_or_queued"] == 1
            busy = await client.post("/v1/generate", headers=HEADERS,
                                     json={"request_id": "blocked", "prompt": "hello"})
            assert busy.status_code == 429
            assert busy.json()["error"]["type"] == "busy"
            assert (await client.get("/health", headers=HEADERS)).json()["active_or_queued"] == 1
            resume.set()
            assert (await first).status_code == 200
            assert (await client.get("/health", headers=HEADERS)).json()["active_or_queued"] == 0
    try:
        asyncio.run(exercise())
    finally:
        store.close()


def test_classified_error_keeps_n8n_response_and_sqlite_diagnostics(tmp_path):
    class FailingRunner:
        async def run(self, **kwargs):
            kwargs["progress"]("Codex session started")
            failure = GatewayError("codex_cli_argument_error", "Codex CLI rejected arguments", 502)
            failure.exit_code = 2
            failure.diagnostic = "unexpected argument '--model'"
            raise failure
    app, store = make_app(tmp_path, FailingRunner())
    try:
        with TestClient(app) as client:
            resp = client.post("/v1/generate", headers=HEADERS, json={"request_id": "failure", "prompt": "hello"})
            assert resp.status_code == 502
            assert resp.json()["error"] == {"type": "codex_cli_argument_error", "message": "Codex CLI rejected arguments"}
            job = store.list_jobs(search="failure")[0]
            assert job["exit_code"] == 2
            assert "--model" in job["diagnostic_preview"]
    finally:
        store.close()


def test_image_binary_contract_and_token_tracking(tmp_path):
    """Image extraction must keep the raw binary response for n8n (not JSON)."""
    from PIL import Image
    image = tmp_path / "generated.png"
    Image.new("RGB", (64, 48), "#336699").save(image)
    app, store = make_app(tmp_path, FakeRunner(image))
    try:
        with TestClient(app) as client:
            resp = client.post("/v1/images/generations", headers=HEADERS,
                               json={"request_id": "scene_image_6r3_initial", "prompt": "draw one image"})
            assert resp.status_code == 200
            assert resp.content.startswith(b"\x89PNG")
            assert resp.headers["content-type"] == "image/png"
            assert resp.headers["x-request-id"] == "scene_image_6r3_initial"
            jobs = client.get("/v1/jobs", headers=HEADERS).json()["jobs"]
            assert jobs[0]["status"] == "completed"
            assert jobs[0]["usage"]["input_tokens"] == 7
            assert store.get_usage(jobs[0]["id"])["total_tokens"] == 9
    finally:
        store.close()


def test_image_failure_preserves_classification_and_binary_route_contract(tmp_path):
    class FailingImage:
        async def run_image(self, **kwargs):
            kwargs["progress"]("Launching Codex")
            err = GatewayError("codex_cli_argument_error", "Codex CLI rejected arguments", 502)
            err.exit_code = 2
            err.diagnostic = "unexpected argument '--image'"
            raise err
    app, store = make_app(tmp_path, FailingImage())
    try:
        with TestClient(app) as client:
            response = client.post("/v1/images/generations", headers=HEADERS,
                                   json={"request_id": "scene_image_6r3_failed", "prompt": "one image"})
            assert response.status_code == 502
            assert response.json()["error"]["type"] == "codex_cli_argument_error"
            row = store.list_jobs(search="scene_image_6r3_failed")[0]
            assert row["exit_code"] == 2
            assert "--image" in row["diagnostic_preview"]
            assert row["status"] == "failed"
    finally:
        store.close()
