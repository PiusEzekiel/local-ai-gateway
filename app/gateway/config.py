"""Gateway constants, executable discovery and process startup configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
DEFAULT_MODEL = "gpt-5.6-luna"
SETTINGS_PATH = Path(__file__).parents[1] / ".gateway-settings.json"
DATA_DIR = Path(__file__).parents[1] / ".gateway-data"


def find_codex_executable() -> str:

    configured = os.getenv("AI_GATEWAY_CODEX_EXE")

    if configured:

        return configured

    on_path = shutil.which("codex")

    if on_path:

        return on_path

    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:

        candidates = list((Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))

        if candidates:

            return str(max(candidates, key=lambda path: path.stat().st_mtime))

    return "codex"


@dataclass(frozen=True)

class Settings:

    api_token: str

    codex_exe: str

    model: str = DEFAULT_MODEL

    max_concurrency: int = 1

    max_queue: int = 4

    default_timeout_seconds: int = 120

    max_timeout_seconds: int = 300

    # Default for image requests that omit timeout_seconds (previously 600).
    image_timeout_seconds: int = 600

    quota_monitor_enabled: bool = True

    quota_poll_seconds: int = 45

    quota_warning_remaining_percent: int = 20

    quota_critical_remaining_percent: int = 10

    # Privacy switches apply only after gateway restart (like other 7A settings).
    # Existing gallery image/reference artifacts are managed independently in 7C.
    store_prompts: bool = False
    store_outputs: bool = False
    store_diagnostics: bool = True

    # Retention is OFF unless explicitly enabled. Manual preview/run are
    # authenticated separately. Retention policies apply to terminal jobs only.
    auto_cleanup_enabled: bool = False
    history_retention_days: int = 90
    artifact_retention_days: int = 30
    quota_snapshot_retention_days: int = 30
    max_artifact_storage_mb: int = 10240
    cleanup_interval_hours: int = 24



    @classmethod

    def from_env(cls) -> "Settings":

        return cls(

            api_token=os.getenv("AI_GATEWAY_TOKEN", ""),

            codex_exe=find_codex_executable(),

            model=os.getenv("AI_GATEWAY_MODEL") or DEFAULT_MODEL,

            max_concurrency=int(os.getenv("AI_GATEWAY_MAX_CONCURRENCY", "1")),

            max_queue=int(os.getenv("AI_GATEWAY_MAX_QUEUE", "4")),

            default_timeout_seconds=int(os.getenv("AI_GATEWAY_TIMEOUT_SECONDS", "120")),

            max_timeout_seconds=int(os.getenv("AI_GATEWAY_MAX_TIMEOUT_SECONDS", "300")),

            image_timeout_seconds=int(os.getenv("AI_GATEWAY_IMAGE_TIMEOUT_SECONDS", "600")),

            quota_monitor_enabled=os.getenv("AI_GATEWAY_QUOTA_MONITOR", "1").lower() not in {"0", "false", "no"},

            quota_poll_seconds=int(os.getenv("AI_GATEWAY_QUOTA_POLL_SECONDS", "45")),

            quota_warning_remaining_percent=int(os.getenv("AI_GATEWAY_QUOTA_WARNING_PERCENT", "20")),

            quota_critical_remaining_percent=int(os.getenv("AI_GATEWAY_QUOTA_CRITICAL_PERCENT", "10")),
            store_prompts=os.getenv("AI_GATEWAY_STORE_PROMPTS", "0").lower() in {"1", "true", "yes"},
            store_outputs=os.getenv("AI_GATEWAY_STORE_OUTPUTS", "0").lower() in {"1", "true", "yes"},
            store_diagnostics=os.getenv("AI_GATEWAY_STORE_DIAGNOSTICS", "1").lower() not in {"0", "false", "no"},
            auto_cleanup_enabled=os.getenv("AI_GATEWAY_AUTO_CLEANUP", "0").lower() in {"1", "true", "yes"},
            history_retention_days=int(os.getenv("AI_GATEWAY_HISTORY_RETENTION_DAYS", "90")),
            artifact_retention_days=int(os.getenv("AI_GATEWAY_ARTIFACT_RETENTION_DAYS", "30")),
            quota_snapshot_retention_days=int(os.getenv("AI_GATEWAY_QUOTA_RETENTION_DAYS", "30")),
            max_artifact_storage_mb=int(os.getenv("AI_GATEWAY_MAX_ARTIFACT_MB", "10240")),
            cleanup_interval_hours=int(os.getenv("AI_GATEWAY_CLEANUP_INTERVAL_HOURS", "24")),

        )


