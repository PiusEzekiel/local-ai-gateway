# Local AI Gateway

Windows-hosted, self-hosted Codex CLI gateway for n8n, with an authenticated monitoring and operations dashboard. The gateway accepts compatible text, research, chat-shaped and image-generation requests; runs Codex locally under the signed-in Windows account; and returns responses that the existing n8n automation can consume.

**Release checkpoint:** Module 8D (20 September 2026). The Windows regression suite reported **149 passed, 2 dependency deprecation warnings**. A browser screenshot confirmed **Healthy + Live**, recent completed text/image jobs, worker state and Dashboard sync. These checks do not replace a fresh end-to-end smoke test after deployment changes.

## Where it runs

| Item | Location |
| --- | --- |
| Pinokio deployment | `C:\pinokio\api\local-ai-gateway` |
| Python application | `C:\pinokio\api\local-ai-gateway\app` |
| Gateway source | `app\gateway\` |
| Regression tests | `app\tests\` |
| Dashboard | `http://127.0.0.1:8080/` (when launched on the configured default port) |
| Persistent configuration | `app\.gateway-settings.json` |
| SQLite history | `app\.gateway-data\gateway.sqlite3` |
| Managed Gallery files | `app\.gateway-data\artifacts\` |

If `AI_GATEWAY_DATA_DIR` is set, the SQLite database and managed artifacts are stored there instead. The settings file remains at `app\.gateway-settings.json`.

The Codex CLI also maintains generated originals under `CODEX_HOME\generated_images\<thread-id>` (or the Codex default home if `CODEX_HOME` is unset). **The Gallery storage limit and gateway cleanup do not cover Codex's own originals.**

### Reference-image cache

The shared reference-image cache is disabled by default for backward
compatibility. Enable it with `AI_GATEWAY_REFERENCE_CACHE=1`. Cached files are
stored separately from Gallery artifacts under the gateway data directory and
survive gateway restarts. The default freshness window is 24 hours and the
default unused-entry retention is 7 days. The default bounded cache size is
2048 MB. These can be changed with
`AI_GATEWAY_REFERENCE_CACHE_TTL_SECONDS` and
`AI_GATEWAY_REFERENCE_CACHE_RETENTION_DAYS` and
`AI_GATEWAY_REFERENCE_CACHE_MAX_MB`.

Cache lookup identity is the complete source URL, including signed query
parameters. Request-local reference IDs are metadata only and never split
identical URL sources. The gateway still validates HTTPS, host policy, DNS,
redirects, image signatures, size, and local file integrity on every cache
reuse. Entries older than the freshness window are downloaded again, so
mutable provider content is not silently treated as immutable. Cache records
are separate from
`job_references`, Gallery retention, and Codex-generated originals.

Freshness expiration and physical retention are separate policies. A
revalidated unchanged image keeps its immutable local file and receives a new
validation timestamp. An entry unused beyond the retention period becomes
eligible for cleanup, while active leases are always protected. A cache-only
sweep runs on the existing cleanup interval when the cache is enabled; it does
not run Gallery cleanup. The Settings page also provides cache size/count
metrics and confirmed actions to clear expired or all unused cache entries.
Storage limits use deterministic least-recently-used eviction and may be
temporarily exceeded while all over-limit entries are actively leased.

The cache reduces repeated downloads and local duplication. It does not claim
to reduce Codex model input tokens; each independent Codex execution still
receives its image inputs.

Cache leases are reference-counted within the gateway process. A request holds
its leases until the complete image-run scope exits, including download,
timeout, cancellation, subprocess failure, and artifact errors. Cache
filesystem or database persistence failures fall back to the already validated
request-local image; URL, DNS, redirect, size, and image-content failures
remain hard failures. Multiple gateway processes do not coordinate leases, so
shared multi-process deployment requires a later durable lease mechanism.

## Start, stop and sign in

1. Start **Local AI Gateway** using its **Run** action in Pinokio. Do not start multiple application workers: the queue and live event bus belong to one gateway process.
2. Open `http://127.0.0.1:8080/` or the URL shown in Pinokio.
3. Enter the gateway bearer token when the dashboard requests it. The dashboard stores it in the browser tab's session storage; API requests send it in the `Authorization` header.
4. Confirm **Healthy** and **Live**. *Live* describes the dashboard connection; *Healthy* reflects successful authenticated API snapshots.
5. Before stopping or restarting, let active n8n requests finish. Restarting can interrupt queued or running jobs.

The gateway uses the Codex CLI installed and authenticated for the Windows account running Pinokio. After a Codex upgrade, if startup fails, verify the CLI and any `AI_GATEWAY_CODEX_EXE` override. Keep the bearer token in an n8n credential or another secret store, never in workflow exports, screenshots or Git.

