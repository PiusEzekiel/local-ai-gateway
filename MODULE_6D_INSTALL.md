# Module 6D — Diagnostics dashboard (frontend only)

## Before installing

Complete active n8n jobs and stop the Local AI Gateway in Pinokio. Back up your current frontend source. No SQL migration or backend API changes are included.

From `C:\pinokio\api\local-ai-gateway\app` in PowerShell:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:USERPROFILE "Documents\gateway-dashboard-before-6d-$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item .\gateway\dashboard.html $backup
Copy-Item .\gateway\dashboard (Join-Path $backup 'dashboard') -Recurse
Write-Host "Frontend backup: $backup"
```

Extract `local_ai_gateway_module_6d.zip` into **the project root**, `C:\pinokio\api\local-ai-gateway`, retaining the `app\gateway\...` paths. The ZIP contains **only** these files:

```text
app/gateway/dashboard.html            (modified)
app/gateway/dashboard/api.js           (modified)
app/gateway/dashboard/dashboard.js     (modified)
app/gateway/dashboard/dashboard.css    (modified)
app/gateway/dashboard/diagnostics.js   (new)
app/tests/test_module_6d.py            (new)
```

Do not delete existing dashboard modules; Gallery, Jobs, Usage and Performance are reused.

## Run tests

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

If the prior module suite was exactly `57 passed`, installing this ZIP should add **five frontend integration-guard tests**, for **62 passed**. The two existing test-dependency deprecation warnings may remain.

## Browser verification

1. Restart Local AI Gateway via Pinokio; hard refresh dashboard using Ctrl+Shift+R to bypass stale JavaScript/CSS.
2. Connect using your local bearer token. Open **Diagnostics** in the left navigation.
3. Check Today / 24h / 7d / 30d, then Custom range. Summary cards and failure list should agree with filters.
4. Search for an actual failed scene ID, select the row, and inspect stage timeline, exit code, reference count, and redacted diagnostic preview. Historical jobs before schema v5 may correctly show missing exit codes/diagnostics.
5. Select **Copy diagnostic bundle** and paste in a temporary editor. Check that no private URLs, bearer token or local file paths are exposed; do not share diagnostics you have not inspected.
6. Test `Load more failures` if you have over 50 results; navigate to Overview → Recent failures → click a failure to open its inspector.
7. Verify Gallery, Usage and Performance still work, and run one real text and image generation with references after Pinokio restart.

The page polls at 20-second intervals while visible. Silent updates preserve list scroll and selection, and do not discard explicitly loaded later pages. `Refresh failures` resets to the first page.

## Scope and known limitations

- Frontend-only: consumes existing authenticated 6C endpoints. No changes to Codex execution, `/v1/*`, SQLite or gateway token.
- A Chromium navigation attempt to the local test server was blocked by the execution environment. Static desktop/mobile layout was rendered and checked; production browser interaction is the remaining acceptance step on Windows.
- Module 7 Settings is intentionally left as the existing placeholder.
- Prior-to-v5 job history may have no saved diagnostic preview or exit code: UI shows Unavailable instead of inventing telemetry.

## Rollback

Stop the gateway and restore `dashboard.html` and the backed-up `dashboard/` directory. The new `dashboard/diagnostics.js` and `tests/test_module_6d.py` may then be removed if rolling back the entire module. No database rollback is necessary.
