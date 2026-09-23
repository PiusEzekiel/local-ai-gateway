# Module 9B.5 — Gateway onboarding & collapsible sidebar

## Summary

This is a **frontend-only** update built on the verified Module 9B.4 files. It replaces the bare token prompt with an introduction to the Local AI Gateway and a collapsible, keyboard-accessible dashboard sidebar.

- The login page explains the local Pinokio → Codex → n8n relationship and the purpose of the dashboard.
- The token help distinguishes the gateway bearer token from a ChatGPT login or OpenAI API key. It shows the **local** `show-token.ps1` recovery command; the dashboard does **not** fetch or expose the token by itself.
- The token remains a password field stored in this browser tab's session storage after connection. The existing Disconnect behavior is unchanged.
- The top-bar button collapses the desktop sidebar to a 72 px icon/abbreviation rail and re-expands it; on narrow screens it hides/shows the navigation strip. The top-bar control stays accessible.
- The sidebar preference is tab-only. Navigation shortcuts (1–7), the Live indicator, Gallery/Jobs image viewers and n8n jobs retain their prior behavior.

## Before installation

1. In `C:\pinokio\api\local-ai-gateway`, commit your verified 9B.4 state to your current hardening branch if it is not already committed.
2. Let any active n8n jobs complete, stop the gateway in Pinokio, and back up `app\gateway` and the current `app\tests` directory.
3. Extract `local_ai_gateway_module_9b5.zip` into `C:\pinokio\api\local-ai-gateway`, preserving folder names and replacing the included source/tests. **Do not extract over your data directory.** The ZIP contains no `.gateway-data`, bearer token, database, settings or generated images.

## Verify

From Windows PowerShell:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected on your full Windows suite:** 269 passed (previous 262 + seven new 9B.5 tests), assuming 9B.4 installed without extra local tests. The local available regression subset passed 248 tests; all 26 frontend JavaScript tests passed. The HTML/CSS and sidebar click behavior were tested at 1440, 900 and 390 px.

Restart Pinokio and refresh the dashboard with **Ctrl+Shift+R**. Click **Disconnect** to inspect the new login page. Expand **Where do I find my token?** to check the retrieval instructions. Reconnect with your existing token (you do not need to rotate it). Use the button at the **left of the status bar** to collapse/expand the navigation. Open a Gallery image with the sidebar collapsed and confirm its viewer moves with the rail. On mobile, the same button shows/hides the horizontal navigation strip.

## Rollback

Stop the gateway after jobs finish and restore the pre-9B.5 frontend/tests backup, or check out the previous commit on your hardening branch. No database rollback is required.

**No** backend runtime, Codex execution, SQLite, token storage policy, n8n API, automatic cleanup or image files are changed.
