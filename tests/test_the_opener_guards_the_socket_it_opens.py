"""The peer-IP guard must be reachable from a real request, not just present.

Measured 2026-08-20 auditing the suite as an instrument. `build_safe_opener`
had exactly one test on its wiring — test_network_safety.py's
test_build_safe_opener_installs_peer_ip_guard_handlers — and it asserts that
two class NAMES appear among the opener's handlers. The guard itself lives one
layer deeper, in the connection classes those handlers open. Pointing the
handlers at the unguarded stdlib connections left that test green, and the
whole suite with it: 8237 passed, 3 skipped, with the DNS-rebinding protection
removed.

Two sound tests sit either side of the gap and cannot close it:
`_assert_peer_ip_global` is proven correct in isolation against a fake socket,
and the URL-time policy is proven correct on hostnames. Neither says the guard
is on the path a request actually takes — and that path is the whole point,
because the attack is a second DNS answer AFTER the hostname was approved.

So this drives the opener and lies about the peer at connect time. It is a
behaviour test: nothing here mentions a class name, so a rename cannot redden
it and a bypass cannot hide from it.
"""
from __future__ import annotations

import socket
import ssl
import urllib.error

import pytest

from tools.network_safety import NetworkSafetyPolicy, build_safe_opener


class _RebindingSocket:
    """A socket that resolved to a public address and connected to metadata."""

    def __init__(self, peer: tuple[str, int]) -> None:
        self._peer = peer
        self.closed = False

    def getpeername(self) -> tuple[str, int]:
        return self._peer

    def settimeout(self, _timeout) -> None:
        return None

    def setsockopt(self, *_args) -> None:
        return None

    def makefile(self, *_args, **_kwargs):
        raise AssertionError(
            "the request reached the wire — the peer check did not run"
        )

    def sendall(self, *_args) -> None:
        raise AssertionError(
            "the request reached the wire — the peer check did not run"
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "peer_ip",
    ["169.254.169.254", "127.0.0.1", "10.0.0.7", "192.168.1.4"],
)
def test_a_peer_that_turns_private_at_connect_time_is_refused(
    monkeypatch: pytest.MonkeyPatch, peer_ip: str
) -> None:
    fake = _RebindingSocket((peer_ip, 80))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)

    opener = build_safe_opener(NetworkSafetyPolicy(tool_name="probe"))
    with pytest.raises((PermissionError, urllib.error.URLError)) as caught:
        opener.open("http://example.invalid/x", timeout=1)

    error = caught.value
    inner = getattr(error, "reason", error)
    assert isinstance(inner, PermissionError), (
        f"connecting to {peer_ip} raised {type(inner).__name__}: {inner} — the "
        "opener let a private peer through, or failed for an unrelated reason"
    )
    assert peer_ip in str(inner)


def test_a_public_peer_still_gets_to_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary pin: a guard that refuses everything would pass the test above
    while breaking every outbound request."""
    fake = _RebindingSocket(("93.184.216.34", 80))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)

    opener = build_safe_opener(NetworkSafetyPolicy(tool_name="probe"))
    with pytest.raises((AssertionError, urllib.error.URLError)) as caught:
        opener.open("http://example.invalid/x", timeout=1)

    inner = getattr(caught.value, "reason", caught.value)
    assert not isinstance(inner, PermissionError), (
        f"a public peer was refused: {inner}"
    )


@pytest.mark.parametrize("peer_ip", ["169.254.169.254", "10.0.0.7"])
def test_the_https_path_is_guarded_too(
    monkeypatch: pytest.MonkeyPatch, peer_ip: str
) -> None:
    """The HTTPS handler opens a different connection class, so the HTTP case
    above says nothing about it. Note what this pins and what it does not: the
    guard runs AFTER `super().connect()`, so on this path the TLS handshake
    with the hostile peer has already happened when the refusal arrives."""
    fake = _RebindingSocket((peer_ip, 443))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda self, sock, **k: sock)

    opener = build_safe_opener(NetworkSafetyPolicy(tool_name="probe"))
    with pytest.raises((PermissionError, urllib.error.URLError)) as caught:
        opener.open("https://example.invalid/x", timeout=1)

    inner = getattr(caught.value, "reason", caught.value)
    assert isinstance(inner, PermissionError), (
        f"an https request to {peer_ip} raised {type(inner).__name__}: {inner}"
    )
    assert peer_ip in str(inner)