For n8n in Docker Desktop, the usual gateway URL is `http://host.docker.internal:8080`; for n8n running directly on Windows, use `http://127.0.0.1:8080`. Both need `Authorization: Bearer <gateway-token>`. Bind the server to an interface reachable by the n8n container only when necessary, and restrict network/firewall exposure.

## Dashboard guide

| Section | What to use it for |
| --- | --- |
| **Overview** | Gateway health, model, quota/reset windows, daily metrics, recent jobs and worker/queue state. |
| **Jobs** | Find requests, check execution stages, status, usage and job details. |
| **Gallery** | Browse generated images and saved references; open the managed artifact. |
| **Usage** | Inspect token consumption over selectable periods. |
| **Performance** | Inspect latency, throughput and other execution metrics. |
| **Diagnostics** | Filter failed jobs, view stages and redacted error previews, copy a diagnostic bundle. An empty failure list is valid. |
| **Settings** | Edit the model, capacity, timeouts, quota alerts, privacy and retention; inspect System and preview storage cleanup. |

**Dashboard sync:** Click the **Live** badge to see the transport, last API snapshot and latest live event. Normally it reports `Live`. If streaming cannot connect it uses **Polling fallback**; snapshots still supply the authoritative values. **Live · stale data** means the stream exists but the most recent successful API snapshot is old. Use **Refresh dashboard data** or investigate the gateway/network; don't mistake a connected stream for fresh metrics.

The dashboard receives notifications over authenticated `GET /dashboard/api/events` using streaming `fetch()`. The bearer token is sent in an HTTP header, **not in a URL**. While connected, the client periodically refreshes API snapshots; when disconnected it falls back to polling.

## Core n8n routes

| Route | Purpose / response |
| --- | --- |
| `POST /v1/generate` | Text or schema-constrained JSON generation. |
| `POST /v1/research` | Research request using the Codex web-search capability. |
| `POST /v1/chat/completions` | Compatibility response containing `choices[0].message.content`; request streaming is not supported. |
| `POST /v1/images/generations` | Returns **image bytes** with an image content type; configure the n8n HTTP Request node for a file/binary response. |
| `GET /v1/models` | Available Codex model choices. |
| `PUT /v1/models/default` | Change the default model. |
| `GET /v1/jobs` | Authenticated recent job information. |
| `GET /health` | Authenticated gateway health and currently selected model. |

Use `codex_model` for a per-request gateway-model override. The image route accepts `prompt`, optional `request_id`, `timeout_seconds`, and up to 10 `reference_images` objects containing a direct HTTPS `url` and optional `id`. The image model actually used inside Codex's image tool is determined by Codex. Do not treat `temperature`, `max_tokens` or legacy `model` fields on the chat compatibility route as controls for Codex.

### Minimal text smoke test (PowerShell)

Obtain your token from your existing secure local gateway setup; do **not** paste it into this README.

```powershell
$token = Read-Host 'Gateway bearer token'
$headers = @{ Authorization = "Bearer $token" }

Invoke-RestMethod 'http://127.0.0.1:8080/health' -Headers $headers

$body = @{
    request_id = 'smoke_readme_text'
    prompt = 'Reply with one short sentence confirming the text route works.'
    output_format = 'text'
    timeout_seconds = 120
} | ConvertTo-Json

Invoke-RestMethod 'http://127.0.0.1:8080/v1/generate' `
    -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

For images and reference images, run your **existing n8n scene-generation test** instead of inventing a second request shape. Check that the HTTP node receives non-empty image bytes and that Gallery can open the result.

## Settings: immediate versus restart-required

**Immediate:** Saving the default model changes subsequent requests without rebuilding the worker pool.

**Restart required:** Concurrency, maximum queue, text/research and image timeouts, quota monitor and thresholds, prompt/output/diagnostic retention and all retention/automatic-cleanup settings. The Settings page shows active and saved values and identifies pending restarts. A saved setting does not silently alter a running request or resize live workers.

For restart-required fields, an explicitly set `AI_GATEWAY_*` environment variable overrides the saved dashboard value. The saved default model takes precedence over `AI_GATEWAY_MODEL`. If a field is marked as environment-controlled, change its environment configuration and restart rather than expecting the dashboard value to win.

**Factory defaults** (your active settings may differ): one concurrent job, four waiting jobs, 120-second text/research timeout, 300-second text ceiling, 600-second image timeout, quota alert thresholds at 20%/10% remaining, and 45-second quota refresh. The top bar always shows **your effective running values**.

## Privacy, storage and deletion

