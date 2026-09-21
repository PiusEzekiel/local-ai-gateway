"""Local AI Gateway application composition root and HTTP routes.

Modules contain contracts, CLI execution, reference downloading, job history,
JSON schema validation and diagnostics. Keep the n8n-facing /v1 API stable.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .artifact_store import ArtifactStore
from .job_store import JobStore
from .quota_monitor import QuotaMonitor
from .settings_manager import SettingsManager, apply_saved_settings
from .config import (
    DATA_DIR, DEFAULT_MODEL, MODELS, SETTINGS_PATH, Settings,
    find_codex_executable, validate_quota_poll_seconds,
    validate_reference_cache_settings,
)
from .contracts import (
    ChatCompletionRequest, ChatMessage, ChatResponseFormat, GatewayError,
    GenerateRequest, ImageReference, ImageRequest, ImageRunResult,
    ModelSelection, RunResult,
)
from .codex_diagnostics import codex_failure_diagnostic, codex_event_summary, classify_codex_failure
from .job_history import JobHistory
from .schema_validation import SCHEMA_DIR, MAX_SCHEMA_BYTES, resolve_schema
from .reference_images import (
    MAX_REFERENCE_IMAGE_BYTES, allowed_reference_host, detect_image_extension,
    download_reference_image,
)
from .reference_cache import ReferenceCache
from .episode_sessions import EpisodeSessions
from .codex_runner import CodexRunner as _CodexRunner, extract_usage, used_web_search
from .dashboard_routes import create_dashboard_router
from .generation_routes import create_generation_router
from .settings_routes import create_settings_router
from .retention import RetentionService
from .retention_routes import create_retention_router
from .system_routes import create_system_router
from .live_events import LiveEventBus, create_live_router, watch_quota

LOG = logging.getLogger("uvicorn.error")


class CodexRunner(_CodexRunner):
    """Compatibility export for callers patching gateway.app.download_reference_image.

    Older tests/tools patch the downloader through gateway.app; inject that
    dependency explicitly without binding the core runner back to app.py.
    """

    async def run_image(self, **kwargs: Any) -> ImageRunResult:
        if self.reference_cache is None:
            kwargs.setdefault("reference_downloader", download_reference_image)
        return await super().run_image(**kwargs)


def create_app(settings: Settings | None = None, runner: CodexRunner | None = None,
               job_store: JobStore | None = None, artifact_store: ArtifactStore | None = None,
               quota_monitor: QuotaMonitor | None = None) -> FastAPI:

    # Construct settings before the runner/worker pool/quota monitor: persisted
    # restart-required settings must take effect on THIS startup, not midway
    # through an already running gateway. Injected Settings remain authoritative
    # for test instances and other programmatic callers.
    use_saved_settings = settings is None
    runner_was_provided = runner is not None
    settings_manager = SettingsManager(SETTINGS_PATH, MODELS)
    if use_saved_settings:
        settings = apply_saved_settings(Settings.from_env(), settings_manager.load(), MODELS)
    assert settings is not None
    # Environment values and injected Settings must obey the same bounds as
    # dashboard PATCH and the real monitor constructor. Fail clearly before
    # creating stores, workers, or an unavailable quota monitor.
    validate_quota_poll_seconds(settings.quota_poll_seconds)
    validate_reference_cache_settings(
        settings.reference_cache_enabled,
        settings.reference_cache_ttl_seconds,
        settings.reference_cache_retention_days,
        settings.reference_cache_max_mb,
    )
    if settings.max_concurrency < 1 or settings.max_queue < 0:

        raise ValueError("Invalid concurrency settings")

    data_dir = Path(os.getenv("AI_GATEWAY_DATA_DIR") or DATA_DIR)
    if job_store is None:
        try:
            job_store = JobStore(data_dir / "gateway.sqlite3")
        except Exception:
            LOG.exception("telemetry_store_unavailable")
    if artifact_store is None:
        try:
            artifact_store = ArtifactStore(data_dir / "artifacts")
        except Exception:
            LOG.exception("artifact_store_unavailable")

    reference_cache = None
    if job_store is not None:
        try:
            reference_cache = ReferenceCache(
                data_dir / "reference-cache",
                job_store,
                ttl_seconds=settings.reference_cache_ttl_seconds,
                retention_days=settings.reference_cache_retention_days,
                max_bytes=settings.reference_cache_max_mb * 1024 * 1024,
            )
        except Exception:
            LOG.exception("reference_cache_unavailable")

    episode_sessions = None
    if settings.episode_sessions_enabled:
        try:
            episode_sessions = EpisodeSessions(data_dir / "episode-sessions.sqlite3")
        except Exception as exc:
            # Do not silently revert to independent sessions for an opted-in user.
            raise RuntimeError("Episode session storage is unavailable.") from exc

    runner = runner or CodexRunner(
        [settings.codex_exe], model=settings.model,
        reference_cache=reference_cache if settings.reference_cache_enabled else None,
    )

    if job_store:
        try:
            interrupted = job_store.mark_interrupted_jobs()
            if interrupted:
                LOG.warning("telemetry_reconciled_interrupted_jobs count=%s", interrupted)
        except Exception:
            LOG.exception("telemetry_reconciliation_failed")

    live_bus = LiveEventBus()
    history = JobHistory(job_store, store_diagnostics=settings.store_diagnostics, events=live_bus)
    retention = RetentionService(job_store, artifact_store) if job_store and artifact_store else None

    if quota_monitor is None and settings.quota_monitor_enabled and not runner_was_provided:
        quota_monitor = QuotaMonitor(
            settings.codex_exe,
            job_store,
            poll_seconds=settings.quota_poll_seconds,
            warning_remaining=settings.quota_warning_remaining_percent,
            critical_remaining=settings.quota_critical_remaining_percent,
        )

    selected_model = settings.model if settings.model in MODELS else DEFAULT_MODEL



    @asynccontextmanager

    async def lifespan(_: FastAPI):

        if len(settings.api_token) < 24:

            raise RuntimeError("Set AI_GATEWAY_TOKEN to a random value of at least 24 characters.")

        if not Path(settings.codex_exe).is_file():

            raise RuntimeError(f"Codex executable not found: {settings.codex_exe}")

        if settings.episode_sessions_enabled and not runner_was_provided:
            # Read-only, quota-free CLI capability check. We do not silently
            # downgrade opted-in episodes to ephemeral runs.
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                probe = await asyncio.create_subprocess_exec(
                    settings.codex_exe, "exec", "resume", "--help",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
                out, err = await asyncio.wait_for(probe.communicate(), timeout=10)
            except (OSError, asyncio.TimeoutError) as exc:
                raise RuntimeError("Could not verify Codex CLI episode-resume support.") from exc
            help_text = (out + err).decode("utf-8", errors="replace")
            if probe.returncode != 0 or "--image" not in help_text:
                raise RuntimeError("Installed Codex CLI does not advertise image-capable exec resume; episode sessions remain unavailable.")

        if quota_monitor:

            await quota_monitor.start()

        quota_events_task = None
        if quota_monitor:
            quota_events_task = asyncio.create_task(watch_quota(live_bus, quota_monitor))
        cleanup_task = None
        if retention and settings.auto_cleanup_enabled:
            async def cleanup_periodically():
                # Delay first sweep: startup/reconciliation should never surprise
                # users with immediate artifact deletion.
                while True:
                    await asyncio.sleep(settings.cleanup_interval_hours * 3600)
                    try:
                        result = await asyncio.to_thread(retention.run, settings)
                        history.drop_expired_jobs(result.pop('_deleted_job_ids', []))
                        live_bus.publish("storage.changed", {})
                    except Exception:
                        LOG.exception("automatic_retention_cleanup_failed")
            cleanup_task = asyncio.create_task(cleanup_periodically())
        cache_cleanup_task = None
        if reference_cache and settings.reference_cache_enabled:
            async def cleanup_reference_cache_periodically():
                while True:
                    await asyncio.sleep(settings.cleanup_interval_hours * 3600)
                    try:
                        result = await asyncio.to_thread(reference_cache.cleanup)
                        if result["removed"]:
                            live_bus.publish("storage.changed", {})
                    except Exception:
                        LOG.exception("automatic_reference_cache_cleanup_failed")
            cache_cleanup_task = asyncio.create_task(cleanup_reference_cache_periodically())
        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except asyncio.CancelledError:
                    pass
            if cache_cleanup_task is not None:
                cache_cleanup_task.cancel()
                try:
                    await cache_cleanup_task
                except asyncio.CancelledError:
                    pass
            if quota_events_task is not None:
                quota_events_task.cancel()
                try:
                    await quota_events_task
                except asyncio.CancelledError:
                    pass
            live_bus.close()
            if episode_sessions is not None:
                episode_sessions.close()
            if quota_monitor:
                await quota_monitor.stop()



    app = FastAPI(title="Local AI Gateway", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.state.history = history
    app.state.live_events = live_bus
    app.state.job_store = job_store
    app.state.reference_cache = reference_cache
    app.state.episode_sessions = episode_sessions
    app.state.artifact_store = artifact_store
    app.state.reference_cache = reference_cache
    app.state.quota_monitor = quota_monitor
    app.state.settings_manager = settings_manager
    app.state.retention = retention
    app.state.effective_settings = settings

    app.state.started_monotonic = time.monotonic()

    dashboard_dir = Path(__file__).parent / "dashboard"
    if dashboard_dir.is_dir():
        app.mount("/dashboard/assets", StaticFiles(directory=dashboard_dir), name="dashboard-assets")



    @app.get("/", include_in_schema=False)

    async def dashboard() -> FileResponse:

        return FileResponse(Path(__file__).parent / "dashboard.html", headers={"Cache-Control": "no-store"})



    async def require_token(authorization: str | None = Header(default=None)) -> None:

        supplied = authorization.removeprefix("Bearer ") if authorization else ""

        if not authorization or not authorization.startswith("Bearer ") or not secrets.compare_digest(supplied, settings.api_token):

            raise GatewayError("unauthorized", "Invalid or missing bearer token.", 401)



    # Generation endpoints own the single worker pool and mutable model state.
    # /health and dashboard read the same runtime; there are no stale snapshots.
    generation_router, generation_runtime = create_generation_router(
        require_token=require_token,
        settings=settings,
        runner=runner,
        history=history,
        job_store=job_store,
        artifact_store=artifact_store,
        settings_manager=settings_manager,
        selected_model=selected_model,
        events=live_bus,
        sessions=episode_sessions,
    )
    app.state.generation_runtime = generation_runtime

    @app.get("/health", dependencies=[Depends(require_token)])

    async def health() -> dict[str, Any]:

        return {"status": "ok", "codex_available": True, "active_or_queued": generation_runtime.pending, "default_model": generation_runtime.selected_model}



    @app.get("/v1/jobs", dependencies=[Depends(require_token)])

    async def jobs() -> dict[str, Any]:

        return {"jobs": [history.public(job) for job in history.jobs]}


    @app.get("/dashboard/api/episode-sessions", dependencies=[Depends(require_token)])
    async def episode_session_list() -> dict[str, Any]:
        return {"enabled": settings.episode_sessions_enabled,
                "sessions": episode_sessions.list() if episode_sessions else []}

    @app.post("/dashboard/api/episode-sessions/reset", dependencies=[Depends(require_token)])
    async def episode_session_reset(body: dict[str, Any]) -> dict[str, Any]:
        if episode_sessions is None:
            raise GatewayError("unavailable", "Episode sessions are disabled.", 503)
        if (not isinstance(body, dict) or set(body) != {"episode_title", "confirmation"}
                or body.get("confirmation") != "RESET_EPISODE_SESSION"
                or not isinstance(body.get("episode_title"), str)):
            raise GatewayError("invalid_request", "Confirm an episode_title with RESET_EPISODE_SESSION.", 422)
        # Reset cannot interleave with a Codex turn for the same episode.
        async with episode_sessions.lock(body["episode_title"]):
            removed = episode_sessions.reset(body["episode_title"])
        return {"reset": removed}

    # All /dashboard/api/* endpoints are registered together and receive
    # existing stores/state rather than importing the app (avoids cycles).
    app.include_router(create_dashboard_router(
        require_token=require_token,
        settings=settings,
        history=history,
        job_store=job_store,
        artifact_store=artifact_store,
        quota_monitor=quota_monitor,
        get_selected_model=lambda: generation_runtime.selected_model,
        started_monotonic=app.state.started_monotonic,
    ))

    # Module 7A: dedicated settings API. No worker pool or monitor is rebuilt
    # by a PATCH; restart-required values are persisted for next startup.
    app.include_router(create_settings_router(
        require_token=require_token,
        manager=settings_manager,
        effective_settings=settings,
        generation_runtime=generation_runtime,
        job_store=job_store,
        history=history,
        events=live_bus,
        reference_cache=reference_cache,
    ))

    # Module 7C: read-only preview plus explicit, confirmed cleanup API.
    app.include_router(create_retention_router(
        require_token=require_token,
        retention=retention,
        effective_settings=settings,
        history=history,
        events=live_bus,
    ))

    # Module 7D: safe, authenticated system status (never expose host paths).
    app.include_router(create_system_router(
        require_token=require_token, settings=settings, runtime=generation_runtime,
        history=history, job_store=job_store,
        started_monotonic=app.state.started_monotonic, gateway_version=app.version,
    ))

    # Module 8A: authenticated, read-only SSE notification channel. Browser
    # integration is separate (8B); original polling routes remain unchanged.
    app.include_router(create_live_router(require_token=require_token, bus=live_bus))

    # Keep the legacy /v1 endpoint order and all existing wire contracts.
    app.include_router(generation_router)

    def generic_error(request: Request, kind: str, message: str, status_code: int) -> JSONResponse:

        task = request.url.path.removeprefix("/v1/") if request.url.path.startswith("/v1/") else None

        return JSONResponse(status_code=status_code, content={

            "success": False,

            "request_id": None,

            "task": task,

            "response": None,

            "raw_response": None,

            "duration_ms": 0,

            "usage": None,

            "web_search_used": False,

            "error": {"type": kind, "message": message},

        })



    @app.exception_handler(GatewayError)

    async def gateway_exception_handler(request: Request, exc: GatewayError) -> JSONResponse:

        return generic_error(request, exc.kind, exc.message, exc.status_code)



    @app.exception_handler(RequestValidationError)

    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:

        fields = sorted({str(error["loc"][-1]) for error in exc.errors() if error.get("loc")})

        message = "Invalid request field(s): " + ", ".join(fields) if fields else "Invalid request body."

        return generic_error(request, "invalid_request", message, 422)



    @app.exception_handler(Exception)

    async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:

        LOG.exception("unexpected_gateway_error")

        return generic_error(request, "internal_error", "The gateway failed unexpectedly.", 500)



    return app




app = create_app()
