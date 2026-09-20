# AI Gateway Control Plane implementation

The control-plane upgrade is deliberately split into gated chunks. Existing `/v1/*` generation behavior remains the compatibility boundary; observability is best-effort and must not become a dependency of generation.

## Chunk 0 — Audit and compatibility baseline (complete)

- Audited the FastAPI routes, Codex CLI runner, image-reference flow, dashboard, settings file, launcher scripts, and current Pinokio logs.
- Confirmed Codex CLI `0.155.0-alpha.2.6` and an available `codex app-server --stdio` transport.
- Confirmed the launcher already uses the required URL capture and `local.set` pattern.
- Added route-level compatibility tests using a deterministic runner.

## Chunk 1 — Telemetry foundation (complete)

- Versioned SQLite schema and automatic migration.
- Durable jobs, job events, usage, reference IDs, and artifact metadata.
- Queue, Codex, reference-download, artifact-processing, and total timing fields.
- Retry grouping for `_initial` and `_retry_N` request IDs.
- Structured image result retaining Codex usage and thread ID.
- Opaque persistent artifact copies; storage paths never enter API responses.
- Atomic settings manager; current model behavior remains compatible.

Privacy defaults remain conservative: prompts, generated text, reference URLs, credentials, and raw CLI command lines are not stored.

## Chunk 2 — Dashboard shell (complete)

- Split the single dashboard file into local HTML/CSS/JavaScript modules.
- Add the Overview and Jobs sections, compact application status bar, independently scrollable execution feed, filters, and job inspector.
- Add authenticated dashboard summary/jobs APIs backed by SQLite while preserving `/v1/jobs`.

Gate: pagination, filtering, empty-state, authentication, and existing-route tests must pass.

## Chunk 3 — Gallery and artifact delivery (complete)

- Thumbnail generation and artifact/reference APIs with opaque IDs.
- Authenticated Blob loading, object-URL cleanup, gallery filters, and accessible zoom/pan lightbox.
- Missing-file and directory-traversal resilience.

Gate: artifact authorization, traversal, deletion, and image-view tests must pass.

## Chunk 4 — Codex quota monitor (complete)

- Generate and inspect the installed app-server schema before implementation.
- Long-lived app-server session for supported `account/rateLimits/read` and update notifications.
- Normalize and persist only values actually reported by Codex; unavailable windows remain unavailable.
- Multiple buckets, reset countdowns, warning thresholds, and quota history.

Implementation note: the installed Codex CLI schema was regenerated against `0.155.0-alpha.9.2`. Sparse `account/rateLimits/updated` notifications trigger a full supported read so missing fields are never inferred. The monitor is supervised and optional; authentication or app-server failures report unavailable telemetry without interrupting generation.

Gate: malformed, unavailable, single-window, dual-window, and multi-bucket tests must pass without affecting generation.

## Chunk 5 — Usage and performance (complete)

- Time-range aggregations, token and task charts, latency percentiles, success rates, and model breakdowns.
- Clearly separate cumulative task time, Codex compute time, queue time, and wall-clock busy time.
- Add Today, 24h, 7d, 30d, and bounded custom ranges with SQL-downsampled chart series.
- Show observed quota history and pace without predicting exhaustion; reset labels use the viewer's local day and time.

Implementation note: schema version 4 records each job's operation type. Aggregate queries return at most 121 chart buckets, model/error breakdowns are bounded, and exact latency percentiles are selected in SQL without loading full histories. Wall-clock busy time is the union of overlapping worker intervals, while cumulative task time remains the sum across jobs. Safety rewrite counts stay explicitly unavailable because that signal is not currently captured.

Gate: aggregation queries must be checked against fixed fixtures and bounded for large histories.

## Chunk 6 — Diagnostics

- Failure inspector, event timeline, safe stderr preview, redaction, search, and copyable diagnostic bundles.

Gate: redaction tests must cover bearer tokens, authorization headers, passwords, and filesystem paths.

## Chunk 7 — Settings, privacy, and retention

- Validated settings sections, live/restart-required labels, privacy switches, retention cleanup, and disk quotas.
- Keep runtime concurrency restart-required unless a correct worker manager is introduced.

Gate: invalid settings, cleanup safety, active-job protection, and orphan handling tests must pass.

## Chunk 8 — Live UX and polish

- SSE with polling fallback, live counters and transitions, command palette, keyboard navigation, responsive/light themes, and final visual QA.

Gate: reconnect behavior, scroll-position retention, accessibility, frontend checks, and end-to-end n8n route smoke tests must pass.

## Live-token boundary

Completed `codex exec` usage is persisted now. Running jobs display no invented usage. The quota app-server session is independent from the ephemeral `codex exec` processes, so its turn notifications cannot be safely correlated with gateway jobs. Live per-job tokens therefore remain unavailable unless a separately evaluated execution-architecture migration brings execution into the same app-server session.
