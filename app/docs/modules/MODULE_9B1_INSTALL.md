# Module 9B.1 — Gallery layout & bounded image viewer

**Baseline:** Windows-verified Module 9A.3.1 (225 passing tests) on your private Git hardening branch. This is a frontend-only incremental patch, not a replacement for the whole gateway.

## Changes

- Center each Gallery card image and preserve its aspect ratio (`object-fit: contain`) instead of cropping. Provide a consistent padded image area, card gap, card borders and readable metadata spacing.
- Render the Gallery's image viewer **to the right of the sidebar and below the top health bar**, in a bounded panel. The main menu and top health information remain visible. The full image is fitted and centered without cropping; the zoom/fit/100% and previous/next controls remain.
- Reset the image viewer loading state between images, ignore earlier fetch results arriving after Next/Close, restore focus to the triggering card when closing, and dismiss the viewer when navigating to another dashboard section.
- Add `gallery-polish.css` after the existing stylesheet; bump the *whole frontend JavaScript import graph* to `9b1-20260920` to prevent duplicate auth/state modules and browser-cache mismatches. Historical tests asserting an older release version are updated while preserving their original assertions.

**Not included:** No changes to Python backend, API contracts, SQLite, Pinokio launcher, quotas, token handling, or generation. Jobs reference-thumbnail cropping/clickable references and associating references with a Gallery output are the approved **Module 9B.2**, after Windows verifies this layout release.

## Install

1. Commit verified 9A.3 source changes first, if pending, on your private hardening branch. Check `git status --short` before installing. Leave the `v0.9-control-plane-complete` tag intact.
2. Let active n8n requests finish, stop the gateway in Pinokio, and back up `app/gateway/dashboard.html` and `app/gateway/dashboard/` (or make a Git commit).
3. Extract the ZIP into `C:\pinokio\api\local-ai-gateway`, preserving the `app/` folder layout, replacing only the supplied files. Do not delete frontend assets not present in this archive.
4. In PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
Get-ChildItem .\gateway\dashboard -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.Name)" } }
```

**Expected full Windows result: 231 passed** (225 verified + six 9B.1 tests), assuming no unrelated changes. Available local regression subset: 210 passed (204 existing local + six new tests), 13 Node runtime checks passed. Browser layout and actual Gallery interactions tested with sample media at 1440×900, 900×900 and 390×844; this does not substitute for your authenticated Windows UI check.

5. Restart Pinokio, open Dashboard and press **Ctrl+Shift+R**. In DevTools → Network, verify `/dashboard/assets/gallery-polish.css?v=9b1-20260920` returns 200.
6. In Gallery, confirm padded labels, complete uncropped thumbnails (wide and portrait), image opens *within* the workspace, sidebar and topbar remain visible, zoom/fit/previous/next work, close restores the Gallery, and a sidebar navigation click dismisses the image viewer. Confirm Jobs, Settings and Live status still work. Nothing needs to be deleted or re-generated solely to test the layout.
7. If everything works, commit and push on your private hardening branch:

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git add app/gateway/dashboard.html app/gateway/dashboard app/tests
git commit -m "Gallery 9B.1 - center cards and constrain image viewer"
git push -u origin HEAD
```

## Rollback

If the dashboard fails before you can verify, stop Pinokio and restore the frontend HTML/assets and matching tests from the prior Git commit/backup. Keep the backend, `.gateway-settings.json`, token and `.gateway-data` untouched. Cache-bust/refresh after restoring. Do not use `git reset --hard` without ensuring no wanted hardening work is uncommitted.
