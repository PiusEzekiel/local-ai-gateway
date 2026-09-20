# Local AI Gateway — Module 6B: persistent Codex failure diagnostics

## Files in this package

- `app/gateway/app.py` — existing gateway plus 6B error context and reference-download telemetry.
- `app/gateway/job_store.py` — additive SQLite schema v4 → v5 migration and sanitized persistence.
- `app/tests/test_module_6b.py` — focused tests; no live Codex or Google Drive needed.

Requires `app/gateway/diagnostics.py` from the already installed Module 6A. This ZIP deliberately does not replace that file.

## Install

1. Stop the gateway in Pinokio and finish any running n8n calls.
2. Back up existing `app/gateway/app.py`, `app/gateway/job_store.py` and, with the gateway stopped, `app/.gateway-data/gateway.sqlite3`.
3. Extract this ZIP at `C:\pinokio\api\local-ai-gateway`, preserving directory structure.
4. In VS Code Terminal, from `C:\pinokio\api\local-ai-gateway\app`:

```powershell
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m py_compile gateway\app.py gateway\job_store.py gateway\diagnostics.py
.\.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_module_6b.py -q -p no:cacheprovider --basetemp "$TestBase"
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

The `--basetemp` directory must be dedicated to pytest, since pytest owns/cleans its contents. `-p no:cacheprovider` avoids the previously observed `.pytest_cache` permission warning. If `$env:LOCALAPPDATA` is not writable, choose a new **dedicated writable** test-only folder (e.g. under Documents) instead.

5. Restart in Pinokio. The gateway migrates SQLite automatically on startup. Check that existing Gallery, quota and image APIs still work.

## Result

On failure, each job can retain:

- `exit_code` — numeric Codex process code when relevant (null for pre-Codex failures).
- `diagnostic_preview` — redacted, bounded CLI stderr and explicit Codex error events.
- `last_successful_stage` — last *confirmed* stage, not merely a started stage.
- `references_downloaded` — validated successful downloads, independent of gallery storage success.

These are backend fields for the dedicated Diagnostics API/UI in Modules 6C/6D; this module intentionally makes no frontend changes.

**Testing note:** focused module tests run with a fake Codex runner/process; production Codex and Google Drive integration must still be smoke-tested on your Windows installation.
