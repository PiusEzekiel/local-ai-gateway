# Module 9C.2 — Read-only managed-file investigation

9C.1 reported 11/11 healthy registered artifacts, zero missing originals, zero missing thumbnails, zero eligible orphan files, and 50 **recent managed files not registered in SQLite**. That last number does **not** establish that those files are disposable. This release adds an independent, observational breakdown for investigation. Nothing is deleted, repaired, re-registered, or modified.

## What changes

- `GET /dashboard/api/storage/inventory`: authenticated, metadata-only inventory of the gateway-managed artifact directory. Categories distinguish registered originals, registered thumbnails, unregistered opaque managed originals, unregistered opaque managed thumbnails, staging files, other regular files, symlinks, subdirectories and unreadable entries. For each category it reports counts, bytes and age buckets: under 5 minutes; 5–60 minutes; 1–24 hours; over 24 hours.
- Settings → Storage → **What is using artifact storage?** with a separate **Investigate files** button, counters, grouped age breakdown and capped anonymous examples. This is independent of the existing **Preview cleanup** and **Run cleanup** controls.
- Database row scan capped at 5,000; directory scan capped at 10,000 entries; anonymous examples capped at 12. If the registration inventory is incomplete, no unregistered managed file is called an orphan and complete totals are marked unknown. Symlinks are never followed; directories are never traversed. No contents, full/local paths, filenames, artifact IDs, bearer tokens, prompts, or source URLs are returned.
- All frontend JS imports and HTML asset URLs use `9c2-20260920`, including the three JavaScript test imports, so auth state remains shared and browsers don't mix 9C.1 and 9C.2 modules.

**Interpretation:** Names like `art_<opaqueid>.png` or `art_<opaqueid>_thumb.webp` indicate managed naming convention, not a guarantee that an unregistered file can be deleted. The scan cannot prove whether an image was in-flight, abandoned during a failed DB write, or is awaiting registration. A `.staging` file is never treated as an eligible orphan by this feature. The 1 GiB limit and this scan cover only the gateway's managed directory, not Codex-owned images. The scan is observational and can change while jobs are running.

## Install

1. Commit your verified 9C.1 branch to private GitHub; verify that `.gateway-data`, `.gateway-token`, `.gateway-settings.json` and Codex auth files are not staged.
2. Wait for running n8n jobs to complete, stop the gateway in Pinokio, and back up `app\gateway` and `app\tests`. The ZIP contains source and tests only, but your `.gateway-data` backup should remain available separately.
3. Extract `local_ai_gateway_module_9c2.zip` into `C:\pinokio\api\local-ai-gateway`, preserving directory paths and replacing matching files.
4. Run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **289 passing tests** if the previous Windows suite had 279: this package adds 10 tests. The locally available suite passed **268 tests** (the 9C.1 local baseline of 258 plus 10); 26 JavaScript runtime tests passed. Browser presentation was inspected at 1440, 900 and 390 pixels. These numbers do not replace real Windows verification.

5. Restart Pinokio and hard-refresh the dashboard (`Ctrl+Shift+R`). Open **Settings → Storage → What is using artifact storage? → Investigate files**. Compare the `Recent unregistered managed` count with the earlier 50. Send a screenshot of the category breakdown, age buckets and sizes, together with the existing Artifact Health panel, to determine whether another *non-destructive* investigation is needed.

**Do not click Run cleanup, enable automatic cleanup, change retention, or delete any file to test this release.** The 50 recent files are not cleanup candidates based on the previous inspection. The new report does not authorize deletion.

## Rollback

Stop after jobs finish, restore the previous source/tests backup or verified 9C.1 Git commit and restart. No SQLite migration, settings change, or media modification occurred in this module. Do not replace newer live databases as part of a source rollback.
