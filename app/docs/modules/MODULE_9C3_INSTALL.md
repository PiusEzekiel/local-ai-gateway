# Module 9C.3 — Storage-limit accuracy (read-only accounting)

9C.2 is already verified on Windows (289 Python tests). It identified 11 healthy registered originals, 11 healthy thumbnails, and 25 unregistered managed original/thumbnail pairs (50 files, ~5.88 KiB total), all less than 24 hours old at inspection. **Those 50 files are not deletion candidates in this release.**

9C.3 makes the existing authenticated **Preview cleanup** report honest about the best-effort storage cap. It changes **the preview and its Settings presentation only**: the cleanup algorithm, confirmation phrase, age limits, automatic-cleanup setting, file deletion, image generation, database schema, credentials, and n8n API remain unchanged.

## Changes

- `retention.py`: adds total directory usage versus physically present registered bytes (unique managed files, no double-counting duplicate legacy database paths), bytes not linked to SQLite (including recognized unregistered files, staging and other regular files), current over-limit bytes, an estimated after-preview-cleanup usage, and estimated remaining over-limit bytes. `limit_is_strict: false` documents that cleanup is a best-effort target, not a hard filesystem quota. These fields are added to the **existing** authenticated `GET /dashboard/api/storage/preview` response under `storage`.
- The existing preview still selects terminal-job and age-eligible artifacts; optionally, over-24-hour managed orphan candidates, only when explicitly requested. In-flight and recent files, symlinks, unknown names and Codex-owned output outside the gateway directory are not reclaimed to force the limit. An incomplete/racing filesystem observation is flagged and described conservatively.
- Settings → Storage: read-only cards for “Not linked to SQLite / other files,” “Over configured limit now,” and “Projected use after preview,” with an explanatory best-effort warning when previewed cleanup cannot meet the limit.
- Versioned frontend module imports are advanced together to `9c3-20260920` to prevent multiple copies of the token-bearing API module; historical structural tests are versioned consistently.
- 12 new regression tests cover the real 50-file scenario; active-job protection; recent vs eligible managed orphans; over-cap unknown files; projection with terminal artifacts; duplicate recorded paths; authenticated API responses; and read-only behavior.

**Interpretation**: “Not linked to SQLite / other files” does not mean “safe to delete.” Projection is an estimate from the observed snapshot, not a guaranteed post-cleanup amount. The configured cap covers only the managed gateway artifact directory, not Codex's independent generated-image storage.

## Install

1. From `C:\pinokio\api\local-ai-gateway`, commit the verified 9C.2 code to the private GitHub branch if needed. Check `git status` and confirm `.gateway-data`, `.gateway-token`, `.gateway-settings.json`, and Codex authentication are not staged.
2. Wait for active n8n jobs to finish; stop the gateway in Pinokio. Back up `app\gateway` and `app\tests`. Retain your separate data/settings backup.
3. Extract `local_ai_gateway_module_9c3.zip` into `C:\pinokio\api\local-ai-gateway` with its folders intact, replacing matching files.
4. Run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **301 passed** on your Windows installation (verified 289 + 12 new). The available local subset passed **280**, including the new 12 tests; 26 JavaScript tests passed. Static browser layout was checked at widths 1440, 900, and 390 px. Local quota monitor is a stand-in: Windows remains the definitive compatibility check.

5. Restart Pinokio and hard-refresh the dashboard with `Ctrl+Shift+R`. Open Settings → Storage → **Preview cleanup**. Verify the new read-only cards show approximately 5.88 KiB not linked to SQLite (if the files remain), 0 B over the configured 1 GiB limit, and a projected size near the current 18.43 MiB. Values may differ as jobs and scans progress.

**Do not click Run cleanup**, enable automatic cleanup, or change the retention settings to test this module. It is not necessary and the old deletion policy remains unchanged.

## Rollback

Stop Pinokio after jobs finish; restore the saved gateway/tests files or the verified 9C.2 Git commit and restart. No database, media, or settings migration is involved. Do not restore an old live database as part of source-only rollback.
