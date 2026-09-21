"""Authenticated, explicitly bounded settings control-plane API (Module 7A).

Neither this module nor the manager imports the FastAPI app. A saved restart-
required change does not mutate the live worker pool, timeouts or quota monitor.
"""
from __future__ import annotations

import logging
import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Depends

from .config import Settings
from .contracts import GatewayError
from .generation_state import GenerationRuntime
from .job_history import JobHistory
from .job_store import JobStore
from .settings_manager import SettingsManager, SettingsValidationError
from .live_events import LiveEventBus
from .reference_cache import ReferenceCache

LOG = logging.getLogger("uvicorn.error")


def create_settings_router(
    *,
    require_token: Callable[..., None],
    manager: SettingsManager,
    effective_settings: Settings,
    generation_runtime: GenerationRuntime,
    job_store: JobStore | None = None,
    history: JobHistory | None = None,
    events: LiveEventBus | None = None,
    reference_cache: ReferenceCache | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard/api/settings", dependencies=[Depends(require_token)])
    async def read_settings() -> dict[str, Any]:
        return manager.describe(effective_settings, generation_runtime.selected_model)

    @router.patch("/dashboard/api/settings", dependencies=[Depends(require_token)])
    async def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            saved = manager.update(patch, effective=effective_settings)
        except SettingsValidationError as exc:
            raise GatewayError("invalid_request", str(exc), 422) from exc
        # The existing model change pathway is live; keep health, dashboard,
        # all generation routes and future requests reading the SAME state.
        if "model" in patch:
            generation_runtime.selected_model = saved["model"]
            if events:
                events.publish("model.changed", {"model": generation_runtime.selected_model})
        LOG.info("settings_updated keys=%s pending_restart=%s",
                 ",".join(sorted(patch)),
                 any(key != "model" for key in patch))
        return manager.describe(effective_settings, generation_runtime.selected_model)

    @router.get("/dashboard/api/privacy/storage", dependencies=[Depends(require_token)])
    async def privacy_storage() -> dict[str, Any]:
        """Counts only; never return stored text from the privacy overview."""
        if job_store is None:
            return {"available": False, "retained_text": None}
        return {"available": True, "retained_text": job_store.content_stats()}

    @router.post("/dashboard/api/privacy/purge", dependencies=[Depends(require_token)])
    async def privacy_purge(body: dict[str, Any]) -> dict[str, Any]:
        """Explicit destructive action; no implicit deletion when toggles change.

        This does not purge logs, images, or temporary Codex files. SQLite WAL
        may retain forensic copies until VACUUM/checkpoint, so no secure erase
        guarantee is made.
        """
        if not job_store:
            raise GatewayError("unavailable", "Telemetry store is unavailable.", 503)
        if (not isinstance(body, dict) or body.get("confirmation") != "DELETE_RETAINED_DATA"
                or body.get("scope") not in {"text", "diagnostics", "both"}
                or set(body) != {"confirmation", "scope"}):
            raise GatewayError("invalid_request", "Confirm deletion with confirmation=DELETE_RETAINED_DATA and a valid scope.", 422)
        cleared_text = job_store.purge_content() if body["scope"] in {"text", "both"} else 0
        cleared_diag = job_store.purge_diagnostic_previews() if body["scope"] in {"diagnostics", "both"} else 0
        if body["scope"] in {"diagnostics", "both"} and history:
            for job in history.jobs:
                job["diagnostic_preview"] = None
        if events:
            events.publish("storage.changed", {})
        LOG.warning("privacy_purge_completed scope=%s text_rows=%s diagnostic_rows=%s",
                    body["scope"], cleared_text, cleared_diag)
        return {"scope": body["scope"], "retained_text_rows_deleted": cleared_text,
                "diagnostic_previews_cleared": cleared_diag}

    @router.get("/dashboard/api/reference-cache", dependencies=[Depends(require_token)])
    async def reference_cache_stats() -> dict[str, Any]:
        if reference_cache is None:
            return {"available": False, "enabled": False, "stats": None, "analytics": None}
        return {"available": True, "enabled": effective_settings.reference_cache_enabled,
                "stats": await asyncio.to_thread(reference_cache.stats),
                "analytics": reference_cache.analytics()}

    @router.post("/dashboard/api/reference-cache/clear", dependencies=[Depends(require_token)])
    async def reference_cache_clear(body: dict[str, Any]) -> dict[str, Any]:
        if reference_cache is None:
            raise GatewayError("unavailable", "Reference cache is unavailable.", 503)
        if (not isinstance(body, dict)
                or body.get("confirmation") != "DELETE_REFERENCE_CACHE"
                or body.get("scope") not in {"expired", "unused"}
                or set(body) != {"confirmation", "scope"}):
            raise GatewayError("invalid_request", "Confirm cache deletion with confirmation=DELETE_REFERENCE_CACHE and a valid scope.", 422)
        result = await asyncio.to_thread(
            reference_cache.cleanup,
            clear_all_unused=body["scope"] == "unused",
            clear_expired=body["scope"] == "expired",
        )
        if events and result["removed"]:
            events.publish("storage.changed", {})
        LOG.warning("reference_cache_cleanup_completed scope=%s removed=%s bytes=%s",
                    body["scope"], result["removed"], result["bytes_removed"])
        return {"scope": body["scope"], "result": result, "stats": reference_cache.stats()}

    return router
