// Keep the frozen desktop server alive while at least one application page exists.
(function () {
  "use strict";

  const clientId = (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID().replaceAll("-", "")
    : `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const base = `/api/desktop/clients/${encodeURIComponent(clientId)}`;
  let supported = true;
  let timer = null;

  async function heartbeat() {
    if (!supported) return;
    try {
      const response = await fetch(`${base}/heartbeat`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        keepalive: true,
      });
      if (response.status === 404) {
        supported = false;
        if (timer !== null) window.clearInterval(timer);
      }
    } catch (_) {
      // The next lease tick retries while the local server is still available.
    }
  }

  function disconnect() {
    if (!supported) return;
    const url = `${base}/disconnect`;
    if (!navigator.sendBeacon || !navigator.sendBeacon(url, new Blob([]))) {
      fetch(url, { method: "POST", keepalive: true, credentials: "same-origin" })
        .catch(() => {});
    }
  }

  heartbeat();
  timer = window.setInterval(heartbeat, 3000);
  // Foreground/visibility changes fire an immediate heartbeat so a page that
  // was backgrounded (and had its timers throttled to ~1/min) refreshes its
  // lease the moment the user returns, before they can click anything.
  window.addEventListener("pageshow", heartbeat);
  window.addEventListener("focus", heartbeat);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") heartbeat();
  });
  window.addEventListener("pagehide", disconnect);
}());
