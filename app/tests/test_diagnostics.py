"""Module 6A tests: preserve useful error details without leaking secrets."""

from gateway.diagnostics import build_diagnostic_bundle, redact_diagnostic


def test_bearer_token_is_redacted():
    result = redact_diagnostic("Authorization: Bearer myLongPrivateToken12345")
    assert "myLongPrivateToken12345" not in result
    assert "[REDACTED]" in result


def test_bare_bearer_token_is_redacted():
    result = redact_diagnostic("Bearer myLongPrivateToken12345")
    assert "myLongPrivateToken12345" not in result


def test_json_and_environment_secrets_are_redacted():
    source = '''"api_key": "secretA123", password=hunter2, AI_GATEWAY_TOKEN=privateB456'''
    result = redact_diagnostic(source)
    for secret in ("secretA123", "hunter2", "privateB456"):
        assert secret not in result


def test_google_drive_url_and_query_id_are_redacted():
    source = "GET https://drive.google.com/uc?id=privateDriveId123&export=download"
    result = redact_diagnostic(source)
    assert "privateDriveId123" not in result
    assert "[REDACTED_URL]" in result


def test_redirect_url_is_redacted():
    result = redact_diagnostic("redirected to https://drive.usercontent.google.com/download?token=private")
    assert "private" not in result


def test_windows_and_posix_paths_are_redacted():
    source = r"ref=C:\Users\piuse\Documents\private.png and /home/user/.codex/auth.json"
    result = redact_diagnostic(source)
    assert "piuse" not in result
    assert "auth.json" not in result
    assert result.count("[REDACTED_PATH]") == 2


def test_cli_argument_names_and_exit_error_remain_readable():
    result = redact_diagnostic("error: unexpected argument '--image'; exit code 2")
    assert "--image" in result
    assert "exit code 2" in result


def test_preview_is_bounded():
    result = redact_diagnostic("x" * 4000, max_chars=100)
    assert result.startswith("x" * 100)
    assert result.endswith("[truncated]")


def test_bytes_and_none_are_supported():
    assert "--image" in redact_diagnostic(b"invalid --image flag")
    assert redact_diagnostic(None) == ""


def test_diagnostic_bundle_uses_only_allowlisted_fields():
    bundle = build_diagnostic_bundle(
        request_id="scene_image_1_initial",
        task="image",
        model="gpt-5.6-luna",
        error_type="codex_cli_argument_error",
        message="Unexpected argument --image",
        last_successful_stage="references_ready",
        exit_code=2,
        duration_ms=3656,
        reference_count=2,
        references_downloaded=2,
        stderr="Authorization: Bearer myLongPrivateToken12345 at "
               "https://drive.google.com/uc?id=secretDriveId",
    )
    assert bundle["exit_code"] == 2
    assert bundle["reference_count"] == 2
    assert bundle["references_downloaded"] == 2
    assert "myLongPrivateToken12345" not in str(bundle)
    assert "secretDriveId" not in str(bundle)
    assert "prompt" not in bundle
    assert "authorization" not in bundle


def test_negative_duration_and_counts_are_clamped():
    bundle = build_diagnostic_bundle(
        request_id="x", task="image", model="luna", error_type="failed",
        duration_ms=-5, reference_count=-2, references_downloaded=-1,
    )
    assert bundle["duration_ms"] == 0
    assert bundle["reference_count"] == 0
    assert bundle["references_downloaded"] == 0
