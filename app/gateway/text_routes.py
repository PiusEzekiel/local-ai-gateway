"""Text/JSON/research/chat API; preserve n8n response and error contracts."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .contracts import ChatCompletionRequest, GatewayError, GenerateRequest
from .generation_state import GenerationContext
from .schema_validation import resolve_schema

LOG = logging.getLogger("uvicorn.error")


def create_text_router(ctx: GenerationContext) -> APIRouter:
    router = APIRouter()

    async def handle(request: GenerateRequest, task: Literal["generate", "research"],
                     operation: str | None = None) -> JSONResponse:

        started = time.monotonic()

        model = request.codex_model or ctx.runtime.selected_model

        def error_response(exc: GatewayError) -> JSONResponse:

            return JSONResponse(

                status_code=exc.status_code,

                content={

                    "success": False,

                    "request_id": request.request_id,

                    "task": task,

                    "model": model,

                    "status": "failed",

                    "response": None,

                    "raw_response": None,

                    "duration_ms": round((time.monotonic() - started) * 1000),

                    "usage": None,

                    "web_search_used": False,

                    "error": {"type": exc.kind, "message": exc.message},

                },

            )

        try:

            schema = resolve_schema(request)

        except GatewayError as exc:

            return error_response(exc)

        timeout = request.timeout_seconds or ctx.settings.default_timeout_seconds

        timeout = min(timeout, ctx.settings.max_timeout_seconds)

        job = ctx.history.add(request.request_id, task, model, operation=operation)
        # For chat, handle() receives the assembled role-labelled user request.
        # Never copy it into JobHistory or any /v1 response besides the normal API.
        ctx.retain_prompt(job, request.prompt)

        async with ctx.counter_lock:

            if ctx.runtime.pending >= ctx.settings.max_concurrency + ctx.settings.max_queue:

                error = GatewayError("busy", "Gateway queue is full; retry later.", 429)

                ctx.history.update(job, status="failed", stage="Queue full", error=error)

                return error_response(error)

            ctx.runtime.pending += 1
            ctx.worker_change()

        acquired = False

        try:

            try:

                await asyncio.wait_for(ctx.slots.acquire(), timeout=timeout)

                acquired = True

            except asyncio.TimeoutError as exc:

                raise GatewayError("timeout", "Timed out waiting for a Codex worker.", 504) from exc

            ctx.history.worker_acquired(job, round((time.monotonic() - started) * 1000))

            remaining = timeout - (time.monotonic() - started)

            if remaining <= 0:

                raise GatewayError("timeout", "Timed out waiting for a Codex worker.", 504)

            ctx.history.codex_started(job, "Codex generating response")

            prompt = request.prompt

            if task == "research":

                prompt = (

                    "Use native web search to verify current information. Include source URLs "

                    "when the requested output format permits.\n\nRequest:\n" + request.prompt

                )

            result = await ctx.runner.run(
                prompt=prompt,
                schema=schema,
                web_search=(task == "research"),
                timeout_seconds=remaining,
                model=model,
                request_id=request.request_id,
                progress=lambda stage: ctx.history.update(
                    job,
                    status="running",
                    stage=stage,
                ),
            )

            duration_ms = round((time.monotonic() - started) * 1000)

            ctx.history.codex_completed(job)

            ctx.history.usage(job, result.usage)

            ctx.history.update(job, status="completed", stage="Response ready")
            ctx.retain_output(job, result.raw_response)

            return JSONResponse(content={

                "success": True,

                "request_id": request.request_id,

                "task": task,

                "model": model,

                "status": "completed",

                "response": result.response,

                "raw_response": result.raw_response,

                "duration_ms": duration_ms,

                "usage": result.usage,

                "web_search_used": result.web_search_used,

                "error": None,

            })

        except GatewayError as exc:

            ctx.history.update(job, status="failed", stage="Codex request failed", error=exc)

            return error_response(exc)

        except Exception as exc:
            LOG.exception(
                "gateway_request_unexpected_failure request_id=%s task=%s exception=%s",
                request.request_id,
                task,
                type(exc).__name__,
            )
            ctx.history.update(
                job,
                status="failed",
                stage="Gateway failed",
                error=GatewayError(
                    "internal_error",
                    "The gateway failed unexpectedly.",
                    500,
                ),
            )
            raise

        finally:

            if acquired:

                ctx.slots.release()

            async with ctx.counter_lock:

                ctx.runtime.pending -= 1
                ctx.worker_change()

    @router.post("/v1/generate", dependencies=[Depends(ctx.require_token)])

    async def generate(request: GenerateRequest) -> JSONResponse:

        operation = "structured" if request.output_format == "json" else "generate"
        return await handle(request, "generate", operation)

    @router.post("/v1/research", dependencies=[Depends(ctx.require_token)])

    async def research(request: GenerateRequest) -> JSONResponse:

        return await handle(request, "research", "research")

    @router.post("/v1/chat/completions", dependencies=[Depends(ctx.require_token)])

    async def chat_completions(request: ChatCompletionRequest) -> JSONResponse:

        if request.stream:

            raise GatewayError("invalid_request", "Streaming is not supported.", 422)

        prompt = "Complete the following conversation. Follow the system instructions and answer the latest user request.\n\n"

        prompt += "\n\n".join(f"[{message.role}]\n{message.content}" for message in request.messages)

        json_mode = request.response_format is not None and request.response_format.type == "json_object"

        if json_mode:

            prompt += "\n\nReturn exactly one valid JSON object. Do not include markdown or commentary."

        if len(prompt) > 80_000:

            raise GatewayError("invalid_request", "Combined messages exceed 80,000 characters.", 422)

        internal = GenerateRequest(

            request_id=request.request_id,

            prompt=prompt,

            output_format="text",

            timeout_seconds=request.timeout_seconds,

            codex_model=request.codex_model,

        )

        result = await handle(internal, "generate", "chat")

        if result.status_code != 200:

            return result

        data = json.loads(result.body)

        content = data["response"]

        if json_mode:

            try:

                parsed = json.loads(content)

            except json.JSONDecodeError as exc:

                raise GatewayError("invalid_response", "Codex did not return a valid JSON object.", 502) from exc

            if not isinstance(parsed, dict):

                raise GatewayError("invalid_response", "Codex did not return a JSON object.", 502)

            content = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

        codex_usage = data["usage"] or {}

        input_tokens = codex_usage.get("input_tokens")

        output_tokens = codex_usage.get("output_tokens")

        usage = {

            "prompt_tokens": input_tokens,

            "completion_tokens": output_tokens,

            "total_tokens": (input_tokens + output_tokens) if isinstance(input_tokens, int) and isinstance(output_tokens, int) else codex_usage.get("total_tokens"),

        }

        return JSONResponse(content={

            "id": "chatcmpl-" + request.request_id,

            "object": "chat.completion",

            "created": int(time.time()),

            "model": data["model"],

            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],

            "usage": usage,

        })

    return router
