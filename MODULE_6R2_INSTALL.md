# Module 6R.2 — Dashboard API router extraction

**Prerequisite:** Module 6R.1 installed and verified (your VS Code run: **43 passed**). This package assumes the 6R.1 `app/gateway/app.py`, not the earlier 3,023-line monolith.

This is a **structural refactor only**. It does not change SQLite schema, migrations, Codex CLI invocation, n8n request/response contracts, frontend assets, gateway token, or running-job/concurrency behavior.

## Files in this package

| File | Operation | Description |
|---|---|---|
| `app/gateway/app.py` | Replace | Registers the extracted dashboard router, retains runtime composition and `/v1/*` routes. |
| `app/gateway/dashboard_routes.py` | New | All 14 authenticated `/dashboard/api/*` routes and existing view/helper functions. |
| `app/tests/test_refactor_6r2.py` | New | Five regression tests for registration, auth, model updates and per-app state isolation. |

`app.py` is reduced from ~1,324 to ~866 lines. `dashboard_routes.py` is ~516 lines. The router takes the *existing* stores, monitor, job history, settings, auth dependency and a current-model getter as parameters. It does not import `gateway.app` or initialize persistent resources.

## Install (VS Code PowerShell)

1. Allow current n8n requests to finish; stop the gateway in Pinokio.
2. From the Pinokio repo root, back up the current module before replacing it:

```powershell
cd C:\pinokio\api\local-ai-gateway
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item .\app\gateway\app.py ".\app\gateway\app-before-6r2-$stamp.py.bak"
```

3. Extract the ZIP into `C:\pinokio\api\local-ai-gateway`, preserving the `app/` structure. Only overwrite `app/gateway/app.py`; the other two files are new.
4. In VS Code PowerShell run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected on your installation: **48 passed** (the previous 43 plus five new tests), assuming no local changes to test count. Deprecation warnings from existing test-client dependencies may remain.

5. Restart the gateway through Pinokio. Verify the Overview/Jobs/Gallery/Usage/Performance pages and Diagnostics API, and run an existing n8n text and image-with-reference request when practical.

**Rollback:** Stop the gateway, restore your `app.py` backup, and remove `app/gateway/dashboard_routes.py` and `app/tests/test_refactor_6r2.py`. No database rollback is needed.

## Verification in this build environment

- Python compilation passed.
- The original 6R.1 HTTP route inventory and the 6R.2 inventory are **identical: 25 registered HTTP routes, including 14 dashboard routes**, with matching route names and ordering.
- **36/36 locally available focused tests passed** (6A, 6B, 6C, 6R.1 plus five new 6R.2 tests).
- The full Windows 48-test run is your installation acceptance gate. Some other runtime modules in the local assembly are minimal stand-ins rather than your complete production source.

## Next phase

**6R.3:** Extract generation HTTP handlers/queue orchestration into a dedicated module, retain application composition and lifespan in `app.py`, and then build **6D: Diagnostics UI**.
