"""9B.2.1: Jobs generated-image preview remains a protected, accessible viewer.

Tests read frontend source; real DOM interaction is covered by the fixture-based
Chromium acceptance test shipped in the installation guide.
"""
from pathlib import Path
import re

D = Path(__file__).resolve().parents[1] / 'gateway' / 'dashboard'
G = D.parent
VERSION = '9c3-20260920'


def test_jobs_generated_image_is_an_accessible_button_not_a_passive_img():
    js = (D / 'jobs.js').read_text(encoding='utf8')
    assert 'el("button", "job-generated-image job-generated-button")' in js
    assert 'imageHost.type = "button"' in js
    assert 'aria-label' in js and 'View full generated image for ${job.request_id}' in js
    assert 'imageHost.addEventListener("click"' in js
    assert '"job:preview-generated"' in js
    assert 'references,' in js
    assert 'protectedImage(outputUrl, generated, generation)' in js


def test_jobs_generated_event_uses_protected_artifact_url_only():
    js = (D / 'jobs.js').read_text(encoding='utf8')
    gallery = (D / 'gallery.js').read_text(encoding='utf8')
    assert 'outputUrl.startsWith("/dashboard/api/artifacts/")' in js
    assert 'url.startsWith("/dashboard/api/artifacts/")' in gallery
    assert 'showProtectedViewerImage(url, requestId || "Generated image"' in gallery
    assert 'requestBlob(path, {signal})' in gallery
    assert '.innerHTML' not in gallery


def test_jobs_viewer_has_references_without_gallery_navigation():
    gallery = (D / 'gallery.js').read_text(encoding='utf8')
    assert 'standaloneReference = {url, requestId}' in gallery
    assert '$('+'"previousImage"'+').hidden = true' in gallery
    assert '$('+'"nextImage"'+').hidden = true' in gallery
    assert 'paintReferenceRail(references, requestId || "job", url, referenceLoadGeneration)' in gallery
    assert 'selectedReference.outputUrl' in gallery
    assert 'paintReferenceRail(detail.job?.references || [], item.request_id' in gallery


def test_generated_button_preserves_fit_and_keyboard_focus():
    css = (D / 'gallery-polish.css').read_text(encoding='utf8')
    assert '.job-generated-button' in css
    assert '.job-generated-button .inspector-image' in css
    assert 'object-fit: contain' in css
    assert 'cursor: zoom-in' in css
    assert 'imageHost.disabled = true' in (D/'jobs.js').read_text(encoding='utf8')


def test_all_frontend_assets_share_new_cache_version():
    html = (G / 'dashboard.html').read_text(encoding='utf8')
    assert f'gallery-polish.css?v={VERSION}' in html
    assert f'dashboard.js?v={VERSION}' in html
    for file in D.glob('*.js'):
        src = file.read_text(encoding='utf8')
        for path in re.findall(r'from\s+[\'\"](\./[^\'\"]+\.js\?v=[^\'\"]+)[\'\"]', src):
            assert path.endswith('?v=' + VERSION), (file.name, path)
