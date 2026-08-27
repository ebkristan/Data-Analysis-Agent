"""Workspace-scoped SQLite persistence for workflow definitions and profiles."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping


from data.workflow_sqlite import configure_workflow_connection, workflow_db_init_lock

WORKFLOW_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_schema_meta (
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    model_policy TEXT NOT NULL DEFAULT 'inherit',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, profile_key, revision)
);
CREATE INDEX IF NOT EXISTS idx_agent_profiles_workspace_key
    ON agent_profiles(workspace_id, profile_key, revision);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    draft_graph_json TEXT NOT NULL,
    draft_input_schema_json TEXT NOT NULL DEFAULT '{}',
    draft_output_schema_json TEXT NOT NULL DEFAULT '{}',
    draft_revision INTEGER NOT NULL DEFAULT 1,
    current_version_id TEXT DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_workspace
    ON workflow_definitions(workspace_id, updated_at);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    graph_json TEXT NOT NULL,
    graph_hash TEXT NOT NULL,
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    output_schema_json TEXT NOT NULL DEFAULT '{}',
    published_by TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    UNIQUE(workflow_id, version_number),
    FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow
    ON workflow_versions(workflow_id, version_number);

CREATE TRIGGER IF NOT EXISTS workflow_versions_no_update
BEFORE UPDATE ON workflow_versions
BEGIN
    SELECT RAISE(ABORT, 'workflow versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS workflow_versions_no_delete
BEFORE DELETE ON workflow_versions
BEGIN
    SELECT RAISE(ABORT, 'workflow versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS agent_profiles_no_update
BEFORE UPDATE ON agent_profiles
BEGIN
    SELECT RAISE(ABORT, 'agent profiles are immutable');
END;

CREATE TRIGGER IF NOT EXISTS agent_profiles_no_delete
BEFORE DELETE ON agent_profiles
BEGIN
    SELECT RAISE(ABORT, 'agent profiles are immutable');
END;
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


class WorkflowStoreError(RuntimeError):
    pass


class WorkflowStore:
    """Transactional workflow storage fixed to one stable Workspace ID."""

    def __init__(self, db_path: Path, workspace_id: str):
        self._path = Path(db_path)
        self.workspace_id = str(workspace_id or "").strip()
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        with workflow_db_init_lock(self._path), self._lock:
            configure_workflow_connection(self._conn, self._path)
            self._conn.executescript(_SCHEMA)
            rows = self._conn.execute(
                "SELECT schema_version FROM workflow_schema_meta"
            ).fetchall()
            if not rows:
                self._conn.execute(
                    "INSERT INTO workflow_schema_meta(schema_version) VALUES (?)",
                    (WORKFLOW_SCHEMA_VERSION,),
                )
            elif len(rows) != 1 or rows[0]["schema_version"] != WORKFLOW_SCHEMA_VERSION:
                raise WorkflowStoreError("unsupported workflow database schema version")
            self._conn.commit()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["key"] = item.pop("profile_key")
        item["allowed_tools"] = _json_load(item.pop("allowed_tools_json"), [])
        return item

    @staticmethod
    def _version_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["graph"] = _json_load(item.pop("graph_json"), {})
        item["input_schema"] = _json_load(item.pop("input_schema_json"), {})
        item["output_schema"] = _json_load(item.pop("output_schema_json"), {})
        return item

    @staticmethod
    def _workflow_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["draft_graph"] = _json_load(item.pop("draft_graph_json"), {})
        item["draft_input_schema"] = _json_load(
            item.pop("draft_input_schema_json"),
            {},
        )
        item["draft_output_schema"] = _json_load(
            item.pop("draft_output_schema_json"),
            {},
        )
        return item

    def create_agent_profile(
        self,
        *,
        key: str,
        name: str,
        role: str,
        instructions: str,
        allowed_tools: tuple[str, ...],
        model_policy: str,
        created_by: str = "",
    ) -> dict[str, Any]:
        with self._transaction():
            latest = self._conn.execute(
                "SELECT revision FROM agent_profiles "
                "WHERE workspace_id = ? AND profile_key = ? "
                "ORDER BY revision DESC LIMIT 1",
                (self.workspace_id, key),
            ).fetchone()
            revision = int(latest["revision"]) + 1 if latest else 1
            profile_id = _new_id("ap")
            now = _now_iso()
            self._conn.execute(
                "INSERT INTO agent_profiles "
                "(id, workspace_id, profile_key, revision, name, role, instructions, "
                "allowed_tools_json, model_policy, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    profile_id,
                    self.workspace_id,
                    key,
                    revision,
                    name,
                    role,
                    instructions,
                    _json_dump(allowed_tools),
                    model_policy,
                    created_by,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return self._profile_from_row(row)

    def get_agent_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ? AND workspace_id = ?",
                (profile_id, self.workspace_id),
            ).fetchone()
        return self._profile_from_row(row) if row else None

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_profiles WHERE workspace_id = ? "
                "ORDER BY profile_key, revision DESC",
                (self.workspace_id,),
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def create_workflow(
        self,
        *,
        name: str,
        description: str,
        graph: Mapping[str, Any],
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        created_by: str = "",
    ) -> dict[str, Any]:
        workflow_id = _new_id("wf")
        now = _now_iso()
        with self._transaction():
            self._conn.execute(
                "INSERT INTO workflow_definitions "
                "(id, workspace_id, name, description, status, draft_graph_json, "
                "draft_input_schema_json, draft_output_schema_json, draft_revision, "
                "created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, 1, ?, ?, ?)",
                (
                    workflow_id,
                    self.workspace_id,
                    name,
                    description,
                    _json_dump(graph),
                    _json_dump(input_schema),
                    _json_dump(output_schema),
                    created_by,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM workflow_definitions WHERE id = ?",
                (workflow_id,),
            ).fetchone()
        return self._workflow_from_row(row)

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_definitions "
                "WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchone()
        return self._workflow_from_row(row) if row else None

    def list_workflows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_definitions WHERE workspace_id = ? "
                "ORDER BY updated_at DESC, id",
                (self.workspace_id,),
            ).fetchall()
        return [self._workflow_from_row(row) for row in rows]

    def update_workflow_draft(
        self,
        workflow_id: str,
        *,
        graph: Mapping[str, Any],
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        name: str | None = None,
        description: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        with self._transaction():
            current = self._conn.execute(
                "SELECT * FROM workflow_definitions "
                "WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchone()
            if current is None:
                return None
            if expected_revision is not None and int(current["draft_revision"]) != int(
                expected_revision
            ):
                raise WorkflowStoreError("workflow draft revision conflict")
            next_revision = int(current["draft_revision"]) + 1
            self._conn.execute(
                "UPDATE workflow_definitions SET name = ?, description = ?, "
                "draft_graph_json = ?, draft_input_schema_json = ?, "
                "draft_output_schema_json = ?, draft_revision = ?, updated_at = ? "
                "WHERE id = ? AND workspace_id = ?",
                (
                    str(name if name is not None else current["name"]),
                    str(
                        description
                        if description is not None
                        else current["description"]
                    ),
                    _json_dump(graph),
                    _json_dump(input_schema),
                    _json_dump(output_schema),
                    next_revision,
                    _now_iso(),
                    workflow_id,
                    self.workspace_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM workflow_definitions WHERE id = ?",
                (workflow_id,),
            ).fetchone()
        return self._workflow_from_row(row)

    def publish_workflow(
        self,
        workflow_id: str,
        *,
        graph_hash: str,
        published_by: str = "",
    ) -> tuple[dict[str, Any], bool] | None:
        with self._transaction():
            workflow = self._conn.execute(
                "SELECT * FROM workflow_definitions "
                "WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchone()
            if workflow is None:
                return None
            latest = self._conn.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? "
                "ORDER BY version_number DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            if latest is not None and all((
                latest["graph_json"] == workflow["draft_graph_json"],
                latest["input_schema_json"] == workflow["draft_input_schema_json"],
                latest["output_schema_json"] == workflow["draft_output_schema_json"],
            )):
                if workflow["current_version_id"] != latest["id"]:
                    self._conn.execute(
                        "UPDATE workflow_definitions SET current_version_id = ?, "
                        "status = 'published', updated_at = ? "
                        "WHERE id = ? AND workspace_id = ?",
                        (
                            latest["id"],
                            _now_iso(),
                            workflow_id,
                            self.workspace_id,
                        ),
                    )
                return self._version_from_row(latest), True

            version_number = int(latest["version_number"]) + 1 if latest else 1
            version_id = _new_id("wfv")
            now = _now_iso()
            self._conn.execute(
                "INSERT INTO workflow_versions "
                "(id, workflow_id, workspace_id, version_number, graph_json, "
                "graph_hash, input_schema_json, output_schema_json, published_by, "
                "published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    workflow_id,
                    self.workspace_id,
                    version_number,
                    workflow["draft_graph_json"],
                    graph_hash,
                    workflow["draft_input_schema_json"],
                    workflow["draft_output_schema_json"],
                    published_by,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE workflow_definitions SET current_version_id = ?, "
                "status = 'published', updated_at = ? "
                "WHERE id = ? AND workspace_id = ?",
                (version_id, now, workflow_id, self.workspace_id),
            )
            row = self._conn.execute(
                "SELECT * FROM workflow_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        return self._version_from_row(row), False

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_versions "
                "WHERE id = ? AND workspace_id = ?",
                (version_id, self.workspace_id),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def list_versions(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_versions "
                "WHERE workflow_id = ? AND workspace_id = ? "
                "ORDER BY version_number DESC",
                (workflow_id, self.workspace_id),
            ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def _table_exists_locked(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _graph_profile_ids(graph: Mapping[str, Any]) -> set[str]:
        if not isinstance(graph, Mapping):
            return set()
        return {
            str(node.get("agent_profile_id") or "").strip()
            for node in graph.get("nodes", [])
            if isinstance(node, Mapping) and str(node.get("agent_profile_id") or "").strip()
        }

    def workflow_delete_plan(self, workflow_id: str) -> dict[str, Any] | None:
        """Describe every durable record owned by one Workflow."""
        with self._lock:
            workflow = self._conn.execute(
                "SELECT * FROM workflow_definitions WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchone()
            if workflow is None:
                return None
            versions = self._conn.execute(
                "SELECT id, graph_json FROM workflow_versions "
                "WHERE workflow_id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchall()
            version_ids = [str(row["id"]) for row in versions]
            profile_ids = self._graph_profile_ids(
                _json_load(workflow["draft_graph_json"], {})
            )
            for version in versions:
                profile_ids.update(self._graph_profile_ids(
                    _json_load(version["graph_json"], {})
                ))

            runs: list[sqlite3.Row] = []
            jobs_by_session: dict[str, list[str]] = {}
            if version_ids and self._table_exists_locked("workflow_runs"):
                placeholders = ",".join("?" for _ in version_ids)
                runs = self._conn.execute(
                    f"SELECT id, session_id, status FROM workflow_runs "
                    f"WHERE workspace_id = ? AND workflow_version_id IN ({placeholders})",
                    (self.workspace_id, *version_ids),
                ).fetchall()
                run_ids = [str(row["id"]) for row in runs]
                if run_ids:
                    run_placeholders = ",".join("?" for _ in run_ids)
                    job_rows = self._conn.execute(
                        f"SELECT n.job_id, r.session_id FROM workflow_node_runs n "
                        f"JOIN workflow_runs r ON r.id = n.run_id "
                        f"WHERE n.run_id IN ({run_placeholders}) AND n.job_id <> ''",
                        tuple(run_ids),
                    ).fetchall()
                    for row in job_rows:
                        jobs_by_session.setdefault(str(row["session_id"]), []).append(
                            str(row["job_id"])
                        )

            return {
                "workflow_id": str(workflow["id"]),
                "name": str(workflow["name"]),
                "version_ids": version_ids,
                "run_ids": [str(row["id"]) for row in runs],
                "active_run_ids": [
                    str(row["id"]) for row in runs
                    if str(row["status"]) not in {"canceled", "succeeded", "failed"}
                ],
                "jobs_by_session": {
                    session_id: list(dict.fromkeys(job_ids))
                    for session_id, job_ids in jobs_by_session.items()
                },
                "profile_ids": sorted(profile_ids),
            }

    def delete_workflow_cascade(self, workflow_id: str) -> dict[str, Any] | None:
        """Physically delete one Workflow and all of its durable run data."""
        plan = self.workflow_delete_plan(workflow_id)
        if plan is None:
            return None
        if plan["active_run_ids"]:
            raise WorkflowStoreError(
                "workflow has active runs: " + ", ".join(plan["active_run_ids"])
            )

        counts = {
            "versions": 0,
            "runs": 0,
            "node_runs": 0,
            "approvals": 0,
            "events": 0,
            "manifests": 0,
            "consumptions": 0,
            "agent_profiles": 0,
        }
        with self._transaction():
            current = self._conn.execute(
                "SELECT id FROM workflow_definitions WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchone()
            if current is None:
                return None

            version_rows = self._conn.execute(
                "SELECT id FROM workflow_versions "
                "WHERE workflow_id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            ).fetchall()
            version_ids = [str(row["id"]) for row in version_rows]
            run_rows: list[sqlite3.Row] = []
            if version_ids and self._table_exists_locked("workflow_runs"):
                version_placeholders = ",".join("?" for _ in version_ids)
                run_rows = self._conn.execute(
                    f"SELECT id, session_id, status FROM workflow_runs "
                    f"WHERE workspace_id = ? AND workflow_version_id IN "
                    f"({version_placeholders})",
                    (self.workspace_id, *version_ids),
                ).fetchall()
            active = [
                row for row in run_rows
                if str(row["status"]) not in {"canceled", "succeeded", "failed"}
            ]
            if active:
                raise WorkflowStoreError(
                    "workflow has active runs: "
                    + ", ".join(str(row["id"]) for row in active)
                )
            run_ids = [str(row["id"]) for row in run_rows]
            jobs_by_session: dict[str, list[str]] = {}
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                job_rows = self._conn.execute(
                    f"SELECT n.job_id, r.session_id FROM workflow_node_runs n "
                    f"JOIN workflow_runs r ON r.id = n.run_id "
                    f"WHERE n.run_id IN ({run_placeholders}) AND n.job_id <> ''",
                    tuple(run_ids),
                ).fetchall()
                for row in job_rows:
                    jobs_by_session.setdefault(str(row["session_id"]), []).append(
                        str(row["job_id"])
                    )
            if run_ids and self._table_exists_locked("workflow_runs"):
                placeholders = ",".join("?" for _ in run_ids)
                for table, key, run_column in (
                    ("workflow_knowledge_candidates", "knowledge_candidates", "run_id"),
                    ("workflow_run_templates", "templates", "run_id"),
                    ("workflow_artifact_consumptions", "consumptions", "run_id"),
                    ("workflow_approvals", "approvals", "run_id"),
                    ("workflow_artifact_manifests", "manifests", "run_id"),
                    ("workflow_events", "events", "run_id"),
                    ("workflow_event_sequences", None, "run_id"),
                    ("workflow_node_runs", "node_runs", "run_id"),
                    ("workflow_runs", "runs", "id"),
                ):
                    cursor = self._conn.execute(
                        f"DELETE FROM {table} WHERE {run_column} IN ({placeholders})",
                        tuple(run_ids),
                    )
                    if key:
                        counts[key] = max(0, int(cursor.rowcount))

            self._conn.execute("DROP TRIGGER IF EXISTS workflow_versions_no_delete")
            cursor = self._conn.execute(
                "DELETE FROM workflow_versions WHERE workflow_id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            )
            counts["versions"] = max(0, int(cursor.rowcount))
            self._conn.execute(
                "CREATE TRIGGER workflow_versions_no_delete "
                "BEFORE DELETE ON workflow_versions BEGIN "
                "SELECT RAISE(ABORT, 'workflow versions are immutable'); END"
            )
            self._conn.execute(
                "DELETE FROM workflow_definitions WHERE id = ? AND workspace_id = ?",
                (workflow_id, self.workspace_id),
            )

            remaining_profiles: set[str] = set()
            for row in self._conn.execute(
                "SELECT draft_graph_json FROM workflow_definitions WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchall():
                remaining_profiles.update(self._graph_profile_ids(
                    _json_load(row["draft_graph_json"], {})
                ))
            for row in self._conn.execute(
                "SELECT graph_json FROM workflow_versions WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchall():
                remaining_profiles.update(self._graph_profile_ids(
                    _json_load(row["graph_json"], {})
                ))
            removable_profiles = [
                profile_id for profile_id in plan["profile_ids"]
                if profile_id not in remaining_profiles
            ]
            if removable_profiles:
                placeholders = ",".join("?" for _ in removable_profiles)
                self._conn.execute("DROP TRIGGER IF EXISTS agent_profiles_no_delete")
                cursor = self._conn.execute(
                    f"DELETE FROM agent_profiles WHERE workspace_id = ? "
                    f"AND id IN ({placeholders})",
                    (self.workspace_id, *removable_profiles),
                )
                counts["agent_profiles"] = max(0, int(cursor.rowcount))
                self._conn.execute(
                    "CREATE TRIGGER agent_profiles_no_delete "
                    "BEFORE DELETE ON agent_profiles BEGIN "
                    "SELECT RAISE(ABORT, 'agent profiles are immutable'); END"
                )

        return {
            "workflow_id": workflow_id,
            "name": plan["name"],
            "deleted": counts,
            "jobs_by_session": {
                session_id: list(dict.fromkeys(job_ids))
                for session_id, job_ids in jobs_by_session.items()
            },
            "source_data_preserved": True,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def path(self) -> Path:
        return self._path
