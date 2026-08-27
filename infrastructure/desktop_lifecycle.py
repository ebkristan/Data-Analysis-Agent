"""Track browser pages that keep the frozen desktop server alive."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class DesktopClientRegistry:
    """Thread-safe lease registry for one or more local browser pages."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        now = self._clock()
        with self._lock:
            self._created_at = now
            self._last_empty_at = now
            self._has_seen_client = False
            self._clients: dict[str, float] = {}

    def heartbeat(self, client_id: str) -> None:
        now = self._clock()
        with self._lock:
            self._has_seen_client = True
            self._clients[client_id] = now

    def disconnect(self, client_id: str) -> None:
        now = self._clock()
        with self._lock:
            self._clients.pop(client_id, None)
            if self._has_seen_client and not self._clients:
                self._last_empty_at = now

    def should_shutdown(
        self,
        *,
        startup_timeout: float,
        idle_timeout: float,
        heartbeat_timeout: float,
    ) -> bool:
        now = self._clock()
        with self._lock:
            expired = [
                client_id
                for client_id, seen_at in self._clients.items()
                if now - seen_at >= heartbeat_timeout
            ]
            for client_id in expired:
                self._clients.pop(client_id, None)
            if expired and not self._clients:
                self._last_empty_at = now

            if self._clients:
                return False
            if not self._has_seen_client:
                return now - self._created_at >= startup_timeout
            return now - self._last_empty_at >= idle_timeout

    def active_count(self) -> int:
        with self._lock:
            return len(self._clients)


desktop_clients = DesktopClientRegistry()