- Prompt and generated-text retention are **off by default**. If enabled, saved content is plaintext in the local SQLite database. Redacted diagnostic previews are on by default; no redaction should be treated as a guarantee that every imaginable secret is removed.
- Automatic cleanup is **off by default**. Configured defaults are 90 days of terminal job history, 30 days for managed image/reference artifacts, 30 days for quota snapshots, and a 10 GiB managed artifact limit. They do not cause deletion merely by being configured.
- Use **Settings → Storage → Preview** to inspect candidates. Preview does not delete anything. A confirmed manual cleanup *does* delete eligible data; do not click it simply to test the UI. The run recalculates its plan, so a preview is advisory rather than a promise of exact deletion counts.
- History retention removes eligible terminal jobs and their associated records. Artifact age/space cleanup can remove managed image files without discarding token/performance history. Running and queued jobs are protected.
- The orphan option is separate and should be used deliberately. Files that Codex owns outside the gateway's managed artifact directory are not part of that cleanup.
- Turning off retention of prompts or outputs does **not** erase previously stored text. The **Privacy storage** purge is an explicit destructive operation. SQLite purges are not a secure erase of backups, WAL files or other copies.

## Back up and restore

**Before any upgrade, cleanup-policy change, or destructive operation:** wait for n8n jobs to finish; stop the gateway; copy `app\gateway`, `app\.gateway-settings.json`, and all of `app\.gateway-data` to a location outside the project. Protect backups as sensitive data if content retention has ever been enabled. If `AI_GATEWAY_DATA_DIR` redirects storage, back up that directory instead. Preserve any separately managed gateway-token file or recovery procedure without committing it to source control.

To roll back a code-only change, stop Pinokio and restore the backed-up code, settings and—**only when required by a schema-changing release**—its matching database backup. Never overwrite a newer database with an older one while the gateway is running. The 8D patch itself does not introduce a schema migration.

## Run the regression suite

Open PowerShell in the deployed `app` directory:

```powershell
cd C:\pinokio\api\local-ai-gateway\app

$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null

.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**8D checkpoint:** `149 passed, 2 warnings` on the user's Windows install. Those two warnings were from FastAPI/Starlette's test dependency imports (`httpx`/`httpx2`, AnyIO `BlockingPortal`), not failing gateway tests. Test counts will change if tests are added or removed.

If Node is installed, the frontend live/sync regression command is:

```powershell
node --experimental-default-type=module --test tests\frontend_live.test.mjs tests\frontend_sync.test.mjs
```

## Quick post-change smoke checklist

- [ ] Gateway starts in Pinokio and `/health` responds using the bearer token.
- [ ] Dashboard opens and displays **Healthy + Live**, or clearly identifies polling fallback.
- [ ] Default model in Settings matches Overview; changing it takes effect immediately.
- [ ] One real text request completes from n8n with the expected response shape.
- [ ] One real image request (including reference images in your usual workflow) returns usable binary data.
- [ ] Jobs and the Live activity inspector update without manual refresh; Running/Queued return to idle after completion.
- [ ] Generated image opens in Gallery and its thumbnail loads.
- [ ] Usage/Performance and Diagnostics load; an empty Diagnostics list is not an error.
- [ ] Settings save and pending-restart messaging behave correctly.
- [ ] Storage **Preview** works without running cleanup; no unexpected browser-console errors.
- [ ] Current code, database, artifacts and settings have a recoverable backup.

## Common checks

| Symptom | Check |
| --- | --- |
| Dashboard asks for token / API returns 401 | Use the **gateway** bearer token, not a ChatGPT API key; ensure n8n sends `Authorization: Bearer …`. |
| Healthy but Polling fallback | Inspect Dashboard sync, browser console, and authenticated `/dashboard/api/events`; ordinary API polling should continue. |
| Live but stale data | Use Refresh dashboard data; confirm authenticated API snapshots and gateway health. |
| Queue is full / 429 | Check Overview capacity and queue; lower n8n concurrency or update queue settings and restart. |
| Settings appear not to apply | Check pending restart and environment overrides; restart Pinokio after jobs complete. |
| Gallery grows beyond the configured cap | Check whether space is in `.gateway-data\artifacts` versus separate `CODEX_HOME\generated_images`; cleanup is off unless enabled or explicitly confirmed. |
| Image request succeeds but n8n cannot use result | Check the HTTP Request node's **binary/file response** mode and expected binary field, not a JSON parser. |

**Scope:** This gateway is intended for trusted local/n8n use. The Codex process inherits access available to its Windows user. A read-only Codex sandbox is not an operating-system-level guarantee that other local files are unreadable. Avoid exposing the gateway on the public internet without an independent security review and stronger isolation.
