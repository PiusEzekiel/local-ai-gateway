from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from gateway.app import ImageRunResult, RunResult, Settings, create_app
from gateway.artifact_store import ArtifactStore
from gateway.job_store import JobStore
from gateway.quota_monitor import normalize_rate_limits, notification_requests_refresh
from gateway.settings_manager import SettingsManager


TOKEN = "test-token-that-is-at-least-24-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeRunner:
    def __init__(self, image: Path):
        self.image = image

    async def run(self, **kwargs):
        kwargs["progress"]("Codex is working")
        return RunResult("ok", "ok", {"input_tokens": 10, "cached_input_tokens": 4,
                                       "output_tokens": 3, "total_tokens": 13})

    async def run_image(self, **kwargs):
        for index, reference in enumerate(kwargs.get("reference_images") or []):
            if kwargs.get("reference_ready"):
                kwargs["reference_ready"](index, reference, self.image)
        kwargs["progress"]("Launching Codex")
        kwargs["progress"]("Generating image")
        return ImageRunResult(self.image, "image/png", {"input_tokens": 7, "output_tokens": 2}, "thread-1")


class FakeQuotaMonitor:
    def __init__(self, snapshot):
        self.value = snapshot
        self.started = False
        self.stopped = False
        self.refreshes = 0

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def snapshot(self):
        return self.value

    async def refresh_now(self):
        self.refreshes += 1
        return self.value


def make_app(tmp_path: Path, quota_monitor=None):
    image = tmp_path / "source.png"
    Image.new("RGB", (320, 180), "#335577").save(image, format="PNG")
    store = JobStore(tmp_path / "gateway.sqlite3")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    settings = Settings(api_token=TOKEN, codex_exe=sys.executable)
    return create_app(settings, FakeRunner(image), store, artifacts, quota_monitor), store


def test_health_model_and_generate_contracts(tmp_path):
    app, store = make_app(tmp_path)
    with TestClient(app) as client:
        health = client.get("/health", headers=HEADERS)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/v1/models", headers=HEADERS).status_code == 200
        response = client.post("/v1/generate", headers=HEADERS, json={"request_id": "text_1", "prompt": "hello"})
        assert response.status_code == 200
        assert response.json()["response"] == "ok"
        job = client.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert job["status"] == "completed"
        assert job["usage"]["total_tokens"] == 13
        assert len(job["events"]) >= 5
        persisted = store.get_job(job["id"])
        assert persisted["queue_ms"] is not None
        assert persisted["codex_ms"] is not None
        usage = store.get_usage(job["id"])
        assert usage["uncached_input_tokens"] == 6


def test_image_contract_persists_usage_and_artifact(tmp_path):
    app, store = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/v1/images/generations", headers=HEADERS, json={
            "request_id": "scene_image_47_retry_1", "prompt": "one image",
            "reference_images": [{"id": "mara", "url": "https://drive.google.com/example"}],
        })
        assert response.status_code == 200
        assert response.content.startswith(b"\x89PNG")
        job = client.get("/v1/jobs", headers=HEADERS).json()["jobs"][0]
        assert job["artifact_id"].startswith("art_")
        assert job["group_id"] == "scene_image_47"
        assert job["attempt"] == 2
        assert store.get_usage(job["id"])["total_tokens"] == 9
        references = store.get_references(job["id"])
        assert references[0]["reference_id"] == "mara"
        assert references[0]["artifact_id"].startswith("art_")
        detail = client.get(f"/dashboard/api/jobs/{job['id']}", headers=HEADERS).json()["job"]
        assert detail["references"][0]["thumbnail_url"].endswith("/thumbnail")
        assert client.get(detail["references"][0]["thumbnail_url"], headers=HEADERS).status_code == 200


def test_artifact_store_rejects_paths_outside_root(tmp_path):
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"data")
    assert artifact_store.resolve(str(outside)) is None


def test_settings_manager_preserves_unrelated_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"theme":"dark"}', encoding="utf-8")
    manager = SettingsManager(path, ("luna", "terra"))
    saved = manager.save_model("luna")
    assert saved == {"theme": "dark", "model": "luna"}


