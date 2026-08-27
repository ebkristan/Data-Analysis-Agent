"""Persistent task board scoped to the currently mounted workspace."""
from __future__ import annotations

import json
import logging
log = logging.getLogger(__name__)
import threading
import uuid
from datetime import datetime
from pathlib import Path

from data.workspace import workspace_manager

VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class WorkspaceTaskError(ValueError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class WorkspaceTaskStore:
    def __init__(self, session_id: str, *, workspace_id: str | None = None) -> None:
        fixed_id = (
            str(workspace_manager.workspace_id_for_session(session_id) or "")
            if workspace_id is None else str(workspace_id or "")
        )
        runtime = workspace_manager.get_by_workspace(fixed_id) if fixed_id else None
        if runtime is None:
            raise WorkspaceTaskError("no workspace is mounted for this session")
        self._path: Path = runtime.meta_dir / "agent_tasks.json"
        key = str(runtime.workdir)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.RLock())

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.debug("[tasks] task store load failed: %s", exc)
            raise WorkspaceTaskError(f"task store is unreadable: {exc}") from exc
        return data if isinstance(data, list) else []

    def _save(self, tasks: list[dict]) -> None:
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self._path)

    def create(
        self, title: str, description: str = "", assignee: str = "",
        blocks: list[str] | None = None, blocked_by: list[str] | None = None,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise WorkspaceTaskError("task title is required")
        with self._lock:
            tasks = self._load()
            task = {
                "id": uuid.uuid4().hex[:10], "title": title,
                "description": description or "", "assignee": assignee or "",
                "status": "pending", "blocks": list(dict.fromkeys(blocks or [])),
                "blocked_by": list(dict.fromkeys(blocked_by or [])),
                "created_at": _now(), "updated_at": _now(),
            }
            tasks.append(task)
            self._save(tasks)
            return task

    def get(self, task_id: str) -> dict:
        with self._lock:
            task = next((item for item in self._load() if item.get("id") == task_id), None)
        if task is None:
            raise WorkspaceTaskError(f"task not found: {task_id}")
        return task

    def list(self, status: str = "", assignee: str = "") -> list[dict]:
        if status and status not in VALID_STATUSES:
            raise WorkspaceTaskError("invalid task status")
        with self._lock:
            tasks = self._load()
        if status:
            tasks = [task for task in tasks if task.get("status") == status]
        if assignee:
            tasks = [task for task in tasks if task.get("assignee") == assignee]
        return tasks

    def update(
        self, task_id: str, *, status: str | None = None,
        assignee: str | None = None, description: str | None = None,
        add_blocks: list[str] | None = None, add_blocked_by: list[str] | None = None,
    ) -> dict:
        if status is not None and status not in VALID_STATUSES:
            raise WorkspaceTaskError("invalid task status")
        with self._lock:
            tasks = self._load()
            task = next((item for item in tasks if item.get("id") == task_id), None)
            if task is None:
                raise WorkspaceTaskError(f"task not found: {task_id}")
            if status is not None:
                task["status"] = status
            if assignee is not None:
                task["assignee"] = assignee
            if description is not None:
                task["description"] = description
            for field, values in (("blocks", add_blocks), ("blocked_by", add_blocked_by)):
                if values:
                    task[field] = list(dict.fromkeys([*task.get(field, []), *values]))
            task["updated_at"] = _now()
            self._save(tasks)
            return task
