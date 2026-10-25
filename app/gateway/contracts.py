"""Public request models, runner results, and stable GatewayError type."""
from __future__ import annotations


import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .config import DEFAULT_MODEL

class GenerateRequest(BaseModel):

    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    prompt: str = Field(min_length=1, max_length=80_000)

    episode_title: str | None = Field(default=None, min_length=1, max_length=300)

    output_format: Literal["text", "json"] = "text"

    schema_id: str | None = None

    output_schema: dict[str, Any] | None = None

    timeout_seconds: int | None = Field(default=None, ge=5, le=300)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | None = None



# -----------------------------------------------------------
# MODULE 9E.1 — MULTIMODAL CHAT REQUEST CONTRACT
# -----------------------------------------------------------
# Supports:
#   1. Existing text-only chat messages.
#   2. OpenAI-compatible text + image_url content parts.
#
# This module only validates the request structure.
# Image attachment/execution is implemented in 9E.2/9E.3.
#
# Security:
# - Base64 data URLs only; remote URLs are not accepted.
# - JPEG, PNG and WebP only.
# - Maximum 5 MiB per image.
# - Maximum 4 images per chat request.
# - Do not log image data or validation input values.
# -----------------------------------------------------------

MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CHAT_IMAGES_PER_REQUEST = 4

# Includes the Base64 data and the MIME/header prefix.
MAX_CHAT_IMAGE_DATA_URL_CHARS = 7_000_100

SUPPORTED_CHAT_IMAGE_PREFIXES = {
    "data:image/jpeg;base64": "jpeg",
    "data:image/png;base64": "png",
    "data:image/webp;base64": "webp",
}


class ChatTextPart(BaseModel):
    """Text inside an OpenAI-compatible content-parts array."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]

    text: str = Field(
        min_length=1,
        max_length=40_000,
    )


class ChatImageURL(BaseModel):
    """A Base64-encoded image URL; no external URL fetching."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=MAX_CHAT_IMAGE_DATA_URL_CHARS,
    )

    @field_validator("url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        header, separator, encoded = value.partition(",")

        image_type = SUPPORTED_CHAT_IMAGE_PREFIXES.get(
            header
        )

        if not separator or image_type is None:
            raise ValueError(
                "image_url must be a Base64 JPEG, PNG, "
                "or WebP data URL"
            )

        # Reject oversized input before Base64 decoding.
        max_encoded_length = 4 * (
            (MAX_CHAT_IMAGE_BYTES + 2) // 3
        )

        if (
            not encoded
            or len(encoded) > max_encoded_length
        ):
            raise ValueError(
                "image_url is empty or exceeds "
                "the 5 MiB image limit"
            )

        try:
            image_bytes = base64.b64decode(
                encoded,
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "image_url contains invalid Base64"
            ) from exc

        if (
            not image_bytes
            or len(image_bytes) > MAX_CHAT_IMAGE_BYTES
        ):
            raise ValueError(
                "image_url is empty or exceeds "
                "the 5 MiB image limit"
            )

        # Check that the declared MIME is consistent
        # with the actual leading file signature.
        #
        # Full image decoding, image dimensions and
        # safe temporary-file creation belong to 9E.2.
        if image_type == "jpeg":
            signature_ok = image_bytes.startswith(
                b"\xff\xd8\xff"
            )

        elif image_type == "png":
            signature_ok = image_bytes.startswith(
                b"\x89PNG\r\n\x1a\n"
            )

        else:
            signature_ok = (
                image_bytes.startswith(b"RIFF")
                and image_bytes[8:12] == b"WEBP"
            )

        if not signature_ok:
            raise ValueError(
                "image_url media signature does "
                "not match its declared MIME type"
            )

        return value


class ChatImagePart(BaseModel):
    """One image attachment in a chat content-parts array."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]

    image_url: ChatImageURL


# Discriminated union ensures each content part
# is validated against its declared type.
ChatContentPart = Annotated[
    ChatTextPart | ChatImagePart,
    Field(discriminator="type"),
]

ChatStringContent = Annotated[
    str,
    Field(min_length=1, max_length=40_000),
]

ChatPartsContent = Annotated[
    list[ChatContentPart],
    Field(min_length=1, max_length=8),
]


class ChatMessage(BaseModel):
    """Backward-compatible text or multimodal chat message."""

    role: Literal[
        "system",
        "user",
        "assistant",
    ]

    content: ChatStringContent | ChatPartsContent

    @model_validator(mode="after")
    def validate_parts(self):
        if isinstance(self.content, list):

            # Only user messages can provide images.
            if self.role != "user":
                raise ValueError(
                    "Only user messages may contain "
                    "multimodal content"
                )

            # A text-only message should continue using
            # the existing string-based content contract.
            has_image = any(
                isinstance(part, ChatImagePart)
                for part in self.content
            )

            if not has_image:
                raise ValueError(
                    "Multipart content must include "
                    "an image_url part"
                )

        return self



class ChatResponseFormat(BaseModel):

    type: Literal["json_object", "text"]


class ChatCompletionRequest(BaseModel):

    # Matches the JSON chat payloads used by the selected n8n workflow.

    model: str | None = Field(default=None, max_length=100)

    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

    episode_title: str | None = Field(default=None, min_length=1, max_length=300)

    response_format: ChatResponseFormat | None = None

    temperature: float | None = Field(default=None, ge=0, le=2)

    max_tokens: int | None = Field(default=None, ge=1)

    stream: bool = False

    safe: str | None = Field(default=None, max_length=100)

    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    timeout_seconds: int | None = Field(default=None, ge=5, le=300)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | None = None



    # -------------------------------------------------------
    # MODULE 9E.1 — REQUEST-WIDE IMAGE COUNT LIMIT
    # -------------------------------------------------------
    # Individual messages can have multiple content parts,
    # but the complete chat request may contain at most
    # four images.
    # -------------------------------------------------------

    @model_validator(mode="after")
    def limit_images(self):
        image_count = sum(
            isinstance(part, ChatImagePart)
            for message in self.messages
            if isinstance(message.content, list)
            for part in message.content
        )

        if image_count > MAX_CHAT_IMAGES_PER_REQUEST:
            raise ValueError(
                "Chat request exceeds the four-image limit"
            )

        return self



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

    episode_title: str | None = Field(default=None, min_length=1, max_length=300)



    # Keep the reference list deliberately small. Scene generation should pass only

    # the assets that are actually needed for this scene, not the entire registry.

    reference_images: list[ImageReference] = Field(default_factory=list, max_length=10)



    request_id: str = Field(

        default_factory=lambda: str(uuid4()),

        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",

    )

    # If omitted, use the configured image default (600s unless changed).
    timeout_seconds: int | None = Field(default=None, ge=30, le=900)

    codex_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.5"] | None = None


class ModelSelection(BaseModel):

    model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]


@dataclass(frozen=True)

class RunResult:

    response: Any

    raw_response: str

    usage: dict[str, Any] | None

    web_search_used: bool = False

    thread_id: str | None = None


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


