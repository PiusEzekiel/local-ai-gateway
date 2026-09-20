"""Per-application worker pool and shared model/queue state.

The gateway's Settings manager remains the source of persisted default-model
state. Concurrency is fixed for each app instance until restart.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from dataclasses import dataclass
import logging
from typing import Any, Callable

from .artifact_store import ArtifactStore
from .config import Settings
from .job_history import JobHistory
from .job_store import JobStore
from .settings_manager import SettingsManager
from .live_events import LiveEventBus
from .episode_sessions import EpisodeSessions

LOG = logging.getLogger("uvicorn.error")


@dataclass
class GenerationRuntime:
    """Mutable app-scoped counters and selected model, shared by all routers."""
    selected_model: str
    pending: int = 0


@dataclass
class GenerationContext:
    """Explicit dependencies for image and text routes; no app import cycle."""
    require_token: Callable[..., None]
    settings: Settings
    runner: Any
    history: JobHistory
    job_store: JobStore | None
    artifact_store: ArtifactStore | None
    settings_manager: SettingsManager
    runtime: GenerationRuntime
    slots: asyncio.Semaphore
    counter_lock: asyncio.Lock
    events: LiveEventBus | None = None
    sessions: EpisodeSessions | None = None

    def worker_change(self) -> None:
        if self.events:
            self.events.publish("workers.changed", {
                "pending": self.runtime.pending,
                "concurrency": self.settings.max_concurrency,
                "max_queue": self.settings.max_queue,
            })

    def retain_prompt(self, job: dict[str, Any], prompt: str) -> None:
        """Best-effort, OPT-IN SQLite write; never affect a Codex request."""
        if self.settings.store_prompts and self.job_store:
            try:
                self.job_store.save_prompt(job["id"], prompt)
            except Exception:
                LOG.exception("retained_prompt_save_failed job_id=%s", job["id"])

    def retain_output(self, job: dict[str, Any], output: str) -> None:
        """Only completed text responses, not image artifacts or CLI stdout."""
        if self.settings.store_outputs and self.job_store:
            try:
                self.job_store.save_output(job["id"], output)
            except Exception:
                LOG.exception("retained_output_save_failed job_id=%s", job["id"])


@asynccontextmanager
async def episode_turn(ctx: GenerationContext, title: str | None) -> AsyncIterator[str | None]:
    """One in-flight turn per title across image, text and research routes."""
    if not title or not ctx.sessions:
        yield None
        return
    async with ctx.sessions.lock(title):
        yield ctx.sessions.get(title)
