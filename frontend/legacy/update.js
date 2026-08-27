// Check for updates via GitHub Releases.
import { $, esc } from "../core/dom.js";

export async function runUpdate() {
  const btn       = $("update-btn");
  const stateEl   = $("update-state");
  const versionEl = $("update-version-info");
  const assetsEl  = $("update-assets");
  const outEl     = $("update-output");
  const dlBtn     = $("update-download-btn");

  btn.disabled = true;
  versionEl.classList.add("hidden");
  assetsEl.classList.add("hidden");
  outEl.classList.add("hidden");
  dlBtn.classList.add("hidden");
  stateEl.className = "update-state update-loading";
  stateEl.innerHTML = `<span class="update-spinner"></span><span class="update-state-text">${t("update.checking")}</span>`;

  try {
    const r = await fetch("/api/system/check-update", { signal: AbortSignal.timeout(20000) });
    const d = await r.json();

    if (!d.ok) {
      stateEl.className = "update-state update-err";
      stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${esc(d.error || t("update.check_fail"))}</span>`;
      outEl.textContent = d.code === "github_rate_limited"
        ? "GitHub 匿名 API 有访问次数限制。稍后重试，或直接打开 Releases 页面查看最新版本。"
        : (d.error || "");
      outEl.classList.remove("hidden");
      dlBtn.href = d.release_url || "https://github.com/Zafer-Liu/Data-Analysis-Agent/releases/latest";
      dlBtn.textContent = "前往 GitHub 查看最新版";
      dlBtn.classList.remove("hidden");
      return;
    }

    // Show version comparison
    versionEl.innerHTML = `<div class="update-ver-row"><span>${t("update.current")}</span><strong>${esc(d.current_version)}</strong></div>`
      + `<div class="update-ver-row"><span>${t("update.latest")}</span><strong>${esc(d.latest_version)}</strong></div>`;
    versionEl.classList.remove("hidden");

    if (d.warning) {
      outEl.textContent = d.warning;
      outEl.classList.remove("hidden");
    }

    if (!d.has_update) {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">✅</span><span class="update-state-text">${t("update.ok_latest")}</span>`;
    } else {
      stateEl.className = "update-state update-ok";
      stateEl.innerHTML = `<span class="update-state-icon">🆕</span><span class="update-state-text">${t("update.new_version")}</span>`;

      // Keep downloads on GitHub so users can review the release and choose an installer.
      if (d.release_notes) {
        outEl.textContent = d.release_notes;
        outEl.classList.remove("hidden");
      }
      dlBtn.href = d.release_url || "https://github.com/Zafer-Liu/Data-Analysis-Agent/releases/latest";
      dlBtn.textContent = "前往 GitHub 下载最新版";
      dlBtn.classList.remove("hidden");
    }
  } catch (e) {
    const isTimeout = e.name === "AbortError";
    const msg = isTimeout
      ? (t("update.req_timeout") || "Request timed out")
      : (t("update.req_fail") || "Request failed: ") + esc(String(e));
    stateEl.className = "update-state update-err";
    stateEl.innerHTML = `<span class="update-state-icon">❌</span><span class="update-state-text">${msg}</span>`;
  } finally {
    btn.disabled = false;
  }
}
