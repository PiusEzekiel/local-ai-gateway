"""Module 10A.2: deterministic, no-network cache analytics regression tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from gateway.codex_runner import CodexRunner
from gateway.config import Settings
from gateway.contracts import GatewayError, ImageReference
from gateway.job_store import JobStore
from gateway.reference_cache import ReferenceCache

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05"
    b"\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_cache(tmp_path: Path, monkeypatch, ttl: int = 3600):
    monkeypatch.setattr("gateway.reference_cache._public_addresses", lambda _host: ("1.1.1.1",))
    store = JobStore(tmp_path / "telemetry.sqlite3")
    cache = ReferenceCache(tmp_path / "reference-cache", store,
                           ttl_seconds=ttl, max_bytes=1024 * 1024)
    return cache, store


def fake_downloader(monkeypatch, *, fail: bool = False):
    calls = []

    def retrieve(reference, workdir, index, _request_id):
        calls.append(reference.url)
        if fail:
            raise GatewayError("reference_download_failed", "Simulated retrieval failure", 502)
        dest = workdir / f"reference_{index}.png"
        dest.write_bytes(PNG)
        return dest

    monkeypatch.setattr("gateway.codex_runner.download_reference_image", retrieve)
    return calls


def test_same_url_different_episode_ids_reuses_local_file_and_saves_bytes(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    calls = fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=cache)
    first = ImageReference(id="episode-1-character", url="https://drive.google.com/uc?id=same")
    second = ImageReference(id="episode-2-character", url=first.url)
    with cache.lease_scope():
        assert runner._download_reference(first, tmp_path, 0) == runner._download_reference(second, tmp_path, 1)
    report = cache.analytics()
    assert len(calls) == 1
    assert (report["cache_hits"], report["cache_misses"], report["remote_downloads"]) == (1, 1, 1)
    assert report["hit_rate_percent"] == 50.0
    assert report["estimated_bytes_avoided"] == len(PNG)
    assert report["downloaded_bytes"] == len(PNG)
    assert report["remote_download_failures"] == report["cache_write_failures"] == 0
    assert len(cache.stats().get("analytics", {})) == 0  # existing storage contract unchanged
    assert len(store.list_reference_cache()) == 1
    store.close()


def test_same_episode_local_id_with_different_url_downloads_twice(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    calls = fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=cache)
    a = ImageReference(id="same", url="https://drive.google.com/uc?id=aaa")
    b = ImageReference(id="same", url="https://drive.google.com/uc?id=bbb")
    with cache.lease_scope():
        assert runner._download_reference(a, tmp_path, 0) != runner._download_reference(b, tmp_path, 1)
    assert len(calls) == 2
    assert cache.analytics()["cache_misses"] == 2
    assert cache.analytics()["cache_hits"] == 0
    store.close()


def test_stale_source_redownloads_once_then_returns_to_hit(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch, ttl=60)
    calls = fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=cache)
    ref = ImageReference(url="https://drive.google.com/uc?id=stale")
    with cache.lease_scope():
        runner._download_reference(ref, tmp_path, 0)
        expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        store._db.execute("UPDATE reference_cache SET validated_at=?", (expired,))
        store._db.commit()
        runner._download_reference(ref, tmp_path, 1)
        runner._download_reference(ref, tmp_path, 2)
    report = cache.analytics()
    assert len(calls) == 2
    assert (report["cache_misses"], report["cache_hits"], report["remote_downloads"]) == (2, 1, 2)
    assert len(store.list_reference_cache()) == 1  # unchanged content revalidation
    store.close()


def test_failed_remote_download_is_not_counted_as_success(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    fake_downloader(monkeypatch, fail=True)
    runner = CodexRunner(["codex"], reference_cache=cache)
    ref = ImageReference(url="https://drive.google.com/uc?id=fail")
    with pytest.raises(GatewayError):
        with cache.lease_scope():
            runner._download_reference(ref, tmp_path, 0)
    report = cache.analytics()
    assert report["cache_misses"] == 1
    assert report["remote_download_failures"] == 1
    assert report["remote_downloads"] == report["downloaded_bytes"] == 0
    store.close()


def test_cache_write_failure_falls_back_to_validated_file_and_is_counted(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=cache)
    monkeypatch.setattr(cache, "store", lambda *_args, **_kw: (_ for _ in ()).throw(OSError("test")))
    with cache.lease_scope():
        path = runner._download_reference(ImageReference(url="https://drive.google.com/uc?id=fallback"), tmp_path, 0)
        assert path.is_file()
    report = cache.analytics()
    assert report["cache_write_failures"] == 1
    assert report["remote_downloads"] == 1
    assert report["cache_hits"] == 0
    store.close()


def test_corrupt_cached_file_is_a_miss_not_a_hit(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=cache)
    ref = ImageReference(url="https://drive.google.com/uc?id=corrupt")
    with cache.lease_scope():
        initial = runner._download_reference(ref, tmp_path, 0)
    initial.write_bytes(b"corrupted")
    with cache.lease_scope():
        runner._download_reference(ref, tmp_path, 1)
    report = cache.analytics()
    assert report["cache_misses"] == 2
    assert report["cache_hits"] == 0
    assert report["remote_downloads"] == 2
    store.close()


def test_parallel_hits_have_atomic_counters_and_no_url_leak(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    ref = ImageReference(id="private-episode-id", url="https://drive.google.com/uc?id=PRIVATE_SIGNED_VALUE")
    source = tmp_path / "test.png"
    source.write_bytes(PNG)
    cache.release(cache.store(ref, source, mime_type="image/png"))

    def hit(_index):
        with cache.lease_scope():
            assert cache.resolve(ref) is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(hit, range(48)))
    report = cache.analytics()
    assert report["cache_hits"] == 48
    assert report["cache_misses"] == 0
    assert report["estimated_bytes_avoided"] == 48 * len(PNG)
    assert report["hit_rate_percent"] == 100.0
    assert report["top_references"][0]["hits"] == 48
    assert "drive.google.com" not in str(report)
    assert "PRIVATE_SIGNED_VALUE" not in str(report)
    assert "private-episode-id" not in str(report)
    store.close()


def test_analytics_resets_on_new_instance_even_when_files_persist(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    ref = ImageReference(url="https://drive.google.com/uc?id=persist")
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    cache.release(cache.store(ref, source, mime_type="image/png"))
    with cache.lease_scope():
        cache.resolve(ref)
    assert cache.analytics()["cache_hits"] == 1
    fresh = ReferenceCache(cache.root, store, ttl_seconds=3600, max_bytes=1024 * 1024)
    assert fresh.analytics()["cache_hits"] == 0
    assert fresh.stats()["active_entry_count"] == 1
    store.close()


def test_disabled_cache_runner_does_not_record_cache_activity(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, monkeypatch)
    calls = fake_downloader(monkeypatch)
    runner = CodexRunner(["codex"], reference_cache=None)
    ref = ImageReference(url="https://drive.google.com/uc?id=disabled")
    assert runner._download_reference(ref, tmp_path, 0).is_file()
    assert len(calls) == 1
    assert cache.analytics()["cache_misses"] == cache.analytics()["remote_downloads"] == 0
    store.close()


def test_authenticated_stats_endpoint_exposes_analytics_without_source_urls(tmp_path, monkeypatch):
    from gateway import app as root
    token = "reference-analytics-test-token-abcdefghijklmnopqrstuvwxyz"
    store = JobStore(tmp_path / "api.sqlite3")
    settings = Settings(api_token=token, codex_exe=sys.executable, quota_monitor_enabled=False)
    gateway = root.create_app(settings=settings, runner=object(), job_store=store)
    try:
        with TestClient(gateway) as client:
            assert client.get("/dashboard/api/reference-cache").status_code == 401
            response = client.get("/dashboard/api/reference-cache",
                                  headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            body = response.json()
            assert body["analytics"]["cache_hits"] == 0
            assert body["analytics"]["remote_downloads"] == 0
            assert body["analytics"]["top_references"] == []
            assert body["enabled"] is False
            assert "storage_path" not in str(body)
    finally:
        store.close()


def test_dashboard_analytics_controls_exist_and_do_not_change_release_version():
    root = Path(__file__).resolve().parents[1] / "gateway"
    html = (root / "dashboard.html").read_text(encoding="utf-8")
    js = (root / "dashboard" / "settings.js").read_text(encoding="utf-8")
    css = (root / "dashboard" / "settings.css").read_text(encoding="utf-8")
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key == "id":
                    self.ids.append(value)
    parser = Parser()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    assert html.index('id="referenceCacheAnalyticsTitle"') < html.index('id="referenceCacheNote"')
    for label in ("referenceCacheHits", "referenceCacheMisses", "referenceCacheHitRate",
                  "referenceCacheDownloads", "referenceCacheDownloadFailures",
                  "referenceCacheBytesAvoided", "referenceCacheSources"):
        assert parser.ids.count(label) == 1
        assert label in js
    assert ".reference-cache-analytics-grid" in css
    assert 'from "./api.js?v=ui-refresh-phase-a-20260923"' in js
    assert "innerHTML" not in js
