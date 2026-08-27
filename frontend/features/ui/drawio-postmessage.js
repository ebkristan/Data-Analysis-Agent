/**
 * draw.io iframe postMessage protocol helpers.
 *
 * Self-hosts draw.io from the local static bundle at /static/drawio/
 * (Flask static_folder serves it). The iframe uses the JSON postMessage
 * embed protocol: https://www.drawio.com/doc/faq/embed-mode.
 *
 * This module is framework-free (pure JS) so it can be used by any
 * Vue island or future rendering layer.
 */

/* ── constants ── */

const DRAWIO_BASE = "/static/drawio/index.html";

const DRAWIO_EMBED_PARAMS =
  "embed=1&proto=json&spin=1&libraries=1&pwa=0&offline=0";

export const DRAWIO_EMBED_URL    = `${DRAWIO_BASE}?${DRAWIO_EMBED_PARAMS}&lang=zh`;
export const DRAWIO_EMBED_URL_EN = `${DRAWIO_BASE}?${DRAWIO_EMBED_PARAMS}&lang=en`;

/* ── helpers ── */

function _embedUrl(lang) {
  return (lang === "en" || lang === "ja") ? DRAWIO_EMBED_URL_EN : DRAWIO_EMBED_URL;
}

export function drawioEmbedUrl({ lang = "zh", cacheBust = "" } = {}) {
  const base = _embedUrl(lang);
  return cacheBust ? `${base}&baa_reload=${encodeURIComponent(cacheBust)}` : base;
}

export async function clearDrawioRuntimeCaches() {
  const drawioPath = "/static/drawio/";
  try {
    if (navigator.serviceWorker?.getRegistrations) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations
        .filter(reg => {
          try { return new URL(reg.scope).pathname.startsWith(drawioPath); }
          catch { return false; }
        })
        .map(reg => reg.unregister()));
    }
  } catch {
    // Cache cleanup is best-effort; iframe retry still handles failures.
  }

  try {
    if (globalThis.caches?.keys) {
      const keys = await caches.keys();
      await Promise.all(keys
        .filter(key => key.includes("static/drawio") || key.includes("drawio"))
        .map(key => caches.delete(key)));
    }
  } catch {
    // Ignore cache API failures in restricted browsers.
  }
}

/**
 * Create a draw.io iframe element (does NOT insert into DOM).
 * Caller appends it wherever needed.
 *
 * @param {{ lang?: string }} opts
 * @returns {HTMLIFrameElement}
 */
export function createDrawioIframe(opts = {}) {
  const iframe = document.createElement("iframe");
  iframe.src = drawioEmbedUrl({
    lang: opts.lang || "zh",
    cacheBust: opts.cacheBust || "",
  });
  iframe.style.cssText = "width:100%;height:100%;border:none;";
  iframe.setAttribute("allow", "clipboard-read; clipboard-write");
  return iframe;
}

/**
 * Wire up the bidirectional postMessage protocol.
 *
 * Returns a handle with methods for sending commands and a cleanup function.
 *
 * @param {HTMLIFrameElement} iframe  – already in the DOM and loaded
 * @param {{
 *   onReady?: () => void,
 *   onAutosave?: (xml: string) => void,
 *   onExport?: (data: { format: string, dataUrl: string, xml?: string }) => void,
 * }} callbacks
 * @returns {{
 *   loadXml: (xml: string) => void,
 *   exportDiagram: (format: string) => Promise<{ dataUrl: string, xml?: string }>,
 *   requestExport: (format: string) => void,
 *   destroy: () => void,
 * }}
 */
export function initDrawioProtocol(iframe, callbacks = {}) {
  let _ready = false;
  let _exportResolver = null;

  function _onMessage(event) {
    // Only accept messages from the draw.io iframe window
    if (event.source !== iframe.contentWindow) return;
    // draw.io embed protocol sends messages as JSON strings (e.g.
    // '{"event":"init"}'); parse them before inspecting fields.
    const raw = event.data;
    let msg = raw;
    if (typeof raw === "string") {
      try { msg = JSON.parse(raw); } catch { return; }
    }
    if (!msg || typeof msg !== "object") return;

    // ── init ──
    if (msg.event === "init") {
      _ready = true;
      callbacks.onReady?.();
      return;
    }

    // ── autosave ──
    if (msg.event === "autosave") {
      callbacks.onAutosave?.(msg.xml || "");
      return;
    }

    // ── export ── (response to our export request)
    if (msg.action === "export") {
      const result = {
        format: msg.format || "",
        dataUrl: msg.data || "",
        xml: msg.xml || undefined,
      };
      callbacks.onExport?.(result);
      if (_exportResolver) {
        _exportResolver(result);
        _exportResolver = null;
      }
      return;
    }

    // ── save ── (response to save request — same shape as export)
    if (msg.action === "save") {
      const result = {
        format: msg.format || "",
        dataUrl: msg.data || "",
        xml: msg.xml || "",
      };
      callbacks.onExport?.(result);
      if (_exportResolver) {
        _exportResolver(result);
        _exportResolver = null;
      }
      return;
    }
  }

  window.addEventListener("message", _onMessage);

  // ── outgoing commands ──

  function loadXml(xml) {
    if (!_ready) return;
    iframe.contentWindow.postMessage(
      JSON.stringify({ action: "load", autosave: 1, xml }),
      "*"
    );
  }

  /**
   * Request an export and return a Promise that resolves when the
   * iframe sends back the export data.
   *
   * Supported formats: "png", "svg", "xmlsvg", "pdf", "jpg"
   */
  function exportDiagram(format) {
    if (!_ready) return Promise.reject(new Error("draw.io not ready"));
    return new Promise((resolve) => {
      _exportResolver = resolve;
      iframe.contentWindow.postMessage(
        JSON.stringify({
          action: "export",
          format,
          spin: "Exporting...",
        }),
        "*"
      );
      // Timeout fallback: if iframe never responds, resolve with empty
      setTimeout(() => {
        if (_exportResolver === resolve) {
          _exportResolver = null;
          resolve({ format, dataUrl: "", xml: undefined });
        }
      }, 15000);
    });
  }

  /**
   * Fire-and-forget export — just sends the command, no Promise.
   * Use this when you handle the result via the onExport callback instead.
   */
  function requestExport(format) {
    if (!_ready) return;
    iframe.contentWindow.postMessage(
      JSON.stringify({ action: "export", format, spin: "Exporting..." }),
      "*"
    );
  }

  function destroy() {
    window.removeEventListener("message", _onMessage);
    _ready = false;
    _exportResolver = null;
  }

  return { loadXml, exportDiagram, requestExport, destroy };
}

/* ── file download helper ── */

/**
 * Trigger a browser download from a data URL or blob.
 *
 * @param {string} dataUrl  – base64 data URL or blob URL
 * @param {string} filename – download filename
 */
export function downloadDataUrl(dataUrl, filename) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    // Don't revoke immediately — browser may still be reading
  }, 200);
}

/**
 * Download raw XML as a .drawio file.
 *
 * @param {string} xml  – complete mxfile XML string
 * @param {string} filename
 */
export function downloadXmlFile(xml, filename) {
  const blob = new Blob([xml], { type: "application/xml" });
  const url = URL.createObjectURL(blob);
  downloadDataUrl(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 500);
}
