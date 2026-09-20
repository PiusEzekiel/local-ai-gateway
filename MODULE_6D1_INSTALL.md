# Module 6D.1 — Diagnostics browser cache and layout correction

## Why the browser looked broken

The screenshot matches new Diagnostics HTML being rendered without its corresponding 6D styles: summary labels show as loose text and the two-column inspector is lost. The initial "Waiting for data" state also indicates that the current Diagnostics controller did not finish loading. Old cached assets can produce this mixed-version condition even while the Python regression suite passes.

This package makes the Diagnostics CSS an independent file and versions the full JavaScript import graph (all modules share the SAME api.js instance and token). No backend, worker, database or Codex changes.

## Install

1. Stop gateway through Pinokio AFTER currently running n8n work completes. Backup `app/gateway/dashboard/`, `app/gateway/dashboard.html`, and the two tests `test_module_6d.py` if desired.
2. Extract at `C:\pinokio\api\local-ai-gateway`, preserving `app/gateway/...` folders. All modified JS files are in the ZIP to keep their ESM dependencies on one release.
3. Re-run pytest; expected 65 tests (previous 62 plus 3 new tests).
4. Restart Pinokio and open gateway root with a fresh reload `Ctrl+Shift+R` or a new private tab. Go to Diagnostics and use Clear filters + Refresh failures. If no failed jobs in period, try 30d; this should be shown as an empty state, not as a broken layout.

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

## If still empty or wrong

F12 > Console: check for module import errors, 401, 404 or CSP. F12 > Network > Disable cache and reload; confirm `/dashboard/assets/diagnostics.css?v=6d1-20260920` returns 200 and `diagnostics.js?v=6d1-20260920` returns 200. From Console run:
`getComputedStyle(document.querySelector(".diag-summary")).display`
Expected `grid`.

## Rollback

Stop gateway, restore backed-up dashboard files and `dashboard.html`; remove added `diagnostics.css` and `test_module_6d1.py` and restore prior `test_module_6d.py` if rolling back entirely.
