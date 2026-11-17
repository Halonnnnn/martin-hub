from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class SessionRow:
    group_id: str
    harness: str
    session_id: str
    cwd: str
    updated_at: str


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  group_id TEXT PRIMARY KEY,
                  harness TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  cwd TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cursors (group_id TEXT PRIMARY KEY, seq INTEGER NOT NULL)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  wake_id INTEGER,
                  group_id TEXT,
                  session_id TEXT,
                  started_at TEXT,
                  ended_at TEXT,
                  status TEXT,
                  detail TEXT
                )
                """
            )
            self._conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending (
                  group_id TEXT PRIMARY KEY,
                  messages TEXT NOT NULL,
                  max_seq INTEGER NOT NULL,
                  reason TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_session(self, group_id: str) -> SessionRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT group_id, harness, session_id, cwd, updated_at FROM sessions WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return SessionRow(**dict(row)) if row else None

    def save_session(self, group_id: str, harness: str, session_id: str, cwd: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions (group_id, harness, session_id, cwd, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                  harness = excluded.harness,
                  session_id = excluded.session_id,
                  cwd = excluded.cwd,
                  updated_at = excluded.updated_at
                """,
                (group_id, harness, session_id, cwd, _now()),
            )

    def delete_session(self, group_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE group_id = ?", (group_id,))

    def get_cursor(self, group_id: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq FROM cursors WHERE group_id = ?", (group_id,)
            ).fetchone()
        return int(row["seq"]) if row else None

    def set_cursor(self, group_id: str, seq: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cursors (group_id, seq) VALUES (?, ?)
                ON CONFLICT(group_id) DO UPDATE SET seq = excluded.seq
                """,
                (group_id, seq),
            )

    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_pending(self, group_id: str) -> tuple[list[dict], int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT messages, max_seq FROM pending WHERE group_id = ?", (group_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["messages"]), int(row["max_seq"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set_pending(self, group_id: str, messages: list[dict], max_seq: int, reason: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO pending (group_id, messages, max_seq, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                  messages = excluded.messages,
                  max_seq = excluded.max_seq,
                  reason = excluded.reason,
                  created_at = excluded.created_at
                """,
                (group_id, json.dumps(messages, ensure_ascii=False), max_seq, reason, _now()),
            )

    def delete_pending(self, group_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pending WHERE group_id = ?", (group_id,))

    def record_run(
        self,
        *,
        wake_id: int | None,
        group_id: str,
        session_id: str | None,
        started_at: str,
        status: str,
        detail: str = "",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO runs (wake_id, group_id, session_id, started_at, ended_at, status, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (wake_id, group_id, session_id, started_at, _now(), status, detail[:2000]),
            )
