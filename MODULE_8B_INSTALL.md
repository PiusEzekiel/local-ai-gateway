# Module 8B — Live Dashboard (Authenticated SSE Client)

## Scope and safety

Frontend-only release based on verified 8A. No Python backend changes, SQLite migration,
settings migration, or change to the `/v1` endpoints used by n8n. A single `fetch()` SSE
client makes a bearer-authenticated request to `/dashboard/api/events` using the shared
`api.js` token. **Do not use native `EventSource` or put a token into the URL.**

The new `Live` badge beside gateway health refers to the dashboard transport, NOT the
health of the Codex CLI or quota. The main Health indicator still reflects API snapshots.

- Connect/reconnect: the backend emits `ready` with `snapshot_required`; fetch normal
  Overview/Jobs snapshots and update the visible section.
- Job events: coalesce burst updates (~180ms) and refresh Overview/Jobs; refresh visible
  Gallery after completion; visible Diagnostics after failures; Usage/Performance after
  completed jobs or usage changes. Preserve job-feed scroll and user-selected filters.
- Worker, quota, and model events: refetch Overview summary, not prompts or outputs.
- `resync_required`: do not replay or infer missed stages; fetch authoritative snapshots.
- Transport unavailable / subscriber limit reached: reconnect with exponential backoff
  capped at 30 seconds, and use a **15-second snapshot polling fallback**. While live,
  reconcile Overview/Jobs every 60 seconds.
- On hidden tabs, stop the stream to free server subscriber slots; resume and reconcile
  when the tab becomes visible.
- On HTTP 401, stop reconnecting and ask for a valid gateway token.
- Events contain metadata only. No reference images, prompts, text, stderr, credentials,
  or token usage details flow through SSE; full details still come from authenticated GETs.

## Installation

1. Finish current n8n jobs, stop the gateway in Pinokio.
2. Back up `C:\pinokio\api\local-ai-gateway\app\gateway` (frontend only is changed).
3. Extract `local_ai_gateway_module_8b.zip` to `C:\pinokio\api\local-ai-gateway`.
   Keep the contained `app\gateway\dashboard` and `app\tests` paths. Replace supplied
   files. **Do not extract into the `app` subdirectory** or create `app\app`.
4. VS Code PowerShell terminal:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected **134 passed**: previously confirmed 125 + nine new `test_module_8b.py`
checks. Existing FastAPI/Starlette dependency warnings may remain.

Optional separate JavaScript parser/stream regression test if your installed Node.js
supports ESM test runner:

```powershell
node --experimental-default-type=module --test tests\frontend_live.test.mjs
```

5. Restart through Pinokio; hard-refresh dashboard with **Ctrl+Shift+R** (HTML and the
   entire import graph are versioned `8b-20260920`).

## Windows browser smoke test

1. Enter your token or use the saved tab session. The small badge beside **Healthy**
   should become **Live**. It must not remain **Connecting...** indefinitely.
2. In DevTools > Network, locate `/dashboard/api/events`; it should be an authenticated,
   pending `text/event-stream` response, **not** a request with a token query parameter.
3. Run an ordinary n8n image or text job. Running/Queued numbers and the Overview feed
   should update while the job progresses. Completed images should appear in Gallery.
4. In DevTools > Network, set browser to Offline briefly (or stop Pinokio): badge
   becomes **Polling fallback** while retries occur. Restore connectivity; badge should
   return to **Live** and jobs/summary should recover without refreshing manually.
5. Open Diagnostics, Settings, Gallery, Usage, Performance. The existing page layout,
   manual Refresh, and settings unsaved-changes warning must still work. Do NOT run an
   actual retention cleanup to test live notifications.
6. Switch to another tab for a few seconds, then back. The badge should reconnect and
   the recent job snapshot should catch up.

If live transport cannot connect but normal GETs work, the gateway continues to operate
and the dashboard falls back to periodic snapshots. Inspect HTTP status for the
`/dashboard/api/events` request and the **first red error in DevTools Console**.
Never paste your bearer token, local `.gateway-settings.json`, or private prompts.

## Rollback

Stop Pinokio, restore the frontend files from your backup, restart. The 8A backend event
bus can remain installed safely; it is additive and unused by the previous frontend.
