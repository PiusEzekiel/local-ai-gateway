# Module 7B — Privacy and Opt-in Output Retention

**Baseline:** Module 7A (79 tests verified on user's Windows installation). This is an incremental patch, NOT a full gateway repository. It contains no tokens, personal settings, telemetry databases, or generated images.

## What changes

- Default `store_prompts=false`, `store_outputs=false`, `store_diagnostics=true` (preserves existing Diagnostics).
- Three validated, restart-required settings appear in authenticated `GET/PATCH /dashboard/api/settings`; matching optional environment overrides take precedence after restart.
- SQLite schema moves **v5 → v6**, adding `job_payloads` for opt-in **plaintext** user-request prompt and completed text output. Each is bounded to 16,000 and 32,000 characters, respectively, with explicit truncation flags.
- Image requests can retain their prompt if opted in, but generated images and reference thumbnails remain in the **separate artifact store**, controlled later in Module 7C. Reference URLs are not copied into `job_payloads`.
- `store_diagnostics=false` prevents future CLI preview text from being persisted or kept in the in-memory job cache, after a restart. Error type, generic safe message, exit code, reference counts and execution stages remain available. It does NOT change Codex/gateway log file retention.
- Existing /v1/* and dashboard list/analytics response bodies remain unchanged; retained content is only available from its explicit, authenticated detail endpoint.
- Three new authenticated endpoints:
  - `GET /dashboard/api/jobs/{job_id}/content`: opt-in raw text for ONE job (Cache-Control: no-store). Returns null content if none was captured.
  - `GET /dashboard/api/privacy/storage`: counts only, never text.
  - `POST /dashboard/api/privacy/purge` with `{"confirmation":"DELETE_RETAINED_DATA","scope":"text"|"diagnostics"|"both"}`: explicitly clears opted-in text and/or preview text while keeping job metadata, tokens, images and stats. The action does not automatically disable future retention.
- No Settings UI yet: the placeholder remains until Module 7D. Module 7C adds artifact/history retention, storage limits and automatic cleanup.

**Privacy scope:** Prompts and outputs saved with opt-in are PLAINTEXT in the local SQLite database; they are NOT encrypted at rest. Do not opt in on sensitive projects without considering local-disk protection. This module does not change Codex's temporary output files, Codex HOME-generated images, the gateway's operational logs, reference-artifact storage, copies/backups, or SQLite WAL forensic remnants. Purge is a logical SQL delete, NOT a certified secure erase. A setting switched OFF does NOT retroactively purge previously saved content; use the explicit purge API as needed.

## Files changed

Existing:
- `app/gateway/app.py`
- `app/gateway/config.py`
- `app/gateway/settings_manager.py`
- `app/gateway/settings_routes.py`
- `app/gateway/generation_state.py`
- `app/gateway/text_routes.py`
- `app/gateway/image_routes.py`
- `app/gateway/job_history.py`
- `app/gateway/job_store.py`
- `app/gateway/dashboard_routes.py`
- `app/tests/test_module_6b.py`: schema-version expectations updated v5 → v6, prior tests remain intact.
- `app/tests/test_refactor_6r2.py`: exact HTTP route inventory includes the 3 new endpoints.

New:
- `app/tests/test_module_7b.py`: 12 focused privacy/migration/compatibility tests.

## Install safely in VS Code / Windows

1. Wait until active n8n calls complete. STOP the gateway in Pinokio. The database must NOT be copied while the gateway is writing to it.
2. Back up the entire `app/gateway` source and `app/.gateway-data` directory, including `gateway.sqlite3`, `-wal`, and `-shm` if present, and your local `.gateway-settings.json` (do not upload tokens/settings here).
3. Extract this ZIP **into** `C:\pinokio\api\local-ai-gateway` and replace files, retaining its `app/gateway/` and `app/tests/` folder hierarchy.
4. From PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected on user's Windows install: 91 tests** (= previously verified 79 + 12 new tests). Local harness verified 79 focused tests, excluding the 12 `test_phase1.py` tests: the uploaded partial harness includes placeholder `quota_monitor.py` and `artifact_store.py`, not their real installed versions. The full Windows run is the authoritative acceptance check.

5. Restart Pinokio. Overview, gallery, quota, Diagnostics, and original n8n APIs should function normally. Defaults ensure no new user prompt/output bodies are retained.

## How to opt in later

`PATCH /dashboard/api/settings` using the same existing Bearer authentication:

```json
{"store_prompts":true,"store_outputs":true}
```

After the PATCH the response has `pending_restart=true`. Restart in Pinokio to activate. This is intentionally not a live switch.

## Rollback

**Schema v6 makes this a database migration.** Stop the gateway. Restore BOTH the pre-7B source/test files AND the pre-7B database backup. Earlier v5 code will reject a v6 database. Restore local `.gateway-settings.json` if you edited it. Do not just replace `app.py` or remove the new test. Keep the backup private.
