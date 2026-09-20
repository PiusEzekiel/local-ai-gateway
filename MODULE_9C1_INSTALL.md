# Module 9C.1 — Read-only Artifact Health & Missing-File Clarity

This package is based on the verified 9B.5 frontend and the 9A.2 atomic artifact store. It adds an authenticated, bounded inventory of SQLite artifact records vs. the gateway's managed artifact files, plus more accurate missing-media text in Jobs/Gallery. **It does not delete, regenerate, repair, upload, or modify any existing media or database records.**

## What changes

- `GET /dashboard/api/storage/health`: authenticated, metadata-only inventory of registered originals, registered thumbnails, missing/unsafe/unreadable files, and *eligible* unregistered managed files more than 24 hours old. Orphan counts are marked **unknown** if a scan limit prevents a complete inventory. API responses include no local paths, tokens, prompts, or external reference URLs.
- Settings → Storage → **Artifact health**: current file status, counts, sample opaque job/artifact IDs, and an **Inspect files** refresh button. This is **separate from** Preview cleanup and Run cleanup.
- Jobs/Gallery now distinguish a registered artifact whose original file is missing from a job whose original is no longer retained or whose database record is gone. Optional, never-created thumbnails are *not* counted as missing; thumbnail fetches still fall back to the full original.
- All dashboard JS and CSS URLs are versioned as `9c1-20260920` to avoid mismatched cached frontend assets.

**Interpretation limit:** A missing file cannot reliably be labelled “expired” without a deletion audit trail. Orphan counts cover only files with the gateway's opaque `art_...` name format under its own artifact folder. They do *not* cover Codex-managed files under `CODEX_HOME/generated_images`. This is an observational scan; an active generation or cleanup can alter the inventory while it runs.

The scan caps are 5,000 database artifact rows, 10,000 directory entries and 8 affected-record samples. If the inventory exceeds a cap, the UI explicitly says the orphan count is incomplete instead of presenting a potentially false zero.

## Install

1. Commit the working 9B.5 state to the existing private Git branch. **Do not push `.gateway-data`, `.gateway-token`, `.gateway-settings.json`, Codex auth, or retained content to GitHub.**
2. Wait for active n8n jobs to complete. Stop the gateway in Pinokio. Back up `app\gateway`, `app\tests` and, as a precaution before any backend update, `app\.gateway-data` to a private location.
3. Extract `local_ai_gateway_module_9c1.zip` into `C:\pinokio\api\local-ai-gateway`, preserving paths and replacing matching files. The ZIP contains only source, tests, and this guide — no database, credentials, settings, images, or `.venv`.
4. From PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected:** 279 tests, if your previously verified 9B.5 installation had 269 (10 additional 9C.1 tests). The available local suite passed 258; the original Phase 1 tests require your real installed quota monitor, which is deliberately NOT in this ZIP. JavaScript runtime tests: 26 passed.

5. Restart through Pinokio and hard-refresh the browser with **Ctrl+Shift+R**. Open Settings → Storage. Click **Inspect files** (or enter Settings; the first read-only health scan runs automatically). Expect three status metrics and an explicit scan-completeness note.
6. Open one generated scene with references in Jobs and Gallery. Original and reference previews should still open as before. An intentional real-world deletion is **not** required or recommended to test error labels: disposable fixture tests cover those failure cases.

## Rollback

Stop the gateway after active jobs finish and restore your backed-up source files/tests or the previous commit. This module has no SQLite migration or settings change; do not restore an old database over newer production data as part of a source-only rollback. Restoring source does not undo media deletion performed separately by a user.
