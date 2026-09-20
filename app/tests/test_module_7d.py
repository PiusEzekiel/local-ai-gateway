"""Module 7D: frontend controls and bounded authenticated system metadata."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from gateway import app as root
from gateway.config import Settings
from gateway.job_store import JobStore
from gateway.system_info import codex_version, system_snapshot

TOKEN = "test-7d-token-abcdefghijklmnopqrstuvwxyz"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
GATEWAY = Path(__file__).resolve().parents[1] / "gateway"


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])


def test_settings_html_is_complete_and_ids_are_unique():
    html = (GATEWAY / "dashboard.html").read_text(encoding="utf-8")
    parser = IDs(); parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    for label in ("settingsSave", "settingsDiscard", "settingsGroups", "settingsRestartNotice",
                  "settingsPreview", "settingsCleanup", "settingsIncludeOrphans", "settingsSystemRows",
                  "settingsCleanupDialog", "settingsPurgeDialog", "settingsPurgePhrase", "privacyPurgeText"):
        assert label in parser.ids
    assert 'class="page settings-page"' in html
    assert "Chunk 7" not in html


def test_settings_assets_are_versioned_and_ui_is_modular():
    html = (GATEWAY / "dashboard.html").read_text(encoding="utf-8")
    dashboard = (GATEWAY / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    settings = (GATEWAY / "dashboard" / "settings.js").read_text(encoding="utf-8")
    assert "/dashboard/assets/settings.css?v=9c3-20260920" in html
    assert 'from "./settings.js?v=9c3-20260920"' in dashboard
    assert 'initializeSettings();' in dashboard
    assert 'if (page === "settings") loadSettings();' in dashboard
    assert 'from "./api.js?v=9c3-20260920"' in settings
    assert "innerHTML" not in settings
    assert "DELETE_EXPIRED_DATA" in settings and "DELETE_RETAINED_DATA" in settings
    assert 'getStoragePreview($("settingsIncludeOrphans").checked)' in settings
    for name in ("getSettings", "patchSettings", "getPrivacyStorage", "purgePrivacy",
                 "getStoragePreview", "runStorageCleanup", "getSystemInfo"):
        assert f"export const {name}" in (GATEWAY / "dashboard" / "api.js").read_text(encoding="utf-8")


def test_settings_css_isolated_and_responsive():
    css = (GATEWAY / "dashboard" / "settings.css").read_text(encoding="utf-8")
    for name in (".settings-layout", ".settings-field", ".settings-dialog", ".settings-restart",
                 "@media(max-width:820px)", "@media(max-width:520px)"):
        assert name in css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in (GATEWAY / "dashboard" / "diagnostics.css").read_text(encoding="utf-8")


def test_codex_version_requires_codex_prefix(monkeypatch):
    class FakeCompleted:
        def __init__(self, output):
            self.returncode = 0; self.stdout = output
    monkeypatch.setattr("gateway.system_info.subprocess.run", lambda *a, **kw: FakeCompleted(b"python 3.13\n"))
    assert codex_version("fake") is None
    monkeypatch.setattr("gateway.system_info.subprocess.run", lambda *a, **kw: FakeCompleted(b"codex-cli 0.99.1\n"))
    assert codex_version("fake") == "codex-cli 0.99.1"


def test_codex_version_timeout_or_failure_is_safe(monkeypatch):
    import subprocess
    def explode(*args, **kwargs):
        raise subprocess.TimeoutExpired("codex", 3)
    monkeypatch.setattr("gateway.system_info.subprocess.run", explode)
    assert codex_version("fake") is None


def test_system_snapshot_redacts_host_path(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.system_info.codex_version", lambda _: "codex-cli 0.9")
    db = tmp_path / "private-folder" / "gateway.sqlite3"
    db.parent.mkdir(); db.write_bytes(b"DBDATA")
    data = system_snapshot(codex_exe="PRIVATE_EXECUTABLE", db_path=db,
                           started_monotonic=0, gateway_version="0.1.0",
                           active=1, queued=2, concurrency=3, max_queue=4)
    assert data["database_bytes"] == 6
    assert data["workers"] == {"active": 1, "queued": 2, "concurrency": 3, "max_queue": 4}
    assert "PRIVATE_EXECUTABLE" not in str(data)
    assert "private-folder" not in str(data)


def test_system_endpoint_bearer_and_operational_data(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.system_info.codex_version", lambda _: None)
    app = root.create_app(settings=Settings(api_token=TOKEN, codex_exe=sys.executable,
                                              quota_monitor_enabled=False),
                          runner=object(), job_store=JobStore(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        assert client.get("/dashboard/api/system").status_code == 401
        response = client.get("/dashboard/api/system", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["gateway_version"] == "0.1.0"
        assert body["codex_version"] is None
        assert body["platform"]
        assert body["database_bytes"] > 0
        assert body["workers"]["active"] == 0
        assert "api_token" not in body and "codex_exe" not in body
        assert client.get("/v1/models", headers=HEADERS).status_code == 200


def test_no_unprotected_destructive_actions(tmp_path):
    app = root.create_app(settings=Settings(api_token=TOKEN, codex_exe=sys.executable,
                                              quota_monitor_enabled=False),
                          runner=object(), job_store=JobStore(tmp_path / "gateway.sqlite3"))
    with TestClient(app) as client:
        for path in ("/dashboard/api/privacy/purge", "/dashboard/api/storage/cleanup"):
            assert client.post(path, json={}).status_code == 401
            assert client.post(path, headers=HEADERS, json={}).status_code == 422
