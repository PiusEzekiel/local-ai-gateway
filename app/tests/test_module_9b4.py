"""Module 9B.4 browser-session boundaries; Node executes behavioural cases."""
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'gateway'
FRONT = ROOT / 'dashboard'
VERSION = '9c3-20260920'


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        identifier = dict(attrs).get('id')
        if identifier:
            self.ids.append(identifier)


def test_disconnect_control_is_visible_and_accessible():
    html = (ROOT / 'dashboard.html').read_text('utf8')
    parser = Ids()
    parser.feed(html)
    assert parser.ids.count('disconnectButton') == 1
    assert len(parser.ids) == len(set(parser.ids))
    assert 'aria-label="Disconnect dashboard and clear this tab' in html
    assert 'id="token" type="password" autocomplete="off"' in html


def test_logout_ui_has_isolated_styles_and_mobile_visibility():
    html = (ROOT / 'dashboard.html').read_text('utf8')
    css = (FRONT / 'session.css').read_text('utf8')
    assert f'/dashboard/assets/session.css?v={VERSION}' in html
    for rule in ('.sidebar-disconnect', ':focus-visible', '@media(max-width:760px)'):
        assert rule in css


def test_disconnect_stops_sse_clears_token_and_reloads_tab():
    js = (FRONT / 'dashboard.js').read_text('utf8')
    for snippet in ('function disconnectSession({expired = false}', 'liveClient.stop()',
                    'closeLightbox()', 'cancelGalleryList()', 'abortGalleryMedia()',
                    'setToken("")', '$("token").value = ""',
                    '$("authScreen").hidden = false', 'window.location.reload()'):
        assert snippet in js


def test_unsaved_changes_can_cancel_manual_disconnect_but_not_expiry():
    js = (FRONT / 'dashboard.js').read_text('utf8')
    assert '!expired && settingsHasUnsavedChanges()' in js
    assert 'window.confirm("Disconnect and discard unsaved settings changes?")' in js
    assert 'disconnectSession({expired: true})' in js
    assert '$("disconnectButton").addEventListener("click", () => disconnectSession())' in js


def test_expired_token_clears_credentials_and_leaves_safe_notice():
    js = (FRONT / 'dashboard.js').read_text('utf8')
    assert 'sessionStorage.setItem("gatewayAuthNotice", "expired")' in js
    assert 'sessionStorage.removeItem("gatewayAuthNotice")' in js
    assert 'sessionStorage.getItem("gatewayAuthNotice") === "expired"' in js
    assert 'The gateway token is no longer valid. Connect again.' in js


def test_api_detects_401_for_json_and_image_requests():
    api = (FRONT / 'api.js').read_text('utf8')
    assert api.count('if (response.status === 401) unauthorizedHandler?.();') == 2
    assert 'export function setUnauthorizedHandler(callback)' in api
    assert 'setUnauthorizedHandler(() => disconnectSession({expired: true}))' in (FRONT / 'dashboard.js').read_text('utf8')


def test_authentication_changes_do_not_modify_server_or_v1_contracts():
    js = (FRONT / 'dashboard.js').read_text('utf8')
    assert 'getSummary()' in js and 'getJobs({limit: 50})' in js
    assert 'renderGallery(' in js
    assert not any(x in js for x in ('/v1/images', 'POST /v1', 'window.close()'))
    assert (ROOT / 'app.py').is_file()


def test_browser_assets_share_consistent_import_version():
    html = (ROOT / 'dashboard.html').read_text('utf8')
    assert f'/dashboard/assets/dashboard.js?v={VERSION}' in html
    for file in FRONT.glob('*.js'):
        for ref in re.findall(r'from [\'\"](\./[^\'\"]+\.js\?v=[^\'\"]+)[\'\"]',file.read_text('utf8')):
            assert ref.endswith(f'?v={VERSION}'), (file.name,ref)
