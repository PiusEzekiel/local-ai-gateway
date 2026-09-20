# Local AI Gateway

Local AI Gateway is a Windows-hosted Codex service for the selected n8n workflow. It calls the Codex CLI through the ChatGPT login already established on this machine. Each request gets a fresh, ephemeral Codex execution and temporary working directory. The gateway never accepts shell commands or filesystem paths from an HTTP caller.

The gateway provides text/JSON, research, chat-shaped, and image-file routes for the test copies of workflow `sIhQefHZCmJ7dtnP` and its called subworkflows. [The workflow contract](docs/CODEX_WORKFLOW_CONTRACT.md) lists the request shapes. Other provider requests remain in n8n.

## Project location

The maintained source is `C:\Users\piuse\Documents\ChatGPT\local-ai-gateway`. Make future code and documentation changes here. The Pinokio runtime at `C:\pinokio\api\local-ai-gateway\app` is a deployment copy; copy changed application files from this project to that directory and restart the Pinokio app. The earlier `AI workflow` folder is retired and should not be used for new work.

## Setup

From PowerShell in this directory:

```powershell
.\setup.ps1
$env:AI_GATEWAY_CODEX_EXE = 'C:\Users\piuse\AppData\Local\OpenAI\Codex\bin\eab8377aebac6c07\codex.exe'
& $env:AI_GATEWAY_CODEX_EXE login status
.\run.ps1 -Bind 0.0.0.0
```

Keep this PowerShell window open while testing. On first start, `run.ps1` generates a random bearer token and saves it encrypted for this Windows user in the Git-ignored `.gateway-token` file. On later starts, it reuses that token. In a **second** PowerShell window in this directory, run `.\show-token.ps1` and copy the value into an n8n credential. Do not paste it into a workflow export or commit it to Git. If you set `AI_GATEWAY_TOKEN` in the gateway PowerShell session before starting, that value overrides the saved token; `show-token.ps1` continues to show the saved value, so use the environment override in n8n for that run.

Your running `n8n` instance is in Docker. Start the gateway with `.\run.ps1 -Bind 0.0.0.0`; I verified that the `n8n` container can reach `http://host.docker.internal:8080` using a temporary test port. Keep the Windows firewall limited to the local Docker network and send the Bearer token on every request.

The desktop Codex executable's hash directory can change after updates. `AI_GATEWAY_CODEX_EXE` can be omitted: the service tries `codex` on PATH and then discovers the newest desktop executable. Explicitly setting it makes the first pilot easier to diagnose.

## HTTP smoke tests

In a second PowerShell window in this directory, load the saved token and call the gateway:

```powershell
$token = .\show-token.ps1
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod http://127.0.0.1:8080/health -Headers $headers

$body = @{
    request_id = 'scene_52'
    prompt = 'Generate five stock-footage search queries for a person walking through a rainy city street at night. Do not use tools.'
    output_format = 'json'
    schema_id = 'stock_queries_v1'
    timeout_seconds = 120
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8080/v1/generate -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

A successful response has `success: true`, `response.queries` with five strings, a duration, optional `usage`, and `web_search_used`. Errors use the same envelope with `success: false` and an `error` object. `output_format: text` returns a string in `response` and needs no schema.

## n8n pilot

Use a **disconnected** test branch in workflow `sIhQefHZCmJ7dtnP`:

1. Add a Manual Trigger and an Edit Fields node that supplies `request_id`, `prompt`, `output_format: json`, and `schema_id: stock_queries_v1`.
2. Add an HTTP Request node. Set method `POST`, URL `http://host.docker.internal:8080/v1/generate` if n8n is in Docker Desktop, or `http://127.0.0.1:8080/v1/generate` if n8n runs on Windows. Send a JSON body from the Edit Fields node.
3. Add `Authorization: Bearer <token>` as a header, preferably through an n8n credential. Keep the token out of exported workflow JSON.
4. Set the HTTP node timeout above the requested `timeout_seconds` (for example 135 seconds for a 120-second Codex timeout). Inspect `success`, `response.queries`, and `error` after manually executing this branch.

The research endpoint is `POST /v1/research` with the same body. It enables Codex's native live web search and instructs it to verify information with sources. Test it separately after generation works.

## Workflow chat route

`POST /v1/chat/completions` accepts the `model`, `messages`, `temperature`, `max_tokens`, `stream: false`, `response_format: {"type":"json_object"}`, and `safe` fields present in the selected workflow's text request nodes. It returns `choices[0].message.content` as a JSON string, which their existing parser nodes read. Only Codex runs; the legacy `model`, `temperature`, `max_tokens`, and `safe` fields are accepted for request compatibility but do not control Codex. Use `codex_model` to override the gateway default for one request. Streaming is rejected.

The selected test workflow's text request nodes use this route and the saved `Chatgpt in CLI` header credential. The copies remain inactive.

## Image route

`POST /v1/images/generations` accepts JSON `{"prompt":"..."}` with optional `request_id` and `timeout_seconds` (30–900). It uses Codex's integrated image generation tool and returns image bytes with an image content type. The two Asset Gen and six FFmpeg Scene Render image request nodes in the test copies send their existing prompt fields to this route and keep n8n's File response format in the `data` field. They use the same bearer credential as text requests and a 900-second HTTP timeout. Restart the Pinokio gateway after updating its app files.

