"""9B.5 onboarding and navigational shell behavior; see frontend_sidebar.test.mjs."""
from html.parser import HTMLParser
from pathlib import Path
import re

G = Path(__file__).resolve().parents[1] / 'gateway'
D = G / 'dashboard'
V = '9c3-20260920'

class Parsed(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = []
    def handle_starttag(self, tag, attrs):
        key = dict(attrs).get('id')
        if key: self.ids.append(key)


def test_login_describes_gateway_and_token_without_leaking_secret():
    html = (G/'dashboard.html').read_text('utf8')
    for phrase in ('n8n', 'Codex', 'Pinokio', 'Gateway access token',
                   'not</strong> your ChatGPT login or an OpenAI API key',
                   'show-token.ps1', 'session storage', 'Keep it private'):
        assert phrase in html
    assert 'autocomplete="off"' in html
    assert 'type="password"' in html
    assert '<details class="auth-help">' in html
    assert '<code class="auth-command">powershell -NoProfile -ExecutionPolicy Bypass -File .\\show-token.ps1</code>' in html


def test_sidebar_toggle_is_keyboard_accessible_and_has_stable_targets():
    html = (G/'dashboard.html').read_text('utf8')
    parsed = Parsed(); parsed.feed(html)
    assert len(parsed.ids) == len(set(parsed.ids))
    for key in ('appShell', 'primarySidebar', 'sidebarToggle', 'authForm', 'authError', 'disconnectButton'):
        assert parsed.ids.count(key) == 1
    assert 'aria-controls="primarySidebar" aria-expanded="true" aria-label="Collapse sidebar"' in html
    for page in ('Overview','Jobs','Gallery','Usage','Performance','Diagnostics','Settings'):
        assert f'aria-label="{page}" title="{page}"' in html


def test_collapsed_rail_preserves_navigation_and_disconnect_controls():
    html = (G/'dashboard.html').read_text('utf8')
    css = (D/'shell.css').read_text('utf8')
    assert html.count('class="nav-mini"') == 7
    assert 'class="disconnect-label"' in html
    for rule in ('.app-shell.sidebar-collapsed', '.nav-mini', '.sidebar-toggle',
                 '.app-shell.sidebar-collapsed .nav-item',
                 '.app-shell.sidebar-collapsed .sidebar-disconnect'):
        assert rule in css
    assert '.app-shell.sidebar-collapsed .sidebar {display:none;}' in css


def test_collapsing_resizes_existing_viewer_but_preserves_status_bar():
    css = (D/'shell.css').read_text('utf8')
    assert '.app-shell.sidebar-collapsed ~ .lightbox {inset:65px 0 0 72px;}' in css
    assert '.app-shell.sidebar-collapsed ~ .lightbox {inset:65px 0 0 0;}' in css
    assert '.health-item' not in css


def test_toggle_restores_tab_preference_without_changing_backend():
    js = (D/'dashboard.js').read_text('utf8')
    assert 'const SIDEBAR_PREF = "gatewaySidebarCollapsed";' in js
    assert 'function setSidebarCollapsed(collapsed)' in js
    assert 'setSidebarCollapsed(sessionStorage.getItem(SIDEBAR_PREF) === "1")' in js
    assert '$("sidebarToggle").addEventListener("click"' in js
    assert 'sessionStorage.setItem(SIDEBAR_PREF, isCollapsed ? "1" : "0")' in js
    assert 'liveClient.stop()' not in js[js.index('function setSidebarCollapsed'):js.index('function setConnected')]
    assert (G/'app.py').is_file()


def test_new_shell_css_loads_last_and_script_version_is_cache_busted():
    html = (G/'dashboard.html').read_text('utf8')
    assert f'/dashboard/assets/shell.css?v={V}' in html
    assert f'/dashboard/assets/dashboard.js?v={V}' in html
    assert html.index('session.css?v=') < html.index('shell.css?v=') < html.index('</head>')
    for file in D.glob('*.js'):
        for path in re.findall(r'from [\'"](\./[^\'"]+\.js\?v=[^\'"]+)[\'"]',file.read_text('utf8')):
            assert path.endswith(f'?v={V}'),(file.name,path)


def test_all_previous_gallery_reference_and_logout_integrations_remain():
    js = (D/'dashboard.js').read_text('utf8')
    html = (G/'dashboard.html').read_text('utf8')
    for marker in ('initializeLightbox()', 'disconnectSession()', 'createLiveClient(',
                   'renderGallery(', 'abortGalleryMedia()'):
        assert marker in js
    for marker in ('id="lightboxReferences"', 'id="lightboxOutput"',
                   'id="syncDetailsToggle"', 'id="appShell" hidden'):
        assert marker in html
