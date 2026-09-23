# Module 9C.4 — Test Isolation & Storage Hygiene

## Purpose

This is a **test-harness-only** hardening release. It prevents pytest from touching the installed Gateway's live `.gateway-data` directory or `.gateway-settings.json`, including during **test collection** when `gateway.app` may be imported before ordinary fixtures run.

The investigation also identified the likely source of the 50 tiny unregistered files seen in Settings → Storage: Module 9C.3's regression fixture creates exactly **25 × 161-byte originals and 25 × 80-byte thumbnails**. Those dimensions match the live inventory exactly. The new harness prevents future test runs from writing those fixture files into production storage.

## Files changed

- `app/tests/conftest.py` — creates a disposable storage sandbox before test collection and refuses live data/settings paths during pytest.
- `app/tests/test_module_9c4.py` — ten regression tests for collection-time isolation, constructor guards, settings-write guards, symlink resolution, `--basetemp` safety, and disposable app instances.

**No production Gateway code is changed.** No n8n endpoints, dashboard code, SQLite schema, settings, or artifacts are modified.

## Install

1. Commit the verified 9C.3 checkpoint.
2. You do **not** need to stop Pinokio for this test-only release, although doing so is harmless.
3. Extract the ZIP into:

   `C:\pinokio\api\local-ai-gateway`

4. From `C:\pinokio\api\local-ai-gateway\app`, run:

```powershell
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null

.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected on the verified Windows installation: **311 passed** (301 previous + 10 new tests).

## Optional proof that tests do not touch live artifacts

Before running pytest:

```powershell
$ArtifactDir = '.\.gateway-data\artifacts'
$Before = Get-ChildItem $ArtifactDir -File -ErrorAction SilentlyContinue |
  Sort-Object Name |
  ForEach-Object { "{0}|{1}|{2}" -f $_.Name,$_.Length,$_.LastWriteTimeUtc.Ticks }
```

Run pytest, then:

```powershell
$After = Get-ChildItem $ArtifactDir -File -ErrorAction SilentlyContinue |
  Sort-Object Name |
  ForEach-Object { "{0}|{1}|{2}" -f $_.Name,$_.Length,$_.LastWriteTimeUtc.Ticks }

Compare-Object $Before $After
```

No output means the live artifact directory was unchanged by the test run.

## Existing 50 tiny files

This module intentionally does **not** delete them. They occupy only ~5.88 KiB and are not registered artifacts. Once they cross the existing 24-hour orphan threshold, the Gateway's preview-first cleanup flow can report them as eligible; inspect the preview before deleting anything.

## Rollback

Delete `app/tests/conftest.py` and `app/tests/test_module_9c4.py`, or restore those paths from your previous Git commit. Since this release changes tests only, rollback does not affect runtime data.
