# Codex contract for the selected n8n workflow

Scope: `sIhQefHZCmJ7dtnP` (`AI Gateway — Codex Request`) and its called subworkflows, inspected 2026-09-18. The gateway runs Codex only. Existing image, transcription, stock source, and FFMPEG nodes remain in n8n.

## Request nodes that fit the gateway

| Workflow | Nodes | Request shape | Response used downstream |
| --- | --- | --- | --- |
| Main | Generate EP Metadata; Assets Images Prompts; Image Assets Extractor; Batch Scene Creative Director; Batch AI Stock Query Builder | POST JSON with system/user `messages`, `model`, `temperature`, `max_tokens`, `stream:false`, `response_format:{"type":"json_object"}`, `safe` | `choices[0].message.content` containing a JSON object |
| FFMPEG Only v7 (Polly), `EapmKYuNbhCNCjoE` | AI Image Motion Planner1/2/3; Pollinations Rewrite Scene Prompt For Safety | Same JSON chat shape | `choices[0].message.content` containing a JSON object |
| Main pilot node | Image Assets Extractor1 | Direct `prompt`, `output_format`, `schema_id` | Existing `/v1/generate` response envelope |

The called Asset Gen subworkflow `d9ooUD6ksloPfM4E` is not exposed through n8n MCP. Its read-only n8n export showed Pollinations image GET requests and no text chat request. The STT, Stock, and Save VO subworkflows similarly contain no Codex-shaped text request. The main workflow's other service calls remain where they are.

## Chat request and response

Endpoint: `POST http://host.docker.internal:8080/v1/chat/completions` from n8n Docker. Send the saved gateway bearer credential as `Authorization: Bearer <token>`.

```json
{
  "model": "openai",
  "messages": [
    {"role": "system", "content": "Return only valid JSON."},
    {"role": "user", "content": "Return an object with a title field for this scene."}
  ],
  "temperature": 0.5,
  "max_tokens": 5000,
  "stream": false,
  "response_format": {"type": "json_object"},
  "safe": "privacy,secrets"
}
```

The response has the familiar `choices[0].message.content` string. Its string contents are a valid JSON object in JSON mode. The endpoint also returns `id`, `object`, `created`, `model`, and token usage when Codex reports it. `model`, `temperature`, `max_tokens`, and `safe` are accepted for compatibility but are not applied to Codex. The actual model is the gateway's `AI_GATEWAY_MODEL` or the Codex CLI default. Streaming, image parts, tools, and arbitrary provider routing are outside this route.

The gateway checks JSON syntax and the top-level object. It does not infer the detailed task schema from the prompt. Existing n8n parse and validation logic stays relevant. Requests over 80,000 combined prompt characters fail with 422. Codex's run timeout defaults to 120 seconds and is capped at 300 seconds; the n8n HTTP node timeout should exceed it when nodes are migrated.

## Later migration sequence

Migration status, 2026-09-18: `Generate EP Metadata` in the main workflow now uses `http://host.docker.internal:8080/v1/chat/completions` and the existing `Chatgpt in CLI` header credential. Its JSON body and downstream `Metadata Fields` node were left intact. The workflow is inactive, and this migrated node has not been executed yet. The other text request nodes still use their prior provider.

1. Keep the pilot `/v1/generate` node connected to the existing gateway bearer credential.
2. On a representative run, inspect `Generate EP Metadata` and the following `Metadata Fields` parser. Adjust that node's prompt or validation only if required.
3. Repeat the URL and credential change for the remaining main workflow text nodes, then the FFMPEG subworkflow's motion and rewrite nodes. Remove unnecessary Pollinations key selection branches only after their dependent text nodes have moved; image generation still needs its provider key.

No other n8n request node was modified in this first migration step.
