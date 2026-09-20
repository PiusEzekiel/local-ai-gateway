"""Public request models, runner results, and stable GatewayError type."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field
from .config import DEFAULT_MODEL

class GenerateRequest(BaseModel):

    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    prompt: str = Field(min_length=1, max_length=80_000)

    output_format: Literal["text", "json"] = "text"

    schema_id: str | None = None

    output_schema: dict[str, Any] | None = None

    timeout_seconds: int | None = Field(default=None, ge=5, le=300)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | None = None


class ChatMessage(BaseModel):

    role: Literal["system", "user", "assistant"]

    content: str = Field(min_length=1, max_length=40_000)


class ChatResponseFormat(BaseModel):

    type: Literal["json_object", "text"]


class ChatCompletionRequest(BaseModel):

    # Matches the JSON chat payloads used by the selected n8n workflow.

    model: str | None = Field(default=None, max_length=100)

    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

    response_format: ChatResponseFormat | None = None

    temperature: float | None = Field(default=None, ge=0, le=2)

    max_tokens: int | None = Field(default=None, ge=1)

    stream: bool = False

    safe: str | None = Field(default=None, max_length=100)

    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    timeout_seconds: int | None = Field(default=None, ge=5, le=300)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | None = None


class ImageReference(BaseModel):

    """One externally hosted image that should be attached to the Codex image request.



    The HTTP caller sends a stable asset ID plus a direct HTTPS URL. The gateway

    downloads the image itself, validates it, stores it in the ephemeral Codex

    working directory, and then attaches the local file to the Codex CLI request.

    """



    id: str | None = Field(default=None, max_length=128)

    url: str = Field(min_length=1, max_length=2_000)


class ImageRequest(BaseModel):

    """Request body accepted by POST /v1/images/generations."""



    prompt: str = Field(min_length=1, max_length=32_000)



    # Keep the reference list deliberately small. Scene generation should pass only

    # the assets that are actually needed for this scene, not the entire registry.

    reference_images: list[ImageReference] = Field(default_factory=list, max_length=10)



    request_id: str = Field(

        default_factory=lambda: str(uuid4()),

        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",

    )

    # If omitted, use the configured image default (600s unless changed).
    timeout_seconds: int | None = Field(default=None, ge=30, le=900)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | None = None


class ModelSelection(BaseModel):

    model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]


@dataclass(frozen=True)

class RunResult:

    response: Any

    raw_response: str

    usage: dict[str, Any] | None

    web_search_used: bool = False


@dataclass(frozen=True)
class ImageRunResult:

    path: Path

    mime_type: str

    usage: dict[str, Any] | None

    thread_id: str | None


class GatewayError(Exception):

    def __init__(self, kind: str, message: str, status_code: int):

        super().__init__(message)

        self.kind = kind

        self.message = message

        self.status_code = status_code


