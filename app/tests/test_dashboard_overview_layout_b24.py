"""Phase B.4: overview rearrangement retains live data bindings and B.3 enhancements."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "gateway"
HTML = ROOT / "dashboard.html"
STYLE = ROOT / "dashboard" / "overview-layout-b24.css"


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.ids = []
        self.parents = {}
        self.direct = {}
        self.stylesheets = []
        self.go = []

    def handle_starttag(self, tag, raw):
        attrs = dict(raw)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs.get("href"))
        if attrs.get("data-go"):
            self.go.append(attrs["data-go"])
        if "id" in attrs:
            ident = attrs["id"]
            self.ids.append(ident)
            self.parents[ident] = tuple(self.stack)
        if self.stack:
            parent = self.stack[-1][1].get("class", "")
            if "metric-grid" in parent.split():
                self.direct.setdefault("metrics", []).append((tag, attrs))
        if tag not in {"link", "meta", "input", "img", "br", "hr", "source"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def parse():
    parser = Structure()
    parser.feed(HTML.read_text(encoding="utf-8"))
    return parser


def test_live_state_spans_metric_grid_without_losing_bindings():
    p = parse()
    assert len(p.ids) == len(set(p.ids)), [name for name, n in Counter(p.ids).items() if n > 1]
    direct = p.direct["metrics"]
    assert len([1 for tag, a in direct if tag == "article" and "metric" in a.get("class", "").split()]) == 6
    assert len([1 for tag, a in direct if tag == "section" and "overview-live" in a.get("class", "").split()]) == 1
    assert any("metric-grid" in a.get("class", "").split() for _, a in p.parents["workerActive"])
    assert not any("overview-side" in a.get("class", "").split() for _, a in p.parents["workerActive"])
    assert any("overview-side" in a.get("class", "").split() for _, a in p.parents["recentFailures"])
    for name in ("metricJobs", "metricSuccess", "metricTokens", "metricAverage", "metricCompute", "metricImages",
                 "workerActive", "workerCapacity", "workerQueue", "recentJobs", "recentFailures"):
        assert p.ids.count(name) == 1, name


def test_layout_only_adds_new_cache_busted_stylesheet_and_keeps_actions():
    p = parse()
    assert STYLE.is_file()
    assert p.stylesheets[-1] == "/dashboard/assets/overview-layout-b24.css?v=ui-refresh-phase-b24-20260923"
    assert p.stylesheets[-2] == "/dashboard/assets/overview-b23.css?v=ui-refresh-phase-b23-20260923"
    assert set(p.go) == {"jobs", "gallery", "usage", "performance", "diagnostics", "settings"}


def test_layout_has_desktop_span_narrow_fallback_and_scope():
    css = STYLE.read_text(encoding="utf-8")
    for required in ("grid-column:4", "grid-row:1 / span 2", "grid-column:1 / -1",
                     "@media(max-width:1180px)", "@media(max-width:1050px)",
                     "@media(max-width:820px)", "@media(max-width:520px)",
                     ".overview-side", ".recent-surface"):
        assert required in css, required
    assert "#page-overview" in css
    assert "url(http" not in css and "@import" not in css
