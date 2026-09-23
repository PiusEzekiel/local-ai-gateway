# Module 8A — Backend live-event bus + authenticated SSE

## Scope

This release is **backend-only**. It adds a per-app, bounded, best-effort event bus and
`GET /dashboard/api/events`. Existing `/v1` request and response formats, image FileResponse,
SQLite schema v6, dashboard polling, settings, and retention policies are unchanged.
Module 8B will replace selected dashboard polls with a reconnecting `fetch()` SSE client.

## Install

1. Let n8n jobs finish, then stop the gateway in Pinokio.
2. Back up `C:\pinokio\api\local-ai-gateway\app\gateway` and, as general safety practice,
   `.gateway-settings.json` / `.gateway-data` if present. No DB migration is required.
3. Extract `local_ai_gateway_module_8a.zip` to `C:\pinokio\api\local-ai-gateway`, preserving the `app\gateway` and `app\tests` paths. Replace only the supplied files.
4. In PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected on the full Windows suite: **125 passed** (111 previous + 14 Module 8A tests).
The development copy includes only a placeholder for your actual `quota_monitor.py`,
so locally it ran 113 tests while excluding the original phase-one test file.

5. Restart through Pinokio; check Overview, Settings, Gallery, a text request,
   and an image request. Existing dashboard polling remains unchanged.

## Optional live stream check

Use the gateway's actual URL/port, not a token in the URL. For example:

```powershell
$GatewayUrl = 'http://127.0.0.1:YOUR_PORT'
# If AI_GATEWAY_TOKEN is present in this shell:
curl.exe -N -H "Authorization: Bearer $env:AI_GATEWAY_TOKEN" "$GatewayUrl/dashboard/api/events"
```

The first messages should include `: connected`, `retry: 3000`, and `event: ready`.
Run a normal n8n generation job in another window to see `job.created`, `job.updated`,
`job.usage`, and `workers.changed` notifications. `Ctrl+C` stops the stream.
If you do not have the token in that shell, use the token in your own local gateway
configuration without pasting it into chat.

## Behavior, safety, and limits

- Existing Bearer header is required; token query parameters are not accepted.
- `text/event-stream`, no cache, keepalive comments every ~15 seconds.
- Per-app subscriber cap: 16; queue cap per subscriber: 64. A slow subscriber
  receives `resync_required` and must GET normal API snapshots.
- No event replay; `ready` indicates a fresh snapshot is required, including reconnect.
- Only allowlisted job metadata, status, stage, model, anonymous job UUID, worker
  counts, and simple change notifications. No prompts, generated text, raw CLI
  stderr, auth secrets, source paths, or retained payloads.
- Quota watcher reads the monitor's **cached** snapshot every 20s; it does not
  make extra Codex/app-server quota queries.
- The frontend remains on normal polling until 8B. Browser `EventSource` is not
  suitable for this authenticated endpoint because it cannot set Bearer headers;
  8B will use streaming `fetch()` with AbortController and reconnection.

## Rollback

Stop Pinokio, restore the backed-up `app\gateway` files and prior tests, restart.
No DB rollback or settings rollback is required.
