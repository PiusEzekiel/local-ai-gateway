"""Validated, atomic, local-only gateway settings (Module 7A).

Only the selected model is live-editable in this release. Other settings are
validated and saved but take effect on gateway restart. Bearer tokens, Codex
credentials, executable paths and internal data-directory paths are NEVER
configurable or returned by this module.

Precedence for restart-required fields: explicitly configured environment
variable > validated saved JSON > built-in defaults. The saved default-model
setting retains its pre-7A behavior and takes precedence over AI_GATEWAY_MODEL.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Mapping

from .config import Settings

LOG = logging.getLogger("uvicorn.error")


class SettingsValidationError(ValueError):
    """Invalid editable setting or unsafe/corrupt configuration file."""


@dataclass(frozen=True)
class FieldSpec:
    kind: type
    default: Any
    restart_required: bool
    minimum: int | None = None
    maximum: int | None = None
    env: str | None = None
    choices: tuple[str, ...] = ()
    label: str = ""
    group: str = ""


# Never add api_token, codex_exe or any credential field here.
FIELD_SPECS: dict[str, FieldSpec] = {
    "model": FieldSpec(str, "gpt-5.6-luna", False, env="AI_GATEWAY_MODEL", label="Default model", group="model"),
    "max_concurrency": FieldSpec(int, 1, True, 1, 16, "AI_GATEWAY_MAX_CONCURRENCY", label="Concurrent workers", group="execution"),
    "max_queue": FieldSpec(int, 4, True, 0, 128, "AI_GATEWAY_MAX_QUEUE", label="Maximum queued jobs", group="execution"),
    "default_timeout_seconds": FieldSpec(int, 120, True, 5, 300, "AI_GATEWAY_TIMEOUT_SECONDS", label="Default text / research timeout", group="timeouts"),
    "max_timeout_seconds": FieldSpec(int, 300, True, 5, 300, "AI_GATEWAY_MAX_TIMEOUT_SECONDS", label="Maximum text / research timeout", group="timeouts"),
    "image_timeout_seconds": FieldSpec(int, 600, True, 30, 900, "AI_GATEWAY_IMAGE_TIMEOUT_SECONDS", label="Default image timeout", group="timeouts"),
    "quota_monitor_enabled": FieldSpec(bool, True, True, env="AI_GATEWAY_QUOTA_MONITOR", label="Quota monitor", group="quota"),
    "quota_poll_seconds": FieldSpec(int, 45, True, 15, 3600, "AI_GATEWAY_QUOTA_POLL_SECONDS", label="Quota refresh interval", group="quota"),
    "quota_warning_remaining_percent": FieldSpec(int, 20, True, 1, 100, "AI_GATEWAY_QUOTA_WARNING_PERCENT", label="Quota warning threshold", group="quota"),
    "quota_critical_remaining_percent": FieldSpec(int, 10, True, 0, 99, "AI_GATEWAY_QUOTA_CRITICAL_PERCENT", label="Quota critical threshold", group="quota"),
    # Opt-in for plaintext content. Never store prompt/output bodies by default.
    "store_prompts": FieldSpec(bool, False, True, env="AI_GATEWAY_STORE_PROMPTS", label="Retain prompt text", group="privacy"),
    "store_outputs": FieldSpec(bool, False, True, env="AI_GATEWAY_STORE_OUTPUTS", label="Retain generated text", group="privacy"),
    "store_diagnostics": FieldSpec(bool, True, True, env="AI_GATEWAY_STORE_DIAGNOSTICS", label="Retain safe diagnostic previews", group="privacy"),
    "auto_cleanup_enabled": FieldSpec(bool, False, True, env="AI_GATEWAY_AUTO_CLEANUP", label="Enable automatic cleanup", group="retention"),
    "history_retention_days": FieldSpec(int, 90, True, 1, 3650, "AI_GATEWAY_HISTORY_RETENTION_DAYS", label="Completed job history (days)", group="retention"),
    "artifact_retention_days": FieldSpec(int, 30, True, 1, 3650, "AI_GATEWAY_ARTIFACT_RETENTION_DAYS", label="Image/reference retention (days)", group="retention"),
    "quota_snapshot_retention_days": FieldSpec(int, 30, True, 1, 3650, "AI_GATEWAY_QUOTA_RETENTION_DAYS", label="Quota snapshot retention (days)", group="retention"),
    "max_artifact_storage_mb": FieldSpec(int, 10240, True, 1, 1000000, "AI_GATEWAY_MAX_ARTIFACT_MB", label="Artifact storage limit (MB)", group="retention"),
    "cleanup_interval_hours": FieldSpec(int, 24, True, 1, 168, "AI_GATEWAY_CLEANUP_INTERVAL_HOURS", label="Automatic cleanup interval (hours)", group="retention"),
}


def _validate_field(key: str, value: Any, models: tuple[str, ...]) -> Any:
    spec = FIELD_SPECS[key]
    if spec.kind is bool:
        if type(value) is not bool:
            raise SettingsValidationError(f"{key} must be a boolean.")
    elif spec.kind is int:
        if type(value) is not int or not spec.minimum <= value <= spec.maximum:
            raise SettingsValidationError(f"{key} must be an integer from {spec.minimum} to {spec.maximum}.")
    elif spec.kind is str:
        if type(value) is not str or value not in models:
            raise SettingsValidationError(f"{key} must be one of the available models.")
    return value


def _validate_relationships(values: Mapping[str, Any]) -> None:
    if values["default_timeout_seconds"] > values["max_timeout_seconds"]:
        raise SettingsValidationError("Default timeout must not exceed maximum text timeout.")
    if values["quota_critical_remaining_percent"] >= values["quota_warning_remaining_percent"]:
        raise SettingsValidationError("Critical quota threshold must be below warning threshold.")


def apply_saved_settings(base: Settings, saved: Mapping[str, Any],
                         models: tuple[str, ...], environ: Mapping[str, str] | None = None) -> Settings:
    """Derive effective startup settings without touching secrets or process paths.

    Environment overrides restart-required settings, while saved model wins as
    it always did in prior gateway versions. Invalid/malformed saved values are
    ignored with safe key-only warnings so they cannot prevent startup.
    """
    env = os.environ if environ is None else environ
    updates: dict[str, Any] = {}
    for key, spec in FIELD_SPECS.items():
        if key not in saved:
            continue
        if key != "model" and spec.env in env:
            continue
        try:
            updates[key] = _validate_field(key, saved[key], models)
        except SettingsValidationError:
            LOG.warning("saved_setting_invalid key=%s", key)
    values = {key: getattr(base, key) for key in FIELD_SPECS}
    values.update(updates)
    try:
        _validate_relationships(values)
    except SettingsValidationError:
        # Revert only the conflicting saved pair; keep valid independent fields.
        for key in (
            "default_timeout_seconds", "max_timeout_seconds",
            "quota_critical_remaining_percent", "quota_warning_remaining_percent",
        ):
            if key in updates:
                values[key] = getattr(base, key)
                updates.pop(key)
        LOG.warning("saved_settings_relationship_invalid using_startup_defaults")
    return replace(base, **updates)


class SettingsManager:
    def __init__(self, path: Path, models: tuple[str, ...]):
        self.path = Path(path)
        self.models = tuple(models)
        self._lock = RLock()

    def _read(self, *, strict: bool) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if strict:
                raise SettingsValidationError("Settings file could not be read.") from exc
            LOG.warning("settings_load_failed type=%s", type(exc).__name__)
            return {}
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("root is not an object")
            return value
        except (ValueError, UnicodeError) as exc:
            if strict:
                raise SettingsValidationError("Settings file is not a valid JSON object; repair it before saving.") from exc
            LOG.warning("settings_json_invalid using_defaults")
            return {}

    def load(self) -> dict[str, Any]:
        """Preserve old API, including unrelated keys used by older installs."""
        with self._lock:
            return self._read(strict=False)

    def _write(self, data: dict[str, Any]) -> None:
        """Atomic same-directory write; a failed replace leaves previous file intact."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def update(self, patch: Mapping[str, Any], *, effective: Settings | None = None) -> dict[str, Any]:
        """Validate entire candidate document before writing anything.

        Only allowlisted fields are editable; preserve unknown preexisting keys.
        `effective` provides the running values for cross-field validation when
        a saved override for the companion field does not exist.
        """
        if not isinstance(patch, dict) or not patch:
            raise SettingsValidationError("Supply a nonempty settings object.")
        unknown = set(patch) - FIELD_SPECS.keys()
        if unknown:
            raise SettingsValidationError("Unknown or non-editable setting: " + ", ".join(sorted(unknown)))
        with self._lock:
            existing = self._read(strict=True)
            candidate = dict(existing)
            for key, value in patch.items():
                candidate[key] = _validate_field(key, value, self.models)
            baseline = {key: getattr(effective, key) if effective is not None else spec.default
                        for key, spec in FIELD_SPECS.items()}
            for key, value in candidate.items():
                if key in FIELD_SPECS:
                    baseline[key] = _validate_field(key, value, self.models)
            _validate_relationships(baseline)
            self._write(candidate)
            return candidate

    def save_model(self, model: str) -> dict[str, Any]:
        """Backwards-compatible operation used by PUT /v1/models/default."""
        return self.update({"model": model})

    def describe(self, effective: Settings, selected_model: str,
                 environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Whitelisted control-plane snapshot; never expose secrets or raw JSON."""
        env = os.environ if environ is None else environ
        saved = self.load()
        fields: dict[str, dict[str, Any]] = {}
        for key, spec in FIELD_SPECS.items():
            active = selected_model if key == "model" else getattr(effective, key)
            stored = saved.get(key) if key in saved else None
            # Never display untrusted invalid values as pending configuration.
            if stored is not None:
                try:
                    stored = _validate_field(key, stored, self.models)
                except SettingsValidationError:
                    stored = None
            env_overridden = key != "model" and spec.env in env
            fields[key] = {
                "value": active,
                "saved": stored,
                "pending_restart": bool(spec.restart_required and stored is not None and stored != active and not env_overridden),
                "environment_override": bool(env_overridden),
                "restart_required": spec.restart_required,
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "choices": list(self.models) if key == "model" else None,
                "label": spec.label,
                "group": spec.group,
            }
        return {"fields": fields, "pending_restart": any(f["pending_restart"] for f in fields.values())}
