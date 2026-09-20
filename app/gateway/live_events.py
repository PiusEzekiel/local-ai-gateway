"""Module 8A: bounded, best-effort, per-app live event fan-out.

A live connection is NOT a source of truth. Clients must GET their normal
snapshot after connecting/reconnecting, and after any resync_required signal.
No history/replay, prompt contents, output text, stderr, tokens, credentials,
or filesystem paths travel through this transport.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import threading
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .contracts import GatewayError

LOG = logging.getLogger("uvicorn.error")

EVENT_NAMES = frozenset({"job.created", "job.updated", "job.usage", "workers.changed",
                         "model.changed", "quota.changed", "storage.changed"})


def sse_frame(event: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    """Frame data as one JSON line; never interpolate untrusted text into SSE syntax."""
    if not (event in EVENT_NAMES or event in {"ready", "resync_required"}):
        raise ValueError("Unsupported live event")
    json_data = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {json_data}\n\n"


class LiveEventBus:
    """One bus per FastAPI app, never shared between separate gateway instances.

    Publishing is synchronous and never awaits a client. A slow client loses
    intermediate updates and receives resync_required instead of blocking
    generation. Publishing from non-event-loop threads is safely marshalled.
    """

    def __init__(self, *, max_clients: int = 16, queue_size: int = 64):
        if not 1 <= max_clients <= 256 or not 1 <= queue_size <= 1024:
            raise ValueError("Invalid event bus limits")
        self.max_clients = max_clients
        self.queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._lock = threading.RLock()
        self._last_id = 0
        self._next_client = 0
        self._clients: dict[int, asyncio.Queue[tuple[int | None, str, dict[str, Any]]]] = {}
        self._closed = False

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise GatewayError("unavailable", "Live updates are unavailable.", 503)
            if self._loop is not None and self._loop is not loop:
                raise GatewayError("unavailable", "Live updates use a different event loop.", 503)
            if len(self._clients) >= self.max_clients:
                raise GatewayError("busy", "Too many dashboard connections.", 503)
            self._loop = loop
            self._loop_thread = threading.get_ident()
            self._next_client += 1
            number = self._next_client
            channel: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
            self._clients[number] = channel
            return number, channel

    def unsubscribe(self, client_id: int) -> None:
        with self._lock:
            self._clients.pop(client_id, None)
            if not self._clients:
                # TestClient starts a fresh event loop per context. Allow the
                # same app to be re-entered after all connections close.
                self._loop = None
                self._loop_thread = None

    def publish(self, event: str, data: dict[str, Any]) -> None:
        if event not in EVENT_NAMES:
            raise ValueError("Unsupported live event")
        with self._lock:
            if self._closed or not self._clients:
                return
            self._last_id += 1
            item = (self._last_id, event, dict(data))
            loop = self._loop
            if not loop or loop.is_closed():
                return
            if threading.get_ident() == self._loop_thread:
                self._fanout(item)
            else:
                try:
                    loop.call_soon_threadsafe(self._fanout, item)
                except RuntimeError:
                    LOG.debug("live_event_loop_closed")

    def _fanout(self, item: tuple[int, str, dict[str, Any]]) -> None:
        with self._lock:
            if self._closed:
                return
            for channel in self._clients.values():
                if channel.full():
                    # A late subscriber must refetch authoritative GET data.
                    # Clear all stale updates, preserve the latest cursor only.
                    while not channel.empty():
                        channel.get_nowait()
                    channel.put_nowait((item[0], "resync_required", {"reason": "slow_client"}))
                else:
                    channel.put_nowait(item)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._clients.clear()
            self._loop = None
            self._loop_thread = None


async def watch_quota(bus: LiveEventBus, monitor: Any, *, interval_seconds: int = 20) -> None:
    """Watch the monitor's cached snapshot; NEVER refresh Codex quota here."""
    previous: str | None = None
    while True:
        try:
            snapshot = monitor.snapshot()
            fingerprint = json.dumps(snapshot, sort_keys=True, default=str)
            if previous is not None and fingerprint != previous:
                bus.publish("quota.changed", {
                    "status": str(snapshot.get("status") or "unavailable")[:32],
                    "observed_at": snapshot.get("observed_at"),
                })
            previous = fingerprint
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("live_quota_snapshot_failed")
        await asyncio.sleep(interval_seconds)


def create_live_router(*, require_token: Callable[..., None], bus: LiveEventBus) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard/api/events", dependencies=[Depends(require_token)], include_in_schema=True)
    async def live_events(request: Request) -> StreamingResponse:
        # Allocate before response is committed so a full client limit returns
        # a regular authenticated JSON 503, never a failed partial SSE stream.
        client_id, queue = bus.subscribe()

        async def stream():
            try:
                yield ": connected\nretry: 3000\n\n"
                yield sse_frame("ready", {"replay": False, "snapshot_required": True})
                while not await request.is_disconnected():
                    try:
                        event_id, event, data = await asyncio.wait_for(queue.get(), timeout=15)
                        yield sse_frame(event, data, event_id=event_id)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                bus.unsubscribe(client_id)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        })

    return router
