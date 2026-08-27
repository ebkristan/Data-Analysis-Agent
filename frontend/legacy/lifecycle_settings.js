import { uiRegistry } from "../core/ui-registry.js";

const root = document.getElementById("lifecycle-settings-root");

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes;
  let index = -1;
  do {
    size /= 1024;
    index += 1;
  } while (size >= 1024 && index < units.length - 1);
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

function setStatus(message = "", type = "") {
  const node = document.getElementById("lifecycle-status");
  if (!node) return;
  node.textContent = message;
  node.className = `lifecycle-status ${type}`;
}

function renderReport(report) {
  const target = document.getElementById("lifecycle-summary");
  if (!target) return;
  const entries = Object.entries(report.locations || {});
  target.innerHTML = [
    `<div class="lifecycle-total">共 ${report.total_files || 0} 个文件，占用 ${formatBytes(report.total_bytes)}</div>`,
    ...entries.map(([name, data]) => `<div class="lifecycle-row"><span>${name}</span><span>${data.files || 0} 个 · ${formatBytes(data.bytes)}</span></div>`),
  ].join("");
}

function renderTrash(items) {
  const target = document.getElementById("lifecycle-trash-list");
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="lifecycle-empty">回收站为空</div>';
    return;
  }
  target.innerHTML = items.map(item => `
    <div class="lifecycle-trash-item">
      <div><strong>${item.source_filename || "已删除会话"}</strong><small>${item.deleted_at || ""} · ${item.files} 个文件 · ${formatBytes(item.bytes)}</small></div>
      <button class="btn-sm btn-sm-ghost" data-lifecycle-restore="${item.id}">恢复</button>
    </div>
  `).join("");
  target.querySelectorAll("[data-lifecycle-restore]").forEach(button => {
    button.addEventListener("click", () => restore(button.dataset.lifecycleRestore));
  });
}

async function load() {
  if (!root) return;
  setStatus("正在读取存储信息…");
  try {
    const [reportResponse, trashResponse] = await Promise.all([
      fetch("/api/lifecycle/report"),
      fetch("/api/lifecycle/session-trash"),
    ]);
    const reportData = await reportResponse.json();
    const trashData = await trashResponse.json();
    if (!reportResponse.ok || !reportData.ok) throw new Error(reportData.error || "读取存储统计失败");
    if (!trashResponse.ok || !trashData.ok) throw new Error(trashData.error || "读取回收站失败");
    renderReport(reportData.report);
    renderTrash(trashData.items || []);
    setStatus("");
  } catch (error) {
    setStatus(`读取失败：${error.message || error}`, "error");
  }
}

async function reclaim() {
  const raw = document.getElementById("lifecycle-retention-days")?.value || "30";
  const retentionDays = Number(raw);
  if (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650) {
    setStatus("保留天数必须是 0 到 3650 的整数", "error");
    return;
  }
  if (!confirm(`将永久清理已在回收站保留超过 ${retentionDays} 天的会话文件。此操作不可恢复，是否继续？`)) return;
  setStatus("正在清理…");
  try {
    const response = await fetch("/api/lifecycle/session-trash/reclaim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ retention_days: retentionDays }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "清理失败");
    const summary = data.summary || {};
    setStatus(`已清理 ${summary.groups || 0} 组、${summary.files || 0} 个文件，释放 ${formatBytes(summary.bytes)}`, "ok");
    uiRegistry.toast?.("回收站清理完成");
    await load();
  } catch (error) {
    setStatus(`清理失败：${error.message || error}`, "error");
  }
}

async function restore(trashId) {
  setStatus("正在恢复会话…");
  try {
    const response = await fetch(`/api/lifecycle/session-trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "恢复失败");
    setStatus(`已恢复 ${data.summary.restored.length} 个会话文件`, "ok");
    uiRegistry.toast?.("会话已恢复");
    await load();
  } catch (error) {
    setStatus(`恢复失败：${error.message || error}`, "error");
  }
}

function init() {
  if (!root) return;
  document.getElementById("lifecycle-refresh")?.addEventListener("click", load);
  document.getElementById("lifecycle-reclaim")?.addEventListener("click", reclaim);
  document.getElementById("lifecycle-open")?.addEventListener("click", load);
}

document.addEventListener("DOMContentLoaded", init);

export { load };
