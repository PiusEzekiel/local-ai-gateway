from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import asyncio
from pathlib import Path

import pytest

from gateway.contracts import ImageReference
from gateway.job_store import JobStore
from gateway.reference_cache import ReferenceCache
from gateway.codex_runner import CodexRunner


PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05"
    b"\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_cache(tmp_path: Path, *, ttl: int = 3600, max_bytes: int = 1024 * 1024):
    store = JobStore(tmp_path / "gateway.sqlite3")
    cache = ReferenceCache(tmp_path / "reference-cache", store, ttl_seconds=ttl, max_bytes=max_bytes)
    return cache, store


def test_cache_miss_store_and_hit(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(id="character-1", url="https://drive.google.com/uc?id=abc")

    cached = cache.store(reference, source, mime_type="image/png")

    assert cached.is_file()
    assert cache.resolve(reference) == cached
    assert store.list_reference_cache()[0]["content_sha256"]
    store.close()


def test_cache_revalidates_mutable_source_after_ttl(tmp_path):
    cache, store = make_cache(tmp_path, ttl=60)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(id="character-1", url="https://drive.google.com/uc?id=abc")
    cached = cache.store(reference, source, mime_type="image/png")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store._db.execute("UPDATE reference_cache SET validated_at=?", (old,))
    store._db.commit()

    assert cache.resolve(reference) is None
    assert cached.is_file()
    store.close()


def test_cache_rejects_corrupt_or_missing_file(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(id="character-1", url="https://drive.google.com/uc?id=abc")
    cached = cache.store(reference, source, mime_type="image/png")
    cached.write_bytes(b"corrupt")

    assert cache.resolve(reference) is None
    assert not store.list_reference_cache()
    store.close()


def test_cache_database_failure_removes_published_file(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(id="character-1", url="https://drive.google.com/uc?id=abc")

    def fail(_record, _previous):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "replace_reference_cache", fail)
    with pytest.raises(RuntimeError):
        cache.store(reference, source, mime_type="image/png")
    assert not list(cache.root.glob("ref_*"))
    store.close()


def test_reference_counted_leases_do_not_underflow(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=abc")

    with cache.lease_scope():
        first = cache.store(reference, source, mime_type="image/png")
        second = cache.resolve(reference)
        assert second == first
        assert cache.lease_count(first) == 2
    assert cache.lease_count(first) == 0

    cache.release(first)
    assert cache.lease_count(first) == 0
    store.close()


def test_concurrent_jobs_keep_shared_reference_protected_until_both_finish(tmp_path):
    cache, store = make_cache(tmp_path, max_bytes=len(PNG) - 1)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    shared = ImageReference(url="https://drive.google.com/uc?id=shared")
    other = ImageReference(url="https://drive.google.com/uc?id=other")
    shared_path = cache.store(shared, source, mime_type="image/png")
    cache.release(shared_path)

    async def jobs() -> None:
        first_acquired = asyncio.Event()
        second_acquired = asyncio.Event()
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        paths: list[Path] = []

        async def job(acquired: asyncio.Event, release: asyncio.Event) -> None:
            with cache.lease_scope():
                path = cache.resolve(shared)
                assert path is not None
                paths.append(path)
                acquired.set()
                await release.wait()

        first = asyncio.create_task(job(first_acquired, release_first))
        second = asyncio.create_task(job(second_acquired, release_second))
        await first_acquired.wait()
        await second_acquired.wait()
        assert paths == [shared_path, shared_path]
        assert cache.lease_count(shared_path) == 2

        release_first.set()
        await first
        assert cache.lease_count(shared_path) == 1

        other_path = cache.store(other, source, mime_type="image/png")
        cache.release(other_path)
        cache._evict_locked(exclude=set())
        assert shared_path.is_file()
        assert cache.lease_count(shared_path) == 1

        release_second.set()
        await second
        assert cache.lease_count(shared_path) == 0
        cache._evict_locked(exclude=set())
        assert not shared_path.exists()

    asyncio.run(jobs())
    store.close()


def test_runner_cancellation_releases_acquired_reference(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=cancel")
    path = cache.store(reference, source, mime_type="image/png")
    cache.release(path)

    runner = CodexRunner(["codex"], reference_cache=cache)
    def cancel_after_acquisition(_index, _reference, acquired_path):
        assert acquired_path == path
        assert cache.lease_count(path) == 1
        raise asyncio.CancelledError

    async def run_cancelled() -> None:
        with pytest.raises(asyncio.CancelledError):
            await runner.run_image(
                prompt="test",
                timeout_seconds=1,
                reference_images=[reference],
                reference_ready=cancel_after_acquisition,
                reference_downloader=lambda ref, _workdir, _index, _request_id: cache.resolve(ref),
            )

    asyncio.run(run_cancelled())
    assert cache.lease_count(path) == 0
    store.close()


def test_same_url_reuses_cache_across_request_local_ids(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    first = ImageReference(id="scene-local-a", url="https://drive.google.com/uc?id=abc")
    second = ImageReference(id="scene-local-b", url=first.url)

    path = cache.store(first, source, mime_type="image/png")
    assert cache.resolve(second) == path
    assert len(store.list_reference_cache()) == 1
    store.close()


def test_expired_unchanged_content_refreshes_without_replacing_file(tmp_path):
    cache, store = make_cache(tmp_path, ttl=60)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=abc")
    path = cache.store(reference, source, mime_type="image/png")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store._db.execute("UPDATE reference_cache SET validated_at=?,last_accessed_at=?", (old, old))
    store._db.commit()

    refreshed = cache.store(reference, source, mime_type="image/png")

    assert refreshed == path
    row = store.list_reference_cache()[0]
    assert row["validated_at"] != old
    assert len(list(cache.root.glob("ref_*"))) == 1
    store.close()


def test_changed_content_preserves_active_old_version(tmp_path):
    cache, store = make_cache(tmp_path, ttl=60)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=abc")
    with cache.lease_scope():
        old_path = cache.store(reference, source, mime_type="image/png")
        source.write_bytes(PNG + b"changed")
        new_path = cache.store(reference, source, mime_type="image/png")
        assert new_path != old_path
        assert old_path.is_file()
        assert cache.lease_count(old_path) == 1
    assert cache.lease_count(old_path) == 0
    assert len(store.list_reference_cache(include_retired=True)) == 2
    store.close()


def test_storage_limit_includes_new_and_retired_versions(tmp_path):
    cache, store = make_cache(tmp_path, max_bytes=len(PNG) + 1)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    first = ImageReference(url="https://drive.google.com/uc?id=one")
    second = ImageReference(url="https://drive.google.com/uc?id=two")

    first_path = cache.store(first, source, mime_type="image/png")
    cache.release(first_path)
    second_path = cache.store(second, source, mime_type="image/png")
    cache.release(second_path)

    assert len(store.list_reference_cache()) == 1
    assert len(list(cache.root.glob("ref_*"))) == 1
    store.close()


def test_storage_limit_defers_active_eviction_then_reclaims(tmp_path):
    cache, store = make_cache(tmp_path, max_bytes=len(PNG) + 1)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    first = ImageReference(url="https://drive.google.com/uc?id=one")
    second = ImageReference(url="https://drive.google.com/uc?id=two")

    with cache.lease_scope():
        first_path = cache.store(first, source, mime_type="image/png")
        cache.store(second, source, mime_type="image/png")
        assert first_path.is_file()
        assert len(list(cache.root.glob("ref_*"))) == 2
    cache._evict_locked(exclude=set())
    assert len(list(cache.root.glob("ref_*"))) == 1
    store.close()


def test_retention_cleanup_protects_active_reference_then_removes_it(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=retention")
    old = datetime.now(timezone.utc) - timedelta(days=8)

    with cache.lease_scope():
        path = cache.store(reference, source, mime_type="image/png")
        store._db.execute(
            "UPDATE reference_cache SET last_accessed_at=?",
            (old.isoformat(),),
        )
        store._db.commit()
        result = cache.cleanup(now=datetime.now(timezone.utc))
        assert result["removed"] == 0
        assert result["skipped_active"] == 1
        assert path.is_file()

    result = cache.cleanup(now=datetime.now(timezone.utc))
    assert result["removed"] == 1
    assert result["bytes_removed"] == len(PNG)
    assert not path.exists()
    assert not store.list_reference_cache(include_retired=True)
    store.close()


def test_scheduled_cleanup_does_not_delete_recently_used_freshness_expired_entry(tmp_path):
    cache, store = make_cache(tmp_path, ttl=60)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=expired")
    path = cache.store(reference, source, mime_type="image/png")
    cache.release(path)
    old = datetime.now(timezone.utc) - timedelta(minutes=2)
    store._db.execute(
        "UPDATE reference_cache SET validated_at=?,last_accessed_at=?",
        (old.isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    store._db.commit()

    result = cache.cleanup(now=datetime.now(timezone.utc))
    assert result["removed"] == 0
    assert path.exists()
    assert store.list_reference_cache(include_retired=True)

    result = cache.cleanup(clear_expired=True, now=datetime.now(timezone.utc))
    assert result["removed"] == 1
    assert not path.exists()
    store.close()


def test_force_refresh_bypasses_freshness_without_changing_identity(tmp_path, monkeypatch):
    cache, store = make_cache(tmp_path, ttl=3600)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    reference = ImageReference(url="https://drive.google.com/uc?id=force")
    path = cache.store(reference, source, mime_type="image/png")
    cache.release(path)
    calls = []
    monkeypatch.setattr(
        "gateway.reference_cache._public_addresses",
        lambda host: calls.append(host) or ("1.1.1.1",),
    )
    assert cache.resolve(reference, force_refresh=True) is None
    assert calls
    cache.release(path)
    assert len(store.list_reference_cache(include_retired=True)) == 1
    store.close()


def test_same_id_with_different_urls_does_not_share_cache_identity(tmp_path):
    cache, store = make_cache(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    first = ImageReference(id="episode-reference", url="https://drive.google.com/uc?id=one")
    second = ImageReference(id="episode-reference", url="https://drive.google.com/uc?id=two")

    first_path = cache.store(first, source, mime_type="image/png")
    cache.release(first_path)
    second_path = cache.store(second, source, mime_type="image/png")
    cache.release(second_path)

    assert first_path != second_path
    assert len(store.list_reference_cache(include_retired=True)) == 2
    store.close()


def test_runner_releases_all_acquired_references_when_later_download_fails(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(PNG)
    second.write_bytes(PNG)
    paths = [first, second]

    class FakeCache:
        def __init__(self):
            self.acquired = []
            self.released = []

        @contextmanager
        def lease_scope(self):
            try:
                yield
            finally:
                self.released.extend(self.acquired)

        def resolve(self, reference):
            if len(self.acquired) == 0:
                self.acquired.append(paths[0])
                return paths[0]
            raise RuntimeError("second reference failed")

    cache = FakeCache()
    runner = CodexRunner(["codex"], reference_cache=cache)
    references = [
        ImageReference(url="https://drive.google.com/uc?id=one"),
        ImageReference(url="https://drive.google.com/uc?id=two"),
    ]

    with pytest.raises(RuntimeError):
        asyncio.run(runner.run_image(
            prompt="test", timeout_seconds=1, reference_images=references,
        ))
    assert cache.released == [first]
