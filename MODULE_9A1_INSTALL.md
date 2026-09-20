# Module 9A.1 — Quota Settings Contract Safety

**Baseline:** your installed, 149-test Module 8D release. This ZIP contains only three replacement Python source files, a new test file, and this guide. It does not contain `quota_monitor.py`, `artifact_store.py`, SQLite databases, tokens, or any dashboard assets.

## What changed

- Quota poll settings accepted by Settings now match the real `QuotaMonitor` constructor: integer **30–60 seconds inclusive**, default 45.
- Settings API metadata exposes those bounds automatically so the Settings page uses matching min/max controls.
- `Settings.from_env()` validates `AI_GATEWAY_QUOTA_POLL_SECONDS` before runtime initialization. Invalid explicitly configured environment values fail with a clear error rather than only failing inside the monitor.
- `create_app()` checks even directly injected `Settings`, before any data stores or workers are created.
- Older saved JSON values such as 15 or 3600 are **ignored at startup in favor of the default** (unless an environment override applies); they remain in the existing JSON until you explicitly correct them, so the patch never mutates your settings without consent. You can save a valid value in Settings to repair them.
- The running quota monitor is not recreated or modified by a settings PATCH; a restart remains required.

**This release deliberately does not edit the actual quota monitor implementation** because the production implementation was not included in the uploaded source. It uses the constructor range independently observed by Copilot and includes a Windows regression test that calls the **real, installed QuotaMonitor constructor** for each accepted UI value without starting Codex or making a quota request. If the monitor implementation differs, this test catches it.

## Recommended Git branch

First check your working tree; commit or stash unrelated edits before extracting the ZIP:

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git switch -c hardening/9a-1-quota-settings
```

Do not delete or modify your existing `v0.9-control-plane-complete` tag.

## Installation

1. Allow any active n8n jobs to finish, then stop the gateway in Pinokio.
2. Back up your `app/gateway` source folder and `app/.gateway-settings.json`. The archive **does not** overwrite the settings JSON or database.
3. Extract the ZIP into `C:\pinokio\api\local-ai-gateway`, preserving its `app/` folder structure. Replace just the included files.
4. In VS Code / PowerShell, run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected full Windows result: 172 passed** if your existing 149-test suite is unchanged (23 new parametrized tests). The development copy passed 151 local tests, including all 23 new cases. That local harness uses a strict 30–60-second quota-monitor stand-in because your actual `quota_monitor.py` is available only on your Windows installation; the delivered regression test uses the real implementation there.

5. Restart the gateway through Pinokio. Check Overview, Live status, and Settings → Quota, where the interval must show minimum 30 / maximum 60. Save `30`, `45`, or `60`; ensure the UI marks it pending restart when it differs from the active value. A restart applies it.
6. Confirm one real text/image request through n8n after restart, when convenient.

**No need to run storage cleanup** to verify this patch.

## After passing tests

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git add app/gateway/config.py app/gateway/settings_manager.py app/gateway/app.py app/tests/test_module_9a1.py
git commit -m "Hardening 9A.1: align quota settings with runtime"
git push -u origin hardening/9a-1-quota-settings
```

Do not merge into `main` until the Windows tests, restart, and UI checks pass. Your existing private repo and tagged release remain your rollback point.

## Rollback

With Pinokio stopped, restore the three Python source files from the 8D release/tag (or your backup) and remove `app/tests/test_module_9a1.py`. Do not roll back `.gateway-data` or overwrite `.gateway-settings.json` with unrelated historical data. Avoid `git reset --hard` without first checking uncommitted work.
