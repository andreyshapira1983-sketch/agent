"""Tests for brain/status_server.py."""

from __future__ import annotations

import json
import urllib.request

import pytest

from brain.status_server import StatusServer


# ════════════════════════════════════════════════════════════════════
# Helper
# ════════════════════════════════════════════════════════════════════

def _get(port: int, path: str) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

class TestStatusServer:

    def test_healthz_always_ok(self):
        server = StatusServer(port=0)
        server.start()
        try:
            status, body = _get(server.port, "/healthz")
            assert status == 200
            assert body["ok"] is True
        finally:
            server.stop()

    def test_status_aggregates_providers(self):
        server = StatusServer(
            port=0,
            providers={
                "a": lambda: {"x": 1},
                "b": lambda: {"y": 2},
            },
        )
        server.start()
        try:
            status, body = _get(server.port, "/status")
            assert status == 200
            assert body["a"] == {"x": 1}
            assert body["b"] == {"y": 2}
            assert "uptime_secs" in body
        finally:
            server.stop()

    def test_provider_error_isolated(self):
        def boom():
            raise RuntimeError("boom")
        server = StatusServer(port=0, providers={
            "ok": lambda: {"hi": 1},
            "bad": boom,
        })
        server.start()
        try:
            status, body = _get(server.port, "/status")
            # Whole endpoint still 200 — error is contained in payload
            assert status == 200
            assert body["ok"] == {"hi": 1}
            assert "error" in body["bad"]
        finally:
            server.stop()

    def test_ready_returns_503_when_provider_errors(self):
        def boom():
            raise RuntimeError("boom")
        server = StatusServer(port=0, providers={"x": boom})
        server.start()
        try:
            status, body = _get(server.port, "/ready")
            assert status == 503
            assert body["ok"] is False
        finally:
            server.stop()

    def test_404_for_unknown_paths(self):
        server = StatusServer(port=0)
        server.start()
        try:
            status, body = _get(server.port, "/whatever")
            assert status == 404
        finally:
            server.stop()

    def test_post_returns_405(self):
        server = StatusServer(port=0)
        server.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/status",
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=2)  # noqa: S310
                assert False, "expected 405"
            except urllib.error.HTTPError as exc:
                assert exc.code == 405
        finally:
            server.stop()
