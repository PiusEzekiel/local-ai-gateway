"""Versioned, local-only telemetry storage; opt-in bounded text retention."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from .diagnostics import redact_diagnostic


LOG = logging.getLogger("uvicorn.error")

SCHEMA_VERSION = 7


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Telemetry database was created by a newer gateway version")
            if version == 0:
                self._db.executescript("""
                    CREATE TABLE jobs (
                        id TEXT PRIMARY KEY, request_id TEXT NOT NULL, group_id TEXT,
                        attempt INTEGER, parent_job_id TEXT, task TEXT NOT NULL, operation TEXT,
                        model TEXT NOT NULL,
                        status TEXT NOT NULL, stage TEXT NOT NULL,
                        created_at TEXT NOT NULL, queued_at TEXT,
                        worker_acquired_at TEXT, codex_started_at TEXT,
                        codex_completed_at TEXT, completed_at TEXT,
                        reference_download_started_at TEXT,
                        reference_download_completed_at TEXT, artifact_ready_at TEXT,
                        elapsed_ms INTEGER NOT NULL DEFAULT 0,
                        queue_ms INTEGER, reference_download_ms INTEGER,
                        codex_ms INTEGER, artifact_processing_ms INTEGER,
                        error_type TEXT, error_message TEXT, reference_count INTEGER NOT NULL DEFAULT 0,
                        artifact_id TEXT,
                        exit_code INTEGER, diagnostic_preview TEXT,
                        last_successful_stage TEXT, references_downloaded INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX jobs_created_idx ON jobs(created_at DESC);
                    CREATE INDEX jobs_filter_idx ON jobs(status, task, model, created_at DESC);
                    CREATE INDEX jobs_operation_idx ON jobs(operation, created_at DESC);
                    CREATE INDEX jobs_request_idx ON jobs(request_id);
                    CREATE TABLE job_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        timestamp TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
                        stage TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info'
                    );
                    CREATE INDEX job_events_job_idx ON job_events(job_id, id);
                    CREATE TABLE job_usage (
                        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                        input_tokens INTEGER, cached_input_tokens INTEGER,
                        uncached_input_tokens INTEGER, output_tokens INTEGER,
                        reasoning_output_tokens INTEGER, total_tokens INTEGER,
                        raw_json TEXT NOT NULL
                    );
                    CREATE TABLE job_references (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        ordinal INTEGER NOT NULL, reference_id TEXT,
                        artifact_id TEXT, UNIQUE(job_id, ordinal)
                    );
                    CREATE INDEX job_references_job_idx ON job_references(job_id, ordinal);
                    CREATE TABLE artifacts (
                        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        artifact_type TEXT NOT NULL, mime_type TEXT NOT NULL,
                        width INTEGER, height INTEGER, size_bytes INTEGER NOT NULL,
                        created_at TEXT NOT NULL, storage_path TEXT NOT NULL,
                        thumbnail_path TEXT
                    );
                    CREATE INDEX artifacts_job_idx ON artifacts(job_id);
                    CREATE TABLE quota_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observed_at TEXT NOT NULL, status TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX quota_snapshots_observed_idx ON quota_snapshots(observed_at DESC,id DESC);
                    CREATE TABLE job_payloads (
                        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                        prompt_text TEXT, output_text TEXT,
                        prompt_truncated INTEGER NOT NULL DEFAULT 0,
                        output_truncated INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE reference_cache (
                        cache_id TEXT PRIMARY KEY,
                        source_key TEXT NOT NULL UNIQUE,
                        provider TEXT NOT NULL,
                        provider_file_id TEXT,
                        content_sha256 TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        width INTEGER,
                        height INTEGER,
                        size_bytes INTEGER NOT NULL,
                        storage_path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        validated_at TEXT NOT NULL,
                        last_accessed_at TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ready'
                    );
                    CREATE INDEX reference_cache_access_idx ON reference_cache(last_accessed_at);
                    PRAGMA user_version=7;
                """)
            elif version < 2:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
                if "parent_job_id" not in columns:
                    self._db.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT")
                self._db.executescript("""
                    CREATE TABLE IF NOT EXISTS job_references (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        ordinal INTEGER NOT NULL, reference_id TEXT,
                        artifact_id TEXT, UNIQUE(job_id, ordinal)
                    );
                    CREATE INDEX IF NOT EXISTS job_references_job_idx ON job_references(job_id, ordinal);
                    PRAGMA user_version=2;
                """)
            if version != 0 and version < 3:
                self._db.executescript("""
                    CREATE TABLE IF NOT EXISTS quota_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observed_at TEXT NOT NULL, status TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS quota_snapshots_observed_idx
                    ON quota_snapshots(observed_at DESC,id DESC);
                    PRAGMA user_version=3;
                """)
            if version != 0 and version < 4:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
                if "operation" not in columns:
                    self._db.execute("ALTER TABLE jobs ADD COLUMN operation TEXT")
                self._db.executescript("""
                    UPDATE jobs SET operation=task WHERE operation IS NULL;
                    CREATE INDEX IF NOT EXISTS jobs_operation_idx ON jobs(operation, created_at DESC);
                    PRAGMA user_version=4;
                """)

            # Upgrade deployed v4 databases in place. Never delete or recreate
            # jobs, references, artifacts or quota history during the migration.
            if version != 0 and version < 5:
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
                additions = {
                    "exit_code": "INTEGER",
                    "diagnostic_preview": "TEXT",
                    "last_successful_stage": "TEXT",
                    "references_downloaded": "INTEGER NOT NULL DEFAULT 0",
                }
                for name, column_type in additions.items():
                    if name not in columns:
                        self._db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}")
                self._db.execute("PRAGMA user_version=5")

            # Module 7B: opt-in, bounded plaintext content, isolated from job
            # listings/analytics so prompts can never leak through existing APIs.
            if version != 0 and version < 6:
                self._db.executescript("""
                    CREATE TABLE IF NOT EXISTS job_payloads (
                        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                        prompt_text TEXT, output_text TEXT,
                        prompt_truncated INTEGER NOT NULL DEFAULT 0,
                        output_truncated INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version=6;
                """)
            if version != 0 and version < 7:
                self._db.executescript("""
                    CREATE TABLE IF NOT EXISTS reference_cache (
                        cache_id TEXT PRIMARY KEY,
                        source_key TEXT NOT NULL UNIQUE,
                        provider TEXT NOT NULL,
                        provider_file_id TEXT,
                        content_sha256 TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        width INTEGER,
                        height INTEGER,
                        size_bytes INTEGER NOT NULL,
                        storage_path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        validated_at TEXT NOT NULL,
                        last_accessed_at TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ready'
                    );
                    CREATE INDEX IF NOT EXISTS reference_cache_access_idx
                    ON reference_cache(last_accessed_at);
                    PRAGMA user_version=7;
                """)

    # -------------------------------------------------------------------------
    # Module 7B: Prompt/output text is OPT-IN and isolated from telemetry.
    # Images/references remain in ArtifactStore, not this text table.
    # Never put content into jobs, job events or the in-memory JobHistory.
    # -------------------------------------------------------------------------
    MAX_PROMPT_CHARS = 16_000
    MAX_OUTPUT_CHARS = 32_000

    def _save_text_field(self, job_id: str, field: str, value: str, limit: int) -> None:
        if field not in {"prompt", "output"}:
            raise ValueError("Invalid retained text field")
        if not isinstance(value, str):
            raise TypeError("Retained text must be a string")
        # Limit materialization inside SQLite and browser reads. Keep truncation
        # explicit; content may include user-provided secrets, so DO NOT log it.
        shortened = value[:limit]
        truncated = int(len(value) > limit)
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO job_payloads(job_id,prompt_text,output_text,
                       prompt_truncated,output_truncated,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       prompt_text=CASE WHEN ?='prompt' THEN excluded.prompt_text ELSE job_payloads.prompt_text END,
                       output_text=CASE WHEN ?='output' THEN excluded.output_text ELSE job_payloads.output_text END,
                       prompt_truncated=CASE WHEN ?='prompt' THEN excluded.prompt_truncated ELSE job_payloads.prompt_truncated END,
                       output_truncated=CASE WHEN ?='output' THEN excluded.output_truncated ELSE job_payloads.output_truncated END,
                       updated_at=excluded.updated_at""",
                (job_id, shortened if field == "prompt" else None,
                 shortened if field == "output" else None,
                 truncated if field == "prompt" else 0,
                 truncated if field == "output" else 0,
                 datetime.now(timezone.utc).isoformat(),
                 field, field, field, field),
            )

    def save_prompt(self, job_id: str, prompt: str) -> None:
        self._save_text_field(job_id, "prompt", prompt, self.MAX_PROMPT_CHARS)

    def save_output(self, job_id: str, output: str) -> None:
        self._save_text_field(job_id, "output", output, self.MAX_OUTPUT_CHARS)

    def get_content(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                """SELECT prompt_text,output_text,prompt_truncated,output_truncated,
                          updated_at FROM job_payloads WHERE job_id=?""", (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "prompt_truncated": bool(row["prompt_truncated"]),
                "output_truncated": bool(row["output_truncated"])}

    def content_stats(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute("""SELECT COUNT(*) rows,
                COUNT(prompt_text) prompts,COUNT(output_text) outputs,
                COALESCE(SUM(LENGTH(prompt_text)),0) prompt_chars,
                COALESCE(SUM(LENGTH(output_text)),0) output_chars
                FROM job_payloads""").fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def purge_content(self) -> int:
        """Explicitly delete ALL opt-in text; preserve jobs, tokens and images."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM job_payloads").rowcount

    def purge_diagnostic_previews(self) -> int:
        """Delete persisted preview text, retaining error type and exit code."""
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE jobs SET diagnostic_preview=NULL WHERE diagnostic_preview IS NOT NULL"
            ).rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def mark_interrupted_jobs(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._db:
            cursor = self._db.execute(
                """UPDATE jobs SET status='failed', stage='Gateway restarted', completed_at=?,
                error_type='gateway_restarted', error_message='Gateway restarted before the job completed.'
                WHERE status IN ('queued','running')""", (now,),
            )
            return cursor.rowcount

    def save_job(self, job: dict[str, Any]) -> None:
        error = job.get("error") or {}
        columns = (
            "id", "request_id", "group_id", "attempt", "parent_job_id", "task", "operation", "model", "status", "stage",
            "created_at", "queued_at", "worker_acquired_at", "codex_started_at",
            "codex_completed_at", "completed_at", "reference_download_started_at",
            "reference_download_completed_at", "artifact_ready_at", "elapsed_ms", "queue_ms",
            "reference_download_ms", "codex_ms", "artifact_processing_ms", "reference_count",
            "artifact_id", "exit_code", "diagnostic_preview",
            "last_successful_stage", "references_downloaded",
        )
        values = [job.get(name) for name in columns]
        # Defense in depth: sanitize at the SQLite boundary as well as in
        # JobHistory. This also protects direct JobStore.save_job() callers.
        values[columns.index("diagnostic_preview")] = (
            redact_diagnostic(job.get("diagnostic_preview")) or None
        )
        values[columns.index("last_successful_stage")] = (
            redact_diagnostic(job.get("last_successful_stage"), max_chars=120) or None
        )
        values[columns.index("references_downloaded")] = max(
            0, min(int(job.get("references_downloaded") or 0),
                   int(job.get("reference_count") or 0)),
        )
        names = (*columns, "error_type", "error_message")
        values.extend((
            redact_diagnostic(error.get("type"), max_chars=80) or None,
            redact_diagnostic(error.get("message"), max_chars=500) or None,
        ))
        placeholders = ",".join("?" for _ in names)
        updates = ",".join(f"{name}=excluded.{name}" for name in names if name != "id")
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO jobs ({','.join(names)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}", values,
            )

    def add_event(self, job_id: str, timestamp: str, elapsed_ms: int, stage: str, level: str = "info") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO job_events(job_id,timestamp,elapsed_ms,stage,level) VALUES (?,?,?,?,?)",
                (job_id, timestamp, elapsed_ms, redact_diagnostic(stage, max_chars=240), level),
            )

    def save_usage(self, job_id: str, usage: dict[str, Any]) -> None:
        def token(name: str) -> int | None:
            value = usage.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        input_tokens = token("input_tokens")
        cached = token("cached_input_tokens")
        output = token("output_tokens")
        total = token("total_tokens")
        uncached = input_tokens - cached if input_tokens is not None and cached is not None and cached <= input_tokens else None
        if total is None and input_tokens is not None and output is not None:
            total = input_tokens + output
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO job_usage VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                input_tokens=excluded.input_tokens, cached_input_tokens=excluded.cached_input_tokens,
                uncached_input_tokens=excluded.uncached_input_tokens, output_tokens=excluded.output_tokens,
                reasoning_output_tokens=excluded.reasoning_output_tokens,
                total_tokens=excluded.total_tokens, raw_json=excluded.raw_json""",
                (job_id, input_tokens, cached, uncached, output, token("reasoning_output_tokens"),
                 total, json.dumps(usage, separators=(",", ":"))[:4096]),
            )

    def save_artifact(self, artifact: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                tuple(artifact.get(key) for key in (
                    "id", "job_id", "artifact_type", "mime_type", "width", "height",
                    "size_bytes", "created_at", "storage_path", "thumbnail_path",
                )),
            )

    def rollback_artifact_record(self, artifact_id: str) -> None:
        """Undo a partially completed artifact registration without deleting jobs.

        The caller removes files only after this transaction succeeds. The
        reference list and job statistics survive a failed image upload.
        """
        with self._lock, self._db:
            self._db.execute("UPDATE jobs SET artifact_id=NULL WHERE artifact_id=?", (artifact_id,))
            self._db.execute("UPDATE job_references SET artifact_id=NULL WHERE artifact_id=?", (artifact_id,))
            self._db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))

    def save_quota_snapshot(self, snapshot: dict[str, Any]) -> None:
        observed_at = snapshot.get("observed_at") or datetime.now(timezone.utc).isoformat()
        status = str(snapshot.get("status") or "unavailable")
        payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO quota_snapshots(observed_at,status,payload_json) VALUES (?,?,?)",
                (observed_at, status, payload),
            )

    def list_quota_snapshots(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload_json FROM quota_snapshots ORDER BY observed_at DESC,id DESC LIMIT ?",
                (min(max(limit, 1), 1000),),
            )
            snapshots: list[dict[str, Any]] = []
            for row in rows:
                try:
                    value = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    snapshots.append(value)
            return snapshots

    def save_references(self, job_id: str, references: list[dict[str, Any]]) -> None:
        """Persist stable reference IDs only; external URLs are intentionally omitted."""
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO job_references(job_id,ordinal,reference_id,artifact_id) VALUES (?,?,?,NULL)",
                [(job_id, index, reference.get("id")) for index, reference in enumerate(references, start=1)],
            )

    def set_reference_artifact(self, job_id: str, ordinal: int, artifact_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE job_references SET artifact_id=? WHERE job_id=? AND ordinal=?",
                (artifact_id, job_id, ordinal),
            )

    def get_reference_cache(self, source_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM reference_cache WHERE source_key=? AND status='ready'", (source_key,)
            ).fetchone()
            return dict(row) if row else None

    def _list_reference_cache(self, include_retired: bool) -> list[dict[str, Any]]:
        with self._lock:
            query = "SELECT * FROM reference_cache"
            if not include_retired:
                query += " WHERE status='ready'"
            return [dict(row) for row in self._db.execute(query)]

    def list_reference_cache(self, include_retired: bool = False) -> list[dict[str, Any]]:
        return self._list_reference_cache(include_retired)

    def save_reference_cache(self, record: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO reference_cache
                (cache_id,source_key,provider,provider_file_id,content_sha256,mime_type,
                 width,height,size_bytes,storage_path,created_at,validated_at,last_accessed_at,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(record.get(key) for key in (
                    "cache_id", "source_key", "provider", "provider_file_id",
                    "content_sha256", "mime_type", "width", "height", "size_bytes",
                    "storage_path", "created_at", "validated_at", "last_accessed_at",
                )) + ("ready",),
            )

    def replace_reference_cache(self, record: dict[str, Any], previous_cache_id: str | None) -> None:
        with self._lock, self._db:
            if previous_cache_id:
                previous = self._db.execute(
                    "SELECT source_key FROM reference_cache WHERE cache_id=?", (previous_cache_id,)
                ).fetchone()
                if previous:
                    self._db.execute(
                        "UPDATE reference_cache SET source_key=?,status='retired' WHERE cache_id=?",
                        (f"{previous['source_key']}:{previous_cache_id}", previous_cache_id),
                    )
            self._db.execute(
                """INSERT INTO reference_cache
                (cache_id,source_key,provider,provider_file_id,content_sha256,mime_type,
                 width,height,size_bytes,storage_path,created_at,validated_at,last_accessed_at,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(record.get(key) for key in (
                    "cache_id", "source_key", "provider", "provider_file_id",
                    "content_sha256", "mime_type", "width", "height", "size_bytes",
                    "storage_path", "created_at", "validated_at", "last_accessed_at",
                )) + ("ready",),
            )

    def refresh_reference_cache(self, cache_id: str, validated_at: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE reference_cache SET validated_at=?,last_accessed_at=?,status='ready' WHERE cache_id=?",
                (validated_at, validated_at, cache_id),
            )

    def touch_reference_cache(self, cache_id: str, accessed_at: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE reference_cache SET last_accessed_at=? WHERE cache_id=? AND status='ready'",
                (accessed_at, cache_id),
            )

    def invalidate_reference_cache(self, cache_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM reference_cache WHERE cache_id=?", (cache_id,))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_events(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._db.execute(
                "SELECT timestamp,elapsed_ms,stage,level FROM job_events WHERE job_id=? ORDER BY id", (job_id,),
            )]

    def get_diagnostic_events(self, job_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Read only the latest N events; return them in chronological order.

        The general job inspector retains its existing get_events() contract.
        This diagnostic-specific query prevents a noisy failure from loading
        an unbounded event history into the browser.
        """
        with self._lock:
            rows = [dict(row) for row in self._db.execute(
                """SELECT timestamp,elapsed_ms,stage,level FROM job_events
                WHERE job_id=? ORDER BY id DESC LIMIT ?""",
                (job_id, min(max(int(limit), 1), 500)),
            )]
        rows.reverse()
        return rows

    def get_usage(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM job_usage WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_references(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._db.execute(
                """SELECT r.ordinal,r.reference_id,r.artifact_id,
                a.mime_type,a.width,a.height,a.size_bytes
                FROM job_references r LEFT JOIN artifacts a ON a.id=r.artifact_id
                WHERE r.job_id=? ORDER BY r.ordinal""",
                (job_id,),
            )]

    def list_jobs(self, *, limit: int = 50, before_created_at: str | None = None,
                  before_id: str | None = None, status: str | None = None,
                  task: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
        where: list[str] = []
        values: list[Any] = []
        if before_created_at and before_id:
            where.append("(j.created_at < ? OR (j.created_at = ? AND j.id < ?))")
            values.extend((before_created_at, before_created_at, before_id))
        if status:
            where.append("j.status = ?")
            values.append(status)
        if task:
            where.append("j.task = ?")
            values.append(task)
        if search:
            where.append("(j.request_id LIKE ? OR j.model LIKE ? OR j.task LIKE ? OR j.error_type LIKE ?)")
            term = f"%{search[:128]}%"
            values.extend((term, term, term, term))
        clause = " WHERE " + " AND ".join(where) if where else ""
        values.append(min(max(limit, 1), 100))
        query = f"""SELECT j.*,
            u.input_tokens,u.cached_input_tokens,u.uncached_input_tokens,u.output_tokens,
            u.reasoning_output_tokens,u.total_tokens
            FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id
            {clause} ORDER BY j.created_at DESC,j.id DESC LIMIT ?"""
        with self._lock:
            return [dict(row) for row in self._db.execute(query, values)]

    # -------------------------------------------------------------------------
    # MODULE 6C — READ-ONLY DIAGNOSTICS QUERIES
    # -------------------------------------------------------------------------
    # The live /v1/* request contracts are deliberately untouched. Diagnostic
    # filtering is SQLite-backed and never loads all job rows into Python.

    @staticmethod
    def _failure_conditions(
        *, since: str, until: str, task: str | None = None,
        operation: str | None = None, model: str | None = None,
        error_type: str | None = None,
        search: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Construct a parameterized WHERE clause shared by the list and summary.

        Search is literal: `%` and `_` are not treated as LIKE wildcards.
        The date bounds are UTC ISO timestamps generated by analytics_range.
        """
        parts = ["j.status='failed'", "j.created_at>=?", "j.created_at<?"]
        values: list[Any] = [since, until]
        for column, value in (
            ("task", task), ("operation", operation),
            ("model", model), ("error_type", error_type),
        ):
            if value:
                parts.append(f"j.{column}=?")  # Column names are static, never user-supplied.
                values.append(value)
        if search:
            literal = search[:128].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{literal}%"
            parts.append("(" + " OR ".join(
                f"j.{column} LIKE ? ESCAPE '\\'"
                for column in ("request_id", "model", "error_type", "error_message", "diagnostic_preview")
            ) + ")")
            values.extend([term] * 5)
        return " AND ".join(parts), values

    def list_failures(
        self, *, since: str, until: str, limit: int = 50,
        before_created_at: str | None = None, before_id: str | None = None,
        task: str | None = None, operation: str | None = None,
        model: str | None = None, error_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one bounded page of failed jobs with the useful failure fields."""
        where, values = self._failure_conditions(
            since=since, until=until, task=task, operation=operation, model=model,
            error_type=error_type, search=search,
        )
        if before_created_at and before_id:
            where += " AND (j.created_at<? OR (j.created_at=? AND j.id<?))"
            values.extend((before_created_at, before_created_at, before_id))
        values.append(min(max(int(limit), 1), 101))
        query = f"""SELECT j.id,j.request_id,j.group_id,j.attempt,j.task,j.operation,
            j.model,j.status,j.stage,j.created_at,j.completed_at,j.elapsed_ms,
            j.queue_ms,j.codex_ms,j.reference_download_ms,
            j.error_type,j.error_message,j.exit_code,j.diagnostic_preview,
            j.last_successful_stage,j.reference_count,j.references_downloaded,
            u.total_tokens
            FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id
            WHERE {where}
            ORDER BY j.created_at DESC,j.id DESC LIMIT ?"""
        with self._lock:
            return [dict(row) for row in self._db.execute(query, values)]

    def failure_summary(
        self, *, since: str, until: str,
        task: str | None = None, operation: str | None = None,
        model: str | None = None, error_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return actual failure counts, not a sample of the current page."""
        where, values = self._failure_conditions(
            since=since, until=until, task=task, operation=operation, model=model,
            error_type=error_type, search=search,
        )
        with self._lock:
            totals = dict(self._db.execute(
                f"""SELECT COUNT(*) failures,
                COALESCE(SUM(CASE WHEN j.task='image' THEN 1 ELSE 0 END),0) image_failures,
                COALESCE(SUM(CASE WHEN j.task!='image' THEN 1 ELSE 0 END),0) text_failures
                FROM jobs j WHERE {where}""", values,
            ).fetchone())
            types = [dict(row) for row in self._db.execute(
                f"""SELECT COALESCE(j.error_type,'unknown') error_type,COUNT(*) failures
                FROM jobs j WHERE {where} GROUP BY COALESCE(j.error_type,'unknown')
                ORDER BY failures DESC,error_type LIMIT 20""", values,
            )]
            models = [dict(row) for row in self._db.execute(
                f"""SELECT j.model,COUNT(*) failures FROM jobs j WHERE {where}
                GROUP BY j.model ORDER BY failures DESC,j.model LIMIT 20""", values,
            )]
        return {**totals, "error_types": types, "models": models}

    def get_artifact_for_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM artifacts WHERE job_id=? AND artifact_type='image' ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            return dict(row) if row else None

    def list_gallery(self, *, limit: int = 48, before_created_at: str | None = None,
                     before_id: str | None = None, category: str = "all",
                     search: str | None = None) -> list[dict[str, Any]]:
        where = ["j.task='image'"]
        values: list[Any] = []
        if before_created_at and before_id:
            where.append("(j.created_at < ? OR (j.created_at = ? AND j.id < ?))")
            values.extend((before_created_at, before_created_at, before_id))
        if category == "scenes":
            where.append("j.request_id LIKE 'scene_%'")
        elif category == "assets":
            where.append("j.request_id NOT LIKE 'scene_%'")
        elif category == "failed":
            where.append("j.status='failed'")
        elif category == "retried":
            where.append("j.attempt > 1")
        if search:
            where.append("(j.request_id LIKE ? OR j.model LIKE ? OR j.error_type LIKE ?)")
            term = f"%{search[:128]}%"
            values.extend((term, term, term))
        values.append(min(max(limit, 1), 100))
        query = f"""SELECT j.id job_id,j.request_id,j.group_id,j.attempt,j.model,j.status,
            j.elapsed_ms,j.reference_count,j.created_at,j.error_type,j.error_message,
            u.total_tokens,a.id artifact_id,a.mime_type,a.width,a.height,a.size_bytes
            FROM jobs j
            LEFT JOIN job_usage u ON u.job_id=j.id
            LEFT JOIN artifacts a ON a.job_id=j.id AND a.artifact_type='image'
            WHERE {' AND '.join(where)} ORDER BY j.created_at DESC,j.id DESC LIMIT ?"""
        with self._lock:
            return [dict(row) for row in self._db.execute(query, values)]


    # -------------------------------------------------------------------------
    # Module 7C: serialized retention operations. Every destructive WHERE
    # condition RECHECKS terminal status under the SQLite lock. SQLite cascades
    # keep events, usage, references, payloads and artifacts consistent.
    # -------------------------------------------------------------------------
    def retention_expired_jobs(self, cutoff: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                """SELECT id,created_at,completed_at FROM jobs
                WHERE status IN ('completed','failed')
                  AND COALESCE(completed_at,created_at) < ?
                ORDER BY COALESCE(completed_at,created_at),id LIMIT ?""", (cutoff, limit))]

    def artifact_health_records(self, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Metadata-only, bounded artifact inventory for the read-only health API."""
        if not 1 <= limit <= 5_000:
            raise ValueError("Health inventory limit must be between 1 and 5000")
        with self._lock:
            total = int(self._db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0])
            rows = self._db.execute(
                'SELECT id,job_id,storage_path,thumbnail_path FROM artifacts ORDER BY id LIMIT ?',
                (limit,),
            )
            return [dict(row) for row in rows], total

    def retention_artifacts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                """SELECT a.*,j.status FROM artifacts a JOIN jobs j ON j.id=a.job_id
                ORDER BY a.created_at,a.id""")]

    def retention_registered_files(self) -> set[Path]:
        with self._lock:
            rows = self._db.execute('SELECT storage_path,thumbnail_path FROM artifacts')
            return {Path(value).resolve(strict=False)
                    for row in rows for value in row if isinstance(value, str) and value}

    def retention_quota_count(self, cutoff: str) -> int:
        with self._lock:
            return int(self._db.execute(
                'SELECT COUNT(*) FROM quota_snapshots WHERE observed_at<?', (cutoff,)).fetchone()[0])

    def retention_delete_quota(self, cutoff: str) -> int:
        with self._lock, self._db:
            return self._db.execute(
                'DELETE FROM quota_snapshots WHERE observed_at<?', (cutoff,)).rowcount

    def retention_delete_artifact(self, artifact_id: str, artifact_store: Any) -> bool:
        """Drop only a terminal job's selected artifact, keeping its telemetry."""
        with self._lock, self._db:
            row = self._db.execute(
                """SELECT a.* FROM artifacts a JOIN jobs j ON j.id=a.job_id
                   WHERE a.id=? AND j.status IN ('completed','failed')""", (artifact_id,)).fetchone()
            if row is None:
                return False
            try:
                artifact_store.delete_for_retention(dict(row))
            except (OSError, ValueError) as exc:
                LOG.warning('retention_artifact_skipped id=%s error=%s', artifact_id, type(exc).__name__)
                return False
            # Foreign-key references are intentionally nullable; remove dead
            # pointers before deleting the artifact's database row.
            self._db.execute('UPDATE job_references SET artifact_id=NULL WHERE artifact_id=?', (artifact_id,))
            self._db.execute('UPDATE jobs SET artifact_id=NULL WHERE artifact_id=?', (artifact_id,))
            self._db.execute('DELETE FROM artifacts WHERE id=?', (artifact_id,))
            return True

    def retention_delete_job(self, job_id: str, artifact_store: Any) -> tuple[bool, int]:
        """Expire a terminal job only after files are confirmed safely removable."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT status FROM jobs WHERE id=? AND status IN ('completed','failed')", (job_id,)).fetchone()
            if row is None:
                return False, 0
            rows = self._db.execute('SELECT * FROM artifacts WHERE job_id=? ORDER BY id', (job_id,)).fetchall()
            for artifact in rows:
                try:
                    artifact_store.delete_for_retention(dict(artifact))
                except (OSError, ValueError) as exc:
                    LOG.warning('retention_job_skipped job_id=%s error=%s', job_id, type(exc).__name__)
                    return False, 0
            self._db.execute("DELETE FROM jobs WHERE id=? AND status IN ('completed','failed')", (job_id,))
            return True, len(rows)

    def today_summary(self, since: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("""SELECT COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) failed,
                COALESCE(SUM(CASE WHEN task='image' AND status='completed' THEN 1 ELSE 0 END),0) images,
                COALESCE(SUM(elapsed_ms),0) cumulative_task_ms,
                COALESCE(SUM(codex_ms),0) codex_compute_ms,
                COALESCE(SUM(u.total_tokens),0) total_tokens,
                AVG(CASE WHEN status='completed' THEN elapsed_ms END) average_latency_ms
                FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id WHERE j.created_at>=?""", (since,)).fetchone()
            completed_count = self._db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='completed' AND created_at>=?", (since,),
            ).fetchone()[0]
            p95 = None
            if completed_count:
                offset = max(0, int((completed_count - 1) * 0.95))
                p95 = self._db.execute(
                    "SELECT elapsed_ms FROM jobs WHERE status='completed' AND created_at>=? ORDER BY elapsed_ms LIMIT 1 OFFSET ?",
                    (since, offset),
                ).fetchone()[0]
            failures = [dict(item) for item in self._db.execute("""SELECT id,request_id,task,model,error_type,error_message,completed_at
                FROM jobs WHERE status='failed' AND created_at>=? ORDER BY created_at DESC LIMIT 5""", (since,))]
            models = [dict(item) for item in self._db.execute("""SELECT model,COUNT(*) jobs FROM jobs
                WHERE created_at>=? GROUP BY model ORDER BY jobs DESC""", (since,))]
        result = dict(row)
        result["p95_latency_ms"] = p95
        result["recent_failures"] = failures
        result["model_distribution"] = models
        return result

    def usage_analytics(self, since: str, until: str, bucket_seconds: int) -> dict[str, Any]:
        """Return bounded aggregate usage data; prompts and outputs never participate."""
        bounds = (since, until)
        with self._lock:
            summary_row = self._db.execute("""SELECT
                COUNT(*) total_tasks,
                COALESCE(SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END),0) failed,
                COALESCE(SUM(CASE WHEN j.status='running' THEN 1 ELSE 0 END),0) running,
                COALESCE(SUM(CASE WHEN j.status='queued' THEN 1 ELSE 0 END),0) queued,
                COALESCE(SUM(CASE WHEN j.attempt>1 THEN 1 ELSE 0 END),0) retried,
                COALESCE(SUM(CASE WHEN COALESCE(NULLIF(j.operation,''),j.task)='image' AND j.status='completed' THEN 1 ELSE 0 END),0) images_generated,
                COALESCE(SUM(CASE WHEN COALESCE(NULLIF(j.operation,''),j.task)='research' THEN 1 ELSE 0 END),0) research_jobs,
                COALESCE(SUM(CASE WHEN COALESCE(NULLIF(j.operation,''),j.task)='chat' THEN 1 ELSE 0 END),0) chat_jobs,
                COALESCE(SUM(CASE WHEN COALESCE(NULLIF(j.operation,''),j.task)='structured' THEN 1 ELSE 0 END),0) structured_jobs,
                COALESCE(SUM(CASE WHEN COALESCE(NULLIF(j.operation,''),j.task)='generate' THEN 1 ELSE 0 END),0) generation_jobs,
                COALESCE(SUM(u.input_tokens),0) input_tokens,
                COALESCE(SUM(u.cached_input_tokens),0) cached_input_tokens,
                COALESCE(SUM(u.uncached_input_tokens),0) uncached_input_tokens,
                COALESCE(SUM(u.output_tokens),0) output_tokens,
                COALESCE(SUM(u.reasoning_output_tokens),0) reasoning_output_tokens,
                COALESCE(SUM(u.total_tokens),0) total_tokens,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN j.elapsed_ms ELSE 0 END),0) cumulative_task_ms,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN j.codex_ms ELSE 0 END),0) codex_compute_ms,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN j.queue_ms ELSE 0 END),0) queue_wait_ms,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN j.reference_download_ms ELSE 0 END),0) reference_download_ms,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN j.artifact_processing_ms ELSE 0 END),0) artifact_processing_ms,
                COALESCE(SUM(CASE WHEN j.status IN ('completed','failed') THEN
                    MAX(0,j.elapsed_ms-COALESCE(j.codex_ms,0)-COALESCE(j.queue_ms,0)-
                    COALESCE(j.reference_download_ms,0)-COALESCE(j.artifact_processing_ms,0))
                    ELSE 0 END),0) gateway_overhead_ms
                FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id
                WHERE j.created_at>=? AND j.created_at<?""", bounds).fetchone()

            wall_row = self._db.execute("""WITH intervals AS (
                SELECT
                    CASE WHEN worker_acquired_at<? THEN ? ELSE worker_acquired_at END started,
                    CASE WHEN completed_at IS NULL OR completed_at>? THEN ? ELSE completed_at END ended
                FROM jobs
                WHERE worker_acquired_at IS NOT NULL AND worker_acquired_at<?
                  AND (completed_at IS NULL OR completed_at>?)
            ), ordered AS (
                SELECT started,ended,
                    MAX(ended) OVER (ORDER BY started,ended ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) prior_end
                FROM intervals WHERE ended>started
            ), marked AS (
                SELECT started,ended,CASE WHEN prior_end IS NULL OR started>prior_end THEN 1 ELSE 0 END new_group
                FROM ordered
            ), islands AS (
                SELECT started,ended,SUM(new_group) OVER (ORDER BY started,ended) group_id FROM marked
            ), merged AS (
                SELECT MIN(started) started,MAX(ended) ended FROM islands GROUP BY group_id
            ) SELECT COALESCE(ROUND(SUM((julianday(ended)-julianday(started))*86400000)),0) busy_ms FROM merged""",
                (since, since, until, until, until, since),
            ).fetchone()

            chart_rows = [dict(row) for row in self._db.execute("""SELECT
                (CAST(strftime('%s',j.created_at) AS INTEGER)/?)*? timestamp,
                COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END),0) failed,
                COALESCE(SUM(u.input_tokens),0) input_tokens,
                COALESCE(SUM(u.cached_input_tokens),0) cached_input_tokens,
                COALESCE(SUM(u.output_tokens),0) output_tokens,
                COALESCE(SUM(u.reasoning_output_tokens),0) reasoning_output_tokens,
                COALESCE(SUM(u.total_tokens),0) total_tokens
                FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id
                WHERE j.created_at>=? AND j.created_at<?
                GROUP BY timestamp ORDER BY timestamp LIMIT 121""",
                (bucket_seconds, bucket_seconds, since, until),
            )]

            operations = [dict(row) for row in self._db.execute("""SELECT
                COALESCE(NULLIF(operation,''),task) operation,COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) failed
                FROM jobs WHERE created_at>=? AND created_at<?
                GROUP BY COALESCE(NULLIF(operation,''),task) ORDER BY jobs DESC LIMIT 20""", bounds)]
            models = [dict(row) for row in self._db.execute("""SELECT j.model,COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END),0) failed,
                COALESCE(SUM(u.total_tokens),0) total_tokens
                FROM jobs j LEFT JOIN job_usage u ON u.job_id=j.id
                WHERE j.created_at>=? AND j.created_at<? GROUP BY j.model
                ORDER BY jobs DESC LIMIT 25""", bounds)]

        summary = dict(summary_row)
        summary["gateway_wall_clock_busy_ms"] = int(wall_row["busy_ms"] or 0)
        terminal = summary["successful"] + summary["failed"]
        summary["success_rate"] = round(summary["successful"] * 100 / terminal, 1) if terminal else None
        return {"summary": summary, "timeline": chart_rows, "operations": operations, "models": models}

    def _latency_stats(self, since: str, until: str, operation_group: str,
                       model: str | None = None) -> dict[str, int | float | None]:
        predicate = "task='image'" if operation_group == "image" else "task!='image'"
        values: list[Any] = [since, until]
        model_clause = ""
        if model is not None:
            model_clause = " AND model=?"
            values.append(model)
        with self._lock:
            row = self._db.execute(f"""SELECT COUNT(*) count,AVG(elapsed_ms) average_ms
                FROM jobs WHERE created_at>=? AND created_at<? AND status IN ('completed','failed')
                AND {predicate}{model_clause}""", values).fetchone()
            count = int(row["count"])
            result: dict[str, int | float | None] = {
                "count": count,
                "average_ms": round(row["average_ms"], 2) if row["average_ms"] is not None else None,
                "p50_ms": None,
                "p95_ms": None,
            }
            for label, percentile in (("p50_ms", 0.50), ("p95_ms", 0.95)):
                if not count:
                    continue
                offset = max(0, math.ceil(count * percentile) - 1)
                percentile_values = [since, until]
                if model is not None:
                    percentile_values.append(model)
                percentile_values.append(offset)
                value = self._db.execute(f"""SELECT elapsed_ms FROM jobs
                    WHERE created_at>=? AND created_at<? AND status IN ('completed','failed')
                    AND {predicate}{model_clause} ORDER BY elapsed_ms LIMIT 1 OFFSET ?""",
                    percentile_values,
                ).fetchone()
                result[label] = value[0] if value else None
            return result

    def performance_analytics(self, since: str, until: str) -> dict[str, Any]:
        bounds = (since, until)
        with self._lock:
            summary = dict(self._db.execute("""SELECT
                COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) failed,
                COALESCE(SUM(CASE WHEN attempt>1 THEN 1 ELSE 0 END),0) retries,
                COALESCE(SUM(CASE WHEN error_type='rate_limit' THEN 1 ELSE 0 END),0) rate_limit_failures,
                COALESCE(SUM(CASE WHEN error_type IN ('reference_download_failed','invalid_reference_image') THEN 1 ELSE 0 END),0) reference_failures
                FROM jobs WHERE created_at>=? AND created_at<?""", bounds).fetchone())
            rates = {}
            for name, predicate in (("overall", "1=1"), ("image", "task='image'"), ("text", "task!='image'")):
                row = self._db.execute(f"""SELECT
                    COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) successful,
                    COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) failed
                    FROM jobs WHERE created_at>=? AND created_at<? AND {predicate}""", bounds).fetchone()
                terminal = row["successful"] + row["failed"]
                rates[name] = round(row["successful"] * 100 / terminal, 1) if terminal else None
            model_rows = [dict(row) for row in self._db.execute("""SELECT model,COUNT(*) jobs,
                COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) successful,
                COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) failed,
                AVG(CASE WHEN status IN ('completed','failed') THEN elapsed_ms END) average_ms
                FROM jobs WHERE created_at>=? AND created_at<? GROUP BY model
                ORDER BY jobs DESC LIMIT 25""", bounds)]
            error_rows = [dict(row) for row in self._db.execute("""SELECT error_type,COUNT(*) failures
                FROM jobs WHERE created_at>=? AND created_at<? AND status='failed'
                GROUP BY error_type ORDER BY failures DESC LIMIT 20""", bounds)]

        for row in model_rows:
            terminal = row["successful"] + row["failed"]
            row["failure_rate"] = round(row["failed"] * 100 / terminal, 1) if terminal else None
            row["average_ms"] = round(row["average_ms"], 2) if row["average_ms"] is not None else None
            # Model latency combines image and text jobs, so query the exact ordered set directly.
            with self._lock:
                count = self._db.execute("""SELECT COUNT(*) FROM jobs WHERE created_at>=? AND created_at<?
                    AND status IN ('completed','failed') AND model=?""", (since, until, row["model"])).fetchone()[0]
                if count:
                    offset = max(0, math.ceil(count * 0.95) - 1)
                    value = self._db.execute("""SELECT elapsed_ms FROM jobs WHERE created_at>=? AND created_at<?
                        AND status IN ('completed','failed') AND model=? ORDER BY elapsed_ms LIMIT 1 OFFSET ?""",
                        (since, until, row["model"], offset),
                    ).fetchone()
                    row["p95_ms"] = value[0] if value else None
                else:
                    row["p95_ms"] = None
        summary["success_rate"] = rates["overall"]
        summary["image_success_rate"] = rates["image"]
        summary["text_success_rate"] = rates["text"]
        summary["safety_rewrites"] = None
        return {
            "summary": summary,
            "latency": {
                "image": self._latency_stats(since, until, "image"),
                "text": self._latency_stats(since, until, "text"),
            },
            "models": model_rows,
            "errors": error_rows,
        }

    def quota_analytics(self, since: str, until: str, bucket_seconds: int) -> dict[str, Any]:
        """Downsample quota history and calculate observed, non-predictive consumption pace."""
        with self._lock:
            rows = list(self._db.execute("""WITH ranked AS (
                SELECT observed_at,payload_json,
                    (CAST(strftime('%s',observed_at) AS INTEGER)/?)*? timestamp,
                    ROW_NUMBER() OVER (
                        PARTITION BY (CAST(strftime('%s',observed_at) AS INTEGER)/?)
                        ORDER BY observed_at DESC,id DESC
                    ) row_number
                FROM quota_snapshots WHERE observed_at>=? AND observed_at<?
            ) SELECT timestamp,observed_at,payload_json FROM ranked
              WHERE row_number=1 ORDER BY timestamp LIMIT 121""",
                (bucket_seconds, bucket_seconds, bucket_seconds, since, until),
            ))
            pace_start = min(
                datetime.fromisoformat(since).astimezone(timezone.utc),
                datetime.now(timezone.utc) - timedelta(hours=24),
            ).isoformat()
            raw_rows = list(self._db.execute("""SELECT observed_at,payload_json FROM quota_snapshots
                WHERE observed_at>=? AND observed_at<? ORDER BY observed_at DESC,id DESC LIMIT 2500""",
                (pace_start, until),
            ))

        series: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            for bucket in payload.get("buckets") or []:
                for window_type in ("primary", "secondary"):
                    window = bucket.get(window_type)
                    if not isinstance(window, dict) or window.get("used_percent") is None:
                        continue
                    series.append({
                        "timestamp": row["timestamp"],
                        "observed_at": row["observed_at"],
                        "limit_id": bucket.get("limit_id"),
                        "limit_name": bucket.get("limit_name"),
                        "window_type": window_type,
                        "window_minutes": window.get("window_duration_mins"),
                        "used_percent": window.get("used_percent"),
                        "remaining_percent": window.get("remaining_percent"),
                        "resets_at": window.get("resets_at"),
                    })

        raw_points: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in reversed(raw_rows):
            try:
                payload = json.loads(row["payload_json"])
                observed = datetime.fromisoformat(row["observed_at"]).astimezone(timezone.utc)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for bucket in payload.get("buckets") or []:
                for window_type in ("primary", "secondary"):
                    window = bucket.get(window_type)
                    used = window.get("used_percent") if isinstance(window, dict) else None
                    if not isinstance(used, (int, float)) or isinstance(used, bool):
                        continue
                    key = (str(bucket.get("limit_id") or "unknown"), window_type)
                    raw_points.setdefault(key, []).append({
                        "observed": observed, "used": used, "resets_at": window.get("resets_at"),
                        "limit_name": bucket.get("limit_name"), "window_minutes": window.get("window_duration_mins"),
                    })

        current = datetime.now(timezone.utc)
        pacing = []
        for (limit_id, window_type), points in raw_points.items():
            if not points:
                continue
            latest = points[-1]
            same_cycle = [point for point in points if point["resets_at"] == latest["resets_at"]]
            deltas: dict[str, float | None] = {}
            for label, cutoff in (
                ("last_hour", current - timedelta(hours=1)),
                ("last_6_hours", current - timedelta(hours=6)),
                ("today", current.replace(hour=0, minute=0, second=0, microsecond=0)),
            ):
                candidates = [point for point in same_cycle if point["observed"] >= cutoff]
                deltas[label] = round(latest["used"] - candidates[0]["used"], 2) if len(candidates) >= 2 else None
            pace_candidates = [point for point in same_cycle if point["observed"] >= current - timedelta(hours=6)]
            pace = None
            if len(pace_candidates) >= 2:
                hours = (latest["observed"] - pace_candidates[0]["observed"]).total_seconds() / 3600
                if hours > 0:
                    pace = round((latest["used"] - pace_candidates[0]["used"]) / hours, 2)
            pacing.append({
                "limit_id": limit_id,
                "limit_name": latest["limit_name"],
                "window_type": window_type,
                "window_minutes": latest["window_minutes"],
                "deltas": deltas,
                "recent_points_per_hour": pace,
            })
        return {"series": series, "pacing": pacing}
