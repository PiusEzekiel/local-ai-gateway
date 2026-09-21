# Module 9B.2.1 — Open generated outputs from Jobs

**Baseline:** Installed 9B.2 with 241 Windows tests passing. Frontend/test-only corrective patch; does not modify Python production code or your image data.

## Fix

- The large generated output in Jobs is now a real keyboard-accessible, clearly labelled button. Click it to open the authenticated full-resolution original in the *same bounded workspace viewer* used by Gallery and job references.
- The viewer keeps the sidebar and status bar visible and shows the selected job's retained reference rail. Click any reference to compare, then select **Show generated** to return to the result.
- A Jobs-only preview hides Gallery previous/next controls and disables arrow navigation between unrelated Gallery images. Close restores focus to the clicked output button.
- The job's output URL is validated as a same-origin `/dashboard/api/artifacts/` path; image fetching still uses authenticated `requestBlob()`. It is not embedded as a public `<img src>` URL, and no bearer token is sent in a URL.
- Existing Gallery previews and Jobs reference previews behave as in 9B.2. All dashboard module imports and cache-busted assets share `9b21-20260920` to avoid stale mixed JavaScript/auth state.

## Install

1. Commit your verified 9B.2 changes first if you have not already. Keep the immutable `v0.9-control-plane-complete` tag.
2. Stop Pinokio after active n8n jobs finish, and back up `app/gateway/dashboard/` and `app/gateway/dashboard.html` (or make a Git commit).
3. Extract `local_ai_gateway_module_9b21.zip` **into** `C:\pinokio\api\local-ai-gateway`, keeping the `app/` folder layout. Replace the supplied files only; do not delete the others.
4. From PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
Get-ChildItem .\gateway\dashboard -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.Name)" } }
node --test .\tests\frontend_live.test.mjs .\tests\frontend_sync.test.mjs
```

**Expected Windows result: 246 passed** (241 + 5 new tests), if no unrelated changes. Two existing upstream deprecation warnings may remain. Local available suite: 225 passed; live/sync Node tests: 13 passed; simulated Chromium acceptance passed at desktop 1440×900, tablet 900×850, and mobile 390×844. These do not replace the Windows test.

5. Restart Pinokio and hard-refresh the dashboard with **Ctrl+Shift+R**. In DevTools Network verify `/dashboard/assets/dashboard.js?v=9b21-20260920` and `/dashboard/assets/gallery-polish.css?v=9b21-20260920` respond 200.
6. In **Jobs**, select an image-generation job and click its large generated image. Check the full image is centered and the sidebar stays visible. Click a reference, click **Show generated**, close and confirm Jobs is still selected. Test a generated job with zero references if one exists.

## Commit / rollback

After acceptance:

```powershell
cd C:\pinokio\api\local-ai-gateway
git add app/gateway/dashboard.html app/gateway/dashboard app/tests
git commit -m "Gallery 9B.2.1 - open generated outputs from Jobs"
git push -u origin HEAD
```

If the browser regresses, stop the gateway, restore the files supplied in this release from the previous 9B.2 Git commit or backup, and restart/hard-refresh. No SQLite migration/rollback is needed. Avoid `git reset --hard` if other work is uncommitted.
