"""Shared SQLite connection setup for Workflow stores."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


_LOCKS_GUARD = threading.Lock()
_INIT_LOCKS: dict[str, threading.RLock] = {}


def workflow_db_init_lock(db_path: Path) -> threading.RLock:
    key = str(Path(db_path).resolve())
    with _LOCKS_GUARD:
        return _INIT_LOCKS.setdefault(key, threading.RLock())


def configure_workflow_connection(conn: sqlite3.Connection, db_path: Path) -> None:
    """Apply shared Workflow SQLite pragmas without racing parallel API calls."""
    with workflow_db_init_lock(db_path):
        conn.execute("PRAGMA busy_timeout=5000")
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(6):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                last_error = None
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
        if last_error is not None:
            raise last_error
        conn.execute("PRAGMA foreign_keys=ON")
