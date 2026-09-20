"""Isolated Codex exec runner for text and image requests."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable
from uuid import UUID
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from .contracts import GatewayError, ImageReference, ImageRunResult, RunResult
from .codex_diagnostics import codex_failure_diagnostic, codex_event_summary, classify_codex_failure
from .reference_images import download_reference_image

LOG = logging.getLogger("uvicorn.error")

def extract_usage(stdout: bytes) -> dict[str, Any] | None:

    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):

        try:

            event = json.loads(line)

        except json.JSONDecodeError:

            continue

        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):

            return event["usage"]

    return None


def used_web_search(stdout: bytes) -> bool:

    for line in stdout.decode("utf-8", errors="replace").splitlines():

        try:

            event = json.loads(line)

        except json.JSONDecodeError:

            continue

        item = event.get("item")

        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "web_search":

            return True

    return False


class CodexRunner:

    def __init__(self, command: list[str], model: str | None = None):

        self.command = command

        self.model = model



    @staticmethod

    async def _communicate(process: asyncio.subprocess.Process, timeout_seconds: float,

                           progress: Callable[[str], None] | None = None) -> tuple[bytes, bytes]:

        async def read_stdout() -> bytes:

            chunks: list[bytes] = []

            assert process.stdout is not None

            while line := await process.stdout.readline():

                chunks.append(line)

                if progress:

                    try:

                        event = json.loads(line)

                    except json.JSONDecodeError:

                        continue

                    event_type = event.get("type")

                    item = event.get("item") if isinstance(event.get("item"), dict) else {}

                    if event_type == "thread.started":

                        progress("Codex session started")

                    elif event_type == "turn.started":

                        progress("Codex is working")

                    elif event_type == "item.started" and item.get("type") == "web_search":

                        progress("Searching the web")

                    elif event_type == "item.started" and item.get("type") in {"image_generation", "image_generation_call"}:

                        progress("Generating image")

                    elif event_type == "turn.completed":

                        progress("Finalizing response")

            return b"".join(chunks)



        assert process.stderr is not None

        stdout_task = asyncio.create_task(read_stdout())

        stderr_task = asyncio.create_task(process.stderr.read())

        try:

            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)

            return await stdout_task, await stderr_task

        except (asyncio.TimeoutError, asyncio.CancelledError):

            if process.returncode is None:

                process.kill()

            await process.wait()

            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

            raise



    async def run(
        self,
        *,
        prompt: str,
        schema: dict[str, Any] | None,
        web_search: bool,
        timeout_seconds: float,
        model: str | None = None,
        request_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> RunResult:
        """Run a normal text/JSON Codex request with concise operational logging."""
        log_request_id = request_id or "unknown"
        selected_model = model or self.model or "default"

        LOG.info(
            "codex_text_run_start request_id=%s model=%s web_search=%s schema=%s "
            "prompt_chars=%s timeout_seconds=%.2f",
            log_request_id,
            selected_model,
            web_search,
            "yes" if schema is not None else "no",
            len(prompt),
            timeout_seconds,
        )

        with tempfile.TemporaryDirectory(prefix="codex-gateway-") as workdir:
            output_path = Path(workdir) / "result.txt"

            args = [
                *self.command,
                "--ask-for-approval", "never",
                "--disable", "shell_tool",
                "--disable", "apps",
                "--disable", "browser_use",
                "--disable", "computer_use",
                "--disable", "multi_agent",
            ]

            if web_search:
                args.append("--search")
            else:
                args.extend(["-c", 'web_search="disabled"'])

            args.extend([
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--json",
                "-C", workdir,
                "-o", str(output_path),
            ])

            if model or self.model:
                args.extend(["--model", model or self.model])

            if schema is not None:
                schema_path = Path(workdir) / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                args.extend(["--output-schema", str(schema_path)])

            # The text route still uses a positional prompt because it does not
            # attach --image arguments and therefore is not affected by the
            # current greedy --image parsing issue.
            args.append(prompt)

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            except OSError as exc:
                LOG.exception(
                    "codex_text_launch_failed request_id=%s exception=%s",
                    log_request_id,
                    type(exc).__name__,
                )
                raise GatewayError(
                    "codex_unavailable",
                    "Could not start Codex CLI.",
                    503,
                ) from exc

            try:
                stdout, stderr = await self._communicate(
                    process,
                    timeout_seconds,
                    progress,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass

                if isinstance(exc, asyncio.CancelledError):
                    raise

                LOG.warning(
                    "codex_text_timeout request_id=%s timeout_seconds=%.2f",
                    log_request_id,
                    timeout_seconds,
                )
                raise GatewayError(
                    "timeout",
                    "Codex did not complete before the timeout.",
                    504,
                ) from exc

            event_summary = codex_event_summary(stdout)

            LOG.info(
                "codex_text_exit request_id=%s exit_code=%s stdout_bytes=%s stderr_bytes=%s events=%s",
                log_request_id,
                process.returncode,
                len(stdout),
                len(stderr),
                event_summary,
            )

            if process.returncode != 0:
                diagnostic = codex_failure_diagnostic(stderr, stdout)
                error = classify_codex_failure(
                    stderr,
                    stdout,
                    process.returncode,
                )

                LOG.warning(
                    "codex_text_failed request_id=%s kind=%s exit_code=%s diagnostic=%s",
                    log_request_id,
                    error.kind,
                    process.returncode,
                    diagnostic,
                )
                # Transport CLI diagnostics to JobHistory without changing the
                # public GatewayError message returned to n8n.
                error.exit_code = process.returncode
                error.diagnostic = diagnostic
                raise error

            try:
                raw = output_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                LOG.warning(
                    "codex_text_output_missing request_id=%s output_file=%s",
                    log_request_id,
                    output_path.name,
                )
                raise GatewayError(
                    "invalid_response",
                    "Codex did not write an output file.",
                    502,
                ) from exc

            if not raw:
                LOG.warning(
                    "codex_text_output_empty request_id=%s",
                    log_request_id,
                )
                raise GatewayError(
                    "invalid_response",
                    "Codex returned an empty response.",
                    502,
                )

            if schema is None:
                response: Any = raw
            else:
                try:
                    response = json.loads(raw)
                    Draft202012Validator(schema).validate(response)
                except (json.JSONDecodeError, ValidationError) as exc:
                    LOG.warning(
                        "codex_text_schema_invalid request_id=%s exception=%s",
                        log_request_id,
                        type(exc).__name__,
                    )
                    raise GatewayError(
                        "invalid_response",
                        "Codex returned JSON that did not match the schema.",
                        502,
                    ) from exc

            LOG.info(
                "codex_text_run_completed request_id=%s output_chars=%s",
                log_request_id,
                len(raw),
            )

            return RunResult(
                response=response,
                raw_response=raw,
                usage=extract_usage(stdout),
                web_search_used=used_web_search(stdout),
            )


    async def run_image(
        self,
        *,
        prompt: str,
        timeout_seconds: float,
        reference_images: list[ImageReference] | None = None,
        model: str | None = None,
        request_id: str | None = None,
        progress: Callable[[str], None] | None = None,
        reference_ready: Callable[[int, ImageReference, Path], None] | None = None,
        reference_downloader: Callable[[ImageReference, Path, int, str], Path] | None = None,
    ) -> ImageRunResult:
        """Generate one image and return its artifact data and completion telemetry.

        Reference-image flow:
        1. The gateway downloads each allow-listed URL itself.
        2. Files live only inside this temporary work directory.
        3. Each local file is attached to `codex exec` with --image.
        4. The generation prompt is sent through stdin, not positionally.
        5. Codex web/browser/shell access remains disabled.

        Logging intentionally records execution metadata only. It never logs the
        full scene prompt or the full reference-image URLs.
        """
        log_request_id = request_id or "unknown"
        selected_model = model or self.model or "default"
        reference_images = reference_images or []

        LOG.info(
            "image_run_start request_id=%s model=%s reference_count=%s prompt_chars=%s timeout_seconds=%.2f",
            log_request_id,
            selected_model,
            len(reference_images),
            len(prompt),
            timeout_seconds,
        )

        with tempfile.TemporaryDirectory(prefix="codex-image-gateway-") as workdir:
            workdir_path = Path(workdir)
            reference_paths: list[Path] = []

            # -----------------------------------------------------------
            # Download and validate all visual references
            # -----------------------------------------------------------
            # urlopen is blocking, so each download runs in a worker thread.
            # A failed reference is treated as a hard failure because silently
            # generating without it would break visual continuity.
            # -----------------------------------------------------------

            for index, reference in enumerate(reference_images):
                if progress:
                    progress(
                        f"Downloading reference image {index + 1}/{len(reference_images)}"
                    )

                reference_path = await asyncio.to_thread(
                    reference_downloader or download_reference_image,
                    reference,
                    workdir_path,
                    index,
                    log_request_id,
                )

                reference_paths.append(reference_path)

                if reference_ready:
                    try:
                        reference_ready(index, reference, reference_path)
                    except Exception:
                        LOG.exception(
                            "reference_artifact_callback_failed request_id=%s index=%s",
                            log_request_id, index + 1,
                        )

            LOG.info(
                "image_references_ready request_id=%s count=%s files=%s",
                log_request_id,
                len(reference_paths),
                ",".join(path.name for path in reference_paths) or "none",
            )

            if progress and reference_paths:
                progress("Reference images ready")

            # -----------------------------------------------------------
            # Build one image-generation instruction
            # -----------------------------------------------------------
            # The prompt itself is deliberately NOT logged.
            # -----------------------------------------------------------

            image_generation_prompt = (
                "$imagegen Generate exactly one image from this prompt. "
                "Use the built-in image generation tool. "
                "Do not use the shell or any other tool. "
            )

            if reference_paths:
                image_generation_prompt += (
                    "The attached images are visual reference assets. "
                    "Use them to preserve recognizable appearance and continuity for "
                    "the corresponding subjects. Their local filenames contain the "
                    "asset IDs. Do not copy white reference backgrounds into the final scene. "
                )

            image_generation_prompt += "\n\nPrompt:\n" + prompt

            # -----------------------------------------------------------
            # Build Codex CLI arguments
            # -----------------------------------------------------------
            # IMPORTANT:
            # - --image belongs to the `exec` command.
            # - The prompt is NOT appended as a positional argument.
            # - Prompt text is written through stdin instead.
            #
            # Current Codex CLI builds define --image as a greedy multi-value
            # option, so stdin avoids the prompt being misread as another file.
            # -----------------------------------------------------------

            args = [
                *self.command,
                "--ask-for-approval", "never",
                "--disable", "shell_tool",
                "--disable", "apps",
                "--disable", "browser_use",
                "--disable", "computer_use",
                "--disable", "multi_agent",
                "--enable", "image_generation",
                "-c", 'web_search="disabled"',
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--json",
                "-C", workdir,
            ]

            if model or self.model:
                args.extend(["--model", model or self.model])

            # Repeat --image once per local reference image.
            for reference_path in reference_paths:
                args.extend(["--image", str(reference_path)])

            # Log only the safe command shape, not full temp paths or prompt.
            LOG.info(
                "image_codex_launch request_id=%s exe=%s model=%s reference_count=%s "
                "stdin_prompt_bytes=%s sandbox=read-only web_search=disabled",
                log_request_id,
                Path(self.command[0]).name if self.command else "codex",
                selected_model,
                len(reference_paths),
                len(image_generation_prompt.encode("utf-8")),
            )

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            if progress:
                progress("Launching Codex")

            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=workdir,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            except OSError as exc:
                LOG.exception(
                    "image_codex_launch_failed request_id=%s exception=%s",
                    log_request_id,
                    type(exc).__name__,
                )
                raise GatewayError(
                    "codex_unavailable",
                    "Could not start Codex CLI.",
                    503,
                ) from exc

            LOG.info(
                "image_codex_process_started request_id=%s pid=%s",
                log_request_id,
                process.pid,
            )

            # -----------------------------------------------------------
            # Send prompt through stdin
            # -----------------------------------------------------------
            # If the CLI exits during argument parsing, drain() can fail with
            # BrokenPipeError/ConnectionResetError. We record that but still
            # collect stdout/stderr so the real CLI error is preserved.
            # -----------------------------------------------------------

            stdin_error: Exception | None = None
            try:
                assert process.stdin is not None
                process.stdin.write(image_generation_prompt.encode("utf-8"))
                await process.stdin.drain()
                LOG.info(
                    "image_codex_stdin_sent request_id=%s bytes=%s",
                    log_request_id,
                    len(image_generation_prompt.encode("utf-8")),
                )
            except (BrokenPipeError, ConnectionResetError) as exc:
                stdin_error = exc
                LOG.warning(
                    "image_codex_stdin_failed request_id=%s exception=%s",
                    log_request_id,
                    type(exc).__name__,
                )
            finally:
                if process.stdin is not None:
                    process.stdin.close()

            # -----------------------------------------------------------
            # Wait for Codex while preserving streamed progress events
            # -----------------------------------------------------------

            try:
                stdout, stderr = await self._communicate(
                    process,
                    timeout_seconds,
                    progress,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass

                if isinstance(exc, asyncio.CancelledError):
                    raise

                LOG.warning(
                    "image_codex_timeout request_id=%s timeout_seconds=%.2f",
                    log_request_id,
                    timeout_seconds,
                )
                raise GatewayError(
                    "timeout",
                    "Image generation did not complete before the timeout.",
                    504,
                ) from exc

            event_summary = codex_event_summary(stdout)

            LOG.info(
                "image_codex_exit request_id=%s exit_code=%s stdout_bytes=%s stderr_bytes=%s "
                "events=%s stdin_error=%s",
                log_request_id,
                process.returncode,
                len(stdout),
                len(stderr),
                event_summary,
                type(stdin_error).__name__ if stdin_error else "none",
            )

            # A non-zero exit code is a CLI/Codex failure, not an image-file
            # discovery problem. Log a concise diagnostic before classifying it.
            if process.returncode != 0:
                diagnostic = codex_failure_diagnostic(stderr, stdout)

                LOG.warning(
                    "image_codex_failed request_id=%s exit_code=%s events=%s diagnostic=%s",
                    log_request_id,
                    process.returncode,
                    event_summary,
                    diagnostic,
                )

                # Module 6B: attach *redacted* CLI facts for persistent history.
                error = classify_codex_failure(stderr, stdout, process.returncode)
                error.exit_code = process.returncode
                error.diagnostic = diagnostic
                raise error

            # Even on exit code 0, non-empty stderr can contain useful warnings.
            if stderr.strip():
                LOG.warning(
                    "image_codex_stderr request_id=%s diagnostic=%s",
                    log_request_id,
                    codex_failure_diagnostic(stderr, b""),
                )

            # -----------------------------------------------------------
            # Locate the Codex thread and generated image
            # -----------------------------------------------------------

            thread_id = None

            for line in stdout.decode("utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "thread.started":
                    thread_id = event.get("thread_id")
                    break

            LOG.info(
                "image_codex_thread request_id=%s thread_id=%s",
                log_request_id,
                thread_id or "missing",
            )

            try:
                parsed_thread_id = UUID(thread_id) if isinstance(thread_id, str) else None
            except ValueError:
                parsed_thread_id = None

            if parsed_thread_id is None:
                LOG.warning(
                    "image_thread_missing request_id=%s events=%s diagnostic=%s",
                    log_request_id,
                    event_summary,
                    codex_failure_diagnostic(stderr, stdout),
                )
                raise GatewayError(
                    "invalid_response",
                    "Codex did not report an image job ID.",
                    502,
                )

            codex_home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
            image_dir = codex_home / "generated_images" / thread_id

            image_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }

            images = [
                path
                for path in image_dir.glob("*")
                if path.is_file() and path.suffix.lower() in image_types
            ]

            LOG.info(
                "image_output_scan request_id=%s directory_exists=%s candidate_count=%s files=%s",
                log_request_id,
                image_dir.is_dir(),
                len(images),
                ",".join(path.name for path in images) or "none",
            )

            if not images:
                LOG.warning(
                    "image_output_missing request_id=%s thread_id=%s image_dir=%s events=%s",
                    log_request_id,
                    thread_id,
                    str(image_dir),
                    event_summary,
                )
                raise GatewayError(
                    "invalid_response",
                    "Codex completed but did not save an image file.",
                    502,
                )

            image_path = max(images, key=lambda path: path.stat().st_mtime)
            mime_type = image_types[image_path.suffix.lower()]

            LOG.info(
                "image_output_ready request_id=%s file=%s bytes=%s mime_type=%s",
                log_request_id,
                image_path.name,
                image_path.stat().st_size,
                mime_type,
            )

            return ImageRunResult(
                path=image_path,
                mime_type=mime_type,
                usage=extract_usage(stdout),
                thread_id=thread_id,
            )


