"""Validated JSON output schemas for the Codex text runner."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from .contracts import GatewayError, GenerateRequest

SCHEMA_DIR = Path(__file__).parent / "schemas"
MAX_SCHEMA_BYTES = 16_384


def resolve_schema(request: GenerateRequest) -> dict[str, Any] | None:

    if request.output_format == "text":

        if request.schema_id or request.output_schema:

            raise GatewayError("invalid_request", "A schema requires output_format=json.", 422)

        return None

    if request.schema_id and request.output_schema:

        raise GatewayError("invalid_request", "Provide schema_id or output_schema, not both.", 422)

    if request.schema_id:

        if request.schema_id != "stock_queries_v1":

            raise GatewayError("invalid_request", "Unknown schema_id.", 422)

        schema = json.loads((SCHEMA_DIR / "stock_queries_v1.json").read_text(encoding="utf-8"))

    elif request.output_schema:

        schema = request.output_schema

    else:

        raise GatewayError("invalid_request", "JSON output requires schema_id or output_schema.", 422)



    serialized = json.dumps(schema, separators=(",", ":"))

    if len(serialized.encode("utf-8")) > MAX_SCHEMA_BYTES:

        raise GatewayError("invalid_request", "Output schema is too large.", 422)



    def reject_refs(value: Any, depth: int = 0) -> None:

        if depth > 20:

            raise GatewayError("invalid_request", "Output schema is too deeply nested.", 422)

        if isinstance(value, dict):

            if "$ref" in value or "$dynamicRef" in value:

                raise GatewayError("invalid_request", "Schema references are not supported.", 422)

            for item in value.values():

                reject_refs(item, depth + 1)

        elif isinstance(value, list):

            for item in value:

                reject_refs(item, depth + 1)



    reject_refs(schema)

    try:

        Draft202012Validator.check_schema(schema)

    except SchemaError as exc:

        raise GatewayError("invalid_request", f"Invalid output schema: {exc.message}", 422) from exc

    if schema.get("type") != "object":

        raise GatewayError("invalid_request", "Output schema root must have type=object.", 422)

    return schema


