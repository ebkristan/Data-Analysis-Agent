"""Business canvas drawer API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from data.business_canvas_store import BusinessCanvasError, get_business_canvas_store
from data.workspace import workspace_manager

bp = Blueprint("business_canvas", __name__)


def _json_error(message: str, status: int = 400, code: str = "business_canvas_error"):
    return jsonify({"ok": False, "error": message, "code": code}), status


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _workspace_id_for_session(session_id: str) -> str:
    runtime = workspace_manager.get(session_id)
    return runtime.workspace_id if runtime else ""


@bp.get("/api/session/<sid>/business-canvas/templates")
def list_templates(sid: str):
    store = get_business_canvas_store()
    return jsonify({"ok": True, "templates": store.list_templates()})


@bp.delete("/api/session/<sid>/business-canvas/projects/<project_id>")
def delete_project(sid: str, project_id: str):
    store = get_business_canvas_store()
    project = store.get_project(project_id)
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    try:
        store.delete_project(project_id)
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True})


@bp.get("/api/session/<sid>/business-canvas/projects")
def list_projects(sid: str):
    store = get_business_canvas_store()
    return jsonify({"ok": True, "projects": store.list_projects()})


@bp.post("/api/session/<sid>/business-canvas/projects")
def create_project(sid: str):
    body = _payload()
    store = get_business_canvas_store()
    try:
        project = store.create_project(
            session_id=sid,
            template_id=str(body.get("template_id") or ""),
            title=str(body.get("title") or ""),
            workspace_id=_workspace_id_for_session(sid),
        )
    except ValueError as exc:
        return _json_error(str(exc), 400, "invalid_request")
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>")
def get_project(sid: str, project_id: str):
    store = get_business_canvas_store()
    project = store.get_project(project_id)
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    return jsonify({"ok": True, "project": project})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>")
def update_project(sid: str, project_id: str):
    body = _payload()
    store = get_business_canvas_store()
    project = store.get_project(project_id)
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    title = str(body.get("title") or "").strip()
    if not title:
        return _json_error("title is required", 400, "invalid_request")
    try:
        project = store.update_project_title(project_id=project_id, title=title)
    except BusinessCanvasError as exc:
        return _json_error(str(exc), 400, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>/blocks/<block_key>")
def update_block(sid: str, project_id: str, block_key: str):
    body = _payload()
    store = get_business_canvas_store()
    try:
        project = store.update_block(
            project_id=project_id,
            block_key=block_key,
            content=body.get("content") or {},
            actor_type="user",
            actor_label=str(body.get("actor_label") or "user"),
            reason=str(body.get("reason") or "用户编辑画布模块"),
        )
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>/revisions")
def list_revisions(sid: str, project_id: str):
    store = get_business_canvas_store()
    try:
        revisions = store.list_revisions(project_id)
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "revisions": revisions})


@bp.get("/api/session/<sid>/business-canvas/projects/<project_id>/diagram")
def get_diagram_xml(sid: str, project_id: str):
    store = get_business_canvas_store()
    project = store.get_project(project_id)
    if not project:
        return _json_error("canvas project not found", 404, "not_found")
    return jsonify({"ok": True, "diagram_xml": project.get("diagram_xml", "")})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>/diagram")
def update_diagram_xml(sid: str, project_id: str):
    body = _payload()
    store = get_business_canvas_store()
    try:
        project = store.update_project_diagram_xml(
            project_id=project_id,
            diagram_xml=str(body.get("diagram_xml") or ""),
            actor_type=str(body.get("actor_type") or "user"),
        )
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "project": project})


@bp.patch("/api/session/<sid>/business-canvas/projects/<project_id>/rendering-mode")
def update_rendering_mode(sid: str, project_id: str):
    body = _payload()
    store = get_business_canvas_store()
    try:
        project = store.update_project_rendering_mode(
            project_id=project_id,
            rendering_mode=str(body.get("rendering_mode") or "card"),
        )
    except BusinessCanvasError as exc:
        text = str(exc)
        status = 404 if "not found" in text else 400
        return _json_error(text, status, "business_canvas_error")
    return jsonify({"ok": True, "project": project})
