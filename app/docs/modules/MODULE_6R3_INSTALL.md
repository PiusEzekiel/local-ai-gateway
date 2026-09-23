# Module 6R.3 — Generation Route Extraction

**Baseline:** Module 6R.2 (`app/gateway/app.py` SHA256 `c63f36712f3bb284ef3a8cd6ed4715973dd941fc6b7409b2b65ad05950694d48`).

## What this changes

- `app/gateway/app.py` — 866 → 288 lines; still creates the FastAPI app, authentication, quota monitor, stores, dashboard and exception handlers.
- `app/gateway/generation_routes.py` — model and capabilities handlers plus registration of the image and text routers; one per-app pool.
- `app/gateway/generation_state.py` — shared app-scoped model/queue counters, semaphore, lock and dependencies.
- `app/gateway/image_routes.py` — the original image binary response, image reference handling, timestamps and telemetry.
- `app/gateway/text_routes.py` — the original `/v1/generate`, `/v1/research`, and `/v1/chat/completions` contracts.
- `app/tests/test_refactor_6r3.py` — 9 additional behavioral tests.

There is **no database migration**. All dashboard routes and `/v1/*` route names/HTTP methods are unchanged. The /v1 image route still returns the binary image, not JSON. The default model remains shared between `/v1/models/default`, `/health`, dashboard summary and future jobs. Model overrides remain per-request.

## Install from VS Code / PowerShell

1. After active n8n jobs complete, stop the gateway in Pinokio.
2. Back up `app/gateway/app.py` and the whole `app/gateway` folder to a separate folder (do not overwrite your previous backup).
3. Extract the ZIP into `C:\pinokio\api\local-ai-gateway`, keeping the `app/gateway/...` and `app/tests/...` paths.
4. The ZIP does NOT include `.gateway-token`, `.gateway-settings.json`, `.gateway-data`, `.venv`, or unrelated app files. Do **not** delete those items.
5. In a VS Code PowerShell terminal:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected:** 57 passed if your previous suite still contains 48 tests (plus nine new tests).

6. Restart in Pinokio. Check dashboard, quota and default model. After that, run ONE safe n8n text task and ONE image task with references to confirm actual Codex execution (the tests use fake runners and cannot validate your real Codex login, reference URL or CLI build).

## Rollback

Stop Pinokio and restore the backed-up `app/gateway/app.py`; then remove only these *new* files: `generation_routes.py`, `generation_state.py`, `image_routes.py`, `text_routes.py`, `tests/test_refactor_6r3.py`. Restore any saved copies if they were present before installation. SQLite is unchanged.

## Local verification

- Baseline and refactored OpenAPI inventories: same 23 HTTP paths/method groups.
- 45 available focused tests passed using the current local harness (the Windows-only installation has additional baseline tests and real runtime dependencies).
- Python module and test compilation passed.
