"""MIR-053 — the suite denies outbound network by default.

Before this existed, nothing refused an unmocked provider call: a diagnostic
run picked live keys up from `.env` and billed one real request, silently.
Test fixtures delete keys per-file, but that is a convention each author must
remember, not a boundary. The boundary now lives in `tests/conftest.py`
(`_deny_outbound_network`): an outbound TCP `connect` raises loudly, loopback
stays open (local test servers are legitimate), and a run that genuinely
needs the network must say so via ``AGENT_TESTS_ALLOW_NETWORK=1``.

The probe target is 192.0.2.1 (RFC 5737 TEST-NET-1): reserved for
documentation, guaranteed non-routable — so even on the OLD code proving the
defect sends nothing anywhere real; the connect just hangs into its timeout
instead of being refused.
"""
from __future__ import annotations

import socket
import threading

import pytest


def test_an_outbound_connect_fails_loudly_instead_of_dialing_out():
    """The registry's missing test, verbatim: an unmocked call fails, not dials."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        with pytest.raises(RuntimeError, match="MIR-053"):
            s.connect(("192.0.2.1", 443))


def test_connect_ex_is_covered_too():
    """`connect_ex` swallows OSError by design, so it needs its own guard —
    a client probing with it would otherwise dial out and just read the code."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        with pytest.raises(RuntimeError, match="MIR-053"):
            s.connect_ex(("192.0.2.1", 443))


def test_loopback_is_still_open():
    """Local test servers are legitimate; the deny must not break them."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = []

    def _accept():
        conn, _ = listener.accept()
        accepted.append(conn)

    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect(("127.0.0.1", port))
        t.join(timeout=5)
        assert accepted, "loopback connect must reach the local listener"
    finally:
        for conn in accepted:
            conn.close()
        listener.close()


def test_localhost_by_name_is_still_open():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect(("localhost", port))
    finally:
        listener.close()
