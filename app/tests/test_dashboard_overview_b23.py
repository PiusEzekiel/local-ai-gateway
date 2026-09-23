"""B.3: preserve original control-plane data model while polishing Overview."""
from html.parser import HTMLParser
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / "gateway"
HTML = GATEWAY / "dashboard.html"
CSS = GATEWAY / "dashboard" / "overview-b23.css"
JS = GATEWAY / "dashboard" / "overview-b23.mjs"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs["href"])
        if tag == "script" and attrs.get("type") == "module":
            self.assets.append(attrs["src"])


def inspect():
    parser = Elements()
    parser.feed(HTML.read_text(encoding="utf-8"))
    return parser


def test_quota_toggle_accessibility_and_existing_bindings_are_preserved():
    markup = HTML.read_text(encoding="utf-8")
    ids = inspect().ids
    assert len(ids) == len(set(ids))
    for ident in ("quotaNotice", "quotaTitle", "quotaSubtitle", "quotaObserved", "quotaBuckets", "topQuota",
                  "topRunning", "topQueued", "recentJobs", "allJobs", "quotaToggle", "quotaMiniValue", "quotaBrief"):
        assert ids.count(ident) == 1
    assert 'id="quotaToggle" aria-expanded="false" aria-controls="quotaBuckets"' in markup
    assert 'class="quota-buckets" id="quotaBuckets" hidden' in markup


def test_phase_b23_loads_new_scripts_separately_without_reversioning_phase_a():
    parsed = inspect()
    assert CSS.is_file() and JS.is_file()
    assert parsed.assets[-1] == "/dashboard/assets/overview-b23.mjs?v=ui-refresh-phase-b23-20260923"
    assert parsed.assets[-4] == "/dashboard/assets/overview-b23.css?v=ui-refresh-phase-b23-20260923"
    assert '/dashboard/assets/dashboard.js?v=ui-refresh-phase-a-20260923' in parsed.assets


def test_quota_capacity_is_derived_from_existing_authenticated_snapshot():
    source = JS.read_text(encoding="utf-8")
    assert 'getElementById("topQuota")' in source
    assert "remainingQuotaLabel(source.dataset.quotaLeft || source.textContent)" in source
    assert 'fetch(' not in source and 'localStorage.setItem("gatewayToken"' not in source
    assert "percentage, not remaining token count" in source


def test_collapsed_feed_is_intrinsic_and_jobs_keep_real_labels():
    css = CSS.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    for phrase in (".overview-grid {align-items:start}", ".recent-surface {align-self:start", "#recentJobs", "max-height:440px"):
        assert phrase in css
    assert 'querySelectorAll(".job-row:not([data-kind-decorated])")' in js
    assert 'icon.setAttribute("aria-hidden", "true")' in js
    assert 'observer.observe(feed, {childList:true})' in js


def test_browser_preferences_cannot_store_auth_or_change_server_configuration():
    source = JS.read_text(encoding="utf-8")
    assert 'gatewayQuotaDetailsExpanded' in source
    assert 'getItem(QUOTA_DETAILS_PREF)' in source
    assert 'setItem(QUOTA_DETAILS_PREF' in source
    for forbidden in ("patchSettings", "setToken(", "api/summary", "api/quota", "database", "gatewayToken"):
        assert forbidden not in source
