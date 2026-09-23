# Module 6R.1 — Safe backend modularization (after Module 6C)

**Baseline:** Your installed Module 6C with **39 passing tests**. This is a structural refactor: no database migrations, no changes to Pinokio startup, HTTP routes, n8n API request/response bodies, models, queue behavior, image generation, or frontend assets.

## What changes

Replace:
- `app/gateway/app.py` — retains `create_app()`, FastAPI route handlers, worker/queue coordination, and public compatibility exports; shrinks from ~3,023 lines to ~1,324.

Create:
- `app/gateway/config.py` — gateway constants, settings and Codex executable lookup.
- `app/gateway/contracts.py` — Pydantic request/response models and `GatewayError`.
- `app/gateway/codex_diagnostics.py` — CLI error classification and event summaries.
- `app/gateway/job_history.py` — live job cache, persistence, events and diagnostic state.
- `app/gateway/schema_validation.py` — JSON output schema validation.
- `app/gateway/reference_images.py` — Google-host allowlist and reference downloads.
- `app/gateway/codex_runner.py` — existing Codex `exec` text and image implementation.
- `app/tests/test_refactor_6r.py` — four focused refactor and route tests.

`gateway.app.CodexRunner`, `gateway.app.Settings`, `gateway.app.GatewayError`, `gateway.app.JobHistory`, etc., continue to import correctly. The test hook `gateway.app.download_reference_image` remains patchable: `gateway.app.CodexRunner` is a thin compatibility subclass that injects the downloader into the extracted runner. Codex CLI flags and command construction remain unchanged.

## Install in VS Code (PowerShell)

1. Finish/stop active n8n requests and stop the Pinokio gateway.
2. Create a backup of `app/gateway/app.py` and your entire `app/.gateway-data/` directory if you also want a database safety copy. No database change is expected for this refactor.
3. Extract `local_ai_gateway_module_6r1.zip` into `C:\pinokio\api\local-ai-gateway`, preserving the `app/` paths. Confirm replacement of `app/gateway/app.py`; the other eight files are new.
4. From the repository root:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **43 tests** if you had 39 before and install these four new tests (assuming no other local changes). If tests fail, restore the backed-up `app.py` and remove the seven newly created gateway modules; the current production data and other modules are unchanged.

5. Restart the gateway via Pinokio; smoke-test `/health`, one text request, and an image request with reference images through your existing n8n flow when practical.

## Verification performed here

- All 27 available Module 6A/6B/6C tests passed.
- Four added refactor tests passed: 31 total.
- Python compile passed.
- Extracted functions/classes (other than `CodexRunner`, which has only explicit downloader injection) match their original Python AST.
- The full 39-test suite was **not** run in this environment because the user's full `artifact_store.py`, `quota_monitor.py`, `analytics.py`, and `settings_manager.py` modules were not included among the uploaded files. Your Windows 43-test run is the acceptance check.

## Next extraction

Module 6R.2 will move the dashboard HTTP routes into a proper router/service using explicit dependency injection instead of brittle nested-function globals. Module 6R.3 will move the `/v1/*` HTTP handlers/queue orchestration into a separate module. After those, `app.py` should mainly compose the application, dependencies, and lifespan, followed by Module 6D Diagnostics UI.
