"""Authenticated dashboard/control-plane HTTP routes (Module 6R.2).

The generation API remains in app.py. Every dependency is passed explicitly
by the composition root; importing this module never creates a FastAPI app,
a database, an artifact store, or a Codex subprocess.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from .analytics import resolve_analytics_range
from .artifact_store import ArtifactStore
from .config import Settings
from .contracts import GatewayError
from .diagnostics import redact_diagnostic, build_diagnostic_bundle
from .job_history import JobHistory
from .job_store import JobStore
from .quota_monitor import QuotaMonitor

LOG = logging.getLogger("uvicorn.error")


def create_dashboard_router(
    *,
    require_token: Callable[..., None],
    settings: Settings,
    history: JobHistory,
    job_store: JobStore | None,
    artifact_store: ArtifactStore | None,
    quota_monitor: QuotaMonitor | None,
    get_selected_model: Callable[[], str],
    started_monotonic: float,
) -> APIRouter:
    """Assemble all dashboard routes with explicit, per-app dependencies.

    Keep the original paths and endpoint ordering; /diagnostics/summary must
    be registered before /diagnostics/{job_id}. No DB schema changes are needed.
    """
    router = APIRouter()

    def encode_cursor(created_at: str, job_id: str) -> str:
        value = json.dumps([created_at, job_id], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


    def decode_cursor(cursor: str | None) -> tuple[str | None, str | None]:
        if not cursor:
            return None, None
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            value = json.loads(raw)
            if not (isinstance(value, list) and len(value) == 2 and all(isinstance(item, str) for item in value)):
                raise ValueError
            return value[0], value[1]
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise GatewayError("invalid_request", "Invalid jobs cursor.", 422) from exc


    def dashboard_job(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        existing_error = result.get("error")
        error_type = result.pop("error_type", None)
        error_message = result.pop("error_message", None)
        result["error"] = {"type": error_type, "message": error_message} if error_type else existing_error
        usage_fields = (
            "input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens",
            "reasoning_output_tokens", "total_tokens",
        )
        usage = {field: result.pop(field, None) for field in usage_fields}
        result["usage"] = usage if any(value is not None for value in usage.values()) else None
        if result.get("artifact_id"):
            artifact_id = result["artifact_id"]
            result["artifact"] = {
                "id": artifact_id,
                "url": f"/dashboard/api/artifacts/{artifact_id}",
                "thumbnail_url": f"/dashboard/api/artifacts/{artifact_id}/thumbnail",
            }
        return result


    def public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
        artifact_id = artifact["id"]
        return {
            key: artifact.get(key) for key in (
                "id", "artifact_type", "mime_type", "width", "height", "size_bytes", "created_at"
            )
        } | {
            "url": f"/dashboard/api/artifacts/{artifact_id}",
            "thumbnail_url": f"/dashboard/api/artifacts/{artifact_id}/thumbnail",
        }


    def current_quota() -> dict[str, Any]:
        if quota_monitor:
            return quota_monitor.snapshot()
        return {
            "status": "unavailable", "observed_at": None,
            "reason": "monitor_disabled", "buckets": [], "reset_credits": None,
        }


    @router.get("/dashboard/api/summary", dependencies=[Depends(require_token)])
    async def dashboard_summary() -> dict[str, Any]:
        active = sum(1 for job in history.jobs if job["status"] == "running")
        queued = sum(1 for job in history.jobs if job["status"] == "queued")
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today = job_store.today_summary(midnight) if job_store else {
            "jobs": 0, "successful": 0, "failed": 0, "images": 0, "total_tokens": 0,
            "cumulative_task_ms": 0, "codex_compute_ms": 0, "average_latency_ms": None,
            "p95_latency_ms": None, "recent_failures": [], "model_distribution": [],
        }
        today["success_rate"] = (
            round(today["successful"] * 100 / today["jobs"], 1) if today["jobs"] else None
        )
        return {
            "gateway": {
                "status": "healthy", "uptime_seconds": round(time.monotonic() - started_monotonic),
                "model": get_selected_model(),
            },
            "workers": {
                "active": active, "concurrency": settings.max_concurrency,
                "queued": queued, "max_queue": settings.max_queue,
            },
            "quota": current_quota(),
            "today": today,
        }


    @router.get("/dashboard/api/quota", dependencies=[Depends(require_token)])
    async def dashboard_quota(
        history_limit: int = Query(default=120, ge=1, le=1000),
    ) -> dict[str, Any]:
        history_rows = job_store.list_quota_snapshots(history_limit) if job_store else []
        return {"current": current_quota(), "history": history_rows}


    @router.post("/dashboard/api/quota/refresh", dependencies=[Depends(require_token)])
    async def dashboard_quota_refresh() -> dict[str, Any]:
        if not quota_monitor:
            return {"quota": current_quota()}
        try:
            quota = await quota_monitor.refresh_now()
        except Exception:
            LOG.warning("quota_manual_refresh_failed", exc_info=True)
            quota = current_quota()
        return {"quota": quota}


    def analytics_range(range_name: str, start: str | None, end: str | None):
        try:
            return resolve_analytics_range(range_name, start, end)
        except ValueError as exc:
            raise GatewayError("invalid_request", str(exc), 422) from exc


    @router.get("/dashboard/api/usage", dependencies=[Depends(require_token)])
    async def dashboard_usage(
        range_name: str = Query(default="24h", alias="range"),
        start: str | None = Query(default=None, max_length=64),
        end: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        selected = analytics_range(range_name, start, end)
        if not job_store:
            return {
                "range": selected.public(),
                "summary": {}, "timeline": [], "operations": [], "models": [],
                "quota": {"series": [], "pacing": []},
            }
        usage = job_store.usage_analytics(selected.start_iso, selected.end_iso, selected.bucket_seconds)
        usage["range"] = selected.public()
        usage["quota"] = job_store.quota_analytics(
            selected.start_iso, selected.end_iso, selected.bucket_seconds,
        )
        return usage


    @router.get("/dashboard/api/performance", dependencies=[Depends(require_token)])
    async def dashboard_performance(
        range_name: str = Query(default="24h", alias="range"),
        start: str | None = Query(default=None, max_length=64),
        end: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        selected = analytics_range(range_name, start, end)
        performance = job_store.performance_analytics(
            selected.start_iso, selected.end_iso,
        ) if job_store else {
            "summary": {}, "latency": {"image": {}, "text": {}}, "models": [], "errors": [],
        }
        performance["range"] = selected.public()
        return performance


    @router.get("/dashboard/api/jobs", dependencies=[Depends(require_token)])
    async def dashboard_jobs(
        limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
        status: str | None = None, task: str | None = None, search: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        if status and status not in {"queued", "running", "completed", "failed"}:
            raise GatewayError("invalid_request", "Invalid job status filter.", 422)
        if task and task not in {"generate", "research", "image"}:
            raise GatewayError("invalid_request", "Invalid job task filter.", 422)
        before_created_at, before_id = decode_cursor(cursor)
        rows = job_store.list_jobs(
            limit=limit + 1, before_created_at=before_created_at, before_id=before_id,
            status=status, task=task, search=search,
        ) if job_store else [history.public(job) for job in list(history.jobs)[:limit + 1]]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
        return {"jobs": [dashboard_job(row) for row in rows], "next_cursor": next_cursor}


    @router.get("/dashboard/api/jobs/{job_id}", dependencies=[Depends(require_token)])
    async def dashboard_job_detail(job_id: str) -> dict[str, Any]:
        row = job_store.get_job(job_id) if job_store else None
        if not row:
            memory_job = next((history.public(job) for job in history.jobs if job["id"] == job_id), None)
            if not memory_job:
                raise GatewayError("not_found", "Job was not found.", 404)
            row = memory_job
        result = dashboard_job(row)
        result["events"] = job_store.get_events(job_id) if job_store else result.get("events", [])
        stored_usage = job_store.get_usage(job_id) if job_store else result.get("usage")
        if stored_usage:
            result["usage"] = {key: stored_usage.get(key) for key in (
                "input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "total_tokens",
            )}
        references = job_store.get_references(job_id) if job_store else []
        for reference in references:
            if reference.get("artifact_id"):
                reference["url"] = f"/dashboard/api/artifacts/{reference['artifact_id']}"
                reference["thumbnail_url"] = f"/dashboard/api/artifacts/{reference['artifact_id']}/thumbnail"
        result["references"] = references
        artifact = job_store.get_artifact_for_job(job_id) if job_store else None
        if artifact:
            result["artifact"] = public_artifact(artifact)
        return {"job": result}


    # ---------------------------------------------------------------------
    # MODULE 6C — AUTHENTICATED DIAGNOSTICS API
    # ---------------------------------------------------------------------
    # These endpoints read existing schema-v5 records. They do not change
    # Codex execution, n8n responses, quotas or artifacts.

    def diagnostics_range(
        range_name: str, start: str | None, end: str | None,
    ):
        return analytics_range(range_name, start, end)

    def checked_diagnostic_task(task: str | None, operation: str | None) -> None:
        if task and task not in {"generate", "research", "image"}:
            raise GatewayError("invalid_request", "Invalid diagnostic task filter.", 422)
        if operation and operation not in {"chat", "generate", "structured", "research", "image"}:
            raise GatewayError("invalid_request", "Invalid diagnostic operation filter.", 422)

    def safe_failure_row(row: dict[str, Any]) -> dict[str, Any]:
        """Whitelist response fields so schema additions cannot leak private data."""
        bundle = history.diagnostic_bundle(row)
        return {
            "id": row.get("id"),
            "request_id": bundle["request_id"],
            "group_id": redact_diagnostic(row.get("group_id"), max_chars=128) or None,
            "attempt": row.get("attempt"),
            "task": bundle["task"],
            "operation": redact_diagnostic(row.get("operation"), max_chars=40),
            "model": bundle["model"],
            "status": "failed",
            "stage": redact_diagnostic(row.get("stage"), max_chars=240),
            "created_at": row.get("created_at"),
            "completed_at": row.get("completed_at"),
            "elapsed_ms": bundle["duration_ms"],
            "queue_ms": row.get("queue_ms"),
            "codex_ms": row.get("codex_ms"),
            "reference_download_ms": row.get("reference_download_ms"),
            "reference_count": bundle["reference_count"],
            "references_downloaded": bundle["references_downloaded"],
            "last_successful_stage": bundle["last_successful_stage"],
            "exit_code": bundle["exit_code"],
            "diagnostic_preview": bundle["diagnostic"],
            "error": {"type": bundle["error_type"], "message": bundle["message"]},
            "total_tokens": row.get("total_tokens"),
        }

    def memory_failures(
        *, since: str, until: str, task: str | None, operation: str | None,
        model: str | None,
        error_type: str | None, search: str | None,
        before_created_at: str | None = None, before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Bounded in-memory fallback if the optional telemetry DB is down."""
        result = []
        for job in history.jobs:
            created = str(job.get("created_at") or "")
            if job.get("status") != "failed" or not since <= created < until:
                continue
            if task and job.get("task") != task:
                continue
            if operation and job.get("operation") != operation:
                continue
            if model and job.get("model") != model:
                continue
            actual_type = (job.get("error") or {}).get("type")
            if error_type and actual_type != error_type:
                continue
            if search and search.casefold() not in " ".join(str(job.get(key) or "") for key in (
                "request_id", "model", "diagnostic_preview",
            )).casefold() and search.casefold() not in " ".join(str((job.get("error") or {}).get(key) or "")
                for key in ("type", "message")).casefold():
                continue
            if before_created_at and before_id and not (
                created < before_created_at or
                (created == before_created_at and job["id"] < before_id)
            ):
                continue
            result.append(history.public(job))
        return sorted(result, key=lambda row: (row["created_at"], row["id"]), reverse=True)

    @router.get("/dashboard/api/diagnostics/summary", dependencies=[Depends(require_token)])
    async def diagnostics_summary(
        range_name: str = Query(default="7d", alias="range"),
        start: str | None = Query(default=None, max_length=64),
        end: str | None = Query(default=None, max_length=64),
        task: str | None = None,
        operation: str | None = None,
        model: str | None = Query(default=None, max_length=100),
        error_type: str | None = Query(default=None, max_length=80),
        search: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        checked_diagnostic_task(task, operation)
        selected = diagnostics_range(range_name, start, end)
        if job_store:
            summary = job_store.failure_summary(
                since=selected.start_iso, until=selected.end_iso,
                task=task, operation=operation, model=model,
                error_type=error_type, search=search,
            )
            limited = False
        else:
            rows = memory_failures(
                since=selected.start_iso, until=selected.end_iso,
                task=task, operation=operation, model=model,
                error_type=error_type, search=search,
            )
            counts: dict[str, int] = {}
            models: dict[str, int] = {}
            for row in rows:
                category = str((row.get("error") or {}).get("type") or "unknown")
                counts[category] = counts.get(category, 0) + 1
                label = str(row.get("model") or "unknown")
                models[label] = models.get(label, 0) + 1
            summary = {
                "failures": len(rows),
                "image_failures": sum(row.get("task") == "image" for row in rows),
                "text_failures": sum(row.get("task") != "image" for row in rows),
                "error_types": [{"error_type": redact_diagnostic(k, max_chars=80), "failures": v}
                                for k, v in sorted(counts.items(), key=lambda p: -p[1])[:20]],
                "models": [{"model": redact_diagnostic(k, max_chars=100), "failures": v}
                           for k, v in sorted(models.items(), key=lambda p: -p[1])[:20]],
            }
            limited = True
        # Re-redact historical values written before schema-v5 sanitization.
        summary["error_types"] = [{"error_type": redact_diagnostic(row["error_type"], max_chars=80),
                                   "failures": row["failures"]} for row in summary["error_types"]]
        summary["models"] = [{"model": redact_diagnostic(row["model"], max_chars=100),
                              "failures": row["failures"]} for row in summary["models"]]
        return {"range": selected.public(), "summary": summary, "memory_only": limited}

    @router.get("/dashboard/api/diagnostics", dependencies=[Depends(require_token)])
    async def diagnostics_list(
        range_name: str = Query(default="7d", alias="range"),
        start: str | None = Query(default=None, max_length=64),
        end: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=1024),
        task: str | None = None,
        operation: str | None = None,
        model: str | None = Query(default=None, max_length=100),
        error_type: str | None = Query(default=None, max_length=80),
        search: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        checked_diagnostic_task(task, operation)
        selected = diagnostics_range(range_name, start, end)
        before_created_at, before_id = decode_cursor(cursor)
        if job_store:
            rows = job_store.list_failures(
                since=selected.start_iso, until=selected.end_iso,
                limit=limit + 1,
                before_created_at=before_created_at, before_id=before_id,
                task=task, operation=operation, model=model,
                error_type=error_type, search=search,
            )
        else:
            rows = memory_failures(
                since=selected.start_iso, until=selected.end_iso,
                before_created_at=before_created_at, before_id=before_id,
                task=task, operation=operation, model=model,
                error_type=error_type, search=search,
            )[:limit + 1]
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
        return {"range": selected.public(), "failures": [safe_failure_row(row) for row in rows],
                "next_cursor": next_cursor, "memory_only": not bool(job_store)}

    @router.get("/dashboard/api/diagnostics/{job_id}", dependencies=[Depends(require_token)])
    async def diagnostics_detail(job_id: str) -> dict[str, Any]:
        row = job_store.get_job(job_id) if job_store else None
        if row is None:
            row = next((history.public(job) for job in history.jobs if job["id"] == job_id), None)
        if row is None or row.get("status") != "failed":
            raise GatewayError("not_found", "Failed job was not found.", 404)
        events = job_store.get_diagnostic_events(job_id) if job_store else row.get("events", [])[-500:]
        safe_events = [{
            "timestamp": event.get("timestamp"),
            "elapsed_ms": event.get("elapsed_ms"),
            "level": event.get("level"),
            "stage": redact_diagnostic(event.get("stage"), max_chars=240),
        } for event in events[:500]]
        references = job_store.get_references(job_id) if job_store else []
        safe_references = [{
            "ordinal": ref.get("ordinal"),
            "reference_id": redact_diagnostic(ref.get("reference_id"), max_chars=128),
            "artifact_id": ref.get("artifact_id"),
            "thumbnail_url": f"/dashboard/api/artifacts/{ref['artifact_id']}/thumbnail"
                if ref.get("artifact_id") else None,
        } for ref in references[:10]]
        return {"job": safe_failure_row(row), "events": safe_events,
                "references": safe_references,
                "bundle": history.diagnostic_bundle(row)}

    @router.get("/dashboard/api/diagnostics/{job_id}/bundle", dependencies=[Depends(require_token)])
    async def diagnostics_bundle(job_id: str) -> dict[str, Any]:
        row = job_store.get_job(job_id) if job_store else None
        if row is None:
            row = next((history.public(job) for job in history.jobs if job["id"] == job_id), None)
        if row is None or row.get("status") != "failed":
            raise GatewayError("not_found", "Failed job was not found.", 404)
        return {"bundle": history.diagnostic_bundle(row)}

    @router.get("/dashboard/api/jobs/{job_id}/content", dependencies=[Depends(require_token)])
    async def dashboard_job_content(job_id: str) -> JSONResponse:
        """Opt-in plaintext content ONLY via explicit authenticated detail route.

        Existing /v1/jobs, jobs list, job inspector, analytics and diagnostic
        bundles never include prompt/output text. The UI can use this later.
        """
        if not job_store:
            raise GatewayError("unavailable", "Retained text storage is unavailable.", 503)
        if not job_store.get_job(job_id):
            raise GatewayError("not_found", "Job was not found.", 404)
        # Raw user text is opt-in and sensitive; browsers/proxies must not cache.
        return JSONResponse(
            {"job_id": job_id, "content": job_store.get_content(job_id)},
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


    @router.get("/dashboard/api/gallery", dependencies=[Depends(require_token)])
    async def dashboard_gallery(
        limit: int = Query(default=48, ge=1, le=100), cursor: str | None = None,
        category: str = "all", search: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        if category not in {"all", "scenes", "assets", "failed", "retried"}:
            raise GatewayError("invalid_request", "Invalid gallery category.", 422)
        before_created_at, before_id = decode_cursor(cursor)
        rows = job_store.list_gallery(
            limit=limit + 1, before_created_at=before_created_at, before_id=before_id,
            category=category, search=search,
        ) if job_store else []
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for row in rows:
            item = {
                key: row.get(key) for key in (
                    "job_id", "request_id", "group_id", "attempt", "model", "status",
                    "elapsed_ms", "reference_count", "created_at", "total_tokens",
                )
            }
            item["category"] = "scene" if row["request_id"].startswith("scene_") else "asset"
            item["error"] = (
                {"type": row.get("error_type"), "message": row.get("error_message")}
                if row.get("error_type") else None
            )
            if row.get("artifact_id"):
                item["artifact"] = public_artifact({
                    "id": row["artifact_id"], "artifact_type": "image", "mime_type": row["mime_type"],
                    "width": row["width"], "height": row["height"], "size_bytes": row["size_bytes"],
                    "created_at": row["created_at"],
                })
            else:
                item["artifact"] = None
            items.append(item)
        next_cursor = encode_cursor(rows[-1]["created_at"], rows[-1]["job_id"]) if has_more and rows else None
        return {"items": items, "next_cursor": next_cursor}


    def artifact_response(artifact_id: str, thumbnail: bool = False) -> FileResponse:
        if not job_store or not artifact_store or not re.fullmatch(r"art_[0-9a-f]{32}", artifact_id):
            raise GatewayError("not_found", "Artifact was not found.", 404)
        artifact = job_store.get_artifact(artifact_id)
        if not artifact:
            raise GatewayError("not_found", "Artifact was not found.", 404)
        requested_path = artifact.get("thumbnail_path") if thumbnail else artifact.get("storage_path")
        media_type = "image/webp" if thumbnail and requested_path else artifact["mime_type"]
        path = artifact_store.resolve(str(requested_path or artifact.get("storage_path", "")))
        if path is None:
            raise GatewayError("artifact_missing", "Artifact file is no longer available.", 404)
        return FileResponse(path, media_type=media_type, headers={
            "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
        })


    @router.get("/dashboard/api/artifacts/{artifact_id}", dependencies=[Depends(require_token)])
    async def dashboard_artifact(artifact_id: str) -> FileResponse:
        return artifact_response(artifact_id)


    @router.get("/dashboard/api/artifacts/{artifact_id}/thumbnail", dependencies=[Depends(require_token)])
    async def dashboard_artifact_thumbnail(artifact_id: str) -> FileResponse:
        return artifact_response(artifact_id, thumbnail=True)

    return router
