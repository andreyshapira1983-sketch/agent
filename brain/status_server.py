"""
brain/status_server.py — tiny /status HTTP endpoint.

A monitor-able read-only HTTP surface for the agent's health. Built on
the stdlib `http.server` to avoid pulling FastAPI in just for one
endpoint. Three endpoints:

    GET  /status     — JSON dict assembled from injected providers
    GET  /healthz    — 200 OK if the server is alive
    GET  /ready      — 200 if every status provider returned without error

Run in a daemon thread so the agent's main loop owns the lifecycle:

    server = StatusServer(host="127.0.0.1", port=8765, providers={
        "brain":   brain.status,
        "audit":   lambda: {"head": audit.head(), "len": len(audit)},
        "budget":  budget.state,
        "jobs":    lambda: {"active": len(job_store.list_active())},
    })
    server.start()
    try:
        ...
    finally:
        server.stop()

Security: bind to 127.0.0.1 by default. Refuse any non-GET. No
authentication — assume the operator put a reverse proxy in front.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

logger = logging.getLogger(__name__)


StatusProvider = Callable[[], dict]


# ════════════════════════════════════════════════════════════════════
# Server
# ════════════════════════════════════════════════════════════════════

class StatusServer:
    """Threaded HTTP server hosting `/status`, `/healthz`, `/ready`."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        providers: dict[str, StatusProvider] | None = None,
    ) -> None:
        self._host = str(host)
        self._port = int(port)
        self._providers = dict(providers or {})
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        self._started_at = 0.0

    # ────────────────────────────────────────────────────────────────

    def add_provider(self, name: str, provider: StatusProvider) -> None:
        self._providers[name] = provider

    def status(self) -> dict:
        """Build the full status dict by calling every provider."""
        out: dict = {
            "host":        self._host,
            "port":        self._port,
            "uptime_secs": round(time.monotonic() - self._started_at, 2)
                            if self._started_at else 0,
        }
        for name, fn in self._providers.items():
            try:
                out[name] = fn()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[StatusServer] provider '%s' failed", name)
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    def is_ready(self) -> bool:
        """True if every provider returned a non-error dict."""
        snapshot = self.status()
        for name, value in snapshot.items():
            if isinstance(value, dict) and "error" in value:
                return False
        return True

    # ────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start serving in a background thread."""
        if self._thread is not None:
            return
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self._host, self._port), handler)
        # Resolve the actual port (in case caller passed port=0)
        self._port = self._server.server_address[1]
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="StatusServer",
            daemon=True,
        )
        self._thread.start()
        logger.info("[StatusServer] listening on http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
        self._started_at = 0.0

    @property
    def port(self) -> int:
        return self._port


# ════════════════════════════════════════════════════════════════════
# Handler factory — closes over the StatusServer instance
# ════════════════════════════════════════════════════════════════════

def _make_handler(status_server: StatusServer):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            # Route HTTP server logs through our logger
            logger.debug("[StatusServer] " + fmt, *args)

        def do_GET(self) -> None:  # noqa: N802 — http.server convention
            path = (self.path or "/").split("?", 1)[0]
            if path == "/healthz":
                self._respond(200, {"ok": True})
                return
            if path == "/ready":
                ok = status_server.is_ready()
                self._respond(200 if ok else 503, {"ok": ok})
                return
            if path == "/status":
                self._respond(200, status_server.status())
                return
            self._respond(404, {"error": "not found", "path": path})

        def do_POST(self) -> None:  # noqa: N802
            self._respond(405, {"error": "method not allowed"})

        # ────────────────────────────────────────────────────────

        def _respond(self, status_code: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return _Handler
