"""Loopback-only browser leases for the frozen desktop launcher."""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from flask import Blueprint, abort, request

from infrastructure.desktop_lifecycle import desktop_clients


bp = Blueprint("desktop", __name__)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _allow_desktop_lifecycle(client_id: str) -> None:
    if os.environ.get("BAA_DESKTOP_LIFECYCLE") != "1":
        abort(404)
    remote = (request.remote_addr or "").split("%", 1)[0]
    if remote not in {"127.0.0.1", "::1"} or not _CLIENT_ID.fullmatch(client_id):
        abort(404)
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        abort(404)
    origin = request.headers.get("Origin")
    if origin:
        try:
            if urlsplit(origin).netloc.lower() != request.host.lower():
                abort(404)
        except ValueError:
            abort(404)


@bp.post("/api/desktop/clients/<client_id>/heartbeat")
def desktop_heartbeat(client_id: str):
    _allow_desktop_lifecycle(client_id)
    desktop_clients.heartbeat(client_id)
    return "", 204


@bp.post("/api/desktop/clients/<client_id>/disconnect")
def desktop_disconnect(client_id: str):
    _allow_desktop_lifecycle(client_id)
    desktop_clients.disconnect(client_id)
    return "", 204
