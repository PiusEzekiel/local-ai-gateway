"""Module 6C diagnostics API: read-only, authenticated, bounded, safe to export.

No Codex process, Google Drive, Pinokio installation or external network needed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.app import GatewayError, JobHistory, Settings, create_app
from gateway.job_store import JobStore

TOKEN = "diagnostics-test-token-24-characters-long"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_api(tmp_path: Path):
    store = JobStore(tmp_path / "gateway.sqlite3")
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    api = create_app(settings=settings, runner=object(), job_store=store)
    return api, store


def fail(history: JobHistory, request_id: str, kind: str, *, task="image", model="gpt-5.6-luna",
         diagnostic="error: unexpected argument '--image'", count=1, downloaded=1,
         operation=None):
    job = history.add(request_id, task, model, reference_count=count, operation=operation)
    history.worker_acquired(job, 3)
    for ordinal in range(1, downloaded + 1):
        history.reference_ready(job, ordinal)
    exc = GatewayError(kind, "Failure; https://drive.google.com/uc?id=PRIVATEID", 502)
    exc.exit_code = 2
    exc.diagnostic = diagnostic
    history.update(job, status="failed", stage="Image generation failed", error=exc)
    return job


def test_diagnostics_routes_require_bearer_auth(tmp_path):
    api, store = make_api(tmp_path)
    try:
        with TestClient(api) as client:
            routes = ["/dashboard/api/diagnostics", "/dashboard/api/diagnostics/summary",
                      "/dashboard/api/diagnostics/example", "/dashboard/api/diagnostics/example/bundle"]
            for route in routes:
                assert client.get(route).status_code == 401, route
                assert client.get(route, headers={"Authorization": "Bearer bad"}).status_code == 401
    finally:
        store.close()


def test_summary_and_list_filter_actual_failures_not_completed_jobs(tmp_path):
    api, store = make_api(tmp_path)
    try:
        h = api.state.history
        fail(h, "scene_image_1_initial", "codex_cli_argument_error")
        fail(h, "scene_image_2_initial", "reference_download_failed", diagnostic="connection refused", count=2, downloaded=0)
        fail(h, "text_1_initial", "model_unavailable", diagnostic="model not available", task="generate", model="gpt-5.6-terra", count=0, downloaded=0, operation="chat")
        okay = h.add("scene_image_99_initial", "image", "gpt-5.6-luna")
        h.update(okay, status="completed", stage="Image ready")
        with TestClient(api) as client:
            summary = client.get("/dashboard/api/diagnostics/summary", headers=AUTH).json()["summary"]
            assert summary["failures"] == 3
            assert summary["image_failures"] == 2
            assert summary["text_failures"] == 1
            assert {r["error_type"]: r["failures"] for r in summary["error_types"]} == {
                "codex_cli_argument_error": 1, "reference_download_failed": 1, "model_unavailable": 1,
            }
            response = client.get("/dashboard/api/diagnostics", headers=AUTH)
            assert response.status_code == 200
            assert len(response.json()["failures"]) == 3
            assert all(r["status"] == "failed" for r in response.json()["failures"])
            for filters, expected in [({"task": "generate"}, 1),
                                      ({"model": "gpt-5.6-terra"}, 1),
                                      ({"operation": "chat"}, 1),
                                      ({"error_type": "reference_download_failed"}, 1),
                                      ({"search": "unexpected argument"}, 1)]:
                r = client.get("/dashboard/api/diagnostics", params=filters, headers=AUTH)
                assert r.status_code == 200
                assert len(r.json()["failures"]) == expected
                total = client.get("/dashboard/api/diagnostics/summary", params=filters, headers=AUTH).json()
                assert total["summary"]["failures"] == expected
    finally:
        store.close()


def test_cursor_pages_and_bad_cursor(tmp_path):
    api, store = make_api(tmp_path)
    try:
        ids = {fail(api.state.history, f"scene_image_{idx}_initial", "codex_failed")["id"] for idx in range(4)}
        with TestClient(api) as client:
            first = client.get("/dashboard/api/diagnostics?limit=2", headers=AUTH)
            assert first.status_code == 200
            assert len(first.json()["failures"]) == 2
            cursor = first.json()["next_cursor"]
            assert cursor
            second = client.get("/dashboard/api/diagnostics", headers=AUTH,
                                params={"limit": 2, "cursor": cursor}).json()
            assert len(second["failures"]) == 2
            assert second["next_cursor"] is None
            assert {r["id"] for r in first.json()["failures"] + second["failures"]} == ids
            assert client.get("/dashboard/api/diagnostics?cursor=invalid", headers=AUTH).status_code == 422
            assert client.get("/dashboard/api/diagnostics?limit=101", headers=AUTH).status_code == 422
    finally:
        store.close()


def test_details_bundle_redacted_and_completed_job_is_not_diagnostic(tmp_path):
    api, store = make_api(tmp_path)
    try:
        failed = fail(api.state.history, "scene_image_17_initial", "codex_cli_argument_error",
                      diagnostic="unexpected argument '--image' Authorization: Bearer superSecretToken12345 \\"
                      "C:\\Users\\piuse\\secret.png https://drive.google.com/uc?id=PRIVATE123", count=2)
        completed = api.state.history.add("completed", "generate", "gpt-5.6-luna")
        api.state.history.update(completed, status="completed", stage="Response ready")
        store.add_event(failed["id"], datetime.now(timezone.utc).isoformat(), 4,
                        "Downloading https://drive.google.com/uc?id=PRIVATE123")
        with TestClient(api) as client:
            route = f"/dashboard/api/diagnostics/{failed['id']}"
            response = client.get(route, headers=AUTH)
            assert response.status_code == 200
            details = response.json()
            assert details["job"]["exit_code"] == 2
            assert details["job"]["last_successful_stage"] == "Reference image 1 ready"
            assert details["job"]["references_downloaded"] == 1
            assert details["bundle"]["exit_code"] == 2
            assert details["events"]
            for part in (details, client.get(route + "/bundle", headers=AUTH).json()):
                serialized = str(part)
                for secret in ("superSecretToken12345", "PRIVATE123", "piuse"):
                    assert secret not in serialized
                assert "--image" in serialized
            assert client.get(f"/dashboard/api/diagnostics/{completed['id']}", headers=AUTH).status_code == 404
            assert client.get("/dashboard/api/diagnostics/no-such-job/bundle", headers=AUTH).status_code == 404
    finally:
        store.close()


def test_literal_like_search_percent_and_underscore(tmp_path):
    api, store = make_api(tmp_path)
    try:
        fail(api.state.history, "scene_image_1_initial", "codex_failed")
        with TestClient(api) as client:
            for search in ("%", "nonexistent%", "foo_bar"):

                result = client.get("/dashboard/api/diagnostics", headers=AUTH,
                                    params={"search": search})
                assert result.status_code == 200
                assert not result.json()["failures"]
            assert len(client.get("/dashboard/api/diagnostics", headers=AUTH,
                                  params={"search": "image_1"}).json()["failures"]) == 1
            assert len(client.get("/dashboard/api/diagnostics", headers=AUTH,
                                  params={"search": "_"}).json()["failures"]) == 1
    finally:
        store.close()


def test_range_and_filter_validation(tmp_path):
    api, store = make_api(tmp_path)
    try:
        with TestClient(api) as client:
            assert client.get("/dashboard/api/diagnostics?task=video", headers=AUTH).status_code == 422
            assert client.get("/dashboard/api/diagnostics/summary?task=video", headers=AUTH).status_code == 422
            assert client.get("/dashboard/api/diagnostics?operation=video", headers=AUTH).status_code == 422
            assert client.get("/dashboard/api/diagnostics?range=unknown", headers=AUTH).status_code == 422
            assert client.get("/dashboard/api/diagnostics?search=" + "x" * 129, headers=AUTH).status_code == 422
    finally:
        store.close()


def test_persistent_failures_remain_inspectable_after_store_reopen(tmp_path):
    db = tmp_path / "gateway.sqlite3"
    store = JobStore(db)
    record = fail(JobHistory(store), "scene_image_9_retry_1", "codex_cli_argument_error")
    store.close()
    reopened = JobStore(db)
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable, quota_monitor_enabled=False)
    api = create_app(settings=settings, runner=object(), job_store=reopened)
    try:
        with TestClient(api) as client:
            result = client.get(f"/dashboard/api/diagnostics/{record['id']}/bundle", headers=AUTH)
            assert result.status_code == 200
            assert result.json()["bundle"]["request_id"] == "scene_image_9_retry_1"
            assert result.json()["bundle"]["exit_code"] == 2
    finally:
        reopened.close()


def test_summary_does_not_require_diagnostic_preview(tmp_path):
    api, store = make_api(tmp_path)
    try:
        job = fail(api.state.history, "scene_image_6_initial", "gateway_restarted")
        with store._lock, store._db:
            store._db.execute("UPDATE jobs SET diagnostic_preview=NULL,exit_code=NULL WHERE id=?", (job["id"],))
        with TestClient(api) as client:
            data = client.get("/dashboard/api/diagnostics/summary", headers=AUTH).json()
            assert data["summary"]["failures"] == 1
            bundle = client.get(f"/dashboard/api/diagnostics/{job['id']}/bundle", headers=AUTH).json()["bundle"]
            assert bundle["diagnostic"] == ""
            assert bundle["exit_code"] is None
    finally:
        store.close()


def test_diagnostic_timeline_is_bounded_and_chronological(tmp_path):
    api, store = make_api(tmp_path)
    try:
        job = fail(api.state.history, "scene_image_501_initial", "codex_failed")
        timestamp = datetime.now(timezone.utc).isoformat()
        for i in range(505):
            store.add_event(job["id"], timestamp, i, f"check-{i}")
        with TestClient(api) as client:
            response = client.get(f"/dashboard/api/diagnostics/{job['id']}", headers=AUTH)
            assert response.status_code == 200
            events = response.json()["events"]
            assert len(events) == 500
            assert events[0]["stage"] == "check-5"
            assert events[-1]["stage"] == "check-504"
    finally:
        store.close()