def test_dashboard_summary_jobs_filters_and_detail(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/dashboard/api/summary").status_code == 401
        empty = client.get("/dashboard/api/summary", headers=HEADERS)
        assert empty.status_code == 200
        assert empty.json()["today"]["jobs"] == 0
        for request_id in ("alpha", "beta"):
            assert client.post("/v1/generate", headers=HEADERS, json={
                "request_id": request_id, "prompt": "hello",
            }).status_code == 200
        page = client.get("/dashboard/api/jobs?limit=1", headers=HEADERS).json()
        assert len(page["jobs"]) == 1
        assert page["next_cursor"]
        second = client.get(
            "/dashboard/api/jobs", headers=HEADERS,
            params={"limit": 1, "cursor": page["next_cursor"]},
        ).json()
        assert second["jobs"][0]["id"] != page["jobs"][0]["id"]
        filtered = client.get("/dashboard/api/jobs", headers=HEADERS, params={"search": "alpha"}).json()
        assert [job["request_id"] for job in filtered["jobs"]] == ["alpha"]
        detail = client.get(f"/dashboard/api/jobs/{filtered['jobs'][0]['id']}", headers=HEADERS).json()["job"]
        assert detail["events"]
        assert detail["usage"]["total_tokens"] == 13
        assert "storage_path" not in str(detail)
        assert client.get("/dashboard/api/jobs?cursor=bad", headers=HEADERS).status_code == 422


def test_gallery_and_authenticated_artifact_delivery(tmp_path):
    app, store = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/v1/images/generations", headers=HEADERS, json={
            "request_id": "scene_image_12_initial", "prompt": "one image",
        })
        assert response.status_code == 200
        gallery = client.get("/dashboard/api/gallery", headers=HEADERS).json()
        assert len(gallery["items"]) == 1
        item = gallery["items"][0]
        assert item["category"] == "scene"
        assert item["artifact"]["width"] == 320
        artifact_id = item["artifact"]["id"]
        assert client.get(f"/dashboard/api/artifacts/{artifact_id}").status_code == 401
        original = client.get(f"/dashboard/api/artifacts/{artifact_id}", headers=HEADERS)
        assert original.status_code == 200
        assert original.headers["content-type"] == "image/png"
        thumbnail = client.get(f"/dashboard/api/artifacts/{artifact_id}/thumbnail", headers=HEADERS)
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/webp"
        assert client.get("/dashboard/api/artifacts/art_not-real", headers=HEADERS).status_code == 404
        assert client.get("/dashboard/api/artifacts/..%2F..%2Fsecret", headers=HEADERS).status_code == 404
        assert client.get("/dashboard/api/gallery?category=unknown", headers=HEADERS).status_code == 422
        artifact = store.get_artifact(artifact_id)
        assert "storage_path" in artifact
        assert "storage_path" not in str(item)
        Path(artifact["storage_path"]).unlink()
        assert client.get(f"/dashboard/api/artifacts/{artifact_id}", headers=HEADERS).status_code == 404


def test_quota_normalization_handles_dual_windows_and_multiple_buckets():
    snapshot = normalize_rate_limits({
        "ordinaryUsageAllowed": True,
        "rateLimits": {"limitId": "legacy", "primary": {"usedPercent": 1}},
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex", "limitName": "Codex", "planType": "plus",
                "primary": {"usedPercent": 55, "windowDurationMins": 300, "resetsAt": 1_900_000_000},
                "secondary": {"usedPercent": 91, "windowDurationMins": 10080, "resetsAt": 1_900_500_000},
            },
            "codex_luna": {
                "limitId": "codex_luna", "limitName": "Luna",
                "primary": {"usedPercent": 5, "windowDurationMins": 60, "resetsAt": None},
                "secondary": None,
            },
        },
        "rateLimitResetCredits": {"availableCount": 2, "credits": None},
    }, observed_at="2026-09-19T00:00:00+00:00")
    assert snapshot["status"] == "critical"
    assert [bucket["limit_id"] for bucket in snapshot["buckets"]] == ["codex", "codex_luna"]
    assert snapshot["buckets"][0]["secondary"]["remaining_percent"] == 9
    assert snapshot["buckets"][1]["secondary"] is None
    assert snapshot["reset_credits"] == {
        "available_count": 2, "details_available": False, "detail_count": None,
    }


