import { state } from "../core/runtime.js";
import { ensureUiIsland } from "./vue-app.js";
import { getUiIsland } from "../core/ui-registry.js";

let _loaded       = false;
let _templates    = [];
let _projects     = [];
let _currentProject = null;

function _base() {
  return `/api/session/${encodeURIComponent(state.SID)}/business-canvas`;
}

async function _jsonFetch(url, options = {}) {
  const res  = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function _ui()   { return getUiIsland("businessCanvas"); }
function _sync(extra = {}) {
  _ui()?.setState?.({
    templates:      _templates,
    projects:       _projects,
    currentProject: _currentProject,
    ...extra,
  });
}
async function _ensureUi() {
  await ensureUiIsland("businessCanvas");
  return _ui();
}

// ── load all data ──────────────────────────────────────────────

async function loadAll({ force = false } = {}) {
  if (_loaded && !force) { _sync(); return; }
  _sync({ loading: true, error: "" });
  try {
    const [tmpl, proj] = await Promise.all([
      _jsonFetch(`${_base()}/templates`),
      _jsonFetch(`${_base()}/projects`),
    ]);
    _templates = tmpl.templates || [];
    _projects  = proj.projects  || [];
    if (_projects.length) {
      const detail = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(_projects[0].id)}`);
      _currentProject = detail.project || null;
    } else {
      _currentProject = null;
    }
    _loaded = true;
    _sync({ loading: false, error: "" });
    // Auto-load diagram XML if current project has one
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ loading: false, error: String(err.message || err) });
  }
}

function _loadCurrentDiagram() {
  const xml = _currentProject?.diagram_xml || "";
  if (xml) _ui()?.loadXml?.(xml);
}

// ── public: open / close ───────────────────────────────────────

async function open() {
  const ui = await _ensureUi();
  document.body.classList.add("business-canvas-open");
  ui?.setOpen?.(true);
  await loadAll();
}

function close() {
  document.body.classList.remove("business-canvas-open", "business-canvas-fullscreen");
  _ui()?.setOpen?.(false);
  _ui()?.setFullscreen?.(false);
}

function setFullscreen(enabled) {
  document.body.classList.toggle("business-canvas-fullscreen", !!enabled);
  _ui()?.setFullscreen?.(!!enabled);
}

// ── public: projects ───────────────────────────────────────────

async function createProject(templateId) {
  const template = _templates.find(t => t.id === templateId);
  const title    = template?.name || "图表";
  _sync({ saving: true, error: "" });
  try {
    const data = await _jsonFetch(`${_base()}/projects`, {
      method: "POST",
      body:   JSON.stringify({ template_id: templateId, title }),
    });
    _currentProject = data.project || null;
    const proj = await _jsonFetch(`${_base()}/projects`);
    _projects = proj.projects || [];
    _sync({ saving: false, error: "" });
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ saving: false, error: String(err.message || err) });
  }
}

async function selectProject(projectId) {
  _sync({ loading: true, error: "" });
  try {
    const data = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`);
    _currentProject = data.project || null;
    _sync({ loading: false, error: "" });
    _loadCurrentDiagram();
  } catch (err) {
    _sync({ loading: false, error: String(err.message || err) });
  }
}

async function refresh() {
  _loaded = false;
  await loadAll({ force: true });
}

async function deleteProject(projectId) {
  if (!projectId) return;
  try {
    await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    _projects = _projects.filter(p => p.id !== projectId);
    if (_currentProject?.id === projectId) {
      _currentProject = null;
    }
    _sync();
  } catch (err) {
    _sync({ error: String(err.message || err) });
  }
}

async function renameProject(projectId, newTitle) {
  if (!projectId || !newTitle) return;
  try {
    const data = await _jsonFetch(`${_base()}/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title: newTitle }),
    });
    const updated = data.project;
    if (updated) {
      _projects = _projects.map(p => p.id === projectId ? { ...p, title: updated.title, updated_at: updated.updated_at } : p);
      if (_currentProject?.id === projectId) {
        _currentProject = { ..._currentProject, title: updated.title };
      }
    }
    _sync();
  } catch (err) {
    _sync({ error: String(err.message || err) });
  }
}

// ── public: diagram XML ────────────────────────────────────────

// Called by the draw.io iframe onAutosave to persist changes
async function saveDiagramXml(projectId, xml) {
  if (!projectId || !xml) return;
  try {
    await _jsonFetch(
      `${_base()}/projects/${encodeURIComponent(projectId)}/diagram`,
      { method: "PATCH", body: JSON.stringify({ diagram_xml: xml, actor_type: "user" }) },
    );
  } catch { /* silent autosave */ }
}

// Called by SSE canvas_event → loadDiagramXml
async function loadDiagramXml(xml) {
  // Ensure island is mounted (open() may not have been called yet)
  const ui = await _ensureUi();
  ui?.loadXml?.(xml);
}

export const businessCanvas = Object.freeze({
  open,
  close,
  setFullscreen,
  loadAll,
  refresh,
  createProject,
  selectProject,
  deleteProject,
  renameProject,
  saveDiagramXml,
  loadDiagramXml,
});
