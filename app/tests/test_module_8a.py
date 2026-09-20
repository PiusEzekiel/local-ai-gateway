"""Module 8A: bounded, authenticated, metadata-only live events."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from gateway import app as root
from gateway.contracts import GatewayError, RunResult
from gateway.job_history import JobHistory
from gateway.job_store import JobStore
from gateway.live_events import LiveEventBus, create_live_router, sse_frame, watch_quota

TOKEN = "module8a-auth-token-abcdefghijklmnopqrstuvwxyz"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


def test_sse_frames_json_escape_untrusted_text_and_cursor():
    frame = sse_frame("job.updated", {"stage": "hello\n\nevent: injected", "ok": True}, event_id=7)
    assert frame.startswith("id: 7\nevent: job.updated\ndata: ")
    assert frame.count("\nevent:") == 1
    assert json.loads(frame.split("data: ")[1].split("\n\n")[0])["stage"] == "hello\n\nevent: injected"


def test_event_names_are_restricted():
    with pytest.raises(ValueError):
        sse_frame("\r\ndata: secret", {})
    with pytest.raises(ValueError):
        LiveEventBus().publish("not-a-real-event", {})
    with pytest.raises(ValueError):
        LiveEventBus(queue_size=0)


def test_subscriber_gets_ordered_updates_and_no_replay():
    async def check():
        bus = LiveEventBus()
        bus.publish("job.created", {"job_id": "before"})
        number, queue = bus.subscribe()
        assert queue.empty()
        bus.publish("job.created", {"job_id": "one"})
        bus.publish("job.updated", {"job_id": "two"})
        first, second = queue.get_nowait(), queue.get_nowait()
        assert (first[0], second[0]) == (1, 2)
        assert (first[1], second[1]) == ("job.created", "job.updated")
        bus.unsubscribe(number)
        assert bus.client_count == 0
    run(check())


def test_isolated_app_instances_do_not_share_subscribers():
    async def check():
        first, second = LiveEventBus(), LiveEventBus()
        a, aq = first.subscribe()
        b, bq = second.subscribe()
        first.publish("model.changed", {"model": "one"})
        assert aq.get_nowait()[2] == {"model": "one"}
        assert bq.empty()
        first.unsubscribe(a); second.unsubscribe(b)
    run(check())


def test_slow_client_receives_resync_not_stale_history():
    async def check():
        bus = LiveEventBus(queue_size=2)
        number, queue = bus.subscribe()
        for ordinal in range(3):
            bus.publish("job.updated", {"ordinal": ordinal})
        assert queue.qsize() == 1
        event_id, event, data = queue.get_nowait()
        assert event_id == 3 and event == "resync_required"
        assert data == {"reason": "slow_client"}
        bus.unsubscribe(number)
    run(check())


def test_client_limit_fails_safely_before_stream_starts():
    async def check():
        bus = LiveEventBus(max_clients=1)
        first, _ = bus.subscribe()
        with pytest.raises(GatewayError) as failure:
            bus.subscribe()
        assert failure.value.status_code == 503
        bus.unsubscribe(first)
        second, _ = bus.subscribe()
        bus.unsubscribe(second)
    run(check())


def test_publish_from_worker_thread_is_marshaled_to_live_loop():
    async def check():
        bus = LiveEventBus()
        number, queue = bus.subscribe()
        thread = threading.Thread(target=lambda: bus.publish("workers.changed", {"pending": 2}))
        thread.start()
        await asyncio.to_thread(thread.join)
        entry = await asyncio.wait_for(queue.get(), 2)
        assert entry[1:] == ("workers.changed", {"pending": 2})
        bus.unsubscribe(number)
    run(check())


def test_history_events_are_metadata_only_even_with_secret_text():
    async def check():
        bus = LiveEventBus()
        client_id, queue = bus.subscribe()
        history = JobHistory(events=bus)
        job = history.add("user-private-request-id", "generate", "gpt-5.6-luna")
        job["prompt"] = "PRIVATE_PROMPT_XYZ"
        history.update(job, status="failed", stage="Gateway failed", error=GatewayError(
            "private", "private-stderr-PASSWORD-HERE", 500,
        ))
        entries = [queue.get_nowait(), queue.get_nowait()]
        joined = repr(entries)
        assert [e[1] for e in entries] == ["job.created", "job.updated"]
        assert entries[-1][2]["error_type"] == "private"
        assert "PRIVATE_PROMPT_XYZ" not in joined
        assert "private-stderr-PASSWORD-HERE" not in joined
        assert "user-private-request-id" not in joined
        bus.unsubscribe(client_id)
    run(check())


def test_history_usage_is_invalidation_only():
    async def check():
        bus = LiveEventBus()
        client_id, queue = bus.subscribe()
        history = JobHistory(events=bus)
        job = history.add("id", "research", "gpt-5.6-luna")
        queue.get_nowait()
        history.usage(job, {"total_tokens": 999, "private_field": "not-for-events"})
        event = queue.get_nowait()
        assert event[1] == "job.usage"
        assert event[2] == {"job_id": job["id"]}
        bus.unsubscribe(client_id)
    run(check())


def test_quota_watch_uses_cached_snapshot_and_only_notifies_on_change():
    async def check():
        class Monitor:
            def __init__(self):
                self.status = "ok"
                self.reads = 0
            def snapshot(self):
                self.reads += 1
                return {"status": self.status, "observed_at": "now", "buckets": []}
            def refresh(self):
                raise AssertionError("Live quota must not trigger new Codex requests")
        monitor = Monitor()
        bus = LiveEventBus()
        cid, queue = bus.subscribe()
        task = asyncio.create_task(watch_quota(bus, monitor, interval_seconds=0.005))
        try:
            await asyncio.sleep(0.025)
            assert queue.empty()
            monitor.status = "limited"
            event = await asyncio.wait_for(queue.get(), 1)
            assert event[1] == "quota.changed" and event[2]["status"] == "limited"
            assert monitor.reads >= 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            bus.unsubscribe(cid)
    run(check())


def test_event_endpoint_requires_bearer_and_does_not_change_v1_contract(tmp_path):
    store = JobStore(tmp_path / "events.sqlite3")
    app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                    quota_monitor_enabled=False),
                          runner=object(), job_store=store)
    try:
        with TestClient(app) as client:
            assert client.get("/dashboard/api/events").status_code == 401
            assert client.get("/dashboard/api/events", headers={"Authorization": "Bearer bad"}).status_code == 401
            assert "/dashboard/api/events" in app.openapi()["paths"]
            assert client.get("/health", headers=AUTH).json()["active_or_queued"] == 0
    finally:
        store.close()


def test_generation_emits_lifecycle_without_changing_n8n_payload(tmp_path):
    class Runner:
        async def run(self, **kwargs):
            kwargs["progress"]("Codex is working")
            return RunResult("expected output", "expected output", {"total_tokens": 4})
    store = JobStore(tmp_path / "events.sqlite3")
    app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                    quota_monitor_enabled=False),
                          runner=Runner(), job_store=store)
    captured = []
    app.state.live_events.publish = lambda event, data: captured.append((event, data))
    try:
        with TestClient(app) as client:
            response = client.post("/v1/generate", headers=AUTH, json={"request_id": "scene-7", "prompt": "hi"})
            assert response.status_code == 200
            assert response.json()["response"] == "expected output"
            assert response.json()["usage"]["total_tokens"] == 4
            assert client.get("/health", headers=AUTH).json()["active_or_queued"] == 0
        names = [name for name, _ in captured]
        assert names[0] == "job.created"
        assert "job.updated" in names and "job.usage" in names
        assert names.count("workers.changed") == 2
        assert captured[-1] == ("workers.changed", {"pending": 0, "concurrency": 1, "max_queue": 4})
        assert "hi" not in repr(captured)
    finally:
        store.close()


def test_model_changes_emit_same_value_as_health(tmp_path, monkeypatch):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    store = JobStore(tmp_path / "events.sqlite3")
    app = root.create_app(settings=root.Settings(api_token=TOKEN, codex_exe=sys.executable,
                                                    quota_monitor_enabled=False),
                          runner=object(), job_store=store)
    captured = []
    app.state.live_events.publish = lambda event, data: captured.append((event, data))
    try:
        with TestClient(app) as client:
            change = client.put("/v1/models/default", headers=AUTH, json={"model": "gpt-5.6-terra"})
            assert change.status_code == 200
            assert client.get("/health", headers=AUTH).json()["default_model"] == "gpt-5.6-terra"
            assert ("model.changed", {"model": "gpt-5.6-terra"}) in captured
            new = client.patch("/dashboard/api/settings", headers=AUTH, json={"model": "gpt-5.6-luna"})
            assert new.status_code == 200
            assert captured[-1] == ("model.changed", {"model": "gpt-5.6-luna"})
    finally:
        store.close()


def test_stream_sends_ready_then_event_and_releases_client():
    async def check():
        bus = LiveEventBus()
        route = create_live_router(require_token=lambda: None, bus=bus).routes[0]
        class ConnectedRequest:
            async def is_disconnected(self):
                return False
        response = await route.endpoint(ConnectedRequest())
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert bus.client_count == 1
        generator = response.body_iterator
        assert "retry: 3000" in await anext(generator)
        assert json.loads((await anext(generator)).split("data: ")[1].strip())["snapshot_required"]
        bus.publish("workers.changed", {"pending": 1})
        message = await asyncio.wait_for(anext(generator), 2)
        assert "event: workers.changed" in message
        assert '"pending":1' in message
        await generator.aclose()
        assert bus.client_count == 0
    run(check())
