# Module 9B.2 — Jobs & Gallery reference-image experience

**Baseline:** Windows-verified Module 9B.1 (**231 tests passed**) on your private hardening branch. This is a frontend-only incremental package. Do not use it as a replacement for the entire gateway.

## Changes

- In **Jobs**, render the complete reference images inside uncropped, accessible, clearly labelled cards; click one to open its full stored image in the existing workspace-scoped viewer. Reference order is preserved.
- In **Gallery**, opening a generated image loads *that specific job's* stored reference metadata on demand via the existing authenticated `GET /dashboard/api/jobs/{job_id}` route; a compact reference rail appears below the output. Select any reference to compare in the same viewer, then use **Show generated** to return to the output. Gallery itself continues to show only generated outputs, not duplicate reference cards.
- Missing/expired/unretained references display an explicit unavailable state. Missing optional thumbnails fall back to the archived full image. Reference URLs are always fetched through the existing authenticated `requestBlob()`, never exposed as unprotected `<img src>` URLs or query-string tokens.
- Results from an earlier selected job/viewer are discarded when switching jobs, navigating images, or closing; object URLs are revoked. Keep the original image viewer, fit/zoom/navigation, sidebar and status bar.
- Apply a coherent asset version `9b2-20260920` to all affected JavaScript imports and HTML asset links so the cached browser cannot accidentally instantiate duplicate API/auth modules.

**No changes** to Python production code, SQLite schema, Codex execution, Pinokio launcher, quotas, SSE event bus, generation API, or live data. Existing job detail and artifact endpoints provide everything required.

**Important retention limit:** This UI can show only references whose artifact copies were successfully archived and have not been deleted by retention. The original externally hosted Drive URL is intentionally not replayed or exposed as a fallback. Jobs created when reference persistence was unavailable may display a reference ID and **Not retained**.

## Safe installation

1. Commit your verified 9B.1 source changes first if pending. Leave `v0.9-control-plane-complete` and your `.gateway-data` backups unchanged.
2. After active n8n jobs finish, stop the gateway in Pinokio. Back up `app/gateway/dashboard.html` and `app/gateway/dashboard/`, or make a Git commit.
3. Extract the package ZIP **into** `C:\pinokio\api\local-ai-gateway`, retaining its `app/` layout. Replace only supplied files. Do not delete files not in this archive, especially the existing `dashboard/state.js`.
4. In PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
Get-ChildItem .\gateway\dashboard -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.Name)" } }
node --test .\tests\frontend_live.test.mjs .\tests\frontend_sync.test.mjs
```

**Expected Windows result: 241 Python tests passing** (231 verified + ten new 9B.2 tests), assuming no unrelated changes; two known library deprecation warnings may remain. Available local regression suite: **220 passed**; Node live/sync: **13 passed**. A mock-API Chromium browser check passed Gallery and Jobs interactions at 1440×900, 900×850, and 390×844. It is *not* a substitute for your live Windows/authenticated reference check.

5. Restart Pinokio and hard-refresh the dashboard (**Ctrl+Shift+R**). Confirm `/dashboard/assets/gallery-polish.css?v=9b2-20260920` and `/dashboard/assets/dashboard.js?v=9b2-20260920` return HTTP 200 in DevTools → Network.

## Browser acceptance checklist

- **Jobs:** Select a newly completed real image job with two Drive character references. Both appear as complete, uncropped thumbnails and labelled Reference 1 / Reference 2; click each and see its complete image in the bounded viewer. Close returns to Jobs.
- **Gallery:** Click that generated output. Sidebar and top status remain present; original image is centered. Below it, see its references. Click Reference 1 and click **Show generated** to return. Previous/next cycles *generated images*, not references.
- Open a job where no reference copy is retained (if one exists); check the **Not retained / Unavailable** state and that the generated image still opens.
- Confirm authentication remains live, Jobs and Gallery can be navigated, Settings has no unsaved changes, and Overview shows Live without console errors.

## Commit and rollback

If accepted:

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git add app/gateway/dashboard.html app/gateway/dashboard app/tests
git commit -m "Gallery 9B.2 - inspect complete reference images in Jobs and Gallery"
git push -u origin HEAD
```

If the UI regresses: stop Pinokio, restore the HTML, JavaScript/CSS, and matching tests from your immediately previous committed 9B.1 state (or backup), then restart and hard-refresh. No database rollback is required. Avoid `git reset --hard` if it would discard other uncommitted work.
