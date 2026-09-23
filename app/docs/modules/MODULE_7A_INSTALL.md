# Module 7A — Settings engine (backend, no UI changes)

**Baseline:** Module 6R.3 backend + Module 6D.1 frontend. This package intentionally contains **no** `dashboard.html`, `.js`, `.css`, bearer token or data directory. It is incremental, not a full repository replacement.

## What this release adds

- `GET /dashboard/api/settings`: authenticated allowlisted view of active values, saved values, environment overrides, pending restart, validation ranges, and field groups.
- `PATCH /dashboard/api/settings`: authenticated, partial, validated, atomic updates.
- Model changes via PATCH apply immediately and synchronize `/v1/models`, `/health`, Overview and future generation requests. The existing `PUT /v1/models/default` still works.
- Other fields are **restart-required**: concurrency, queue, text/research timeouts, image default timeout, quota monitor enablement/polling/warning/critical thresholds.
- Existing `.gateway-settings.json` is preserved, including unrelated existing keys. Invalid existing JSON cannot be silently overwritten by PATCH.
- The existing image request default remains **600 seconds** unless you configure a new default and restart. An explicit `timeout_seconds` in an image request still takes precedence. Images still return binary files.
- Environment variables take precedence over saved restart-required settings. The saved model retains the pre-7A behavior of taking precedence over `AI_GATEWAY_MODEL`.

## Files added

- `app/gateway/settings_routes.py`
- `app/tests/test_module_7a.py`

## Files replaced

- `app/gateway/settings_manager.py`
- `app/gateway/config.py`
- `app/gateway/contracts.py`
- `app/gateway/image_routes.py`
- `app/gateway/app.py`
- `app/tests/test_refactor_6r2.py` — updates the strict legacy 14-route inventory to account for GET/PATCH Settings while checking all original endpoints.

## Install

1. Wait for n8n tasks to finish; stop gateway in Pinokio.
2. Backup `app/gateway/`, `app/tests/test_refactor_6r2.py`, and **your local** `app/.gateway-settings.json` if present. Keep the backup private (never upload a token).
3. Extract `local_ai_gateway_module_7a.zip` **into the repository root** `C:\pinokio\api\local-ai-gateway`, preserving the included `app/gateway` and `app/tests` paths.
4. In VS Code PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **79 passing tests** if the previous suite still has 65 (65 + 14 new tests). Two known third-party deprecation warnings may remain.

5. Restart in Pinokio. Verify Dashboard Overview, model change, one text request and one reference-image request.

## Important: Settings browser page is NOT ready yet

7A is the backend engine and API. The Settings page currently remains the existing placeholder. The full Settings UI will be built in Module 7D after privacy/retention modules. Don't expect fields in the browser yet.

## Example request (optional)

Use your own token locally. Never paste it into this chat or store it in PowerShell history.

```http
GET /dashboard/api/settings
Authorization: Bearer <your-token>
```

```http
PATCH /dashboard/api/settings
Authorization: Bearer <your-token>
Content-Type: application/json

{
  "max_concurrency": 2,
  "max_queue": 6,
  "default_timeout_seconds": 150,
  "image_timeout_seconds": 720,
  "quota_warning_remaining_percent": 30,
  "quota_critical_remaining_percent": 10
}
```

The response shows `saved` values and `pending_restart=true` for changed non-model fields. **They do not affect live workers until restart.** If `environment_override=true`, Pinokio's process environment controls that setting and a saved change will not take effect even after restart until the environment override is adjusted.

## Rollback

Stop gateway. Restore backed-up `app/gateway/` and `test_refactor_6r2.py`, remove `app/tests/test_module_7a.py`, and restore your original `.gateway-settings.json` if you changed it. No SQLite migrations occur in this release.
