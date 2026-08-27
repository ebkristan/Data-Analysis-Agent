"""Frozen desktop entry point for Windows and macOS.

Starts the local Waitress server, waits for the minimal health endpoint, then
opens the user's default browser. The launcher never installs dependencies or
touches source-checkout uploads/outputs.
"""

from __future__ import annotations

import atexit
import json
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path


log = logging.getLogger("desktop_launcher")
SERVICE_ID = "business-analytics-agent"


def _normalize_windows_environment() -> None:
    """Supply a standard Windows variable omitted by some CI launch shells."""
    if os.name == "nt" and not os.environ.get("WINDIR"):
        os.environ["WINDIR"] = os.environ.get("SystemRoot", r"C:\Windows")


def _health_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}/api/health"


def _app_url(host: str, port: int) -> str:
    return _health_url(host, port).removesuffix("/api/health")


def probe_health(url: str, timeout: float = 1.0) -> bool:
    """Return True only for this application's intentionally minimal probe."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return bool(
            response.status == 200
            and payload.get("ok") is True
            and payload.get("status") == "healthy"
            and payload.get("service") == SERVICE_ID
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def wait_for_health(
    url: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.2,
    probe: Callable[[str, float], bool] = probe_health,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if probe(url, min(1.0, max(interval, 0.05))):
            return True
        time.sleep(max(interval, 0.01))
    return False


def _open_browser_when_ready(health_url: str, app_url: str) -> None:
    if wait_for_health(health_url):
        if os.environ.get("BAA_NO_BROWSER") != "1":
            webbrowser.open(app_url, new=1, autoraise=True)
    else:
        log.error("desktop server did not become healthy: %s", health_url)


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _monitor_desktop_clients(registry, shutdown_event, close_server) -> None:
    startup_timeout = _positive_float_env("BAA_DESKTOP_STARTUP_TIMEOUT", 120.0)
    idle_timeout = _positive_float_env("BAA_DESKTOP_IDLE_TIMEOUT", 5.0)
    # Browsers throttle background-tab timers to ~once per minute, so the
    # heartbeat can legitimately stall well past the client's send interval.
    # Keep a generous grace period so a backgrounded page never makes the
    # server shut down under the user (which surfaces as "Failed to fetch").
    # Explicit page close still exits promptly via disconnect + idle_timeout.
    heartbeat_timeout = _positive_float_env("BAA_DESKTOP_HEARTBEAT_TIMEOUT", 90.0)
    while not shutdown_event.wait(0.5):
        if registry.should_shutdown(
            startup_timeout=startup_timeout,
            idle_timeout=idle_timeout,
            heartbeat_timeout=heartbeat_timeout,
        ):
            log.info("all desktop pages closed; stopping local server")
            close_server()
            return


def main() -> int:
    multiprocessing.freeze_support()
    _normalize_windows_environment()
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if os.environ.get("BAA_ONEDIR_SELF_TEST") == "1":
        from frozen_smoke import run_frozen_smoke

        return run_frozen_smoke()

    host = os.environ.get("BAA_DESKTOP_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("BAA_DESKTOP_PORT") or os.environ.get("AGENT_PORT") or 5001)
    except ValueError:
        log.error("BAA_DESKTOP_PORT/AGENT_PORT must be an integer")
        return 2
    if not 0 <= port <= 65535:
        log.error("desktop port must be between 0 and 65535")
        return 2

    health_url = _health_url(host, port)
    app_url = _app_url(host, port)
    if port and probe_health(health_url):
        if os.environ.get("BAA_NO_BROWSER") != "1":
            webbrowser.open(app_url, new=1, autoraise=True)
        return 0

    previous_lifecycle = os.environ.get("BAA_DESKTOP_LIFECYCLE")
    os.environ["BAA_DESKTOP_LIFECYCLE"] = "1"
    try:
        from waitress import create_server
        from app import app
        from infrastructure.desktop_lifecycle import desktop_clients

        desktop_clients.reset()

        server = create_server(
            app,
            host=host,
            port=port,
            send_bytes=1,
            inbuf_overflow=1024 * 1024,
            connection_limit=100,
            channel_timeout=300,
        )
    except Exception as exc:
        if previous_lifecycle is None:
            os.environ.pop("BAA_DESKTOP_LIFECYCLE", None)
        else:
            os.environ["BAA_DESKTOP_LIFECYCLE"] = previous_lifecycle
        log.exception("failed to initialize desktop server: %s", exc)
        return 1

    effective_port = int(server.effective_port)
    health_url = _health_url(host, effective_port)
    app_url = _app_url(host, effective_port)
    closed = False
    shutdown_event = threading.Event()

    def close_server(*_args) -> None:
        nonlocal closed
        if not closed:
            closed = True
            shutdown_event.set()
            server.close()

    atexit.register(close_server)
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, close_server)
        except (OSError, ValueError):
            pass

    threading.Thread(
        target=_open_browser_when_ready,
        args=(health_url, app_url),
        daemon=True,
        name="desktop-browser-opener",
    ).start()
    threading.Thread(
        target=_monitor_desktop_clients,
        args=(desktop_clients, shutdown_event, close_server),
        daemon=True,
        name="desktop-client-monitor",
    ).start()
    log.info("Business Analytics Agent desktop -> %s", app_url)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        close_server()
        if previous_lifecycle is None:
            os.environ.pop("BAA_DESKTOP_LIFECYCLE", None)
        else:
            os.environ["BAA_DESKTOP_LIFECYCLE"] = previous_lifecycle
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
