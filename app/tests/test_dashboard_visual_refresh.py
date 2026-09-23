"""Phase B.1 presentation guardrails: no new fake data or runtime contracts."""
from html.parser import HTMLParser
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / 'gateway'
HTML = GATEWAY / 'dashboard.html'
STYLE = GATEWAY / 'dashboard' / 'visual-refresh.css'


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.shortcuts = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if 'id' in props:
            self.ids.append(props['id'])
        if tag == 'link' and props.get('rel') == 'stylesheet':
            self.links.append(props.get('href', ''))
        if tag == 'button' and 'data-go' in props:
            self.shortcuts.append(props['data-go'])


def test_visual_theme_loads_last_without_reversioning_original_modules():
    html = HTML.read_text(encoding='utf-8')
    parser = IdParser(); parser.feed(html)
    assert STYLE.is_file()
    assert parser.links[-4:] == [
        '/dashboard/assets/visual-refresh.css?v=ui-refresh-phase-b-20260923',
        '/dashboard/assets/polish-b2.css?v=ui-refresh-phase-b2-20260923',
        '/dashboard/assets/overview-b23.css?v=ui-refresh-phase-b23-20260923',
        '/dashboard/assets/overview-layout-b24.css?v=ui-refresh-phase-b24-20260923',
    ]
    assert '/dashboard/assets/dashboard.js?v=ui-refresh-phase-a-20260923' in html
    assert '/dashboard/assets/settings.css?v=ui-refresh-phase-a-20260923' in html


def test_real_summary_bindings_and_settings_anchors_remain_unique():
    parser = IdParser(); parser.feed(HTML.read_text(encoding='utf-8'))
    assert len(parser.ids) == len(set(parser.ids))
    for name in ('metricJobs', 'metricSuccess', 'metricTokens', 'metricAverage',
                 'metricCompute', 'metricImages', 'recentJobs', 'recentFailures',
                 'quotaNotice', 'referenceCacheSettings', 'settingsSave', 'settingsDiscard'):
        assert parser.ids.count(name) == 1


def test_shortcuts_use_existing_dashboard_navigation_contract():
    parser = IdParser(); parser.feed(HTML.read_text(encoding='utf-8'))
    assert set(parser.shortcuts) == {'jobs', 'gallery', 'usage', 'performance', 'diagnostics', 'settings'}
    js = (GATEWAY / 'dashboard' / 'dashboard.js').read_text(encoding='utf-8')
    assert 'document.querySelectorAll("[data-go]")' in js
    assert 'go(node.dataset.go)' in js


def test_visual_css_keeps_existing_pages_and_is_responsive():
    css = STYLE.read_text(encoding='utf-8')
    for selector in ('.overview-heading', '.metric-grid', '.jobs-layout', '.gallery-grid',
                     '.analytics-metrics', '.settings-page', '.nav-icon'):
        assert selector in css
    assert '@media(max-width:820px)' in css
    assert '@media(max-width:520px)' in css
    assert '@media(prefers-reduced-motion:reduce)' in css
    assert 'url(http' not in css and '@import' not in css
