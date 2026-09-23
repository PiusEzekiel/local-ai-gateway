"""Phase B.2 guardrails for existing, authenticated dashboard markup and CSS.

These static checks intentionally protect real metric bindings, cache-busting,
accessibility and the existing operation model without adding test dependencies.
"""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "gateway"
HTML = ROOT / "dashboard.html"
CSS = ROOT / "dashboard" / "polish-b2.css"
BASE = ROOT / "dashboard" / "visual-refresh.css"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.parents[attrs["id"]] = tuple(self.stack)
        self.nodes.append((tag, attrs))
        if tag not in {"link", "meta", "input", "img", "br", "hr", "source"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def page():
    parsed = Elements()
    parsed.feed(HTML.read_text(encoding="utf-8"))
    return parsed


def test_polish_is_last_stylesheet_and_uses_distinct_version():
    links = [a["href"] for t, a in page().nodes if t == "link" and a.get("rel") == "stylesheet"]
    assert BASE.is_file() and CSS.is_file()
    assert links[-4:] == [
        "/dashboard/assets/visual-refresh.css?v=ui-refresh-phase-b-20260923",
        "/dashboard/assets/polish-b2.css?v=ui-refresh-phase-b2-20260923",
        "/dashboard/assets/overview-b23.css?v=ui-refresh-phase-b23-20260923",
        "/dashboard/assets/overview-layout-b24.css?v=ui-refresh-phase-b24-20260923",
    ]
    # Don't change the Phase A module graph just to install a CSS-only upgrade.
    assert '/dashboard/assets/dashboard.js?v=ui-refresh-phase-a-20260923' in HTML.read_text(encoding="utf-8")


def test_all_original_operational_bindings_remain_unique():
    parsed = page()
    ids = [a["id"] for _, a in parsed.nodes if "id" in a]
    assert len(ids) == len(set(ids)), [item for item, n in Counter(ids).items() if n > 1]
    for ident in (
        "topQuota", "quotaNotice", "quotaBuckets", "quotaTitle", "quotaSubtitle", "quotaObserved",
        "metricJobs", "metricSuccess", "metricTokens", "metricAverage", "metricCompute", "metricImages",
        "usageTasks", "usageSuccessful", "usageFailed", "usageRetried", "usageInput", "usageCached",
        "usageOutput", "usageReasoning", "usageTotalTokens", "usageImages", "usageResearch",
        "usageChat", "usageStructured", "usageGenerate", "timeCumulative", "timeBusy",
        "timeCodex", "timeQueue", "timeReferences", "timeArtifacts", "timeOverhead",
        "perfSuccess", "perfImageSuccess", "perfTextSuccess", "perfRetries", "perfRateLimit",
        "perfReference", "perfSafety", "jobsChart", "tokensChart", "quotaHistoryChart",
        "recentJobs", "allJobs", "jobInspector", "galleryGrid", "diagFeed", "settingsSave",
        "referenceCacheSettings", "settingsRestartNotice",
    ):
        assert ids.count(ident) == 1, ident


def test_usage_grouping_keeps_metric_ids_in_respective_sections():
    parsed = page()
    groups = {
        "usageTasks": "usage-activity", "usageCached": "usage-tokens",
        "usageImages": "usage-operations", "timeCumulative": "usage-timing",
    }
    for name, expected in groups.items():
        assert any(expected in parent.get("class", "").split()
                   for _, parent in parsed.parents[name]), name
    assert all(f"{name}Title" in parsed.parents for name in
               ("usageActivity", "usageTokens", "usageOperations", "usageTiming"))


def test_aux_performance_metric_is_not_a_seventh_primary_card():
    parsed = page()
    assert any("performance-aux" in parent.get("class", "").split()
               for _, parent in parsed.parents["perfSafety"])
    assert any("analytics-metrics" in parent.get("class", "").split()
               for _, parent in parsed.parents["perfSuccess"])
    assert any(a.get("id") == "perfSafety" for _, a in parsed.nodes)


def test_metric_icons_are_decorative_and_have_accessible_text_labels():
    parsed = page()
    markup = HTML.read_text(encoding="utf-8")
    assert markup.count('class="metric-glyph" aria-hidden="true"') == 6
    assert markup.count('class="metric-heading"') == 6
    assert markup.count('class="metric-label"') == 6
    assert markup.count('<svg viewBox="0 0 24 24"') >= 6


def test_css_uses_reported_buckets_and_retains_mobile_and_reduced_motion():
    css = CSS.read_text(encoding="utf-8")
    for selector in (".quota-buckets", ".quota-bucket", ".quota-credit", ".metric-heading",
                     ".gallery-thumb", ".usage-group", ".performance-aux", ".settings-nav"):
        assert selector in css
    assert "grid-column:1/-1" in css
    assert "object-fit:contain" in css
    assert "@media(max-width:820px)" in css
    assert "@media(max-width:520px)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "url(http" not in css and "@import" not in css


def test_no_unsupported_metrics_or_runtime_changes_are_introduced():
    text = HTML.read_text(encoding="utf-8")
    for invented in ("CPU usage", "GPU utilization", "Browse models", "Disk utilization"):
        assert invented not in text
    assert '<script type="module" src="/dashboard/assets/dashboard.js?v=ui-refresh-phase-a-20260923"' in text
