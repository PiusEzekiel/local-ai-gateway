"""Module 6R.1: imports, original API/CLI compatibility, and module boundaries."""
from __future__ import annotations

from gateway import app as root
from gateway import config, contracts, codex_diagnostics, codex_runner, job_history, reference_images, schema_validation


def test_app_reexports_original_contracts_and_diagnostics():
    assert root.Settings is config.Settings
    assert root.GenerateRequest is contracts.GenerateRequest
    assert root.ImageReference is contracts.ImageReference
    assert root.ImageRunResult is contracts.ImageRunResult
    assert root.GatewayError is contracts.GatewayError
    assert root.JobHistory is job_history.JobHistory
    assert root.classify_codex_failure is codex_diagnostics.classify_codex_failure
    assert root.resolve_schema is schema_validation.resolve_schema
    assert issubclass(root.CodexRunner, codex_runner.CodexRunner)


def test_original_api_routes_are_registered():
    # FastAPI >= 0.137 keeps included routes behind an _IncludedRouter wrapper.
    # OpenAPI describes effective registered HTTP paths across both layouts.
    paths = {
        (path, method.upper())
        for path, path_item in root.app.openapi()["paths"].items()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    }
    required = {
        ('/health', 'GET'), ('/v1/models', 'GET'), ('/v1/models/default', 'PUT'),
        ('/v1/chat/completions', 'POST'), ('/v1/generate', 'POST'),
        ('/v1/research', 'POST'), ('/v1/images/generations', 'POST'),
        ('/dashboard/api/diagnostics', 'GET'), ('/dashboard/api/diagnostics/summary', 'GET'),
        ('/dashboard/api/diagnostics/{job_id}', 'GET'),
        ('/dashboard/api/diagnostics/{job_id}/bundle', 'GET'),
        ('/dashboard/api/gallery', 'GET'), ('/dashboard/api/summary', 'GET'),
    }
    assert required <= paths


def test_schema_validation_does_not_depend_on_fastapi_app():
    request = contracts.GenerateRequest(prompt='hello', output_format='json', output_schema={
        'type': 'object', 'properties': {'foo': {'type': 'string'}}
    })
    assert schema_validation.resolve_schema(request) == request.output_schema
    assert schema_validation.resolve_schema(contracts.GenerateRequest(prompt='hello')) is None


def test_reference_images_uses_allowlist_and_magic_bytes():
    assert reference_images.allowed_reference_host('drive.google.com')
    assert reference_images.allowed_reference_host('drive.usercontent.google.com')
    assert not reference_images.allowed_reference_host('drive.google.com.evil.example')
    assert reference_images.detect_image_extension(b'\x89PNG\r\n\x1a\nfoo') == '.png'
    assert reference_images.detect_image_extension(b'\xff\xd8\xfffoo') == '.jpg'
