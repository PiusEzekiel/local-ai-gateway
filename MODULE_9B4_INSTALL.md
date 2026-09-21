# Module 9B.4 — Session & Authentication UX

This frontend-only patch builds on your verified **9B.3** installation (254 Python tests expected). It does not rotate the actual server bearer token, restart/stop the Codex gateway as a user action, or interrupt n8n requests. Disconnect affects only the current browser tab's dashboard session.

## Changed behavior

- A visible **Disconnect** button appears near the bottom of the desktop sidebar; on narrow screens it appears beneath the horizontal navigation.
- Clicking it stops the authenticated SSE stream, aborts in-progress Gallery media, closes the image viewer, removes `gatewayToken` from `sessionStorage` and from `api.js` module memory, clears the password input, hides the private dashboard and reloads the page. The reload disposes of private DOM/job state and authenticated blob URLs.
- Unsaved Settings changes require confirmation before **manual** Disconnect; Cancel leaves the session running. A genuinely expired token does not wait for confirmation.
- Any dashboard JSON or artifact request returning HTTP 401, as well as the SSE endpoint returning 401, disconnects immediately. A fixed, non-secret “token no longer valid” notice displays after reload. HTTP 403/503 do not erase the session.
- The bearer token itself is never embedded in a URL or event stream.

## Install

From PowerShell in `C:\pinokio\api\local-ai-gateway`, commit your last verified 9B.3 source first (if not yet committed):

```powershell
cd C:\pinokio\api\local-ai-gateway
git status --short
git add -A
git commit -m "Hardening 9B.3 - Gallery request resilience"
```

Skip the commit if the working tree is clean. Stop the gateway through Pinokio after active n8n requests finish. Back up `app\gateway`, then extract `local_ai_gateway_module_9b4.zip` into the **repository root** (preserve the `app/` prefix).

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected full Windows result: 262 passed**, assuming 9B.3 was 254. The ZIP adds eight Python tests, plus five optional Node runtime tests.

Optional frontend runtime tests (if Node is installed):

```powershell
node --test tests/frontend_session.test.mjs tests/frontend_gallery.test.mjs tests/frontend_live.test.mjs tests/frontend_sync.test.mjs
```

Restart Pinokio and hard-refresh the dashboard (Ctrl+Shift+R).

## Browser checks

1. With the dashboard connected, click Disconnect. It should show the token-free sign-in screen. Reconnecting with the original token should work; **the server token was not revoked**.
2. From Settings, make an unsaved edit, choose Disconnect, then **Cancel**: the dashboard should remain open with the unsaved value. Choose Disconnect again and confirm: return to sign-in.
3. Make sure Gallery, Jobs, and references still work after reconnecting, and the Live badge reconnects.
4. Do **not** rotate your server token or intentionally break production authentication just to test expiry; automated tests exercise that path.

## Rollback

This module has no DB/settings migration. Stop the gateway, restore the backed-up frontend files (or `git restore app/gateway/dashboard.html app/gateway/dashboard` if your previous 9B.3 commit is clean), then hard-refresh. Restore only after ensuring no newer wanted edits will be lost.
