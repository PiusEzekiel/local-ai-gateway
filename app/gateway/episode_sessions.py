"""Opt-in episode title -> Codex conversation registry.

Only safe metadata is stored here: the episode title, Codex UUID and cumulative
usage. Codex itself owns the conversation history under CODEX_HOME. Titles are
stored as plaintext because operators need to identify/reset episodes; opting
into sessions is therefore a separate privacy decision from JobHistory retention.

Locks protect turns within one *gateway process*. Do not run multiple Uvicorn
workers while this feature is enabled; that requires cross-process coordination.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, AsyncIterator
from uuid import UUID

from .contracts import GatewayError


def normalize_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    if not title or len(title) > 300:
        raise GatewayError("invalid_request", "episode_title must contain 1–300 characters.", 422)
    return title


def title_key(title: str) -> str:
    return sha256(normalize_title(title).casefold().encode("utf-8")).hexdigest()


def usage_amounts(usage: dict[str, Any] | None) -> tuple[int, int, int]:
    if not isinstance(usage, dict):
        return (0, 0, 0)
    details = usage.get("input_token_details")
    if not isinstance(details, dict):
        details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = {}
    def integer(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return (integer(usage.get("input_tokens")),
            integer(usage.get("cached_input_tokens", details.get("cached_input_tokens"))),
            integer(usage.get("output_tokens")))


class EpisodeSessions:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("""CREATE TABLE IF NOT EXISTS episode_sessions (
            episode_key TEXT PRIMARY KEY, title TEXT NOT NULL,
            thread_id TEXT, status TEXT NOT NULL DEFAULT 'ready',
            turns INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_usage_turns INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        self._db.commit()
        self._db_lock = RLock()
        self._locks: dict[str, asyncio.Lock] = {}
        # async route handlers run on one event loop; only accessed there.

    @asynccontextmanager
    async def lock(self, title: str) -> AsyncIterator[None]:
        key = title_key(title)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield

    def get(self, title: str) -> str | None:
        with self._db_lock:
            row = self._db.execute(
                "SELECT thread_id FROM episode_sessions WHERE episode_key=? AND status='ready'",
                (title_key(title),),
            ).fetchone()
        return row["thread_id"] if row else None

    def record(self, title: str, thread_id: str | None,
               usage: dict[str, Any] | None = None) -> None:
        if not thread_id:
            raise GatewayError("invalid_response", "Codex did not return a resumable episode session ID.", 502)
        try:
            canonical = str(UUID(thread_id))
        except (ValueError, TypeError) as exc:
            raise GatewayError("invalid_response", "Codex returned an invalid session ID.", 502) from exc
        name = normalize_title(title)
        key = title_key(name)
        inp, cached, out = usage_amounts(usage)
        details = (usage or {}).get("input_token_details") if isinstance(usage, dict) else None
        if not isinstance(details, dict) and isinstance(usage, dict):
            details = usage.get("input_tokens_details")
        cached_known = int(isinstance(usage, dict) and (
            isinstance(usage.get("cached_input_tokens"), int) or
            (isinstance(details, dict) and isinstance(details.get("cached_input_tokens"), int))
        ))
        now = datetime.now(timezone.utc).isoformat()
        with self._db_lock, self._db:
            self._db.execute("""INSERT INTO episode_sessions
                (episode_key,title,thread_id,status,turns,input_tokens,cached_input_tokens,
                 cached_usage_turns,output_tokens,created_at,updated_at)
                VALUES (?,?,?,'ready',1,?,?,?,?,?,?)
                ON CONFLICT(episode_key) DO UPDATE SET
                  title=excluded.title,thread_id=excluded.thread_id,status='ready',
                  turns=CASE WHEN episode_sessions.status='ready' AND
                       episode_sessions.thread_id=excluded.thread_id
                       THEN episode_sessions.turns+1 ELSE 1 END,
                  input_tokens=CASE WHEN episode_sessions.status='ready' AND
                       episode_sessions.thread_id=excluded.thread_id
                       THEN episode_sessions.input_tokens+excluded.input_tokens
                       ELSE excluded.input_tokens END,
                  cached_input_tokens=CASE WHEN episode_sessions.status='ready' AND
                       episode_sessions.thread_id=excluded.thread_id
                       THEN episode_sessions.cached_input_tokens+excluded.cached_input_tokens
                       ELSE excluded.cached_input_tokens END,
                  cached_usage_turns=CASE WHEN episode_sessions.status='ready' AND
                       episode_sessions.thread_id=excluded.thread_id
                       THEN episode_sessions.cached_usage_turns+excluded.cached_usage_turns
                       ELSE excluded.cached_usage_turns END,
                  output_tokens=CASE WHEN episode_sessions.status='ready' AND
                       episode_sessions.thread_id=excluded.thread_id
                       THEN episode_sessions.output_tokens+excluded.output_tokens
                       ELSE excluded.output_tokens END,
                  updated_at=excluded.updated_at""",
                (key,name,canonical,inp,cached,cached_known,out,now,now),
            )

    def mark_uncertain(self, title: str) -> None:
        """Do not resume a possibly half-written Codex turn after a failure."""
        with self._db_lock, self._db:
            self._db.execute("UPDATE episode_sessions SET status='uncertain',updated_at=? WHERE episode_key=?",
                             (datetime.now(timezone.utc).isoformat(), title_key(title)))

    def reset(self, title: str) -> bool:
        """Unlink, don't delete Codex's files/history or reference-image cache."""
        with self._db_lock, self._db:
            cursor = self._db.execute("DELETE FROM episode_sessions WHERE episode_key=?", (title_key(title),))
            return cursor.rowcount > 0

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._db_lock:
            rows = self._db.execute("""SELECT title,thread_id,status,turns,input_tokens,
                cached_input_tokens,cached_usage_turns,output_tokens,created_at,updated_at
                FROM episode_sessions ORDER BY updated_at DESC LIMIT ?""", (min(max(limit,1),200),)).fetchall()
        records = [dict(r) for r in rows]
        for record in records:
            # Zero cached tokens means "none" only when every turn reported the
            # metric. Otherwise the measured sum may be incomplete.
            record["cached_usage_complete"] = (
                record["cached_usage_turns"] == record["turns"]
            )
        return records

    def close(self) -> None:
        with self._db_lock:
            self._db.close()
