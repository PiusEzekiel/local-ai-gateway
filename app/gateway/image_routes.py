"""Image-generation API, reference telemetry, and artifact responses.

Receives one shared GenerationContext. No global semaphore or duplicate store.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from .contracts import GatewayError, ImageReference, ImageRequest, ImageRunResult
from .generation_state import GenerationContext, episode_turn

LOG = logging.getLogger("uvicorn.error")


def create_image_router(ctx: GenerationContext) -> APIRouter:
    router = APIRouter()

    def undo_unregistered_artifact(artifact: dict[str, Any]) -> None:
        """Best-effort compensation when SQLite registration/linking fails.

        Never delete files if SQLite rollback fails: preserving an orphan with
        a DB row is safer than knowingly creating a broken gallery URL.
        """
        if not ctx.artifact_store:
            return
        if ctx.job_store:
            try:
                ctx.job_store.rollback_artifact_record(artifact["id"])
            except Exception:
                LOG.exception("artifact_db_rollback_failed artifact_id=%s", artifact["id"])
                return
        try:
            ctx.artifact_store.delete_for_retention(artifact)
        except Exception:
            LOG.exception("artifact_file_rollback_failed artifact_id=%s", artifact["id"])

    @router.post("/v1/images/generations", dependencies=[Depends(ctx.require_token)])
    async def image_generation(request: ImageRequest) -> FileResponse:

        started = time.monotonic()
        model = request.codex_model or ctx.runtime.selected_model
        timeout_seconds = (request.timeout_seconds if request.timeout_seconds is not None
                           else ctx.settings.image_timeout_seconds)

        LOG.info(
            "image_request_received request_id=%s model=%s reference_count=%s prompt_chars=%s timeout_seconds=%s",
            request.request_id,
            model,
            len(request.reference_images),
            len(request.prompt),
            timeout_seconds,
        )

        job = ctx.history.add(request.request_id, "image", model, len(request.reference_images))
        # Only user text, never reference URLs, and only with explicit opt-in.
        ctx.retain_prompt(job, request.prompt)

        if ctx.job_store and request.reference_images:
            try:
                ctx.job_store.save_references(
                    job["id"], [{"id": reference.id} for reference in request.reference_images],
                )
            except Exception:
                LOG.exception("telemetry_reference_persist_failed job_id=%s", job["id"])

        async with ctx.counter_lock:
            if ctx.runtime.pending >= ctx.settings.max_concurrency + ctx.settings.max_queue:
                error = GatewayError(
                    "busy",
                    "Gateway queue is full; retry later.",
                    429,
                )
                ctx.history.update(job, status="failed", stage="Queue full", error=error)
                raise error

            ctx.runtime.pending += 1
            ctx.worker_change()

            LOG.info(
                "image_request_accepted request_id=%s pending=%s max_concurrency=%s max_queue=%s",
                request.request_id,
                ctx.runtime.pending,
                ctx.settings.max_concurrency,
                ctx.settings.max_queue,
            )

        acquired = False

        try:
            try:
                await asyncio.wait_for(
                    ctx.slots.acquire(),
                    timeout=timeout_seconds,
                )
                acquired = True
            except asyncio.TimeoutError as exc:
                raise GatewayError(
                    "timeout",
                    "Timed out waiting for a Codex worker.",
                    504,
                ) from exc

            queue_wait_ms = round((time.monotonic() - started) * 1000)

            LOG.info(
                "image_worker_acquired request_id=%s queue_wait_ms=%s",
                request.request_id,
                queue_wait_ms,
            )

            ctx.history.worker_acquired(job, queue_wait_ms)

            remaining = timeout_seconds - (time.monotonic() - started)

            if remaining <= 0:
                raise GatewayError(
                    "timeout",
                    "Timed out waiting for a Codex worker.",
                    504,
                )

            def image_progress(stage: str) -> None:
                fields: dict[str, Any] = {}
                if stage.startswith("Downloading reference image") and not job.get("reference_download_started_at"):
                    fields.update(reference_download_started_at=datetime.now(timezone.utc).isoformat(),
                                  _reference_started=time.monotonic())
                if stage == "Reference images ready":
                    fields["reference_download_completed_at"] = datetime.now(timezone.utc).isoformat()
                    if job.get("_reference_started") is not None:
                        fields["reference_download_ms"] = round(
                            (time.monotonic() - job["_reference_started"]) * 1000
                        )
                if stage == "Launching Codex":
                    ctx.history.codex_started(job, stage)
                    return
                ctx.history.update(job, status="running", stage=stage, **fields)

            def persist_reference(index: int, reference: ImageReference, path: Path) -> None:
                # A validated reference download must be recorded even when
                # artifact persistence/gallery storage is unavailable.
                ctx.history.reference_ready(job, index + 1)
                if not ctx.artifact_store or not ctx.job_store:
                    return
                artifact = None
                try:
                    reference_mime = {
                        ".png": "image/png", ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg", ".webp": "image/webp",
                    }[path.suffix.lower()]
                    artifact = ctx.artifact_store.create(
                        job_id=job["id"], source=path, mime_type=reference_mime,
                        artifact_type="reference",
                    )
                    ctx.job_store.save_artifact(artifact)
                    ctx.job_store.set_reference_artifact(job["id"], index + 1, artifact["id"])
                except Exception:
                    if artifact is not None:
                        undo_unregistered_artifact(artifact)
                    LOG.exception(
                        "reference_artifact_persist_failed job_id=%s index=%s reference_id=%s",
                        job["id"], index + 1, reference.id or "none",
                    )

            async with episode_turn(ctx, request.episode_title) as previous_thread:
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise GatewayError("timeout", "Timed out waiting for the episode session.", 504)
                session_args = ({"persistent": True, "session_id": previous_thread}
                                if ctx.sessions and request.episode_title else {})
                try:
                    image_result = await ctx.runner.run_image(
                        prompt=request.prompt,
                        timeout_seconds=remaining,
                        reference_images=request.reference_images,
                        model=model,
                        request_id=request.request_id,
                        progress=image_progress,
                        reference_ready=persist_reference,
                        **session_args,
                    )
                except BaseException:
                    if previous_thread and ctx.sessions and request.episode_title:
                        ctx.sessions.mark_uncertain(request.episode_title)
                    raise
                if isinstance(image_result, tuple):
                    image_result = ImageRunResult(image_result[0], image_result[1], None, None)

                ctx.history.codex_completed(job)
                ctx.history.usage(job, image_result.usage)

                image_path = image_result.path
                mime_type = image_result.mime_type
                artifact_started = time.monotonic()
                # The original image MUST be durable. Reference thumbnails remain
                # optional, but never respond 200 from Codex's source path when the
                # gallery store or the SQLite artifact registration is unavailable.
                if not ctx.artifact_store or not ctx.job_store:
                    raise GatewayError(
                        "artifact_persist_failed",
                        "Image was generated but durable storage is unavailable. Retry later.",
                        503,
                    )
                artifact = None
                try:
                    artifact = ctx.artifact_store.create(
                        job_id=job["id"], source=image_path, mime_type=mime_type,
                    )
                    ctx.job_store.save_artifact(artifact)
                except Exception as exc:
                    LOG.exception("artifact_persist_failed job_id=%s", job["id"])
                    if artifact is not None:
                        undo_unregistered_artifact(artifact)
                    raise GatewayError(
                        "artifact_persist_failed",
                        "Image was generated but could not be saved. Retry later.",
                        503,
                    ) from exc
                image_path = Path(artifact["storage_path"])
                # Commit the session only once the current image has been durably
                # published. Keep the episode lock through publication, otherwise
                # the next turn could reuse/overwrite a source file still in use.
                if ctx.sessions and request.episode_title:
                    ctx.sessions.record(request.episode_title, image_result.thread_id,
                                        image_result.usage)
                ctx.history.update(
                    job, status="running", stage="Artifact ready",
                    artifact_id=artifact["id"], artifact_ready_at=artifact["created_at"],
                    artifact_processing_ms=round((time.monotonic() - artifact_started) * 1000),
                )

                ctx.history.update(
                    job,
                    status="completed",
                    stage="Image ready",
                )

                LOG.info(
                    "image_request_completed request_id=%s duration_ms=%s mime_type=%s output_file=%s",
                    request.request_id,
                    round((time.monotonic() - started) * 1000),
                    mime_type,
                    image_path.name,
                )

                return FileResponse(
                    image_path,
                    media_type=mime_type,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Request-ID": request.request_id,
                    },
                )

        except GatewayError as error:
            ctx.history.update(
                job,
                status="failed",
                stage="Image generation failed",
                error=error,
            )

            LOG.warning(
                "image_request_failed request_id=%s duration_ms=%s error=%s error_message=%s",
                request.request_id,
                round((time.monotonic() - started) * 1000),
                error.kind,
                error.message,
            )
            raise

        except Exception as exc:
            # Do not hide unexpected Python exceptions behind "internal_error".
            # LOG.exception includes the traceback in the local gateway log.
            LOG.exception(
                "image_request_unexpected_failure request_id=%s exception=%s",
                request.request_id,
                type(exc).__name__,
            )

            ctx.history.update(
                job,
                status="failed",
                stage="Gateway failed",
                error=GatewayError(
                    "internal_error",
                    "The gateway failed unexpectedly.",
                    500,
                ),
            )
            raise

        finally:
            if acquired:
                ctx.slots.release()

            async with ctx.counter_lock:
                ctx.runtime.pending -= 1
                ctx.worker_change()

                LOG.info(
                    "image_request_released request_id=%s pending=%s",
                    request.request_id,
                    ctx.runtime.pending,
                )

    return router
