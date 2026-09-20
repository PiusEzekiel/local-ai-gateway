# Local AI Gateway: n8n expansion plan

This is the original broad exploration. The current implementation scope is Codex text and JSON requests from workflow `sIhQefHZCmJ7dtnP` and its subworkflows; see [the current contract](CODEX_WORKFLOW_CONTRACT.md). Media and other provider adapters below are deferred.

## Decision

Use **Local AI Gateway** as the service name. The maintained source folder is `C:\Users\piuse\Documents\ChatGPT\local-ai-gateway`. Keep n8n in Docker and run the gateway on Windows through a Pinokio app launcher. n8n reaches the host at `http://host.docker.internal:8080`. Preserve the existing bearer credential and working `/v1/generate` response during the transition.

Pinokio can provide installation, start/stop, process state, and logs. A separate gateway dashboard is needed for job history, queue depth, provider health, errors, and usage. The launcher and dashboard should be developed as distinct parts of the same app.

## Read-only workflow inventory, 2026-09-18

The n8n MCP listed 51 visible workflows, including 13 active workflows and the pilot workflow `sIhQefHZCmJ7dtnP` (`AI Gateway — Codex Request`). A read-only CLI export returned 52 workflow records, likely including one outside the MCP search listing. Across active workflows, the export showed 463 nodes and 58 HTTP Request nodes: 33 POST, 24 GET, and 1 DELETE. Many URLs are expressions, so counts by static hostname alone are misleading. No workflow was modified or executed for this inventory.

| Request family | Examples observed in nodes | Gateway implication |
| --- | --- | --- |
| Text and structured JSON | `Still Standing Stories_v2`: `Generate EP Metadata`, `Assets Images Prompts`, `Image Assets Extractor`, `Batch Scene Creative Director`, `Batch AI Stock Query Builder`; request bodies contain `model`, `messages`, and `response_format` | Keep the existing Codex JSON endpoint; add an explicit task schema and provider selection rather than a generic chat-completions passthrough. |
| Research and retrieval | `Commentary Evidence Clip Render V1`: clip search intent and evidence reranking; existing stock-query builder | Keep Codex web research for sourced text. Treat vector and stock catalog searches as distinct retrieval capabilities. |
| Vision and image understanding | `Sub-workflow: FFMPEG + AI -> Video v6 Simpler`: Google-style `contents`, `inlineData`, `mimeType` in scene and motion-planning calls | Accept image uploads or trusted asset references, then route to a vision-capable provider. Do not accept arbitrary local filesystem paths from n8n. |
| Image generation | `Sub-workflow: Asset Gen` and `Asset Gen v2 (Gemi)`: Google image generation POST; Pollinations GET with model, size, seed, safety options and file output | Return a generated artifact and metadata. Support provider adapters for existing Gemini/Pollinations paths first. |
| Video generation | `FFMPEG + AI -> Video v6 Simpler`, older AI video and ComfyUI workflows: image-to-video requests, duration/size/seed parameters, binary downloads and retries | Use asynchronous jobs, status polling, durable artifact references, timeouts, and provider-specific progress. Preserve existing FFMPEG assembly in n8n. |
| Speech | `Sub-Workflow - STT`: batch recognition submit, operation polling, and GCS cleanup; other workflows use TTS providers | Add transcription and speech synthesis as later capabilities, with long-running job support where needed. |
| Stock assets | `Stock V2`: Pexels, Pixabay, Wikimedia, and Internet Archive search and binary downloads | Keep stock catalog I/O separate from generative AI routing; optionally offer a common search facade later. |

The current `/v1/generate` endpoint only handles text/JSON; `/v1/research` enables Codex web search. The current Codex runner does not accept image input and cannot create an image or video file. An OpenAI image-generation adapter would require separate API credentials; it cannot be assumed to use the Codex ChatGPT login.

## Target contract

Keep the two existing endpoints unchanged for the pilot workflow. Add capability-specific endpoints incrementally:

```text
POST /v1/vision/analyze
POST /v1/images/generate
POST /v1/videos/generate  -> 202 {job_id, status_url}
POST /v1/audio/transcribe -> 202 {job_id, status_url} when asynchronous
GET  /v1/jobs/{job_id}
GET  /v1/artifacts/{artifact_id}
GET  /v1/capabilities
```

Common request fields: `request_id`, `workflow_id` or caller label, task input, optional provider preference, deadline, and an idempotency key for jobs that cost money or take minutes. Common response fields: `success`, `request_id`, `task`, `provider`, `status`, `duration_ms`, `usage` when reported, artifacts, and a typed error. Preserve the old response envelope for existing clients.

Accept short JSON directly. For large images/audio/video, use bounded uploads or gateway-issued artifact IDs. Store output outside JSON, with expiration and cleanup. Avoid putting large base64 media into n8n execution logs. Validate MIME type, byte size, duration, and allowed remote hosts; keep provider keys only in gateway configuration. Use per-provider concurrency and rate limits.

## Build sequence

1. Package the working gateway as `local-ai-gateway` under Pinokio with install/start menus, a stable port, and URL capture. Reuse the working Python environment setup and Windows Codex login. Move the source only after the launcher starts the existing endpoints correctly; keep the n8n credential and URL stable.
2. Add a small authenticated dashboard for service state, queued/running/completed jobs, provider availability, recent errors, and usage. Do not display prompts, media, or tokens by default.
3. Add a capability registry and adapter interface. Codex remains the first text/JSON/research adapter. Define consistent errors and provider routing.
4. Add vision input with an image-capable adapter, then image generation using providers already found in workflows (Gemini and Pollinations). Implement artifact storage before returning generated media to n8n.
5. Add asynchronous video generation, beginning with one currently used provider, then ComfyUI or additional cloud backends. Add speech and stock-search facades only where consolidation simplifies real workflows.
6. Migrate one node group at a time. Keep each workflow's surrounding logic intact until its new gateway response is proven compatible.

## Deployment notes

- Pinokio is running the installed Local AI Gateway app from `C:\pinokio\api\local-ai-gateway`.
- The Pinokio launcher is under `C:\pinokio\api\local-ai-gateway`, with deployed app code under its `app` subdirectory. Copy updates from the maintained source folder and restart the Pinokio app.
- The current gateway binds to `0.0.0.0` for n8n Docker access. Restrict firewall access to the intended local/Docker network. A localhost-only bind would not satisfy the current n8n connection pattern.
- Do not move the existing encrypted `.gateway-token` to a different Windows account. If the same Windows user runs Pinokio, the token can be preserved during migration; otherwise issue a new credential and update n8n deliberately.
- Pinokio's launcher UI provides lifecycle controls. Its availability alone does not create the proposed job dashboard or provide image/video model access.

