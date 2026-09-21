"""Module 8B: authenticated live dashboard integration guardrails.

Browser stream parser/runtime tests also ship as frontend_live.test.mjs and are
run manually with Node; pytest intentionally doesn't require Node on Windows.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

G = Path(__file__).resolve().parents[1] / 'gateway'
D = G / 'dashboard'
VERSION = '9c3-20260920'


class IDs(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = []
    def handle_starttag(self, tag, attrs):
        item = dict(attrs).get('id')
        if item: self.ids.append(item)


def test_sse_reader_has_bounded_incremental_json_parser():
    code = (D / 'live.js').read_text(encoding='utf8')
    for text in ('createSSEParser', 'TextDecoder', 'MAX_FRAME', 'resync_required',
                 'reader.read()', 'response.body.getReader()', 'reader.cancel()', 'reader.releaseLock()'):
        assert text in code
    assert 'eval(' not in code and 'innerHTML' not in code


def test_sse_is_authenticated_with_bearer_header_not_query_token():
    code = (D / 'api.js').read_text(encoding='utf8')
    assert 'export function openEventStream(signal)' in code
    assert 'new Headers({Authorization: `Bearer ${token}`, Accept: "text/event-stream"})' in code
    assert 'fetch("/dashboard/api/events", {headers, signal, cache: "no-store"})' in code
    assert 'EventSource' not in (D / 'dashboard.js').read_text(encoding='utf8')


def test_sse_retry_stop_and_auth_error_handling():
    code = (D / 'live.js').read_text(encoding='utf8')
    for text in ('new AbortController()', 'current.signal.aborted', 'response.status === 401',
                 'onUnauthorized?.()', 'controller?.abort()', 'initialDelay * 2', 'maximumDelay',
                 'setTimeout', 'clearTimeout', 'signal("retrying")'):
        assert text in code


def test_all_module_imports_and_html_asset_references_share_release():
    html = (G / 'dashboard.html').read_text(encoding='utf8')
    for name in ('dashboard.css', 'settings.css', 'diagnostics.css', 'dashboard.js'):
        assert f'/dashboard/assets/{name}?v={VERSION}' in html
    for file in D.glob('*.js'):
        code = file.read_text(encoding='utf8')
        for ref in re.findall(r'from [\'\"](\./[^\'\"]+\.js\?v=[^\'\"]+)[\'\"]', code):
            assert ref.endswith(f'?v={VERSION}'), (file.name, ref)
    assert f'from "./live.js?v={VERSION}"' in (D / 'dashboard.js').read_text(encoding='utf8')


def test_live_status_accessible_and_no_duplicate_html_ids():
    html = (G / 'dashboard.html').read_text(encoding='utf8')
    ids = IDs(); ids.feed(html)
    assert ids.ids.count('liveStatus') == 1
    assert len(ids.ids) == len(set(ids.ids))
    assert 'role="status" aria-live="polite"' in html
    assert 'id="authForm"' in html and 'id="galleryGrid"' in html


def test_live_transport_does_not_rewrite_existing_dashboard_apis():
    app = (D / 'dashboard.js').read_text(encoding='utf8')
    assert 'getSummary()' in app and 'getJobs({limit: 50})' in app
    assert 'renderSummary(summary)' in app and 'renderFeed(' in app
    assert 'state.page === "gallery"' in app
    assert 'state.page === "diagnostics"' in app
    assert '!settingsHasUnsavedChanges()' in app


def test_transport_failure_uses_slow_poll_with_authoritative_snapshot():
    code = (D / 'dashboard.js').read_text(encoding='utf8')
    assert 'setInterval(pollingInterval, 15000)' in code
    assert 'setInterval(() => {' in code and '}, 60000)' in code
    assert 'lastLiveStatus !== "live"' in code
    assert 'setInterval(() => refresh(), 5000)' not in code
    assert 'if (type === "ready" || type === "resync_required")' in code


def test_event_driven_refresh_is_coalesced_and_context_aware():
    code = (D / 'dashboard.js').read_text(encoding='utf8')
    assert 'changeTimer = setTimeout(async () => {' in code
    assert '}, 180)' in code
    for event in ('quota.changed', 'workers.changed', 'model.changed', 'storage.changed', 'job.'):
        assert event in code
    assert 'document.hidden' in code
    assert 'document.addEventListener("visibilitychange"' in code
    assert 'feed.scrollTop = scroll' in code


def test_settings_and_diagnostics_css_remain_isolated():
    html = (G / 'dashboard.html').read_text(encoding='utf8')
    css = (D / 'dashboard.css').read_text(encoding='utf8')
    assert '.live-status.live' in css and '.live-status.retrying' in css
    assert (D / 'settings.css').is_file() and (D / 'diagnostics.css').is_file()
    assert 'page-settings' in html and 'page-diagnostics' in html
