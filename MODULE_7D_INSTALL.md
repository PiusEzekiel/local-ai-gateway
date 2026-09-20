# Module 7D — Settings and System UI

Baseline: verified Module 7C / SQLite schema version 6 / 103 tests on Windows.

## Stop + backup

Finish n8n jobs and stop Local AI Gateway in Pinokio. Back up `app/gateway`, `app/.gateway-settings.json`, and `app/.gateway-data`. This release does **not** run cleanup automatically and does not require a database migration.

```powershell
cd C:\pinokio\api\local-ai-gateway
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:USERPROFILE "Documents\gateway-before-7d-$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item .\app\gateway "$backup\gateway" -Recurse
if (Test-Path .\app\.gateway-settings.json) { Copy-Item .\app\.gateway-settings.json $backup }
if (Test-Path .\app\.gateway-data) { Copy-Item .\app\.gateway-data "$backup\.gateway-data" -Recurse }
```

Extract `local_ai_gateway_module_7d.zip` into `C:\pinokio\api\local-ai-gateway`, preserving its `app/gateway` and `app/tests` directories. **Do not extract into `app` itself** or Windows may create `app\app`.

## Test

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **111 tests** if your suite was exactly 103 at Module 7C and no additional local tests were added. Note: this build's local suite passed **99 accessible tests**, excluding `test_phase1.py` because the development harness has a stub quota monitor without the real `normalize_rate_limits` implementation. The complete Windows result is authoritative.

## Restart + browser verification

Restart in Pinokio. Ctrl+Shift+R in your browser. Click **Settings** and verify:

1. Model, Execution, Timeouts, Quota, Privacy and Retention form groups load.
2. Changing model to Terra or Luna and clicking Save updates the running model immediately; the topbar reflects it at the next dashboard refresh (about five seconds). Change back when done.
3. Changing concurrency 1 → 2 and clicking Save displays **Restart required** while the topbar still shows active worker capacity 1. Restart through Pinokio and check active capacity 2. Set concurrency back if this was just a test.
4. Change the maximum text timeout to an invalid value (below min or above max): Save should show a validation error and the saved JSON should remain unchanged.
5. The Storage section shows disk usage and a read-only preview. **Do not run cleanup as a smoke test**; it deletes real data.
6. Privacy counts load, and purge/cleanup open confirmation dialogs. Cancel them—no need to perform a destructive test in production.
7. System shows gateway version, platform, uptime, database size, running workers/queue, and Codex CLI version if the CLI reports one. It should not expose Windows host paths or credentials.
8. Diagnostics filter controls no longer clip in the left pane; Summary/Gallery/Usage still load.

## Important behavior

- Default model applies immediately. All other editable settings apply **after Pinokio restart**.
- Environment overrides may lock individual controls; update the launcher/environment to change those values.
- Saving a retention value does NOT automatically delete data.
- Storage preview and manual cleanup use currently active settings, never pending restart values. Cleanup is disabled while a retention setting is pending restart.
- Automatic cleanup stays disabled unless explicitly enabled and applied via restart.
- All destructive actions require typed confirmation and an authenticated POST.
- Codex CLI version is allowed to show **Unavailable**; it never runs image generation or modifies subscription usage.

## Files

New:
- `app/gateway/system_info.py`
- `app/gateway/system_routes.py`
- `app/gateway/dashboard/settings.js`
- `app/gateway/dashboard/settings.css`
- `app/tests/test_module_7d.py`

Modified:
- `app/gateway/app.py`
- `app/gateway/dashboard.html`
- `app/gateway/dashboard/api.js`
- `app/gateway/dashboard/dashboard.js`
- `app/gateway/dashboard/diagnostics.css`
- `app/gateway/dashboard/diagnostics.js`
- `app/gateway/dashboard/gallery.js`
- `app/gateway/dashboard/jobs.js`
- `app/gateway/dashboard/analytics.js`
- `app/tests/test_module_6d.py`
- `app/tests/test_module_6d1.py`
- `app/tests/test_refactor_6r2.py`

Versioned JS module import paths together to avoid importing two API/token modules. `state.js` and `dashboard.css` are unchanged and intentionally omitted from the release.

New protected endpoint: `GET /dashboard/api/system`. No change to `/v1/*` contracts or SQLite schema.

## Rollback

Stop in Pinokio and restore the backed-up `gateway` directory. Restore saved JSON/data only if you intentionally modified them; this module does not migrate SQLite. Restart.
