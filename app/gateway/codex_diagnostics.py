"""CLI diagnostic classification and bounded event metadata (no model output logging)."""
from __future__ import annotations

import json
from .contracts import GatewayError
from .diagnostics import redact_diagnostic

def codex_failure_diagnostic(stderr: bytes, stdout: bytes, max_chars: int = 2_000) -> str:
    """Return a short CLI diagnostic suitable for gateway logs.

    We intentionally inspect only stderr plus explicit Codex error events.
    Normal assistant/model output is ignored so prompts and generated content
    are not copied into operational logs.
    """
    parts: list[str] = []

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if stderr_text:
        parts.append(f"stderr={stderr_text}")

    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        if event_type not in {"error", "turn.failed", "item.failed"}:
            continue

        payload = (
            event.get("message")
            or event.get("error")
            or event.get("item")
            or ""
        )
        if payload:
            parts.append(f"{event_type}={payload}")

    diagnostic = " | ".join(parts)
    diagnostic = " ".join(diagnostic.split())

    if not diagnostic:
        return "no stderr or explicit Codex error event was emitted"

    # Module 6B: keep useful CLI flags/error descriptions, strip known secrets,
    # external URLs and machine-local paths BEFORE logging or persistence.
    return redact_diagnostic(diagnostic, max_chars=max_chars)


def codex_event_summary(stdout: bytes) -> str:
    """Summarize JSON event types without logging model content."""
    event_types: list[str] = []

    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = str(event.get("type") or "").strip()
        if event_type and event_type not in event_types:
            event_types.append(event_type)

    return ",".join(event_types) if event_types else "none"


def classify_codex_failure(stderr: bytes, stdout: bytes, exit_code: int) -> GatewayError:
    """Classify a failed Codex CLI process without exposing prompt/response content."""
    details = codex_failure_diagnostic(stderr, stdout, max_chars=8_000)
    lowered = details.lower()

    if any(
        phrase in lowered
        for phrase in (
            "usage limit",
            "rate limit",
            "quota",
            "too many requests",
            "try again at",
            "credits exhausted",
            "insufficient credits",
        )
    ):
        return GatewayError(
            "rate_limit",
            "Codex usage limit reached. Wait for the reset or choose a model with available capacity.",
            429,
        )

    if any(
        phrase in lowered
        for phrase in (
            "not logged in",
            "authentication",
            "unauthorized",
            "login required",
        )
    ):
        return GatewayError(
            "authentication",
            "Codex login is unavailable to the gateway account.",
            503,
        )

    if "model" in lowered and any(
        phrase in lowered
        for phrase in ("unsupported", "not found", "not available", "invalid")
    ):
        return GatewayError(
            "model_unavailable",
            "The selected Codex model is unavailable for this account.",
            422,
        )

    # Common CLI parsing failures. These are especially useful while using
    # `codex exec --image`, because malformed argument placement fails before
    # a Codex thread is created.
    if "no prompt provided via stdin" in lowered:
        return GatewayError(
            "codex_cli_prompt_error",
            "Codex CLI did not receive the image-generation prompt through stdin.",
            502,
        )

    if any(
        phrase in lowered
        for phrase in (
            "unexpected argument",
            "unrecognized option",
            "unknown option",
            "invalid value",
            "usage: codex exec",
        )
    ):
        return GatewayError(
            "codex_cli_argument_error",
            "Codex CLI rejected one or more command-line arguments.",
            502,
        )

    if "image_generation" in lowered and any(
        phrase in lowered
        for phrase in ("unknown feature", "unrecognized feature", "invalid feature")
    ):
        return GatewayError(
            "codex_feature_error",
            "This Codex CLI build does not recognize the image_generation feature flag.",
            502,
        )

    if "image" in lowered and any(
        phrase in lowered
        for phrase in (
            "does not exist",
            "not found",
            "failed to load",
            "could not load",
            "could not open",
            "unsupported image",
            "invalid image",
        )
    ):
        return GatewayError(
            "codex_image_input_error",
            "Codex CLI could not load one of the attached reference images.",
            502,
        )

    return GatewayError(
        "codex_failed",
        f"Codex exited with code {exit_code}; inspect the gateway log for the diagnostic preview.",
        502,
    )


