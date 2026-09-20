"""9B.3: stale Gallery requests cannot overwrite a newer selection or page.

The accompanying frontend_gallery.test.mjs exercises concurrency with
misbehaving promise mocks that deliberately resolve after cancellation.
"""
from pathlib import Path

D = Path(__file__).resolve().parents[1] / 'gateway' / 'dashboard'
G = D.parent
VERSION = '9c2-20260920'


def test_gallery_list_abort_and_monotonic_generation():
    app = (D/'dashboard.js').read_text()
    assert 'galleryListController?.abort()' in app
    assert '++galleryRequestGeneration' in app
    assert 'getGallery(query, {signal: controller.signal})' in app
    assert 'generation !== galleryRequestGeneration' in app
    assert 'state.page !== "gallery"' in app


def test_search_invalidates_before_debounce_and_filter_cancels():
    app = (D/'dashboard.js').read_text()
    assert 'cancelGalleryList(); // invalidate immediately' in app
    assert 'clearTimeout(gallerySearchTimer)' in app
    assert 'gallerySearchTimer = setTimeout(() => loadGallery(true), 250)' in app
    assert 'cancelGalleryList();\n  update({galleryCategory:' in app


def test_pagination_is_not_duplicated_during_inflight_requests():
    app = (D/'dashboard.js').read_text()
    assert 'galleryLoadMoreInFlight || !state.galleryCursor' in app
    assert 'if (!reset) galleryLoadMoreInFlight = true' in app
    assert '$('+'"galleryLoadMore"'+').disabled = true' in app
    assert 'galleryLoadMoreInFlight = false' in app


def test_leaving_page_and_auth_loss_cancels_media_and_list():
    app = (D/'dashboard.js').read_text()
    gallery = (D/'gallery.js').read_text()
    assert 'abortGalleryMedia();' in app
    assert 'cancelGalleryList();' in app
    assert 'export function abortGalleryMedia()' in gallery
    assert 'revokeCards();\n  revokeReferences();' in gallery


def test_image_requests_accept_abort_signal_without_leaking_token_in_url():
    api = (D/'api.js').read_text()
    assert 'export async function requestBlob(path, {signal} = {})' in api
    assert 'fetch(path, {headers, signal, cache: "no-store"})' in api
    assert 'export const getJob = (id, options = {})' in api
    assert 'getGallery({limit = 48, cursor = "", category = "all", search = ""} = {}, options = {})' in api
    assert 'return request(`/dashboard/api/gallery?${params}`, options)' in api
    assert 'Authorization: `Bearer ${token}`' in api


def test_gallery_card_abort_and_thumbnail_fallback():
    gallery = (D/'gallery.js').read_text()
    assert 'cardRequests.abort();' in gallery
    assert '++cardLoadGeneration' in gallery
    assert 'generation !== cardLoadGeneration' in gallery
    assert 'item.artifact.thumbnail_url || item.artifact.url' in gallery
    assert 'return requestBlob(fallback, {signal})' in gallery
    assert 'if (signal.aborted || generation !== cardLoadGeneration || !image.isConnected) return' in gallery


def test_viewer_and_reference_requests_cancel_on_switch_and_close():
    gallery = (D/'gallery.js').read_text()
    assert 'viewerRequests?.abort()' in gallery
    assert 'referenceRequests.abort()' in gallery
    assert 'const detail = await getJob(item.job_id, {signal: referenceRequests.signal})' in gallery
    assert 'requestBlob(ref.thumbnail_url || ref.url, {signal})' in gallery
    assert 'if (signal.aborted || generation !== imageLoadGeneration || $("lightbox").hidden) return' in gallery


def test_stale_abort_is_not_shown_as_unavailable_and_assets_are_cache_aligned():
    gallery = (D/'gallery.js').read_text()
    html = (G/'dashboard.html').read_text()
    assert 'if (signal.aborted || error?.name === "AbortError"' in gallery
    assert 'if (signal.aborted || generation !== cardLoadGeneration || !image.isConnected) return' in gallery
    assert f'dashboard.js?v={VERSION}' in html
    assert f'gallery-polish.css?v={VERSION}' in html
    assert f'from "./gallery.js?v={VERSION}"' in (D/'dashboard.js').read_text()
