# Module 8D — Production-Readiness Audit

**Baseline:** Module 8C, validated by the user with 143 passing Windows tests. This assessment is based on the reconstructed 8C source, the real uploaded artifact store, local focused/regression tests, and the user's reported Windows results. It is **not** a penetration test, independent Codex API compatibility certification, or a replacement for live n8n smoke tests.

## Verified and preserved

| Area | Evidence and result |
|---|---|
| n8n endpoints | Existing `/v1` contract tests remain in the regression suite. 8D does not modify any `/v1` route, response payload, model selection, worker settings, or CLI invocation. |
| Authentication | Generation, dashboard data, settings, retention, privacy and SSE routers declare the same bearer-token dependency. The HTML/CSS/JS shell itself is public, but dashboard data requires authentication. |
| Live dashboard | Bounded event bus; metadata-only event payloads; browser fetch with bearer header instead of token-in-URL; bounded SSE parser; fallback polling and periodic authoritative API reconciliation. The 13 Node runtime tests remain green. |
| Privacy | Prompt/output retention off by default; SQLite v6 separates opt-in text from telemetry; diagnostics use redacted previews. Purging SQLite rows is **not** guaranteed secure erasure of WAL files/backups or files outside the gateway. |
| Retention | Automatic cleanup off by default; manual cleanup requires confirmation; only terminal job artifacts are selected by expiry/quota; orphan removal requires explicit opt-in and excludes unknown file names and symlinks. |
| Safe path handling | Artifact paths must resolve to the opaque artifact root for retention. The 8D fix addresses original file timestamps incorrectly aging fresh artifact copies. |
| Tests | User: 143 passing at 8C. Local release candidate: 137 Python tests (original phase-one tests require user's non-stub quota-monitor module) and 13 Node live/sync tests. Six new 8D regression tests bring the **expected Windows total to 149**. |

## 8D correction: source-file timestamps versus orphan retention

`ArtifactStore.create()` previously used `shutil.copy2()`. That copied the source file's modification timestamp. An image downloaded or copied from an archive with a modification time older than 24 hours could therefore be classified as an *old orphan* before its SQLite artifact row was saved. During explicitly requested orphan cleanup, the newly created file could be selected for deletion. This is not a claim that a past user's file was deleted; the code path was reproduced in disposable tests.

8D uses `shutil.copyfile()` for the managed copy instead. This copies the file contents without importing historical source timestamps. The source file remains unchanged. Thumbnail handling, artifact IDs, bytes, paths, SQLite schema and public responses remain unchanged. Three old-behavior regression tests failed before this patch and passed after it.

## Boundaries and outstanding live acceptance checks

1. **Codex-generated originals are outside the Gallery cap.** The runner discovers output under `CODEX_HOME/generated_images/<thread-id>`, whereas the configured artifact cap and the 7C cleanup apply to `.gateway-data/artifacts`. The generated original is not the temporary reference work directory. This release deliberately does not delete files inside Codex's home. Check both locations when diagnosing disk usage and do not advertise the Gallery limit as a total disk cap.
2. **Browser acceptance still matters.** Verify the Live badge moves to Live, a real n8n job changes Running/Queued/Jobs, an image request returns actual bytes, Gallery can open the artifact, the live inspector reports a new event, and fallback polling operates on disconnect. The user's 143 passing tests do not prove these interactions with real Codex or the desktop browser.
3. **Backups are operational, not covered by unit tests.** Keep `.gateway-data` and `.gateway-settings.json` in a recoverable backup. Do not perform cleanup or privacy purge just to test the dashboard. A restore drill is recommended before enabling automatic cleanup.
4. **Development-harness limitation.** The local module's quota-monitor file is a placeholder. User's full Windows run, not this local suite, is authoritative for the original quota-monitor/phase-one tests.
5. **Retention can never be perfectly transactional across filesystem and SQLite.** File removal and database deletion are serialized and failures are handled, but an OS interruption between unlink and commit can leave a metadata record requiring later cleanup. The storage preview is advisory; the run re-plans immediately before deletion.

## Release verdict / operational gate

The 8D source correction is narrow and regression-tested. **Do not treat the gateway as fully production-accepted until the Windows test count, one real text request, one real image request, live updating, and a verified backup are confirmed.** The code does not add automated deletion or a new schema migration.
