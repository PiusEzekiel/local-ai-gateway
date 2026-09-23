# Module 8C — Live-state UX, Snapshot Freshness, Accessibility

## Contents and safety

**Frontend-only** patch based on verified Module 8B (134 tests on Windows). No backend Python
changes, API/schema changes, data migration, retention cleanup or new browser storage.
A dedicated `sync_status.js` keeps the Live event stream status separate from the gateway's
API health and records **timestamps / allowlisted event names only**. No bearer token,
prompts, CLI output or SSE payload data go into the sync inspector.

- Click the **Live / Polling fallback / Paused** badge to see the last successful API
  snapshot, last received metadata event, and the current stream state.
- **Refresh dashboard data** performs normal authenticated snapshots; opening the badge
  itself never makes a request or changes settings.
- The badge warns **Live · stale data** if the SSE stream is connected but the latest
  successful API summary is more than two minutes old. An API failure is shown separately
  from the transport state; old metrics remain visible.
- The new sync panel closes on Escape, outside click or tab hide. Sidebar navigation
  communicates the current page to screen readers. A late response from an older
  Overview request cannot overwrite a newer API snapshot.
- Existing 15-second offline polling, 60-second live reconciliation, throttled SSE
  refetches, Settings unsaved-changes protection, and Diagnostics continue as before.

## Installation

1. Finish active n8n jobs and stop the gateway through Pinokio.
2. Back up `C:\pinokio\api\local-ai-gateway\app\gateway`. Do not remove
   `.gateway-data` or `.gateway-settings.json`.
3. Extract `local_ai_gateway_module_8c.zip` at the project root
   `C:\pinokio\api\local-ai-gateway`, preserving `app\gateway` and
   `app\tests`. **Do not extract into `app`, creating `app\app`.**
4. In the VS Code PowerShell terminal:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected: 143 passed** (previously 134 + nine new `test_module_8c.py` tests).
Our available Linux test subset, which lacks the original quota monitor implementation,
passed 131 tests (122 previous subset + nine new). The two existing Starlette/AnyIO
warnings may remain on Windows.

Optional Node runtime verification (13 tests):

```powershell
node --experimental-default-type=module --test tests\frontend_live.test.mjs tests\frontend_sync.test.mjs
```

5. Restart gateway through Pinokio and press **Ctrl+Shift+R** to load the consistently
   versioned `8c-20260920` HTML/JS/CSS asset graph.

## Browser verification

- Confirm **Healthy** (gateway API) and **Live** (event transport) appear independently.
  Click Live and check all three sync indicators populate: API snapshot (e.g. Just now),
  Live activity (after a request), and Transport (Live).
- Run one normal n8n job and confirm Running/Queued + Jobs update and the last
  live-activity text changes. Confirm it does not reveal any prompt or token.
- Disconnect the event stream in DevTools temporarily. The badge should switch to
  Polling fallback and the normal 15-second snapshots still work; reconnect on recovery.
- Switch to another browser tab and back: the stream should pause and resume cleanly.
- Use keyboard Tab + Enter to open the badge and Escape to close. Try mobile-width
  DevTools: panel should stay inside the viewport without horizontal page overflow.
- Visit Settings, Gallery, Diagnostics. No retention deletion is required for testing.

## Rollback

Stop the gateway, restore the backed-up frontend files under `app/gateway` and restart.
The 8A backend stream can remain installed. No database rollback is needed.
