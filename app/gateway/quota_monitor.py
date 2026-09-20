"""Supported Codex app-server quota telemetry over a long-lived stdio session."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
from typing import Any

from .job_store import JobStore


LOG = logging.getLogger("uvicorn.error")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _percent(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > 100:
        return None
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _window(value: Any) -> dict[str, Any] | None:
    if value is None or not isinstance(value, dict):
        return None
    used = _percent(value.get("usedPercent"))
    if used is None:
        return None
    duration = value.get("windowDurationMins")
    resets_at = value.get("resetsAt")
    return {
        "used_percent": used,
        "remaining_percent": round(100 - used, 2),
        "window_duration_mins": _integer(duration) if duration is not None else None,
        "resets_at": _integer(resets_at) if resets_at is not None else None,
    }


def _credits(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    has_credits = value.get("hasCredits")
    unlimited = value.get("unlimited")
    if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
        return None
    balance = value.get("balance")
    return {
        "has_credits": has_credits,
        "unlimited": unlimited,
        "balance": balance if isinstance(balance, str) else None,
    }


def _spend_control(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    remaining = _percent(value.get("remainingPercent"))
    resets_at = _integer(value.get("resetsAt"))
    limit = value.get("limit")
    used = value.get("used")
    if remaining is None or resets_at is None or not isinstance(limit, str) or not isinstance(used, str):
        return None
    return {
        "remaining_percent": remaining,
        "resets_at": resets_at,
        "limit": limit,
        "used": used,
    }


def _bucket(key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    limit_id = _text(value.get("limitId")) or _text(key)
    if not limit_id:
        return None
    return {
        "limit_id": limit_id,
        "limit_name": _text(value.get("limitName")),
        "plan_type": _text(value.get("planType")),
        "normal_model_slug": _text(value.get("normalModelSlug")),
        "rate_limit_reached_type": _text(value.get("rateLimitReachedType")),
        "ordinary_spend_control_reached": (
            value.get("spendControlReached") if isinstance(value.get("spendControlReached"), bool) else None
        ),
        "credits": _credits(value.get("credits")),
        "spend_control": _spend_control(value.get("individualLimit")),
        "primary": _window(value.get("primary")),
        "secondary": _window(value.get("secondary")),
    }


def _reset_credits(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    available = _integer(value.get("availableCount"))
    if available is None:
        return None
    details = value.get("credits")
    return {
        "available_count": available,
        "details_available": isinstance(details, list),
        "detail_count": len(details) if isinstance(details, list) else None,
    }


def _status(buckets: list[dict[str, Any]], ordinary_allowed: bool | None,
            warning_remaining: int, critical_remaining: int) -> str:
    windows = [
        window
        for bucket in buckets
        for window in (bucket.get("primary"), bucket.get("secondary"))
        if window is not None
    ]
    if ordinary_allowed is False or any(
        bucket.get("rate_limit_reached_type") or bucket.get("ordinary_spend_control_reached") is True
        for bucket in buckets
    ):
        return "exhausted"
    if not windows:
        return "unavailable"
    remaining = min(window["remaining_percent"] for window in windows)
    if remaining <= 0:
        return "exhausted"
    if remaining <= critical_remaining:
        return "critical"
    if remaining <= warning_remaining:
        return "warning"
    return "healthy"


def normalize_rate_limits(payload: Any, *, observed_at: str | None = None,
                          warning_remaining: int = 20,
                          critical_remaining: int = 10) -> dict[str, Any]:
    """Normalize only fields reported by the installed app-server schema."""
    observed_at = observed_at or _utc_now()
    if not isinstance(payload, dict):
        return {"status": "unavailable", "observed_at": observed_at, "reason": "invalid_payload", "buckets": []}

    ordinary = payload.get("ordinaryUsageAllowed")
    ordinary = ordinary if isinstance(ordinary, bool) else None
    bucket_values = payload.get("rateLimitsByLimitId")
    buckets: list[dict[str, Any]] = []
    if isinstance(bucket_values, dict) and bucket_values:
        for key, value in bucket_values.items():
            normalized = _bucket(str(key), value)
            if normalized:
                buckets.append(normalized)
    elif isinstance(payload.get("rateLimits"), dict):
        value = payload["rateLimits"]
        normalized = _bucket(_text(value.get("limitId")) or "codex", value)
        if normalized:
            buckets.append(normalized)

    buckets.sort(key=lambda item: (item["limit_name"] or item["limit_id"]).lower())
    status = _status(buckets, ordinary, warning_remaining, critical_remaining)
    result: dict[str, Any] = {
        "status": status,
        "observed_at": observed_at,
        "ordinary_usage_allowed": ordinary,
        "buckets": buckets,
        "reset_credits": _reset_credits(payload.get("rateLimitResetCredits")),
    }
    if not buckets:
        result["reason"] = "no_rate_limit_buckets"
    return result


def notification_requests_refresh(message: Any) -> bool:
    """Accept only a well-shaped supported quota/account notification."""
    if not isinstance(message, dict) or "id" in message:
        return False
    method = message.get("method")
    params = message.get("params")
    if method == "account/rateLimits/updated":
        return isinstance(params, dict) and isinstance(params.get("rateLimits"), dict)
    if method == "account/updated":
        return isinstance(params, dict)
    return False


class AppServerError(RuntimeError):
    pass


class QuotaMonitor:
    """Supervise one Codex app-server process and periodically read quota state."""

    def __init__(self, codex_exe: str, store: JobStore | None = None, *,
                 poll_seconds: float = 45, warning_remaining: int = 20,
                 critical_remaining: int = 10, request_timeout: float = 20) -> None:
        if not 30 <= poll_seconds <= 60:
            raise ValueError("Quota poll interval must be between 30 and 60 seconds")
        if not 0 <= critical_remaining <= warning_remaining <= 100:
            raise ValueError("Invalid quota warning thresholds")
        self.codex_exe = codex_exe
        self.store = store
        self.poll_seconds = poll_seconds
        self.warning_remaining = warning_remaining
        self.critical_remaining = critical_remaining
        self.request_timeout = request_timeout
        self._snapshot: dict[str, Any] = {
            "status": "unavailable", "observed_at": None,
            "reason": "monitor_starting", "buckets": [], "reset_credits": None,
        }
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._stop_event = asyncio.Event()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._snapshot)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._supervise(), name="codex-quota-monitor")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
        await self._close_process()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def refresh_now(self) -> dict[str, Any]:
        async with self._refresh_lock:
            if not self._process or self._process.returncode is not None:
                return self.snapshot()
            result = await self._request(
                "account/rateLimits/read",
                {"excludeResetCreditDetails": True, "supportsLunaReserve": False},
            )
            snapshot = normalize_rate_limits(
                result,
                warning_remaining=self.warning_remaining,
                critical_remaining=self.critical_remaining,
            )
            self._publish(snapshot)
            return self.snapshot()

    def _publish(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot
        if self.store:
            try:
                self.store.save_quota_snapshot(snapshot)
            except Exception:
                LOG.exception("quota_snapshot_persist_failed")

    async def _supervise(self) -> None:
        delay = 3.0
        while not self._stop_event.is_set():
            try:
                await self._run_session()
                delay = 3.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("quota_monitor_unavailable error=%s", type(exc).__name__)
                self._publish({
                    "status": "unavailable", "observed_at": _utc_now(),
                    "reason": "app_server_unavailable", "buckets": [], "reset_credits": None,
                })
            finally:
                await self._close_process()
            if not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 30)

    async def _run_session(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            self.codex_exe, "app-server", "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout(), name="codex-quota-stdout")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="codex-quota-stderr")
        await self._request("initialize", {
            "clientInfo": {
                "name": "local_ai_gateway",
                "title": "Local AI Gateway",
                "version": "0.1.0",
            }
        })
        await self._notify("initialized", {})
        await self.refresh_now()
        while not self._stop_event.is_set():
            self._refresh_event.clear()
            try:
                await asyncio.wait_for(self._refresh_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass
            await self.refresh_now()

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        try:
            await self._write(message)
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin or process.returncode is not None:
            raise AppServerError("Codex app-server is not connected")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        while line := await process.stdout.readline():
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                LOG.warning("quota_monitor_ignored_malformed_message")
                continue
            if not isinstance(message, dict):
                continue
            request_id = message.get("id")
            if request_id in self._pending:
                future = self._pending[request_id]
                if "error" in message:
                    error = message.get("error") or {}
                    future.set_exception(AppServerError(str(error.get("message") or "app-server request failed")))
                else:
                    future.set_result(message.get("result"))
            elif notification_requests_refresh(message):
                self._refresh_event.set()
        error = AppServerError("Codex app-server closed its output stream")
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    async def _drain_stderr(self) -> None:
        process = self._process
        if not process or not process.stderr:
            return
        while await process.stderr.readline():
            pass

    async def _close_process(self) -> None:
        process = self._process
        self._process = None
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
