"""9B.2: existing private job/reference API and uncropped clickable reference UI.

Only fixture images, fake Codex runners, and test SQLite stores are used.
"""
from pathlib import Path
import re
from fastapi.testclient import TestClient
from PIL import Image
from gateway.contracts import ImageReference
from test_module_9a2 import AUTH, make_app

G = Path(__file__).resolve().parents[1] / 'gateway'
D = G / 'dashboard'
VERSION = '9b21-20260920'


def test_authored_job_detail_links_archived_reference_to_gallery_output(tmp_path, monkeypatch):
    api, db, artifacts, runner = make_app(tmp_path, monkeypatch)
    ref_file = tmp_path / 'character.png'
    Image.new('RGB', (40, 120), '#335577').save(ref_file)

    def supply_reference(kwargs):
        kwargs['reference_ready'](0, ImageReference(id='character-1', url='https://example.org/private.png'), ref_file)
    runner.on_image = supply_reference

    with TestClient(api) as client:
        response = client.post('/v1/images/generations', headers=AUTH, json={
            'request_id': 'scene_ref_compare', 'prompt': 'portrait scene',
            'reference_images': [{'id': 'character-1', 'url': 'https://example.org/private.png'}],
        })
        assert response.status_code == 200
        gallery = client.get('/dashboard/api/gallery?limit=5', headers=AUTH).json()['items']
        output = next(item for item in gallery if item['request_id'] == 'scene_ref_compare')
        detail = client.get('/dashboard/api/jobs/' + output['job_id'], headers=AUTH).json()['job']
        assert detail['artifact']['id'] == output['artifact']['id']
        assert detail['reference_count'] == 1
        assert len(detail['references']) == 1
        ref = detail['references'][0]
        assert (ref['ordinal'], ref['reference_id']) == (1, 'character-1')
        assert ref['url'].startswith('/dashboard/api/artifacts/art_')
        assert ref['thumbnail_url'].endswith('/thumbnail')
        assert client.get(ref['url'], headers=AUTH).content == ref_file.read_bytes()
        assert client.get(ref['thumbnail_url'], headers=AUTH).status_code == 200
        assert 'https://example.org/private.png' not in str(detail)
        assert client.get(ref['url']).status_code == 401
    db.close()


def test_unavailable_reference_retains_ordinal_without_unauthorized_source_fallback(tmp_path, monkeypatch):
    api, db, _artifacts, runner = make_app(tmp_path, monkeypatch)
    # No reference_ready event: the saved reference ID survives without an artifact.
    with TestClient(api) as client:
        response = client.post('/v1/images/generations', headers=AUTH, json={
            'request_id': 'scene_no_reference_copy', 'prompt': 'scene',
            'reference_images': [{'id': 'character-2', 'url': 'https://example.org/secret.png'}],
        })
        assert response.status_code == 200
        job = db.list_jobs(search='scene_no_reference_copy')[0]
        ref = client.get('/dashboard/api/jobs/' + job['id'], headers=AUTH).json()['job']['references'][0]
        assert ref['ordinal'] == 1
        assert not ref.get('url') and not ref.get('thumbnail_url')
        assert 'https://' not in str(ref)
    db.close()


def test_gallery_has_reference_rail_and_generated_image_control():
    html = (G/'dashboard.html').read_text(encoding='utf8')
    assert 'id="lightboxReferences"' in html
    assert 'id="lightboxOutput"' in html
    assert 'id="lightboxStage"' in html and 'id="lightboxImage"' in html
    assert 'aria-modal="false"' in html


def test_gallery_joins_only_selected_job_and_preserves_reference_order():
    code = (D/'gallery.js').read_text(encoding='utf8')
    assert 'getJob(item.job_id)' in code
    assert 'for (const [index, ref] of references.entries())' in code
    assert 'showReference(index)' in code
    assert 'ref.url' in code and 'ref.thumbnail_url || ref.url' in code
    assert 'selectedReference.outputUrl' in code
    assert 'showProtectedViewerImage(item.artifact.url' in code
    assert 'showReferencesForJob(item)' in code


def test_reference_image_fetch_uses_existing_authorized_blob_client_only():
    code = (D/'gallery.js').read_text(encoding='utf8')
    jobs = (D/'jobs.js').read_text(encoding='utf8')
    for item in (code, jobs):
        assert 'requestBlob(' in item
        assert 'EventSource(' not in item
        assert 'reference.url' not in item or 'requestBlob' in item
        assert '.innerHTML' not in item
    assert 'getJob' in code


def test_jobs_reference_buttons_show_uncropped_images_and_full_view():
    jobs = (D/'jobs.js').read_text(encoding='utf8')
    css = (D/'gallery-polish.css').read_text(encoding='utf8')
    assert 'referenceButton(reference, job.request_id, generation)' in jobs
    assert '"job:preview-reference"' in jobs
    assert 'url: reference.url' in jobs
    assert '"job-reference-image"' in jobs
    assert '.job-reference-image .reference-thumb' in css
    assert 'object-fit: contain' in css
    assert '.job-reference-card' in css
    assert '.lightbox-reference-frame img' in css


def test_removed_and_unretained_references_have_explicit_states():
    jobs = (D/'jobs.js').read_text(encoding='utf8')
    gallery = (D/'gallery.js').read_text(encoding='utf8')
    assert 'Not retained' in jobs
    assert 'has expired' in jobs
    assert 'Unavailable' in gallery
    assert 'no longer retained' in gallery
    assert 'References unavailable. The output image is unaffected.' in gallery


def test_stale_job_and_reference_requests_cannot_repaint_other_selection():
    jobs = (D/'jobs.js').read_text(encoding='utf8')
    gallery = (D/'gallery.js').read_text(encoding='utf8')
    assert 'generation !== inspectorGeneration' in jobs
    assert 'if (state.selectedJobId === id) inspector.replaceChildren' in jobs
    assert 'generation !== referenceLoadGeneration' in gallery
    assert 'items[currentIndex]?.job_id !== item.job_id' in gallery
    assert 'generation !== imageLoadGeneration' in gallery
    assert 'URL.revokeObjectURL' in gallery and 'URL.revokeObjectURL' in jobs


def test_jobs_reference_preview_reuses_same_bounded_viewer():
    gallery = (D/'gallery.js').read_text(encoding='utf8')
    assert 'document.addEventListener("job:preview-reference"' in gallery
    assert 'showProtectedViewerImage(url, title || "Reference image"' in gallery
    assert '$("previousImage").hidden = true' in gallery
    assert '$("nextImage").hidden = true' in gallery
    assert '$("lightboxReferences").hidden = true' in gallery or 'revokeReferences()' in gallery


def test_version_is_consistent_across_frontend_imports_and_assets():
    html = (G/'dashboard.html').read_text(encoding='utf8')
    assert 'gallery-polish.css?v='+VERSION in html
    assert 'dashboard.js?v='+VERSION in html
    for file in D.glob('*.js'):
        text = file.read_text(encoding='utf8')
        for path in re.findall(r'from\s+[\'"](\./[^\'"]+\.js\?v=[^\'"]+)[\'"]', text):
            assert path.endswith('?v='+VERSION), (file.name, path)
