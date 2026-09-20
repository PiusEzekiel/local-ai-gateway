"""8C: non-destructive live-status inspector, stale data warning and cache guardrails."""
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'gateway'
UI = ROOT / 'dashboard'
VERSION = '9b21-20260920'


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        item = dict(attrs)
        if item.get('id'):
            self.ids.append(item['id'])
            self.tags[item['id']] = (tag, item)


def test_only_adds_frontend_files_and_leaves_api_contracts_untouched():
    js = (UI / 'dashboard.js').read_text(encoding='utf8')
    assert 'getSummary()' in js and 'getJobs({limit: 50})' in js
    assert 'createLiveClient({' in js and 'loadDiagnostics({reset: true' in js
    assert 'settingsHasUnsavedChanges()' in js
    assert 'EventSource' not in js


def test_status_badge_is_inspectable_and_all_ids_unique():
    html = (ROOT / 'dashboard.html').read_text(encoding='utf8')
    parser = IDs()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    for item in ('syncDetailsToggle', 'syncDetails', 'syncLastSnapshot', 'syncLastEvent',
                 'syncTransport', 'syncNow', 'syncDetailsClose', 'syncDescription'):
        assert item in parser.ids
    tag, props = parser.tags['syncDetailsToggle']
    assert tag == 'button' and props['type'] == 'button'
    assert props['aria-controls'] == 'syncDetails' and props['aria-expanded'] == 'false'
    assert 'role="status" aria-live="polite"' in html


def test_all_frontend_assets_use_8c_cache_version():
    html = (ROOT / 'dashboard.html').read_text(encoding='utf8')
    for name in ('dashboard.css','diagnostics.css','settings.css','sync.css','dashboard.js'):
        assert f'/dashboard/assets/{name}?v={VERSION}' in html
    for path in UI.glob('*.js'):
        code = path.read_text(encoding='utf8')
        for ref in re.findall(r'from\s+[\'\"](\./[^\'\"]+\.js\?v=[^\'\"]+)[\'\"]',code):
            assert ref.endswith(f'?v={VERSION}'), (path.name,ref)
    assert f'from "./sync_status.js?v={VERSION}"' in (UI / 'dashboard.js').read_text(encoding='utf8')


def test_status_is_presentation_only_and_does_not_store_credentials():
    code = (UI / 'sync_status.js').read_text(encoding='utf8')
    for forbidden in ('sessionStorage','localStorage','fetch(', 'eval(', 'innerHTML',
                      'gatewayToken', 'Authorization', 'api_token', 'raw_stderr'):
        assert forbidden not in code
    assert 'EVENTS[type]' in code and 'lastEvent = now()' in code
    assert 'lastSnapshot = now()' in code


def test_stale_snapshot_does_not_treat_live_transport_as_healthy_api():
    code = (UI / 'sync_status.js').read_text(encoding='utf8')
    assert 'age > 120000' in code
    assert 'stale && mode === "live"' in code
    assert 'apiConnected &&' in code
    assert 'The gateway snapshot is stale.' in code
    assert 'The last gateway API request failed.' in code


def test_old_overlapping_api_snapshot_is_discarded():
    code = (UI / 'dashboard.js').read_text(encoding='utf8')
    assert 'let summaryGeneration = 0;' in code
    assert 'const generation = ++summaryGeneration;' in code
    assert code.count('if (generation !== summaryGeneration) return;') == 2
    assert 'syncMonitor.recordSnapshot();' in code
    assert 'syncMonitor.setGateway(connected);' in code


def test_sync_monitor_does_not_change_worker_job_or_quota_data():
    code = (UI / 'dashboard.js').read_text(encoding='utf8')
    assert 'syncMonitor.recordEvent(type);' in code
    assert 'syncMonitor.setTransport(next);' in code
    assert 'queueReconcile({jobs: true, selected, gallery: terminal' in code
    assert 'renderQuota(summary.quota)' in code
    assert 'setInterval(pollingInterval, 15000)' in code


def test_inspector_has_hidden_mobile_and_keyboard_safety():
    css = (UI / 'sync.css').read_text(encoding='utf8')
    js = (UI / 'sync_status.js').read_text(encoding='utf8')
    assert '.sync-panel[hidden]{display:none!important}' in css
    assert '@media(max-width:760px)' in css
    assert 'e.key === "Escape"' in js
    assert 'toggle.focus()' in js
    assert 'aria-expanded' in js
    assert 'panel.contains(e.target)' in js


def test_navigation_current_page_updates_without_changing_shortcuts():
    html = (ROOT / 'dashboard.html').read_text(encoding='utf8')
    js = (UI / 'dashboard.js').read_text(encoding='utf8')
    assert 'data-page="overview" aria-current="page"' in html
    assert 'node.setAttribute("aria-current", "page")' in js
    assert 'node.removeAttribute("aria-current")' in js
    assert 'const pages = ["overview", "jobs", "gallery", "usage", "performance", "diagnostics", "settings"]' in js
