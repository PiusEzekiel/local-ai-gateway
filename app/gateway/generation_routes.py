"""Compose authenticated /v1 routes around one per-app GenerationContext.

Keep routes and wire contracts unchanged. The image/text modules own business
handlers; this factory owns the model selector and explicit dependency wiring.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends

from .artifact_store import ArtifactStore
from .config import MODELS, Settings
from .contracts import ModelSelection
from .generation_state import GenerationContext, GenerationRuntime
from .image_routes import create_image_router
from .text_routes import create_text_router
from .job_history import JobHistory
from .job_store import JobStore
from .settings_manager import SettingsManager
from .live_events import LiveEventBus

LOG = logging.getLogger("uvicorn.error")


def create_generation_router(
    *,
    require_token: Callable[..., None],
    settings: Settings,
    runner: Any,
    history: JobHistory,
    job_store: JobStore | None,
    artifact_store: ArtifactStore | None,
    settings_manager: SettingsManager,
    selected_model: str,
    events: LiveEventBus | None = None,
) -> tuple[APIRouter, GenerationRuntime]:
    """Register the original /v1 paths with a single worker pool per app."""
    router = APIRouter()
    runtime = GenerationRuntime(selected_model=selected_model)
    ctx = GenerationContext(
        require_token=require_token,
        settings=settings,
        runner=runner,
        history=history,
        job_store=job_store,
        artifact_store=artifact_store,
        settings_manager=settings_manager,
        runtime=runtime,
        slots=asyncio.Semaphore(settings.max_concurrency),
        counter_lock=asyncio.Lock(),
        events=events,
    )

    @router.get("/v1/models", dependencies=[Depends(require_token)])

    async def models() -> dict[str, Any]:

        return {"default_model": runtime.selected_model, "models": list(MODELS)}



    @router.put("/v1/models/default", dependencies=[Depends(require_token)])

    async def set_default_model(choice: ModelSelection) -> dict[str, str]:


        settings_manager.save_model(choice.model)

        runtime.selected_model = choice.model
        if events:
            events.publish("model.changed", {"model": runtime.selected_model})

        LOG.info("default_model_changed model=%s", runtime.selected_model)

        return {"default_model": runtime.selected_model}



    @router.get("/v1/capabilities", dependencies=[Depends(require_token)])

    async def capabilities() -> dict[str, Any]:

        return {

            "provider": "codex",

            "routes": ["/v1/generate", "/v1/research", "/v1/chat/completions", "/v1/images/generations", "/v1/jobs", "/v1/models"],

            "models": {"default": runtime.selected_model, "available": list(MODELS), "per_request_field": "codex_model"},

            "chat": {"roles": ["system", "user", "assistant"], "response_formats": ["text", "json_object"], "stream": False},

            "images": {"request": "POST JSON {prompt, reference_images?}", "response": "image file", "provider": "codex"},

        }



    # Preserve original registration order: image before text/research/chat.
    router.include_router(create_image_router(ctx))
    router.include_router(create_text_router(ctx))
    return router, runtime
