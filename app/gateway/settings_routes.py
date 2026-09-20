"""Authenticated, explicitly bounded settings control-plane API (Module 7A).

Neither this module nor the manager imports the FastAPI app. A saved restart-
required change does not mutate the live worker pool, timeouts or quota monitor.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends

from .config import Settings
from .contracts import GatewayError
from .generation_state import GenerationRuntime
from .job_history import JobHistory
from .job_store import JobStore
from .settings_manager import SettingsManager, SettingsValidationError
from .live_events import LiveEventBus

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

    return router
