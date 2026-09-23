# Module 7C — Safe history, quota, and artifact retention

**Baseline:** Module 7B, verified at 91 tests on your Windows installation. This is an incremental update, not a full repository. No token, user settings, telemetry DB, or images are bundled.

## Safety first

- **Automatic cleanup is OFF by default.** Installing and restarting does not delete historical data.
- The GET preview is always read-only; it exposes counts and byte totals but **never local paths**.
- Manual cleanup requires an authenticated POST with the exact `DELETE_EXPIRED_DATA` confirmation. It always computes a fresh plan at execution time; a preview is not an irrevocable plan.
- Running/queued jobs cannot be deleted and their artifacts cannot be removed by either age or storage-limit policies.
- A **five-minute grace period** protects new image files when enforcing storage limits, even if a job is marked completed but FastAPI is still streaming the image to n8n.
- Bad/outside-root paths and symlinks are refused, not deleted. Missing files *inside the validated artifact directory* can have their stale DB records cleaned up.
- Orphan cleanup is separate: explicitly request `include_orphans=true`; only opaque-named files older than 24 hours are eligible. Automatic cleanup never removes orphans.
- Work is capped at 250 expired jobs, 500 separately selected artifact records, and 250 orphan files per execution. Repeat preview and cleanup to continue when `truncated=true`.
- File deletes + SQLite changes cannot be literally transactional. If a filesystem deletion fails, the DB record is kept for safe retry; if an earlier file in the same job was already deleted before a later failure, its record remains, and a later run can complete the cleanup.

## What is retained/deleted

- Expired **completed/failed job history**: deletes job metadata, events, token usage, optional retained text and associated artifact files/records via SQLite ON DELETE CASCADE, only if its artifact files are safely removable.
- Expired **generated images and reference thumbnails**: removes file and artifact records; keeps job, timing, token usage, optional text and diagnostics metadata. `jobs.artifact_id` and `job_references.artifact_id` are cleared so the Gallery does not advertise an unavailable image.
- Quota snapshots past the configured retention period: removes old snapshots, not current account quota state.
- Storage limit: best-effort oldest-first removal of terminal-job artifacts after accounting for expired files. Active-job files and images created within five minutes are excluded; storage can remain above the configured cap.
- Does **not** manage raw images in Codex HOME, original files on Google Drive, n8n storage, Pinokio logs, backups, the SQLite WAL forensic remnants, or external media servers.

## New restart-required Settings fields (GET/PATCH /dashboard/api/settings)

| Field | Default | Range |
| --- | ---: | ---: |
| `auto_cleanup_enabled` | `false` | bool |
| `history_retention_days` | `90` | 1–3650 |
| `artifact_retention_days` | `30` | 1–3650 |
| `quota_snapshot_retention_days` | `30` | 1–3650 |
| `max_artifact_storage_mb` | `10240` (10 GiB) | 1–1,000,000 |
| `cleanup_interval_hours` | `24` | 1–168 |

The settings API can save new values now. After restarting Pinokio they become active. If automatic cleanup is later enabled, the first automatic sweep happens **after one complete configured interval**, not at startup. It never includes orphans.

## New authenticated dashboard API

- `GET /dashboard/api/storage/preview?include_orphans=false`
  - Read-only counts, disk usage, planned bytes reclaimable and `truncated` flag.
- `POST /dashboard/api/storage/cleanup`
  - JSON `{"confirmation":"DELETE_EXPIRED_DATA","include_orphans":false}`.
  - Re-plans and attempts cleanup, returns counts and storage usage after.

The visual Settings and storage preview controls will follow in Module 7D. Until then, you can use a Bearer-authenticated HTTP client to view the preview and execute cleanup, but **do not execute cleanup until you have reviewed your retention settings and made a backup**.

## Files in ZIP

Existing files changed:
- `app/gateway/app.py`
- `app/gateway/config.py`
- `app/gateway/settings_manager.py`
- `app/gateway/job_store.py`
- `app/gateway/job_history.py`
- `app/gateway/artifact_store.py`
- `app/tests/test_refactor_6r2.py` (strict effective-route test accounts for two new endpoints)

New files:
- `app/gateway/retention.py`
- `app/gateway/retention_routes.py`
- `app/tests/test_module_7c.py` (12 tests)

No SQLite schema migration: remains v6.

## Install on Windows

1. **Wait for n8n jobs to finish; STOP the gateway in Pinokio.**
2. Back up the `app/gateway` source, `app/.gateway-settings.json`, and `app/.gateway-data/` (including `gateway.sqlite3`, any `-wal`/`-shm` files, and all `artifacts`), while the gateway is stopped. Do not upload the private data here.
3. Extract `local_ai_gateway_module_7c.zip` into `C:\pinokio\api\local-ai-gateway`, preserving `app/gateway/` and `app/tests/`.
4. VS Code PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected on your Windows setup:** **103 passing tests** (your 91 + 12 new tests). Local harness verified 91 tests excluding `test_phase1.py`, whose Google quota/analytics modules were not in the partial source package. The Windows full suite is the authoritative acceptance check.

5. Restart gateway in Pinokio, confirm Overview, Gallery, Diagnostics, `/v1/generate`, and an image request with references. Verify `GET /dashboard/api/settings` lists the six new retention fields and `GET /dashboard/api/storage/preview` reports `dry_run:true`.

## Rollback

With the gateway stopped, restore your previous Module 7B files. No schema downgrade is required (SQLite stays at v6). If you manually ran cleanup, only your **data/artifact backup** can restore deleted files and history. Changing the settings back does **not** undo deletion.
