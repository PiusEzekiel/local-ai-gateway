# Module 6C — Diagnostics API (backend only)

This module builds on 6A and 6B. Install **only after** the 30-test Module 6B suite passes.

## Files included

- `app/gateway/app.py` — four authenticated, read-only diagnostics routes plus in-memory fallback.
- `app/gateway/job_store.py` — bounded SQL failure list/summary/event queries; no schema migration.
- `app/tests/test_module_6c.py` — nine new API tests.

Do not overwrite `diagnostics.py`, `artifact_store.py`, `analytics.py`, or any dashboard files.

## Install

1. Let active n8n gateway requests finish and stop the Pinokio gateway.
2. Back up `app/gateway/app.py`, `app/gateway/job_store.py`, and (gateway stopped) `app/.gateway-data/gateway.sqlite3`. If WAL/SHM exist, copy the entire `.gateway-data` directory as a unit instead of only copying the database file.
3. Extract the ZIP in `C:\pinokio\api\local-ai-gateway` and preserve the `app/` paths. Accept replacement of the **two Python source files**.
4. In the VS Code PowerShell terminal run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m py_compile gateway\app.py gateway\job_store.py gateway\diagnostics.py
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Assuming the same original 30 tests and no other test changes: **39 tests** (30 existing + 9 new).

5. Restart through Pinokio after tests pass. No new dependencies or database migration.

## API: bearer token required on every endpoint

- `GET /dashboard/api/diagnostics/summary?range=7d`
  - Actual failure counts, image/text breakdown, error-type and model distributions.
- `GET /dashboard/api/diagnostics?range=7d&limit=50&task=image&error_type=codex_cli_argument_error&search=--image`
  - Safe, bounded list of failures and opaque `next_cursor` for pagination.
  - Optional `operation=chat|generate|structured|research|image`, `model`, `task`, `error_type`, `search`.
  - Reuse the same `cursor` with the same filters and time range for the next page.
- `GET /dashboard/api/diagnostics/{job_id}`
  - One failed job, latest 500 events, up to 10 reference IDs/thumbnails, redacted diagnostic bundle.
- `GET /dashboard/api/diagnostics/{job_id}/bundle`
  - Only the allowlisted redacted JSON bundle for copying to a support chat.

`range` defaults to **7d**, consistent with the existing analytics resolver. Older failures may require `30d` or `custom` with `start` and `end`. As usual, absent data is not invented. The existing `/v1/*` API contracts are unchanged. The Diagnostics UI is a separate next module (6D), so the placeholder page remains unchanged until then.

**Privacy:** No secret token is included in links or query params. Backend summary/list/detail re-redact data that could have originated before schema v5. Diagnostic previews are only partial: they cannot guarantee removal of every arbitrary secret embedded in freeform prose; don't paste private prompts into diagnostic records.

**Local test note:** This ZIP was tested against the supplied Module 6B files and their 6A/6B tests using lightweight stand-ins for three existing gateway modules that were not uploaded. All 27 available focused tests passed locally (11+7+9). Your full 39-test suite is the installation acceptance test.
