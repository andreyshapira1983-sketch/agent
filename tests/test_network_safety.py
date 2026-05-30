from __future__ import annotations

import socket

import pytest

from tools.network_safety import NetworkSafetyPolicy


def _resolver_for(ip: str):
    def _resolver(host, port, family, socktype, proto, flags):  # noqa: ANN001, ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return _resolver


def test_rejects_localhost_with_trailing_dot():
    policy = NetworkSafetyPolicy(tool_name="web_fetch", resolve_dns=False)

    with pytest.raises(PermissionError, match="local-only"):
        policy.validate_url("http://localhost./admin")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://10.0.0.7/admin",
    "http://172.16.0.7/admin",
    "http://192.168.1.7/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/admin",
])
def test_rejects_private_or_local_ip_literals(url: str):
    policy = NetworkSafetyPolicy(tool_name="web_fetch", resolve_dns=False)

    with pytest.raises(PermissionError, match="public global"):
        policy.validate_url(url)


def test_rejects_hostname_that_resolves_to_private_ip():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("127.0.0.1"),
        resolve_dns=True,
    )

    with pytest.raises(PermissionError, match="public global"):
        policy.validate_url("https://looks-public.example/page")


def test_accepts_hostname_that_resolves_to_public_ip():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
    )

    parsed = policy.validate_url("https://example.com/page")

    assert parsed.hostname == "example.com"


def test_rejects_redirect_to_private_target():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
    )

    with pytest.raises(PermissionError, match="public global"):
        policy.validate_redirect("https://example.com/start", "http://127.0.0.1/admin")


def test_allows_relative_redirect_when_resolved_host_is_public():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
    )

    assert (
        policy.validate_redirect("https://example.com/start", "/next")
        == "https://example.com/next"
    )
