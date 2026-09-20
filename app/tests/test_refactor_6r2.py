"""Module 6R.2: dashboard router extraction, dependency isolation, and API stability."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from gateway import app as root
from gateway.dashboard_routes import create_dashboard_router
from gateway.job_store import JobStore

TOKEN = "dashboard-router-test-token-with-24-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


DASHBOARD_ENDPOINTS = {
    ("GET", "/dashboard/api/summary"),
    ("GET", "/dashboard/api/quota"),
    ("POST", "/dashboard/api/quota/refresh"),
    ("GET", "/dashboard/api/usage"),
    ("GET", "/dashboard/api/performance"),
    ("GET", "/dashboard/api/jobs"),
    ("GET", "/dashboard/api/jobs/{job_id}"),
    ("GET", "/dashboard/api/diagnostics/summary"),
    ("GET", "/dashboard/api/diagnostics"),
    ("GET", "/dashboard/api/diagnostics/{job_id}"),
    ("GET", "/dashboard/api/diagnostics/{job_id}/bundle"),
    ("GET", "/dashboard/api/gallery"),
    ("GET", "/dashboard/api/artifacts/{artifact_id}"),
    ("GET", "/dashboard/api/artifacts/{artifact_id}/thumbnail"),
}


def make_app(tmp_path: Path, model="gpt-5.6-luna"):
    store = JobStore(tmp_path / "db.sqlite3")
    settings = root.Settings(api_token=TOKEN, codex_exe=sys.executable, model=model, quota_monitor_enabled=False)
    app = root.create_app(settings=settings, runner=object(), job_store=store)
    return app, store


def test_router_exposes_exact_14_dashboard_methods(tmp_path):
    app, store = make_app(tmp_path)
    try:
        # Do not assume included router endpoints are flattened into app.routes.
        # The generated OpenAPI schema resolves their effective HTTP paths.
        found = {
            (method.upper(), path)
            for path, path_item in app.openapi()["paths"].items()
            if path.startswith("/dashboard/api/")
            for method in path_item
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        }
        # 6R.2 guarantees the original fourteen endpoints. Modules 7A–7C add independently owned settings, privacy and
        # retention routes; keep the original fourteen checked separately.
        assert DASHBOARD_ENDPOINTS <= found
        assert found - DASHBOARD_ENDPOINTS == {
            ("GET", "/dashboard/api/settings"),
            ("PATCH", "/dashboard/api/settings"),
            ("GET", "/dashboard/api/jobs/{job_id}/content"),
            ("GET", "/dashboard/api/privacy/storage"),
            ("POST", "/dashboard/api/privacy/purge"),
            ("GET", "/dashboard/api/storage/preview"),
            ("GET", "/dashboard/api/storage/health"),
            ("GET", "/dashboard/api/storage/inventory"),
            ("POST", "/dashboard/api/storage/cleanup"),
            ("GET", "/dashboard/api/reference-cache"),
            ("POST", "/dashboard/api/reference-cache/clear"),
            ("GET", "/dashboard/api/system"),
            ("GET", "/dashboard/api/events"),
        }
        assert len(found) == len(DASHBOARD_ENDPOINTS) + 13
        assert callable(create_dashboard_router)
    finally:
        store.close()


def test_original_v1_contract_routes_still_registered(tmp_path):
    app, store = make_app(tmp_path)
    try:
        found = {
            (method.upper(), path)
            for path, path_item in app.openapi()["paths"].items()
            for method in path_item
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        }
        assert {("GET", "/health"), ("GET", "/v1/jobs"),
                ("GET", "/v1/models"), ("PUT", "/v1/models/default"),
                ("GET", "/v1/capabilities"), ("POST", "/v1/generate"),
                ("POST", "/v1/research"), ("POST", "/v1/chat/completions"),
                ("POST", "/v1/images/generations")} <= found
    finally:
        store.close()


def test_extracted_routes_still_require_bearer(tmp_path):
    app, store = make_app(tmp_path)
    try:
        with TestClient(app) as client:
            for method, path in DASHBOARD_ENDPOINTS:
                response = client.request(method, path.replace("{job_id}", "missing")
                                                 .replace("{artifact_id}", "missing"))
                assert response.status_code == 401, (method, path, response.text)
    finally:
        store.close()


def test_router_reads_current_model_after_settings_change(tmp_path, monkeypatch):
    monkeypatch.setattr(root, "SETTINGS_PATH", tmp_path / "settings.json")
    # The shared local harness contains a minimal SettingsManager stand-in; the
    # actual Windows installation uses the real save_model implementation.
    if not hasattr(root.SettingsManager, "save_model"):
        monkeypatch.setattr(root.SettingsManager, "save_model", lambda self, model: {"model": model}, raising=False)
    app, store = make_app(tmp_path)
    try:
        with TestClient(app) as client:
            old = client.get("/dashboard/api/summary", headers=HEADERS)
            assert old.status_code == 200
            assert old.json()["gateway"]["model"] == "gpt-5.6-luna"
            updated = client.put("/v1/models/default", headers=HEADERS,
                                 json={"model": "gpt-5.6-terra"})
            assert updated.status_code == 200
            assert client.get("/dashboard/api/summary", headers=HEADERS).json()["gateway"]["model"] == "gpt-5.6-terra"
    finally:
        store.close()


def test_router_instance_does_not_share_model_or_history(tmp_path):
    first, first_store = make_app(tmp_path / "first", "gpt-5.6-luna")
    second, second_store = make_app(tmp_path / "second", "gpt-5.6-terra")
    try:
        first.state.history.add("first_request", "generate", "gpt-5.6-luna")
        with TestClient(first) as client1, TestClient(second) as client2:
            assert client1.get("/dashboard/api/summary", headers=HEADERS).json()["gateway"]["model"] == "gpt-5.6-luna"
            assert client2.get("/dashboard/api/summary", headers=HEADERS).json()["gateway"]["model"] == "gpt-5.6-terra"
            assert len(client1.get("/dashboard/api/jobs", headers=HEADERS).json()["jobs"]) == 1
            assert len(client2.get("/dashboard/api/jobs", headers=HEADERS).json()["jobs"]) == 0
    finally:
        first_store.close()
        second_store.close()
