"""Module 6D frontend integration guardrails (no browser runtime required)."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / "gateway"


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])


def test_dashboard_ids_unique_and_diagnostics_controls_present():
    html = (GATEWAY / "dashboard.html").read_text(encoding="utf-8")
    parser = IDs()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids)), "Duplicate HTML id found"
    for name in (
        "page-diagnostics", "diagSearch", "diagTask", "diagOperation", "diagModel",
        "diagErrorType", "diagTotal", "diagFeed", "diagInspector", "diagRefresh",
        "diagLoadMore", "diagCustomRange", "diagApplyCustom", "diagError",
    ):
        assert name in parser.ids
    assert 'id="page-diagnostics"' in html
    assert 'aria-label="Failure inspector"' in html


def test_existing_pages_and_media_dialog_survive():
    html = (GATEWAY / "dashboard.html").read_text(encoding="utf-8")
    for name in (
        "page-overview", "page-jobs", "page-gallery", "page-usage", "page-performance",
        "page-settings", "lightbox", "authForm", "recentFailures",
    ):
        assert f'id="{name}"' in html


def test_diagnostics_client_calls_only_auth_client():
    api = (GATEWAY / "dashboard" / "api.js").read_text(encoding="utf-8")
    for name in (
        "getDiagnosticsSummary", "getDiagnostics", "getDiagnostic", "getDiagnosticBundle",
    ):
        assert f"export const {name}" in api
    assert 'headers.set("Authorization"' in api, "Shared API client must keep Bearer header"
    assert 'params.set("error_type"' not in api  # Iterated from a fixed allowlist instead.


def test_diagnostics_controller_imported_and_initialized():
    dashboard = (GATEWAY / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    diagnostics = (GATEWAY / "dashboard" / "diagnostics.js").read_text(encoding="utf-8")
    assert 'from "./diagnostics.js?v=9c2-20260920"' in dashboard
    assert 'initializeDiagnostics();' in dashboard
    assert 'loadDiagnostics({reset: true})' in dashboard
    assert 'inspectDiagnosticJob(failure.id)' in dashboard
    assert 'getDiagnosticBundle(jobId)' in diagnostics
    assert 'textArea.select()' in diagnostics
    assert 'URL.revokeObjectURL' in diagnostics
    assert 'textContent' in diagnostics
    assert '.innerHTML' not in diagnostics


def test_diagnostics_css_uses_independent_scrollers():
    css = (GATEWAY / "dashboard" / "diagnostics.css").read_text(encoding="utf-8")
    for name in (".diag-feed", ".diag-inspector", ".diag-timeline", ".diag-failure.selected"):
        assert name in css
