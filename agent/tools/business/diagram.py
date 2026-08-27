"""Agent tool implementations for draw.io diagram operations.

Tools: display_diagram, edit_diagram, get_diagram, get_shape_library.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from data.business_canvas_store import get_business_canvas_store
from data.diagram_templates import DIAGRAM_TEMPLATES
from agent.tools.business.xml_utils import (
    validate_and_fix_xml,
    wrap_with_mxfile,
    apply_diagram_operations,
    is_mxcell_xml_complete,
)

log = logging.getLogger(__name__)

_SHAPE_LIBS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "shape_libs"


# ── display_diagram ──────────────────────────────────────────


def _fill_template_content(template_id: str, content: dict[str, str]) -> str:
    """Load a template XML and fill content cells with provided text.

    Args:
        template_id: Key into DIAGRAM_TEMPLATES (e.g. "business_model_canvas").
        content: Dict mapping content_cells keys to text strings.

    Returns:
        Complete <mxfile> XML string with content filled in.
    """
    template = DIAGRAM_TEMPLATES.get(template_id)
    if not template:
        raise ValueError(f"Unknown template_id: {template_id}")
    cell_map = template.get("content_cells", {})
    xml = template["xml"]

    for key, text in content.items():
        cell_id = cell_map.get(key)
        if not cell_id:
            continue
        escaped = _escape_xml_value(text)
        # Replace the value="" attribute of the cell with matching id
        # Pattern: <mxCell id="X" value="..." ...
        pattern = re.compile(
            r'(<mxCell\s+id="' + re.escape(cell_id) + r'"\s+value=")([^"]*)(")'
        )
        xml = pattern.sub(
            lambda m: m.group(1) + escaped + m.group(3),
            xml,
            count=1,
        )

    return xml


def _escape_xml_value(text: str) -> str:
    """Escape text for use in an XML attribute value."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("\n", "&#xa;")
    text = text.replace("\r", "")
    return text


def handle_display_diagram(params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Create or replace a diagram in the canvas project.

    Validates XML, auto-fixes if needed, stores in project, and returns
    project data. The frontend receives updates via SSE canvas_event.
    """
    xml = str(params.get("xml") or "")
    title = str(params.get("title") or "Diagram")
    template_id = str(params.get("template_id") or "")
    project_id = str(params.get("project_id") or "")
    content = params.get("content") or None

    # Content-fill mode: use template layout + fill content cells
    if content and isinstance(content, dict) and template_id:
        try:
            xml = _fill_template_content(template_id, content)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    elif content and not template_id:
        return {"ok": False, "error": "template_id is required when using content mode"}

    if not xml:
        return {"ok": False, "error": "either xml or (template_id + content) is required"}

    # Wrap bare XML
    if "<mxfile>" not in xml:
        xml = wrap_with_mxfile(xml)

    # Validate and auto-fix
    result = validate_and_fix_xml(xml)
    if not result["valid"]:
        return {
            "ok": False,
            "error": f"Invalid diagram XML: {result['error']}",
            "fixes_applied": result.get("fixes", []),
        }

    xml = result["fixed"] or xml

    # Check completeness (truncation)
    if not is_mxcell_xml_complete(xml):
        return {
            "ok": False,
            "error": "Diagram XML appears truncated. Please provide complete XML.",
            "truncated": True,
        }

    store = get_business_canvas_store()

    if project_id:
        # Update existing project
        project = store.get_project(project_id, session_id=session_id)
        if not project:
            return {"ok": False, "error": f"Canvas project {project_id} not found"}
        project = store.update_project_diagram_xml(
            project_id=project_id,
            diagram_xml=xml,
            actor_type="agent",
        )
        # Also switch to diagram mode
        project = store.update_project_rendering_mode(
            project_id=project_id,
            rendering_mode="diagram",
        )
        return {"ok": True, "project": project, "xml": xml, "fixes_applied": result.get("fixes", [])}
    else:
        # Create new project with diagram template
        if not template_id:
            template_id = "business_model_canvas"  # default fallback
        try:
            project = store.create_project(
                session_id=session_id or "",
                template_id=template_id,
                title=title,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        # Update diagram XML
        project = store.update_project_diagram_xml(
            project_id=project["id"],
            diagram_xml=xml,
            actor_type="agent",
        )
        project = store.update_project_rendering_mode(
            project_id=project["id"],
            rendering_mode="diagram",
        )
        return {"ok": True, "project": project, "xml": xml, "fixes_applied": result.get("fixes", [])}


# ── edit_diagram ──────────────────────────────────────────────


def handle_edit_diagram(params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Edit specific cells in the current diagram by ID-based operations."""
    project_id = str(params.get("project_id") or "")
    operations = params.get("operations") or []

    if not project_id:
        return {"ok": False, "error": "project_id is required"}
    if not operations:
        return {"ok": False, "error": "operations array is required"}

    store = get_business_canvas_store()
    current_xml = store.get_project_diagram_xml(project_id)
    if not current_xml:
        return {"ok": False, "error": f"No diagram XML found for project {project_id}"}

    # Apply operations
    result = apply_diagram_operations(current_xml, operations)
    new_xml = result["result"]
    errors = result.get("errors", [])

    if errors and not new_xml:
        return {"ok": False, "error": "All operations failed", "operation_errors": errors}

    # Validate the result
    validation = validate_and_fix_xml(new_xml)
    if not validation["valid"]:
        return {"ok": False, "error": f"Result XML invalid after operations: {validation['error']}"}

    new_xml = validation["fixed"] or new_xml

    # Store updated XML
    project = store.update_project_diagram_xml(
        project_id=project_id,
        diagram_xml=new_xml,
        actor_type="agent",
    )

    return {
        "ok": True,
        "project": project,
        "xml": new_xml,
        "operation_errors": errors,
        "fixes_applied": validation.get("fixes", []),
    }


# ── get_diagram ────────────────────────────────────────────────


def handle_get_diagram(params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Return the current diagram XML for a canvas project."""
    project_id = str(params.get("project_id") or "")

    if not project_id:
        return {"ok": False, "error": "project_id is required"}

    store = get_business_canvas_store()
    project = store.get_project(project_id, session_id=session_id)
    if not project:
        return {"ok": False, "error": f"Canvas project {project_id} not found"}

    xml = project.get("diagram_xml", "")
    return {"ok": True, "project_id": project_id, "xml": xml, "template_id": project.get("template_id", "")}


# ── get_shape_library ──────────────────────────────────────────


def handle_get_shape_library(params: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Return draw.io shape library documentation for reference."""
    library = str(params.get("library") or "").strip().lower()

    if not library:
        return {"ok": False, "error": "library parameter is required"}

    # Sanitize: only allow safe filenames
    if not re.match(r"^[a-z0-9_-]+$", library):
        return {"ok": False, "error": "Invalid library name"}

    lib_file = _SHAPE_LIBS_DIR / f"{library}.md"
    if not lib_file.exists():
        available = [f.stem for f in _SHAPE_LIBS_DIR.glob("*.md")] if _SHAPE_LIBS_DIR.exists() else []
        return {"ok": False, "error": f"Library '{library}' not found. Available: {', '.join(available)}"}

    try:
        content = lib_file.read_text(encoding="utf-8")
        return {"ok": True, "library": library, "content": content}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to read library file: {exc}"}
