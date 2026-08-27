"""Persistent per-conversation file history for mounted workspaces."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_SNAPSHOTS = 100
MAX_BACKUP_BYTES = 20 * 1024 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class FileHistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Backup:
    path: str
    existed: bool
    backup_path: str = ""
    size: int = 0


@dataclass(frozen=True)
class Snapshot:
    id: str
    created_at: float
    user_text: str
    file_count: int
    status: str
    finished_at: float = 0
    last_rewind_at: float = 0
    last_rewind_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "user_text": self.user_text,
            "file_count": self.file_count,
            "status": self.status,
            "finished_at": self.finished_at,
            "last_rewind_at": self.last_rewind_at,
            "last_rewind_mode": self.last_rewind_mode,
        }


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class FileHistory:
    """Track files changed by one chat session inside one mounted workspace.

    A snapshot is opened before a user turn. The first mutation of each path in
    that turn stores its pre-mutation bytes (or an ``existed=false`` marker).
    Conversation state is compressed separately so it can be restored without
    restoring files.
    """

    def __init__(self, runtime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = str(session_id)
        self.root = runtime.meta_dir / "file-history" / self.session_id
        self.backups_dir = self.root / "backups"
        self.conversations_dir = self.root / "conversations"
        self.index_path = self.root / "index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(self.index_path)

    def _load(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {"version": 1, "workspace_id": self.runtime.workspace_id, "snapshots": []}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileHistoryError("文件历史索引损坏，无法继续操作。") from exc
        if data.get("version") != 1 or data.get("workspace_id") != self.runtime.workspace_id:
            raise FileHistoryError("文件历史与当前工作目录身份不匹配。")
        data.setdefault("snapshots", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def begin_snapshot(self, user_text: str, conversation_state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            for item in data["snapshots"]:
                if item.get("status") == "running":
                    item["status"] = "interrupted"
            snapshot_id = uuid.uuid4().hex
            conversation_name = f"{snapshot_id}.json.gz"
            conversation_path = self.conversations_dir / conversation_name
            with gzip.open(conversation_path, "wt", encoding="utf-8") as stream:
                json.dump(conversation_state, stream, ensure_ascii=False, default=str)
            snapshot = {
                "id": snapshot_id,
                "created_at": time.time(),
                "user_text": str(user_text or "")[:500],
                "status": "running",
                "conversation_file": conversation_name,
                "files": {},
            }
            data["snapshots"].append(snapshot)
            self._trim(data)
            self._save(data)
            return self._public(snapshot)

    def finalize_snapshot(self, snapshot_id: str, status: str = "completed") -> None:
        with self._lock:
            data = self._load()
            snapshot = self._find(data, snapshot_id)
            if snapshot is None:
                return
            snapshot["status"] = str(status or "completed")
            snapshot["finished_at"] = time.time()
            self._save(data)

    def track_before_write(self, path: Path) -> bool:
        """Back up a user-workspace path once for the active turn.

        Returns False for system workspace paths or when no turn snapshot is
        active. A too-large path fails closed so an unrewindable mutation is not
        allowed to proceed.
        """
        try:
            resolved = path.resolve(strict=False)
            relative = resolved.relative_to(self.runtime.workdir.resolve()).as_posix()
        except (OSError, ValueError):
            return False
        with self._lock:
            data = self._load()
            snapshot = next(
                (item for item in reversed(data["snapshots"]) if item.get("status") == "running"),
                None,
            )
            if snapshot is None or relative in snapshot["files"]:
                return False
            existed = resolved.is_file()
            if resolved.exists() and not existed:
                raise FileHistoryError("文件历史只能跟踪普通文件。")
            entry: dict[str, Any] = {"existed": existed}
            if existed:
                size = resolved.stat().st_size
                if size > MAX_BACKUP_BYTES:
                    raise FileHistoryError("文件超过 20 MB，无法在可回退保护下修改。")
                digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
                backup_rel = f"{snapshot['id']}/{digest}.bin"
                backup_path = self.backups_dir / backup_rel
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resolved, backup_path)
                entry.update({"backup": backup_rel, "size": size})
            snapshot["files"][relative] = entry
            self._save(data)
            return True

    def get_snapshots(self) -> list[Snapshot]:
        with self._lock:
            data = self._load()
            return [self._snapshot(item) for item in reversed(data["snapshots"])]

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [snapshot.to_dict() for snapshot in self.get_snapshots()]

    def rewind(self, snapshot_id: str, mode: str, session) -> dict[str, Any]:
        if mode not in {"code_and_conversation", "conversation_only", "code_only"}:
            raise FileHistoryError("不支持的回退模式。")
        with self._lock:
            data = self._load()
            snapshots = data["snapshots"]
            target_index = next(
                (idx for idx, item in enumerate(snapshots) if item.get("id") == snapshot_id),
                -1,
            )
            if target_index < 0:
                raise FileHistoryError("快照不存在。")
            target = snapshots[target_index]
            changed: list[str] = []
            restore_code = mode in {"code_and_conversation", "code_only"}
            restore_conversation = mode in {"code_and_conversation", "conversation_only"}

            conversation_state = None
            if restore_conversation:
                conversation_path = self.conversations_dir / target["conversation_file"]
                if not conversation_path.is_file():
                    raise FileHistoryError("快照对话备份缺失。")
                with gzip.open(conversation_path, "rt", encoding="utf-8") as stream:
                    conversation_state = json.load(stream)

            if restore_code:
                operations: list[tuple[Path, dict[str, Any], str]] = []
                for snapshot in reversed(snapshots[target_index:]):
                    for relative, entry in snapshot.get("files", {}).items():
                        path = self.runtime.resolve_tool_path(relative, write=True)
                        if entry.get("existed"):
                            backup = (self.backups_dir / str(entry.get("backup") or "")).resolve()
                            try:
                                backup.relative_to(self.backups_dir.resolve())
                            except ValueError as exc:
                                raise FileHistoryError("快照备份路径非法。") from exc
                            if not backup.is_file():
                                raise FileHistoryError(f"快照备份缺失：{relative}")
                        operations.append((path, entry, relative))
                for path, entry, relative in operations:
                    if entry.get("existed"):
                        backup = (self.backups_dir / str(entry.get("backup") or "")).resolve()
                        before = path.read_bytes() if path.is_file() else None
                        payload = backup.read_bytes()
                        if before != payload:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(payload)
                            changed.append(relative)
                    elif path.exists():
                        if not path.is_file():
                            raise FileHistoryError(f"无法删除非文件路径：{relative}")
                        path.unlink()
                        changed.append(relative)

            if conversation_state is not None:
                session.restore_rewind_state(conversation_state)

            if restore_code:
                for item in snapshots[target_index + 1:]:
                    self._delete_snapshot_files(item)
                data["snapshots"] = snapshots[:target_index + 1]
            target["last_rewind_at"] = time.time()
            target["last_rewind_mode"] = mode
            self._save(data)
            return {
                "snapshot_id": snapshot_id,
                "mode": mode,
                "changed_files": sorted(set(changed)),
                "changed_file_count": len(set(changed)),
                "conversation_restored": restore_conversation,
            }

    def _trim(self, data: dict[str, Any]) -> None:
        excess = len(data["snapshots"]) - MAX_SNAPSHOTS
        if excess <= 0:
            return
        for item in data["snapshots"][:excess]:
            self._delete_snapshot_files(item)
        data["snapshots"] = data["snapshots"][excess:]

    def _delete_snapshot_files(self, snapshot: dict[str, Any]) -> None:
        shutil.rmtree(self.backups_dir / str(snapshot.get("id") or ""), ignore_errors=True)
        conversation = self.conversations_dir / str(snapshot.get("conversation_file") or "")
        try:
            conversation.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _find(data: dict[str, Any], snapshot_id: str) -> dict[str, Any] | None:
        return next((item for item in data["snapshots"] if item.get("id") == snapshot_id), None)

    @staticmethod
    def _public(snapshot: dict[str, Any]) -> dict[str, Any]:
        return FileHistory._snapshot(snapshot).to_dict()

    @staticmethod
    def _snapshot(snapshot: dict[str, Any]) -> Snapshot:
        return Snapshot(
            id=str(snapshot.get("id") or ""),
            created_at=float(snapshot.get("created_at") or 0),
            user_text=str(snapshot.get("user_text") or ""),
            file_count=len(snapshot.get("files", {})),
            status=str(snapshot.get("status") or ""),
            finished_at=float(snapshot.get("finished_at") or 0),
            last_rewind_at=float(snapshot.get("last_rewind_at") or 0),
            last_rewind_mode=str(snapshot.get("last_rewind_mode") or ""),
        )


def for_session(session_id: str, workspace_id: str | None = None) -> FileHistory | None:
    from data.workspace import workspace_manager
    runtime = (
        workspace_manager.get_by_workspace(workspace_id)
        if workspace_id is not None else workspace_manager.get(session_id)
    )
    return FileHistory(runtime, session_id) if runtime is not None else None
