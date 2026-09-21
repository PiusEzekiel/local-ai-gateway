# Module 9A.2 — Atomic Artifact Publication & Honest Image Failures

**Baseline:** Module 9A.1 installed and Windows-verified. This ZIP is intentionally an incremental patch, not a full gateway replacement.

## Contents

- `app/gateway/artifact_store.py` — stage/copy/verify before atomic file publication; optional thumbnail; one managed-path validator for serving, deletion and retention.
- `app/gateway/image_routes.py` — do not return HTTP 200 until the original artifact and its SQLite record are saved; compensate failed DB registrations for output and reference artifacts.
- `app/gateway/job_store.py` — `rollback_artifact_record` removes a partially registered row and clears any related job/reference pointers transactionally.
- `app/tests/test_module_9a2.py` — 13 isolated, no-Codex/no-network tests using temporary image files and SQLite databases.

**No `.gateway-data`, `.gateway-token`, `.gateway-settings.json`, quota-monitor stand-in, executable, or dashboard assets are included. No SQLite schema migration or changes to successful image response format.**

## Intent and limitations

- Successful `/v1/images/generations` responses remain binary image responses with `X-Request-ID`. A failed original-file copy, image validation or SQLite artifact insert now produces a stable HTTP **503** JSON error with `error.type = "artifact_persist_failed"`; the job appears as **failed** in Jobs and Diagnostics, and previously recorded token usage is retained.
- Reference-image gallery persistence stays *best effort*: a successfully downloaded reference counts as downloaded even when its optional gallery copy fails; its unregistered copy is removed when safe.
- The original image is copied to a private staging file within the artifact directory, verified, then published through `os.replace`. Thumbnails are also staged and individually atomically published; thumbnail failure does not fail the verified original. Partial staging files are removed on handled failures. The original Codex output is not deleted or altered.
- **Atomicity boundary:** rename of each file is atomic on the artifact filesystem, not a distributed transaction with SQLite. We compensate a failed DB insert by rolling back any inserted artifact record and deleting managed files. If the database rollback itself fails, files are deliberately left intact for later reconciliation rather than creating a known broken Gallery URL. A process crash between file publication and SQLite registration can leave orphan files; existing opt-in orphan cleanup can later reclaim managed files older than 24 hours. A crash during a stage copy can leave a hidden `.staging` file: the current retention system does not automatically delete such files; inspect manually if disk capacity becomes an issue.
- `resolve`/normal `delete` now share the established opaque-path and symlink protections from retention. They will refuse unsafe paths rather than serving/deleting them.

## Git and rollback

Keep your `v0.9-control-plane-complete` tag and current 9A.1 branch/commit. Before installing:

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git add -A
git commit -m "Hardening 9A.1 - validated quota settings"
# Optional: dedicate a branch to this patch
git switch -c hardening/9a-2-artifacts
```

If `git status --short` is already empty, skip the commit. If the branch name exists, do not recreate it; use `git switch hardening/9a-2-artifacts` only if that is the branch you intend to update.

## Install

1. Let active n8n jobs finish. Stop the gateway through Pinokio.
2. Back up `app/gateway` and the whole `app/.gateway-data` directory, plus `app/.gateway-settings.json` if present.
3. Extract the ZIP **into `C:\pinokio\api\local-ai-gateway`**, preserving `app/` and overwriting only the files present in the ZIP.
4. From your project root, inspect `git diff --stat`. Only three gateway source files and one new test should change.
5. Run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected full Windows result: 185 passed**, if the previously verified Module 9A.1 suite had 172 tests and is unchanged (+13 tests). The local available subset passed **164 tests** (previous subset 151 + 13). The local development harness uses placeholder `quota_monitor.py` and `analytics.py` that are **not shipped** in this ZIP; your installed versions remain authoritative.

6. Restart Pinokio. Smoke-check Overview/Live, one real n8n text request, then one real image request *with a reference* (when convenient). Confirm the returned image opens and its Gallery item and Jobs record are present. Open Diagnostics and confirm no unexpected new failure.
7. No need to deliberately fill the disk or run destructive cleanup; all storage-failure tests are isolated to temporary test directories.

If tests fail, **do not restart with the patch**. Restore the three files from the previous commit / backup after ensuring the gateway is stopped. Do not revert `.gateway-data` unless you have a specific reason and have made a fresh independent backup.
