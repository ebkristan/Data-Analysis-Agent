"""SQLite persistence for business canvas drawer projects.

P0 scope:
- built-in templates
- session-scoped canvas projects
- editable blocks
- immutable revision history for every committed block update
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.paths import data_path
from data.diagram_templates import DIAGRAM_TEMPLATES


_SCHEMA_VERSION = 2

_DEFAULT_CONTENT = {
    "summary": "",
    "assumptions": [],
    "evidence_refs": [],
    "risks": [],
    "next_actions": [],
}

_BLANK_DIAGRAM_XML = (
    '<mxfile><diagram name="Blank" id="blank">'
    '<mxGraphModel><root>'
    '<mxCell id="0"/>'
    '<mxCell id="1" parent="0"/>'
    '</root></mxGraphModel></diagram></mxfile>'
)


_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "blank_canvas",
        "name": "空白画布",
        "description": "从零开始的空白 draw.io 画布，自由绘制任意图形。",
        "priority": "P0",
        "rendering_mode": "diagram",
        "blocks": (),
        "diagram_xml": _BLANK_DIAGRAM_XML,
    },
    {
        "id": "business_model_canvas",
        "name": "商业模式画布",
        "description": "用 9 个模块描绘商业模式，适合从用户、价值、渠道、收入和成本整体拆解产品。",
        "priority": "P0",
        "rendering_mode": "both",
        "blocks": (
            ("customer_segments", "客户细分"),
            ("value_proposition", "价值主张"),
            ("channels", "渠道"),
            ("customer_relationships", "客户关系"),
            ("revenue_streams", "收入来源"),
            ("key_resources", "关键资源"),
            ("key_activities", "关键活动"),
            ("key_partners", "关键伙伴"),
            ("cost_structure", "成本结构"),
        ),
    },
    {
        "id": "bcg_matrix",
        "name": "BCG 矩阵",
        "description": "2×2 quadrant: market growth vs relative market share, classify business units.",
        "priority": "P0",
        "rendering_mode": "diagram",
        "blocks": (
            ("stars", "明星业务"),
            ("question_marks", "问题业务"),
            ("cash_cows", "现金牛业务"),
            ("dogs", "瘦狗业务"),
        ),
    },
    {
        "id": "swot_analysis",
        "name": "SWOT 分析",
        "description": "4 quadrant: internal strengths/weaknesses + external opportunities/threats.",
        "priority": "P0",
        "rendering_mode": "diagram",
        "blocks": (
            ("strengths", "优势"),
            ("weaknesses", "劣势"),
            ("opportunities", "机会"),
            ("threats", "威胁"),
        ),
    },
    {
        "id": "value_proposition",
        "name": "价值主张速写",
        "description": "Ad-Lib 句式拆解价值主张的六个组成部分，用一句话说清楚产品为谁、解决什么、有何不同。",
        "priority": "P0",
        "rendering_mode": "diagram",
        "blocks": (
            ("product_service", "产品与服务"),
            ("customer_segments", "客户细分"),
            ("customer_jobs", "客户任务"),
            ("pain_relievers", "痛点缓解"),
            ("gain_creators", "收益创造"),
            ("competitive_alternatives", "竞争价值主张"),
        ),
    },
)

_TEMPLATE_MAP = {tpl["id"]: tpl for tpl in _TEMPLATES}
_ALLOWED_LIST_FIELDS = {"assumptions", "evidence_refs", "risks", "next_actions"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS business_canvas_schema_meta (
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS canvas_projects (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    rendering_mode TEXT NOT NULL DEFAULT 'card',
    diagram_xml TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canvas_projects_session_updated
    ON canvas_projects(session_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS canvas_blocks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    block_key TEXT NOT NULL,
    title TEXT NOT NULL,
    content_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, block_key),
    FOREIGN KEY(project_id) REFERENCES canvas_projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_canvas_blocks_project
    ON canvas_blocks(project_id);

CREATE TABLE IF NOT EXISTS canvas_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_label TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES canvas_projects(id) ON DELETE CASCADE,
    FOREIGN KEY(block_id) REFERENCES canvas_blocks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_canvas_revisions_project_created
    ON canvas_revisions(project_id, created_at DESC);
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
    except (TypeError, json.JSONDecodeError):
        return fallback


def business_canvas_db_path() -> Path:
    override = os.environ.get("BAA_BUSINESS_CANVAS_DB")
    if override:
        return Path(override)
    return data_path("business_canvas", "business_canvas.sqlite")


def list_canvas_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for tpl in _TEMPLATES:
        tpl_data = {
            "id": tpl["id"],
            "name": tpl["name"],
            "description": tpl["description"],
            "priority": tpl["priority"],
            "rendering_mode": tpl.get("rendering_mode", "card"),
            "blocks": [
                {"key": key, "title": title, "content": dict(_DEFAULT_CONTENT)}
                for key, title in tpl["blocks"]
            ],
        }
        # Include pre-built diagram XML if available
        if tpl["id"] == "blank_canvas":
            tpl_data["diagram_xml"] = _BLANK_DIAGRAM_XML
        else:
            diag = DIAGRAM_TEMPLATES.get(tpl["id"])
            if diag and diag.get("xml"):
                tpl_data["diagram_xml"] = diag["xml"]
        templates.append(tpl_data)
    return templates


def get_canvas_template(template_id: str) -> dict[str, Any] | None:
    for tpl in list_canvas_templates():
        if tpl["id"] == template_id:
            return tpl
    return None


def _normalise_content(content: Any) -> dict[str, Any]:
    source = content if isinstance(content, dict) else {}
    normalised = dict(_DEFAULT_CONTENT)
    summary = source.get("summary", "")
    normalised["summary"] = str(summary or "").strip()
    for field in _ALLOWED_LIST_FIELDS:
        raw = source.get(field, [])
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, list):
            values = raw
        else:
            values = []
        normalised[field] = [str(item).strip() for item in values if str(item).strip()]
    return normalised


class BusinessCanvasError(RuntimeError):
    pass


class BusinessCanvasStore:
    def __init__(self, db_path: Path | None = None):
        self._path = Path(db_path or business_canvas_db_path())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            rows = self._conn.execute("SELECT schema_version FROM business_canvas_schema_meta").fetchall()
            if not rows:
                self._conn.execute(
                    "INSERT INTO business_canvas_schema_meta(schema_version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
                self._conn.commit()
            else:
                current_version = int(rows[0]["schema_version"])
                if current_version < _SCHEMA_VERSION:
                    self._migrate(current_version, _SCHEMA_VERSION)

    def _migrate(self, from_version: int, to_version: int) -> None:
        """Apply incremental schema migrations."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if from_version < 2:
                    self._conn.execute(
                        "ALTER TABLE canvas_projects ADD COLUMN rendering_mode TEXT NOT NULL DEFAULT 'card'"
                    )
                    self._conn.execute(
                        "ALTER TABLE canvas_projects ADD COLUMN diagram_xml TEXT NOT NULL DEFAULT ''"
                    )
                self._conn.execute(
                    "UPDATE business_canvas_schema_meta SET schema_version = ?", (to_version,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _block_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["content"] = _json_load(item.pop("content_json"), dict(_DEFAULT_CONTENT))
        return item

    def _project_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def list_templates(self) -> list[dict[str, Any]]:
        return list_canvas_templates()

    def list_projects(self, session_id: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
        # session_id is accepted for backwards-compat but no longer used as a filter.
        # Canvas projects are global (not session-scoped) so they survive server restarts.
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM canvas_projects WHERE status = 'active' "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def create_project(
        self,
        *,
        session_id: str,
        template_id: str,
        title: str,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        template_id = str(template_id or "").strip()
        title = str(title or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        template = _TEMPLATE_MAP.get(template_id)
        if not template:
            raise BusinessCanvasError(f"unknown canvas template: {template_id}")
        if not title:
            title = template["name"]
        # Determine rendering_mode from template
        rendering_mode = template.get("rendering_mode", "card")
        # Pre-load diagram XML from templates if available
        if template_id == "blank_canvas":
            diagram_xml = _BLANK_DIAGRAM_XML
        else:
            diagram_xml = DIAGRAM_TEMPLATES.get(template_id, {}).get("xml", "")
        now = _now_iso()
        project_id = _new_id("canvas")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO canvas_projects"
                    "(id, session_id, workspace_id, template_id, title, status, rendering_mode, diagram_xml, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                    (project_id, session_id, workspace_id or "", template_id, title, rendering_mode, diagram_xml, now, now),
                )
                for block_key, block_title in template["blocks"]:
                    self._conn.execute(
                        "INSERT INTO canvas_blocks"
                        "(id, project_id, block_key, title, content_json, updated_by, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, '', ?)",
                        (
                            _new_id("block"),
                            project_id,
                            block_key,
                            block_title,
                            _json_dump(dict(_DEFAULT_CONTENT)),
                            now,
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        project = self.get_project(project_id, session_id=session_id)
        if project is None:
            raise BusinessCanvasError("created canvas project not found")
        return project

    def get_project(self, project_id: str, *, session_id: str | None = None) -> dict[str, Any] | None:
        # session_id is kept for backwards-compat but no longer used as a filter.
        project_id = str(project_id or "").strip()
        if not project_id:
            return None
        with self._lock:
            project_row = self._conn.execute(
                "SELECT * FROM canvas_projects WHERE id = ? AND status = 'active'",
                (project_id,),
            ).fetchone()
            if not project_row:
                return None
            block_rows = self._conn.execute(
                "SELECT * FROM canvas_blocks WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
        project = self._project_from_row(project_row)
        template = get_canvas_template(project["template_id"])
        project["template"] = template
        project["blocks"] = [self._block_from_row(row) for row in block_rows]
        return project

    def update_block(
        self,
        *,
        project_id: str,
        block_key: str,
        content: Any,
        actor_type: str,
        actor_label: str = "",
        reason: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        # session_id kept for backwards-compat; projects are no longer session-scoped.
        project = self.get_project(project_id)
        if not project:
            raise BusinessCanvasError("canvas project not found")
        allowed = {block["key"] for block in (project.get("template") or {}).get("blocks", [])}
        if block_key not in allowed:
            raise BusinessCanvasError(f"invalid block_key for template: {block_key}")
        actor_type = str(actor_type or "").strip()
        if actor_type not in {"user", "agent"}:
            raise BusinessCanvasError("actor_type must be user or agent")
        new_content = _normalise_content(content)
        now = _now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM canvas_blocks WHERE project_id = ? AND block_key = ?",
                    (project_id, block_key),
                ).fetchone()
                if not row:
                    raise BusinessCanvasError("canvas block not found")
                old_content = _json_load(row["content_json"], dict(_DEFAULT_CONTENT))
                self._conn.execute(
                    "UPDATE canvas_blocks SET content_json = ?, updated_by = ?, updated_at = ? "
                    "WHERE id = ?",
                    (_json_dump(new_content), actor_type, now, row["id"]),
                )
                self._conn.execute(
                    "UPDATE canvas_projects SET updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
                revision_id = _new_id("rev")
                self._conn.execute(
                    "INSERT INTO canvas_revisions"
                    "(id, project_id, block_id, actor_type, actor_label, before_json, after_json, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        revision_id,
                        project_id,
                        row["id"],
                        actor_type,
                        str(actor_label or ""),
                        _json_dump(old_content),
                        _json_dump(new_content),
                        str(reason or ""),
                        now,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id, session_id=session_id) or {}

    def get_project_diagram_xml(self, project_id: str) -> str:
        project_id = str(project_id or "").strip()
        if not project_id:
            return ""
        with self._lock:
            row = self._conn.execute(
                "SELECT diagram_xml FROM canvas_projects WHERE id = ? AND status = 'active'",
                (project_id,),
            ).fetchone()
        return row["diagram_xml"] if row else ""

    def update_project_diagram_xml(
        self,
        *,
        project_id: str,
        diagram_xml: str,
        actor_type: str = "user",
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        if not project_id:
            raise BusinessCanvasError("project_id is required")
        actor_type = str(actor_type or "").strip()
        if actor_type not in {"user", "agent"}:
            raise BusinessCanvasError("actor_type must be user or agent")
        now = _now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET diagram_xml = ?, updated_at = ? WHERE id = ?",
                    (diagram_xml, now, project_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def update_project_rendering_mode(
        self,
        *,
        project_id: str,
        rendering_mode: str,
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        rendering_mode = str(rendering_mode or "card").strip()
        if rendering_mode not in {"card", "diagram", "both"}:
            raise BusinessCanvasError("rendering_mode must be card, diagram, or both")
        now = _now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET rendering_mode = ?, updated_at = ? WHERE id = ?",
                    (rendering_mode, now, project_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def update_project_title(
        self,
        *,
        project_id: str,
        title: str,
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        title = str(title or "").strip()
        if not project_id:
            raise BusinessCanvasError("project_id is required")
        if not title:
            raise BusinessCanvasError("title is required")
        now = _now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE canvas_projects SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, project_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_project(project_id) or {}

    def list_revisions(self, project_id: str, *, session_id: str | None = None) -> list[dict[str, Any]]:
        # session_id kept for backwards-compat; projects are no longer session-scoped.
        project = self.get_project(project_id)
        if not project:
            raise BusinessCanvasError("canvas project not found")
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.*, b.block_key, b.title AS block_title "
                "FROM canvas_revisions r JOIN canvas_blocks b ON b.id = r.block_id "
                "WHERE r.project_id = ? ORDER BY r.created_at DESC",
                (project_id,),
            ).fetchall()
        revisions: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["before"] = _json_load(item.pop("before_json"), {})
            item["after"] = _json_load(item.pop("after_json"), {})
            revisions.append(item)
        return revisions

    def delete_project(self, project_id: str) -> None:
        """Physically delete a canvas project and all related blocks/revisions."""
        project_id = str(project_id or "").strip()
        if not project_id:
            raise BusinessCanvasError("project_id is required")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Delete revisions first (references blocks)
                self._conn.execute(
                    "DELETE FROM canvas_revisions WHERE project_id = ?",
                    (project_id,),
                )
                # Delete blocks
                self._conn.execute(
                    "DELETE FROM canvas_blocks WHERE project_id = ?",
                    (project_id,),
                )
                # Delete the project row itself
                self._conn.execute(
                    "DELETE FROM canvas_projects WHERE id = ?",
                    (project_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_STORE: BusinessCanvasStore | None = None
_STORE_LOCK = threading.RLock()


def get_business_canvas_store() -> BusinessCanvasStore:
    global _STORE
    with _STORE_LOCK:
        path = business_canvas_db_path()
        if _STORE is None or _STORE._path != path:
            if _STORE is not None:
                _STORE.close()
            _STORE = BusinessCanvasStore(path)
        return _STORE


def reset_business_canvas_store() -> None:
    """Close the cached store. Intended for tests that use temporary DB files."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
            _STORE = None
