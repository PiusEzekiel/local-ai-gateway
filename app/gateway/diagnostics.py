"""Bounded, redacted diagnostic records for the Local AI Gateway.

Module 6A intentionally does not import FastAPI, CodexRunner or JobStore.
The production runner can call ``build_diagnostic_bundle`` in Module 6B.

Security boundary: sanitization catches known credential/path patterns, but
cannot guarantee removal of secrets embedded in arbitrary prose. Never pass
complete model prompts, authentication files or raw request bodies here.
"""

from __future__ import annotations

import re
from typing import Any

# A single field must never create an unbounded SQLite row or browser panel.
MAX_DIAGNOSTIC_CHARS = 2_000
MAX_MESSAGE_CHARS = 500

# Mask entire external URLs, including query parameters and Drive IDs.
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"'`]+")

# Examples: Authorization: Bearer abc123; Authorization=Basic abc123.
_AUTH_RE = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*(?:bearer|basic)\s+)([^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{8,})")

# Matches JSON, .env and PowerShell-style key/value secrets. Excludes the
# generic word 'model' and normal numeric token-usage fields.
_SECRET_KV_RE = re.compile(
    r"(?i)(?P<head>[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"ai_gateway_token|openai_api_key|github_token|client_secret|"
    r"password|passwd|passphrase|secret)[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\s\"',;}]+)"
)

# Recognizable standalone token formats; do not attempt to print prefixes.
_STANDALONE_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{12,}|"
    r"gh[pousr]_[A-Za-z0-9]{12,})\b"
)

# Local paths are not useful in public diagnostic bundles. We keep CLI switch
# names such as --image and --sandbox, but remove host-specific file paths.
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\|\\\\)[^\s\"'<>|]+")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users|mnt|tmp|var|etc)/[^\s\"'<>]+")


def redact_diagnostic(value: str | bytes | None, *, max_chars: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """Redact common secrets, external URLs and local paths; bound the result.

    This function is suitable for safe *previews*, not for storing raw payloads.
    Call it before persistence and again before exporting a diagnostic bundle.
    """
    if value is None:
        return ""

    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)

    # Ordering matters: redact URLs before paths and bearer tokens before
    # generic key/value assignments so query strings and credentials don't leak.
    text = _URL_RE.sub("[REDACTED_URL]", text)
    text = _AUTH_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_KV_RE.sub(lambda m: m.group("head") + "[REDACTED]", text)
    text = _STANDALONE_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _WINDOWS_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _POSIX_PATH_RE.sub("[REDACTED_PATH]", text)

    limit = max(1, min(int(max_chars), 8_000))
    if len(text) > limit:
        text = text[:limit] + "… [truncated]"
    return text


def build_diagnostic_bundle(
    *,
    request_id: str,
    task: str,
    model: str,
    error_type: str,
    message: str = "",
    last_successful_stage: str = "",
    exit_code: int | None = None,
    duration_ms: int | None = None,
    reference_count: int = 0,
    references_downloaded: int = 0,
    stderr: str | bytes | None = None,
) -> dict[str, Any]:
    """Create a minimal, JSON-serializable failure record for the UI/export.

    Do not include an unfiltered prompt, authorization header, reference URL,
    entire Codex JSONL output, or environment variables in this object.
    """
    return {
        "request_id": redact_diagnostic(request_id, max_chars=128),
        "task": redact_diagnostic(task, max_chars=40),
        "model": redact_diagnostic(model, max_chars=100),
        "error_type": redact_diagnostic(error_type, max_chars=80),
        "message": redact_diagnostic(message, max_chars=MAX_MESSAGE_CHARS),
        "last_successful_stage": redact_diagnostic(last_successful_stage, max_chars=120),
        "exit_code": int(exit_code) if exit_code is not None else None,
        "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
        "reference_count": max(0, int(reference_count)),
        "references_downloaded": max(0, int(references_downloaded)),
        "diagnostic": redact_diagnostic(stderr),
    }
