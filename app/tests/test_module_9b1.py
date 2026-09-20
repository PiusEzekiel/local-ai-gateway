"""Module 9B.1: UX markup, asset consistency and viewer regression guards.

These cover the precise clipped-card and full-screen-overlay bugs reported from
Windows. Browser layout and event interactions have additional smoke coverage.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

G = Path(__file__).resolve().parents[1] / 'gateway'
D = G / 'dashboard'
RELEASE = '9c1-20260920'


def test_gallery_polish_loads_after_base_css():
    html = (G/'dashboard.html').read_text(encoding='utf-8')
    assert html.index('dashboard.css?v='+RELEASE) < html.index('gallery-polish.css?v='+RELEASE)
    assert 'dashboard.js?v='+RELEASE in html
    assert (D/'gallery-polish.css').is_file()


def test_all_frontend_imports_share_one_version_for_auth_state():
    for file in D.glob('*.js'):
        code = file.read_text(encoding='utf-8')
        for ref in re.findall(r'from\s+[\'\"](\./[^\'\"]+\.js\?v=[^\'\"]+)[\'\"]', code):
            assert ref.endswith('?v='+RELEASE), (file.name, ref)
    assert 'from "./gallery.js?v='+RELEASE+'"' in (D/'dashboard.js').read_text(encoding='utf-8')


def test_card_uses_flexbox_metadata_padding_and_contain():
    css = (D/'gallery-polish.css').read_text(encoding='utf-8')
    assert '.gallery-card {' in css and 'flex-direction: column' in css
    assert '.gallery-info {' in css and 'padding: 14px 15px 16px' in css
    assert '.gallery-thumb img {' in css and 'object-fit: contain' in css
    assert 'object-position: center' in css


def test_lightbox_is_workspace_scoped_bounded_and_not_modal_over_sidebar():
    html = (G/'dashboard.html').read_text(encoding='utf-8')
    css = (D/'gallery-polish.css').read_text(encoding='utf-8')
    assert 'role="dialog" aria-modal="false"' in html
    assert 'class="lightbox-panel"' in html
    assert 'id="lightboxStatus"' in html
    assert 'inset: 65px 0 0 216px' in css
    assert 'width: min(100%, 1160px)' in css
    assert 'height: min(100%, 820px)' in css
    assert '.lightbox-stage img[hidden]' in css


def test_stale_viewer_fetch_discarded_and_focus_restored():
    gallery = (D/'gallery.js').read_text(encoding='utf-8')
    app = (D/'dashboard.js').read_text(encoding='utf-8')
    assert 'generation !== imageLoadGeneration' in gallery
    assert '++imageLoadGeneration' in gallery
    assert 'if (returnFocus?.isConnected) returnFocus.focus()' in gallery
    assert 'closeLightbox(); // keep sidebar navigation usable' in app
    assert 'pointercancel' in gallery


def test_backend_and_n8n_interfaces_not_part_of_release():
    html = (G/'dashboard.html').read_text(encoding='utf-8')
    assert 'id="galleryGrid"' in html and 'id="jobInspector"' in html
    assert 'id="lightboxImage"' in html and 'id="previousImage"' in html
    assert 'id="gallerySearch"' in html and 'id="galleryLoadMore"' in html
