"""Authenticated read-only system telemetry, separated from app composition."""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from fastapi import APIRouter, Depends

from .config import Settings
from .generation_state import GenerationRuntime
from .job_history import JobHistory
from .job_store import JobStore
from .system_info import system_snapshot


def create_system_router(*, require_token: Callable[..., None], settings: Settings,
                         runtime: GenerationRuntime, history: JobHistory, job_store: JobStore | None,
                         started_monotonic: float, gateway_version: str) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard/api/system", dependencies=[Depends(require_token)])
    async def system_info() -> dict[str, Any]:
        # Subprocess --version is bounded, but it must not block the event loop.
        return await asyncio.to_thread(
            system_snapshot, codex_exe=settings.codex_exe,
            db_path=job_store.path if job_store else None,
            started_monotonic=started_monotonic,
            gateway_version=gateway_version,
            active=sum(1 for job in history.jobs if job["status"] == "running"),
            queued=sum(1 for job in history.jobs if job["status"] == "queued"),
            concurrency=settings.max_concurrency, max_queue=settings.max_queue,
        )

    return router