The route returned a PNG in a direct live test. The existing Pollinations model, seed, dimensions, negative prompt, and reference-image parameters are not mapped to the Codex request. Each existing parallel or retry branch may create another image and consume Codex usage.

## Models and monitoring

The dashboard at `http://127.0.0.1:8080/` includes Overview, durable Jobs history, an authenticated image Gallery, Codex quota windows, and Usage and Performance analytics. Analytics support Today, 24h, 7d, 30d, and custom ranges, and distinguish cumulative task time from wall-clock busy time. Quota reset labels use the browser's local weekday, date, and time. The compatibility route `GET /v1/jobs` still exposes the in-memory last 100 jobs to authenticated n8n requests; the dashboard reads the durable SQLite history. HTTP request nodes receive a final response or an error body with `request_id`, `model`, `status`, and an error type and message. n8n cannot display intermediate progress within one synchronous HTTP Request node; a separate monitor workflow can query `/v1/jobs` during an execution.

Select the default model in the dashboard; it persists in the Git-ignored `.gateway-settings.json`. The default is `gpt-5.6-luna`. Available choices are `gpt-5.6-luna`, `gpt-5.6-terra`, and `gpt-5.6-sol`. `GET /v1/models` lists them and `PUT /v1/models/default` with `{"model":"gpt-5.6-terra"}` changes the default. Text, chat, research, and image requests can specify `codex_model` to override it. Image rendering uses Codex's integrated image tool, whose underlying image model is chosen by Codex. Model availability and plan usage are governed by the signed-in Codex account.

Telemetry is also persisted locally in the Git-ignored `.gateway-data` directory. SQLite records job metadata, stage events, timings, completed token usage, stable reference IDs, and artifact metadata. It does not store prompts, generated text, reference URLs, bearer tokens, or credential material. The in-memory last-100-job cache and `/v1/jobs` contract remain available for compatibility. See [the phased control-plane plan](docs/CONTROL_PLANE_IMPLEMENTATION.md).

Quota telemetry uses one supervised `codex app-server --stdio` process and only the supported `account/rateLimits/read` and `account/rateLimits/updated` protocol. It shows every reported bucket and only the primary or secondary windows Codex actually supplies. Quota snapshots are retained in SQLite for later usage charts. If the signed-in account does not expose limits, the dashboard reports unavailable and generation continues normally.

To run the deterministic compatibility and telemetry tests:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_TOKEN` | saved or generated by `run.ps1` | Optional override for the bearer token, at least 24 characters |
| `AI_GATEWAY_CODEX_EXE` | auto-discover | Codex CLI executable |
| `AI_GATEWAY_MODEL` | `gpt-5.6-luna` | Initial gateway model; the dashboard selection takes precedence after it is saved |
| `AI_GATEWAY_MAX_CONCURRENCY` | `1` | Simultaneous Codex jobs |
| `AI_GATEWAY_MAX_QUEUE` | `4` | Waiting HTTP jobs; excess returns 429 |
| `AI_GATEWAY_TIMEOUT_SECONDS` | `120` | Default total request timeout |
| `AI_GATEWAY_MAX_TIMEOUT_SECONDS` | `300` | Server-side timeout ceiling |
| `AI_GATEWAY_QUOTA_MONITOR` | `1` | Set to `0`, `false`, or `no` to disable app-server quota telemetry |
| `AI_GATEWAY_QUOTA_POLL_SECONDS` | `45` | Quota refresh interval; must be 30–60 seconds |
| `AI_GATEWAY_QUOTA_WARNING_PERCENT` | `20` | Remaining-percent warning threshold |
| `AI_GATEWAY_QUOTA_CRITICAL_PERCENT` | `10` | Remaining-percent critical threshold |

For `output_format: json`, use `schema_id: stock_queries_v1` or supply an inline `output_schema` JSON object. Inline schemas are limited to 16 KiB, must have an object root, and cannot contain external references. The gateway validates both the schema and Codex's response. Keep schemas simple because the CLI's structured-output support may reject some valid but complex JSON Schema features.

## Security and current limits

- CLI executions use `--ephemeral`, `--ignore-user-config`, `--sandbox read-only`, and disabled shell, app, browser, computer-use, and multi-agent features. Only `/v1/research` enables native web search.
- The server does not log prompts, model responses, or bearer tokens. HTTP access logs are off in `run.ps1`.
- `read-only` limits writes. It is not an operating-system guarantee that the model cannot read files accessible to the gateway account. Strong filesystem isolation requires a dedicated Windows account or a separate container/worker; keep the gateway account and network exposure limited during this pilot.
- Authentication is inherited from the Windows user running the gateway. A future Docker deployment needs its own supported CLI installation and a secure login persistence plan; this MVP does not copy ChatGPT credentials into a container.
- The queue is in-process. Run one Uvicorn worker for this MVP. A service restart drops waiting jobs.
- A timeout kills the Codex parent process. Windows child-process cleanup is a later hardening task.
- ChatGPT plan usage limits and Codex model availability can change. Measure real scenes before increasing concurrency.
- The first live gateway smoke tests reported 8,903 input tokens for five stock queries and 71,976 input tokens for a web research request (45,696 cached). Codex is a relatively heavy backend for tiny generation jobs; benchmark quality and usage before routing high-volume scene work to it.

