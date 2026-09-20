"""In-memory job hot cache with SQLite persistence and safe failure context."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any
from uuid import uuid4
from .contracts import GatewayError
from .diagnostics import redact_diagnostic, build_diagnostic_bundle
from .job_store import JobStore
from .live_events import LiveEventBus

LOG = logging.getLogger("uvicorn.error")

class JobHistory:

    def __init__(self, store: JobStore | None = None, *, store_diagnostics: bool = True,
                 events: LiveEventBus | None = None) -> None:
        self.jobs: deque[dict[str, Any]] = deque(maxlen=100)
        self.store = store
        self.store_diagnostics = store_diagnostics
        self.live_events = events
        self._group_roots: dict[str, str] = {}

    @staticmethod
    def _attempt_metadata(request_id: str) -> tuple[str | None, int | None]:
        match = re.match(r"^(.*)_(initial|retry_(\d+))$", request_id)
        if not match:
            return None, None
        retry_number = match.group(3)
        return match.group(1), int(retry_number) + 1 if retry_number else 1

    def _persist(self, job: dict[str, Any]) -> None:
        if not self.store:
            return
        try:
            self.store.save_job(job)
        except Exception:
            LOG.exception("telemetry_job_persist_failed job_id=%s", job.get("id"))

    def _event(self, job: dict[str, Any], stage: str, level: str = "info") -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": job["elapsed_ms"], "stage": stage, "level": level,
        }
        job.setdefault("events", []).append(event)
        if self.store:
            try:
                self.store.add_event(job["id"], **event)
            except Exception:
                LOG.exception("telemetry_event_persist_failed job_id=%s", job.get("id"))

    def add(self, request_id: str, task: str, model: str, reference_count: int = 0,
            operation: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        group_id, attempt = self._attempt_metadata(request_id)
        parent_job_id = self._group_roots.get(group_id) if group_id and attempt and attempt > 1 else None
        job = {
            "id": str(uuid4()), "request_id": request_id, "group_id": group_id, "attempt": attempt,
            "parent_job_id": parent_job_id,
            "task": task, "operation": operation or task, "model": model,
            "status": "queued", "stage": "Waiting for Codex worker",
            "created_at": now, "queued_at": now, "elapsed_ms": 0, "error": None,
            "reference_count": reference_count, "queue_ms": None, "codex_ms": None,
            "reference_download_ms": None, "artifact_processing_ms": None, "artifact_id": None,
            # Module 6B: bounded, sanitized error metadata; never store raw stderr.
            "exit_code": None, "diagnostic_preview": None,
            "last_successful_stage": None, "references_downloaded": 0,
            "_started": time.monotonic(), "events": [],
        }
        if group_id and group_id not in self._group_roots:
            self._group_roots[group_id] = job["id"]
        self.jobs.appendleft(job)
        self._persist(job)
        self._event(job, job["stage"])
        if self.live_events:
            self.live_events.publish("job.created", self._live_job(job))
        LOG.info("job_queued request_id=%s task=%s model=%s", request_id, task, model)
        return job

    def update(self, job: dict[str, Any], *, status: str, stage: str,
               error: GatewayError | None = None, **fields: Any) -> None:
        job.update(status=status, stage=stage,
                   elapsed_ms=round((time.monotonic() - job["_started"]) * 1000))
        job.update(fields)

        # Only count confirmed milestones as successful. A stage such as
        # "Downloading reference image 2/3" says what started, not what finished.
        confirmed_stages = {
            "Worker acquired", "Reference images ready", "Codex session started",
            "Codex is working", "Codex completed", "Artifact ready",
            "Response ready", "Image ready",
        }
        if status != "failed" and (
            stage in confirmed_stages or re.fullmatch(r"Reference image \d+ ready", stage)
        ):
            job["last_successful_stage"] = stage

        if error:
            job["error"] = {
                "type": error.kind,
                "message": redact_diagnostic(error.message, max_chars=500),
            }
            # CodexRunner annotates classified CLI errors with these two fields.
            # Download/timeout/queue failures normally have no process exit code.
            job["exit_code"] = getattr(error, "exit_code", None)
            # Metadata (error type/exit code) is still useful without stderr.
            preview = (redact_diagnostic(getattr(error, "diagnostic", ""))
                       if self.store_diagnostics else "")
            job["diagnostic_preview"] = preview or None
        if status in {"completed", "failed"}:
            job["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._persist(job)
        self._event(job, stage, "error" if error else "info")
        if self.live_events:
            self.live_events.publish("job.updated", self._live_job(job))
        LOG.log(
            logging.WARNING if error else logging.INFO,
            "job_%s request_id=%s task=%s model=%s duration_ms=%s stage=%s error=%s error_message=%s",
            status, job["request_id"], job["task"], job["model"], job["elapsed_ms"], stage,
            error.kind if error else "none", error.message if error else "none",
        )

    def reference_ready(self, job: dict[str, Any], ordinal: int) -> None:
        """Count only validated downloads, not downloads merely attempted.

        Called before the optional reference-artifact copy, so gallery storage
        trouble cannot turn a successful download into a false failure.
        """
        self.update(
            job, status="running", stage=f"Reference image {ordinal} ready",
            references_downloaded=max(job.get("references_downloaded", 0), ordinal),
        )

    def diagnostic_bundle(self, job: dict[str, Any]) -> dict[str, Any]:
        """Build an allowlisted, re-redacted record for the future 6C API."""
        error = job.get("error") or {}
        return build_diagnostic_bundle(
            request_id=job.get("request_id") or "",
            task=job.get("task") or "",
            model=job.get("model") or "",
            error_type=error.get("type") or job.get("error_type") or "",
            message=error.get("message") or job.get("error_message") or "",
            last_successful_stage=job.get("last_successful_stage") or "",
            exit_code=job.get("exit_code"),
            duration_ms=job.get("elapsed_ms"),
            reference_count=job.get("reference_count") or 0,
            references_downloaded=job.get("references_downloaded") or 0,
            stderr=job.get("diagnostic_preview"),
        )

    def worker_acquired(self, job: dict[str, Any], queue_ms: int) -> None:
        self.update(job, status="running", stage="Worker acquired",
                    worker_acquired_at=datetime.now(timezone.utc).isoformat(), queue_ms=queue_ms)

    def codex_started(self, job: dict[str, Any], stage: str) -> None:
        fields: dict[str, Any] = {}
        if not job.get("codex_started_at"):
            fields.update(codex_started_at=datetime.now(timezone.utc).isoformat(), _codex_started=time.monotonic())
        self.update(job, status="running", stage=stage, **fields)

    def codex_completed(self, job: dict[str, Any]) -> None:
        fields: dict[str, Any] = {"codex_completed_at": datetime.now(timezone.utc).isoformat()}
        if job.get("_codex_started") is not None:
            fields["codex_ms"] = round((time.monotonic() - job["_codex_started"]) * 1000)
        self.update(job, status="running", stage="Codex completed", **fields)

    def usage(self, job: dict[str, Any], usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        job["usage"] = usage
        self._persist(job)
        if self.store:
            try:
                self.store.save_usage(job["id"], usage)
            except Exception:
                LOG.exception("telemetry_usage_persist_failed job_id=%s", job.get("id"))
        if self.live_events:
            # A change notification only: clients fetch full tokens from jobs API.
            self.live_events.publish("job.usage", {"job_id": job["id"]})

    def drop_expired_jobs(self, ids: list[str]) -> None:
        """Prune the in-memory /v1/jobs hot cache after confirmed DB cleanup."""
        if not ids:
            return
        targets = set(ids)
        self.jobs = deque((job for job in self.jobs if job['id'] not in targets), maxlen=100)
        self._group_roots = {group: root for group, root in self._group_roots.items()
                             if root not in targets}

    @staticmethod
    def _live_job(job: dict[str, Any]) -> dict[str, Any]:
        """Allowlist metadata, no request/prompt/output/CLI/error text in SSE."""
        error = job.get("error") or {}
        return {
            "job_id": job["id"], "status": job["status"],
            "task": job["task"], "operation": job.get("operation"),
            "model": job["model"],
            "stage": redact_diagnostic(job.get("stage"), max_chars=160),
            "elapsed_ms": job.get("elapsed_ms", 0),
            "error_type": redact_diagnostic(error.get("type"), max_chars=80) or None,
            "artifact_id": job.get("artifact_id"),
        }

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in job.items() if not key.startswith("_")}
        if job["status"] in {"queued", "running"}:
            result["elapsed_ms"] = round((time.monotonic() - job["_started"]) * 1000)
        return result


