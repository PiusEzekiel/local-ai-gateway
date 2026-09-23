# Module 8D — Final audit and artifact timestamp safeguard

This small release is based on Module 8C (143 passing tests on Windows). It ships:

- `app/gateway/artifact_store.py`: `copy2` -> `copyfile`, so newly imported images never inherit a stale source mtime that could defeat the orphan grace period.
- `app/tests/test_module_8d.py`: six regression tests, including a real image-runner path check with a fake Codex subprocess (no actual Codex invocation or API charges).
- `PRODUCTION_READINESS_AUDIT.md`: verified safeguards, limitations and live acceptance checklist.

**No new settings, automatic cleanup, schema migration, n8n response change or frontend asset changes.**

## Install

1. Let existing n8n requests finish, then stop the gateway in Pinokio.
2. Back up `C:\pinokio\api\local-ai-gateway\app\gateway`, `.gateway-data`, and `.gateway-settings.json` outside the project folder.
3. Extract the ZIP at `C:\pinokio\api\local-ai-gateway` preserving the `app\gateway` and `app\tests` folder structure. Do not extract into `app` itself.
4. Run in PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected: 149 passed** (143 at 8C + six new tests). Your existing two Starlette/AnyIO deprecation warnings may remain.

Optional JS regression verification (if Node is on PATH):

```powershell
node --experimental-default-type=module --test tests\frontend_live.test.mjs tests\frontend_sync.test.mjs
```

**Expected: 13 passing Node tests.**

5. Restart in Pinokio. Test a normal text request from n8n and a normal image request with the usual reference images. Confirm the image response has usable bytes, appears in the Gallery, and that Running/Queued plus Live activity change without manually refreshing.
6. Open Settings -> Storage and use **Preview only**. Do not run a real cleanup to test this release.
7. Take a screenshot of the Live status inspector and send the Windows test report and any browser errors.

## Rollback

Stop the gateway and restore the backed-up `app\gateway\artifact_store.py`. The added test file can remain (it would fail against the old behavior) or be removed. No database rollback is required and no existing artifact is modified by installation.