def test_quota_normalization_preserves_missing_windows_and_unavailable_state():
    secondary_only = normalize_rate_limits({
        "rateLimits": {
            "limitId": "codex", "primary": None,
            "secondary": {"usedPercent": 30, "windowDurationMins": None, "resetsAt": None},
        }
    })
    assert secondary_only["status"] == "healthy"
    assert secondary_only["buckets"][0]["primary"] is None
    assert secondary_only["buckets"][0]["secondary"]["window_duration_mins"] is None
    assert normalize_rate_limits({"rateLimits": "malformed"})["status"] == "unavailable"
    exhausted = normalize_rate_limits({
        "ordinaryUsageAllowed": False,
        "rateLimits": {"limitId": "codex", "primary": None, "secondary": None},
    })
    assert exhausted["status"] == "exhausted"


def test_quota_notification_validation_rejects_malformed_updates():
    assert notification_requests_refresh({
        "method": "account/rateLimits/updated", "params": {"rateLimits": {"limitId": "codex"}},
    })
    assert not notification_requests_refresh({
        "method": "account/rateLimits/updated", "params": {"rateLimits": "bad"},
    })
    assert not notification_requests_refresh({"method": "account/rateLimits/updated"})
    assert not notification_requests_refresh("not-a-message")


def test_quota_api_auth_history_and_manual_refresh(tmp_path):
    quota = normalize_rate_limits({
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1_900_000_000},
            "secondary": None,
        }
    })
    monitor = FakeQuotaMonitor(quota)
    app, store = make_app(tmp_path, monitor)
    store.save_quota_snapshot(quota)
    with TestClient(app) as client:
        assert monitor.started
        assert client.get("/dashboard/api/quota").status_code == 401
        response = client.get("/dashboard/api/quota?history_limit=10", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["current"]["buckets"][0]["primary"]["remaining_percent"] == 75
        assert len(response.json()["history"]) == 1
        summary = client.get("/dashboard/api/summary", headers=HEADERS).json()
        assert summary["quota"]["status"] == "healthy"
        assert client.post("/dashboard/api/quota/refresh", headers=HEADERS).status_code == 200
        assert monitor.refreshes == 1
    assert monitor.stopped


def _save_analytics_job(store, *, job_id, request_id, task, operation, model, status,
                        created_at, worker_at, completed_at, elapsed_ms, queue_ms,
                        codex_ms, attempt=1, error=None, reference_ms=None, artifact_ms=None):
    store.save_job({
        "id": job_id, "request_id": request_id, "group_id": request_id.split("_")[0],
        "attempt": attempt, "parent_job_id": None, "task": task, "operation": operation,
        "model": model, "status": status, "stage": "Done", "created_at": created_at,
        "queued_at": created_at, "worker_acquired_at": worker_at,
        "codex_started_at": worker_at, "codex_completed_at": completed_at,
        "completed_at": completed_at, "elapsed_ms": elapsed_ms, "queue_ms": queue_ms,
        "codex_ms": codex_ms, "reference_download_ms": reference_ms,
        "artifact_processing_ms": artifact_ms, "reference_count": 0,
        "artifact_id": None, "error": error,
    })


def test_usage_and_performance_aggregations_use_fixed_fixtures(tmp_path):
    app, store = make_app(tmp_path)
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
    stamp = lambda minutes: (base + timedelta(minutes=minutes)).isoformat()
    _save_analytics_job(
        store, job_id="job-a", request_id="scene_a_initial", task="image", operation="image",
        model="gpt-5.6-luna", status="completed", created_at=stamp(0), worker_at=stamp(1),
        completed_at=stamp(3), elapsed_ms=180_000, queue_ms=60_000, codex_ms=90_000,
        reference_ms=10_000, artifact_ms=5_000,
    )
    _save_analytics_job(
        store, job_id="job-b", request_id="chat_b_initial", task="generate", operation="chat",
        model="gpt-5.6-terra", status="completed", created_at=stamp(1), worker_at=stamp(2),
        completed_at=stamp(4), elapsed_ms=180_000, queue_ms=60_000, codex_ms=100_000,
    )
    _save_analytics_job(
        store, job_id="job-c", request_id="scene_c_retry_1", task="image", operation="image",
        model="gpt-5.6-luna", status="failed", created_at=stamp(5), worker_at=stamp(5),
        completed_at=stamp(6), elapsed_ms=60_000, queue_ms=0, codex_ms=40_000,
        attempt=2, error={"type": "rate_limit", "message": "limited"},
    )
    store.save_usage("job-a", {"input_tokens": 80, "cached_input_tokens": 50, "output_tokens": 20, "total_tokens": 100})
    store.save_usage("job-b", {"input_tokens": 150, "cached_input_tokens": 100, "output_tokens": 50, "total_tokens": 200})

    usage = store.usage_analytics(stamp(0), stamp(60), 300)
    assert usage["summary"]["total_tasks"] == 3
    assert usage["summary"]["success_rate"] == 66.7
    assert usage["summary"]["retried"] == 1
    assert usage["summary"]["images_generated"] == 1
    assert usage["summary"]["chat_jobs"] == 1
    assert usage["summary"]["structured_jobs"] == 0
    assert usage["summary"]["total_tokens"] == 300
    assert usage["summary"]["cumulative_task_ms"] == 420_000
    assert usage["summary"]["codex_compute_ms"] == 230_000
    assert usage["summary"]["gateway_wall_clock_busy_ms"] == 240_000
    assert usage["summary"]["gateway_overhead_ms"] == 55_000
    assert {row["operation"]: row["jobs"] for row in usage["operations"]} == {"image": 2, "chat": 1}
    assert len(usage["timeline"]) <= 121

    performance = store.performance_analytics(stamp(0), stamp(60))
    assert performance["summary"]["success_rate"] == 66.7
    assert performance["summary"]["image_success_rate"] == 50.0
    assert performance["summary"]["text_success_rate"] == 100.0
    assert performance["summary"]["rate_limit_failures"] == 1
    assert performance["summary"]["safety_rewrites"] is None
    assert performance["latency"]["image"]["p50_ms"] == 60_000
    assert performance["latency"]["image"]["p95_ms"] == 180_000
    luna = next(row for row in performance["models"] if row["model"] == "gpt-5.6-luna")
    assert luna["failure_rate"] == 50.0
    assert luna["p95_ms"] == 180_000


def test_usage_api_range_validation_and_quota_history(tmp_path):
    app, store = make_app(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(hours=2)
    reset_at = int((now + timedelta(hours=3)).timestamp())
    for index, used in enumerate((20, 30)):
        observed = (start + timedelta(minutes=index * 90)).isoformat()
        store.save_quota_snapshot(normalize_rate_limits({
            "rateLimits": {
                "limitId": "codex", "limitName": "Codex",
                "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": reset_at},
                "secondary": None,
            }
        }, observed_at=observed))
    with TestClient(app) as client:
        params = {"range": "custom", "start": start.isoformat(), "end": now.isoformat()}
        response = client.get("/dashboard/api/usage", headers=HEADERS, params=params)
        assert response.status_code == 200
        data = response.json()
        assert data["range"]["name"] == "custom"
        assert len(data["quota"]["series"]) == 2
        pace = data["quota"]["pacing"][0]
        assert pace["deltas"]["last_6_hours"] == 10
        assert pace["recent_points_per_hour"] > 0
        assert client.get("/dashboard/api/performance", headers=HEADERS, params=params).status_code == 200
        assert client.get("/dashboard/api/usage", headers=HEADERS, params={"range": "bad"}).status_code == 422
        too_old = now - timedelta(days=367)
        invalid = client.get("/dashboard/api/usage", headers=HEADERS, params={
            "range": "custom", "start": too_old.isoformat(), "end": now.isoformat(),
        })
        assert invalid.status_code == 422
