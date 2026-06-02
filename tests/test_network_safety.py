from __future__ import annotations

import gzip
import socket

import pytest

from tools.network_safety import NetworkSafetyPolicy, decompress_gzip_limited


def _resolver_for(ip: str):
    def _resolver(host, port, family, socktype, proto, flags):  # noqa: ANN001, ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return _resolver


def test_rejects_localhost_with_trailing_dot():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolve_dns=False,
        allow_http_hosts=("localhost",),
    )

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
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolve_dns=False,
        allow_http_hosts=("*",),
    )

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
        policy.validate_redirect("https://example.com/start", "https://127.0.0.1/admin")


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


def test_http_is_rejected_by_default_even_for_public_host():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
    )

    with pytest.raises(PermissionError, match="plain HTTP"):
        policy.validate_url("http://example.com/page")


def test_http_can_be_allowed_for_explicit_public_host():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
        allow_http_hosts=("example.com",),
    )

    parsed = policy.validate_url("http://example.com/page")

    assert parsed.scheme == "http"


def test_https_to_http_redirect_is_rejected_even_when_http_host_is_allowed():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
        allow_http_hosts=("example.com",),
    )

    with pytest.raises(PermissionError, match="mixed-mode"):
        policy.validate_redirect("https://example.com/start", "http://example.com/next")


def test_egress_deny_policy_blocks_host():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
        egress_deny_hosts=("example.com",),
    )

    with pytest.raises(PermissionError, match="denied by egress policy"):
        policy.validate_url("https://example.com/page")


def test_egress_allow_policy_blocks_unknown_host():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("93.184.216.34"),
        resolve_dns=True,
        egress_allow_hosts=("*.python.org",),
    )

    with pytest.raises(PermissionError, match="not allowed by egress policy"):
        policy.validate_url("https://example.com/page")


def test_egress_allow_policy_accepts_suffix_pattern():
    policy = NetworkSafetyPolicy(
        tool_name="web_fetch",
        resolver=_resolver_for("151.101.0.223"),
        resolve_dns=True,
        egress_allow_hosts=("*.python.org",),
    )

    parsed = policy.validate_url("https://docs.python.org/3/")

    assert parsed.hostname == "docs.python.org"


def test_decompress_gzip_limited_caps_decompressed_size():
    compressed = gzip.compress(b"a" * 100)

    data, truncated = decompress_gzip_limited(compressed, 10)

    assert data == b"a" * 10
    assert truncated is True
