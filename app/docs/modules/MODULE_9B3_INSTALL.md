# Module 9B.3 — Gallery Request Lifecycle & Async Resilience

**Baseline:** Module 9B.2.1 (Jobs generated-output viewer working, expected 246 Windows tests). Frontend and test-only release. No changes to Codex, Python production code, n8n APIs, SQLite, private settings, images or quota monitoring.

## What changes

- Gallery search/category requests invalidate older requests immediately; the old result cannot replace the selected filter, even if a mock/proxy resolves after abort.
- The **Load more** button is guarded against duplicate concurrent cursor fetches and is restored after success or error.
- Leaving Gallery, an expired token, and unloading the page abort pending Gallery list, card, and reference-thumbnail requests. Previous `blob:` URLs are revoked.
- Viewer and reference requests cancel when changing the selected image, clicking another reference, or closing the viewer. Canceled requests never display a spurious “unavailable” error.
- A missing thumbnail falls back to the retained original. Image-error messages distinguish absent/expired media, access denial and temporary availability.
- Abort signals continue to use authenticated `fetch` headers; no tokens in URLs. Existing Jobs, reference-image comparison, Fit/zoom and sidebar layout remain unchanged.
- Updated the historical tests' frontend cache-release expectations to the new import version `9b3-20260920`; two historical source assertions now recognize the stricter `signal` argument without weakening their actual checks.

## Install

1. Commit your verified 9B.2.1 version on your private GitHub hardening branch before applying this release:
   ```powershell
   cd C:\pinokio\api\local-ai-gateway
   git status --short
   git add app/gateway/dashboard.html app/gateway/dashboard app/tests
   git commit -m "Gallery 9B.2.1 - generated image viewer in Jobs"
   git push -u origin HEAD
   ```
   Skip the commit if the tree is clean. Keep `v0.9-control-plane-complete` intact.
2. Finish any active n8n requests, stop the gateway in Pinokio, and back up the current frontend or rely on the just-created Git commit.
3. Extract **local_ai_gateway_module_9b3.zip** into `C:\pinokio\api\local-ai-gateway`, preserving the `app/` folder and replacing only files in the ZIP.
4. Run from PowerShell:
   ```powershell
   cd C:\pinokio\api\local-ai-gateway\app
   $TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
   .\.venv\Scripts\python.exe -m compileall -q gateway tests
   .\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
   Get-ChildItem .\gateway\dashboard -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.Name)" } }
   node --test .\tests\frontend_gallery.test.mjs .\tests\frontend_live.test.mjs .\tests\frontend_sync.test.mjs
   ```
   Expected **254 Python tests** (246 installed baseline + eight new 9B.3 tests), if no unrelated changes. Separately, **18 Node runtime tests** should pass.
5. Restart Pinokio and hard-refresh with **Ctrl+Shift+R**. Verify `/dashboard/assets/dashboard.js?v=9b3-20260920` and `/dashboard/assets/gallery.js?v=9b3-20260920` respond 200.
6. In Gallery, rapidly enter/change a search, switch All/Scenes/Assets, switch to Jobs while images are loading, then return. Check no stale or duplicated results. If there are more than 48 images, click Load more rapidly and confirm only one page appends. Open one generated image and reference, switch to another, and close. The sidebar and images should behave as in 9B.2.1. DevTools should show canceled requests as normal when switching pages.

## Verification and limitations

- The available local Python regression subset passed **233 tests** (225 existing locally available + eight new tests). Your actual installed original phase-one tests account for the 21-test local/Windows difference.
- **18 Node runtime tests** pass, including five adversarial asynchronous order tests for obsolete responses, duplicated pagination, navigation cancellation, and error handling.
- Chromium fixture-based checks passed for an image-request race and all existing Gallery/Jobs reference-viewer interactions at **1440 px, 900 px, and 390 px**.
- Fetch cancellation conserves network work, but cancellation is not a guarantee that a remote server stops work already started; generation requests and their persistence are unaffected.
- The Dashboard does not offer a manual logout yet; that is separate UI work.

## Commit / rollback

After Windows/browser acceptance:
```powershell
cd C:\pinokio\api\local-ai-gateway
git add app/gateway/dashboard.html app/gateway/dashboard app/tests
git commit -m "Gallery 9B.3 - cancel stale requests and guard pagination"
git push -u origin HEAD
```

For rollback, stop the gateway, restore only the frontend/test files from your pre-install Git commit or backup, restart and hard-refresh. Do not use `git reset --hard` on a dirty working tree. No database or artifact rollback is needed.
