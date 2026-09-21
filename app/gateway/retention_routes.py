"""Authenticated storage controls; preview first, explicit confirmation to delete."""
from __future__ import annotations

import asyncio
from typing import Any, Callable
from fastapi import APIRouter, Depends, Query

from .config import Settings
from .contracts import GatewayError
from .retention import RetentionService
from .artifact_health import inspect_artifact_health
from .artifact_inventory import inspect_storage_inventory
from .job_history import JobHistory
from .live_events import LiveEventBus


def create_retention_router(*, require_token: Callable[..., None],
                            retention: RetentionService | None,
                            effective_settings: Settings, history: JobHistory,
                            events: LiveEventBus | None = None) -> APIRouter:
    router = APIRouter()

    @router.get('/dashboard/api/storage/health', dependencies=[Depends(require_token)])
    async def storage_health() -> dict[str, Any]:
        if retention is None:
            raise GatewayError('unavailable', 'Artifact health is unavailable.', 503)
        return await asyncio.to_thread(
            inspect_artifact_health, retention.store, retention.artifacts,
        )

    @router.get('/dashboard/api/storage/inventory', dependencies=[Depends(require_token)])
    async def storage_inventory() -> dict[str, Any]:
        # A metadata-only scan; never reuse the retention cleanup plan here.
        if retention is None:
            raise GatewayError('unavailable', 'Storage inventory is unavailable.', 503)
        return await asyncio.to_thread(
            inspect_storage_inventory, retention.store, retention.artifacts,
        )

    @router.get('/dashboard/api/storage/preview', dependencies=[Depends(require_token)])
    async def storage_preview(include_orphans: bool = Query(default=False)) -> dict[str, Any]:
        if retention is None:
            raise GatewayError('unavailable', 'Storage retention is unavailable.', 503)
        return await asyncio.to_thread(retention.preview, effective_settings, include_orphans=include_orphans)

    @router.post('/dashboard/api/storage/cleanup', dependencies=[Depends(require_token)])
    async def storage_cleanup(body: dict[str, Any]) -> dict[str, Any]:
        if retention is None:
            raise GatewayError('unavailable', 'Storage retention is unavailable.', 503)
        if (not isinstance(body, dict) or set(body) != {'confirmation', 'include_orphans'}
            or body.get('confirmation') != 'DELETE_EXPIRED_DATA'
            or type(body.get('include_orphans')) is not bool):
            raise GatewayError('invalid_request',
                               'Provide confirmation=DELETE_EXPIRED_DATA and include_orphans=true/false.', 422)
        result = await asyncio.to_thread(retention.run, effective_settings,
                                         include_orphans=body['include_orphans'])
        history.drop_expired_jobs(result.pop('_deleted_job_ids', []))
        if events:
            events.publish('storage.changed', {})
        return result

    return router
