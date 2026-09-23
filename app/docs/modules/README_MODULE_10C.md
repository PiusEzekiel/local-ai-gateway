# Module 10C — Unified, opt-in episode sessions

**Install the gateway ZIP only into** `C:\pinokio\api\local-ai-gateway`.
**Import the three separate n8n JSONs into n8n**; do not extract their ZIP into the gateway installation.

## What it does

- A request may supply an optional `episode_title` to `/v1/chat/completions`, `/v1/generate`, `/v1/research`, or `/v1/images/generations`.
- With **Settings → Execution → Persistent episode sessions** enabled and the gateway restarted, the first request for a title creates a resumable Codex thread, and later text and image calls for that same title resume that exact UUID. Whitespace and case variations match. Different episode titles remain isolated.
- The original ephemeral path remains in use if the setting is off **or** `episode_title` is omitted.
- Title/session ID/aggregate token usage persist in `app/.gateway-data/episode-sessions.sqlite3` (or under `AI_GATEWAY_DATA_DIR`). Codex history itself remains under `CODEX_HOME`. Neither the reference-image cache nor Gallery retention is changed.
- An authenticated, read-only `GET /dashboard/api/episode-sessions` provides titles, status, turn counts, input/output tokens, cached-input tokens and `cached_usage_complete`. This is an API view, not a new Settings navigation page. `POST /dashboard/api/episode-sessions/reset` with `{"episode_title":"...","confirmation":"RESET_EPISODE_SESSION"}` unlinks that title from its Codex session without deleting the underlying Codex history.
- Within one gateway process, jobs for the same title are serialized across chat, text, and image routes. Different titles may run concurrently if the gateway worker setting allows it. **Keep one Uvicorn worker**; do not use multiprocess mode until cross-process locking is implemented.
- A failed/interrupted resumed Codex turn makes its registry row `uncertain`; subsequent requests for that title start a new Codex session rather than silently resuming a possibly partial conversation. The old rollout remains on disk.
- The existing full-URL reference cache and attached-reference behaviour are unchanged. Each image request still attaches its selected reference images; this module does not silently omit references.

## Local CLI compatibility limitation

The supplied final-source ZIP contained `AGENTS.md`, `text_routes.py` and `schema_validation.py` **but no CLI help/version snapshots**. The bundled change has a **quota-free startup check** of your installed `codex exec resume --help` when the feature is enabled. It requires the installed CLI to advertise `--image`. If it does not, the gateway refuses to activate the experimental session mode instead of silently generating in a different session. This is not a substitute for the live two-turn test; `--output-schema`, image generation and resume behaviour must be verified against your installed CLI.

If startup fails, capture your actual CLI capabilities in the Pinokio shell (using the exact configured Codex executable):

```powershell
codex --version
codex exec --help
codex exec resume --help
```

No model call is made by these commands.

## Installation (stop the gateway first)

1. Back up the listed changed files (or commit your current repository state).
2. Extract `gateway-module-10c-episode-sessions-extract-ready.zip` directly into `C:\pinokio\api\local-ai-gateway`, overwriting listed files. No installer script.
3. From PowerShell run the full regression suite **before enabling the feature**:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

4. Restart the gateway. In Settings → Execution, set **Persistent episode sessions** to Enabled, save and restart again.
5. Back up the three existing TEST workflows in n8n. Import the three updated JSONs from the other ZIP into n8n, updating the original workflows or making sure the parent points at the imported child workflow IDs. Do not enable a production parent flow until both child workflows are mapped correctly.
6. Run only a small controlled test: one episode chat request followed by one image request (same title), then inspect `GET /dashboard/api/episode-sessions`. The second turn must share the session ID and the row must show two turns. The child pinned test data does **not** contain `episode_title`, so manually executing those pinned children alone will remain ephemeral unless you add a title to the pinned item.

## What to measure

`cached_input_tokens` is reported only when Codex exposes that field (or `input_token_details.cached_input_tokens`). **Check `cached_usage_complete`:** if false, a displayed zero or sum must not be interpreted as zero model-side caching. Compare both cached and total input tokens, scene quality, wall time and quota usage against an equivalent independent-session run. Prompt history can grow and make session reuse *more expensive*; no token savings are guaranteed.

## Privacy and safety

Enabling persistent Codex sessions saves model conversation history in `CODEX_HOME` even if the gateway's **Retain prompt text** switch is off. Episode titles are plaintext in the local episode-session SQLite registry. Reset only removes the gateway's title-to-thread link; it is **not a secure deletion** of Codex history, database WAL, logs, images, or stored request content. Signed Google Drive URLs are not added to the session registry.

## Changes and test scope

- Source: contracts, config, settings manager, Settings descriptions, runner, generation context/router, text and image routes, app composition, **new** `episode_sessions.py`.
- Tests: **new** `test_module_10c.py`; the exact dashboard route inventory test in `test_refactor_6r2.py` was updated to include the two authenticated session endpoints.
- No launcher, firewall, LAN binding, auth-token management, model selection, cache identity, Gallery retention or n8n response formats were changed. Existing request bodies work without `episode_title`.
- The isolated session/CLI unit subset was tested without paid calls. The complete installed Windows test suite and a live Codex resume/image test **must still be run locally**; they were not available in the source-only ZIP environment.

## Rollback

Disable Persistent episode sessions and restart, or restore your backed-up original gateway files and n8n workflows. Don't manually delete `episode-sessions.sqlite3` or Codex history during rollback; keep that data intact until you intentionally decide on its retention.
