"""Network safety helpers for read-only HTTP tools.

The fetch tools are intentionally small, but their network boundary has to be
strict: a URL that looks public can redirect to localhost, or a hostname can
resolve to a private address. This module centralizes those checks so
`web_fetch` and `rss_fetch` enforce the same SSRF policy.
"""
from __future__ import annotations

import gzip
import io
import ipaddress
import os
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.base import require_ascii_identifier


DEFAULT_MAX_URL_LEN = 2048
DEFAULT_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
})

Resolver = Callable[[str, int | None, int, int, int, int], list[tuple[Any, ...]]]


@dataclass(frozen=True)
class NetworkSafetyPolicy:
    """SSRF guard for URL validation, DNS checks, and redirect targets."""

    tool_name: str
    allowed_schemes: frozenset[str] = DEFAULT_ALLOWED_SCHEMES
    max_url_len: int = DEFAULT_MAX_URL_LEN
    resolver: Resolver | None = None
    resolve_dns: bool = True
    allow_http_hosts: tuple[str, ...] = ()
    egress_allow_hosts: tuple[str, ...] = ()
    egress_deny_hosts: tuple[str, ...] = ()

    def validate_url(self, url: Any, *, role: str | None = None) -> urllib.parse.SplitResult:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        if len(url) > self.max_url_len:
            raise ValueError(f"url too long ({len(url)} > {self.max_url_len})")
        require_ascii_identifier(url, role=role or f"{self.tool_name} url")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in self.allowed_schemes:
            raise PermissionError(
                f"scheme {parsed.scheme!r} not allowed; only "
                f"{sorted(self.allowed_schemes)} are permitted"
            )
        if not parsed.hostname:
            raise ValueError(f"url {url!r} has no hostname")
        host = _normalize_hostname(parsed.hostname)
        if _matches_host(host, self.egress_deny_hosts):
            raise PermissionError(f"hostname {parsed.hostname!r} is denied by egress policy")
        if self.egress_allow_hosts and not _matches_host(host, self.egress_allow_hosts):
            raise PermissionError(
                f"hostname {parsed.hostname!r} is not allowed by egress policy"
            )
        if parsed.scheme == "http" and not _matches_host(host, self.allow_http_hosts):
            raise PermissionError(
                "plain HTTP is disabled by default; add the host to the explicit "
                "HTTP allowlist or use HTTPS"
            )
        self.validate_host(parsed.hostname, parsed.port)
        return parsed

    def validate_redirect(self, from_url: str, to_url: str) -> str:
        absolute = urllib.parse.urljoin(from_url, to_url)
        old = urllib.parse.urlsplit(from_url)
        new = urllib.parse.urlsplit(absolute)
        if old.scheme == "https" and new.scheme == "http":
            raise PermissionError("mixed-mode redirect from HTTPS to HTTP is refused")
        self.validate_url(absolute, role=f"{self.tool_name} redirect url")
        return absolute

    def validate_host(self, hostname: str | None, port: int | None = None) -> None:
        if hostname is None:
            raise ValueError("url has no hostname")
        host_lower = _normalize_hostname(hostname)
        if host_lower in BLOCKED_HOSTNAMES:
            raise PermissionError(
                f"hostname {hostname!r} resolves to a local-only target; refused"
            )

        literal = _parse_ip_literal(host_lower)
        if literal is not None:
            _require_public_ip(literal, tool_name=self.tool_name)
            return

        if not self.resolve_dns:
            return

        resolver = self.resolver or socket.getaddrinfo
        try:
            infos = resolver(host_lower, port, 0, socket.SOCK_STREAM, 0, 0)
        except socket.gaierror as exc:
            raise ValueError(f"DNS resolution failed for {hostname!r}: {exc}") from None

        if not infos:
            raise ValueError(f"DNS resolution returned no addresses for {hostname!r}")

        checked: set[str] = set()
        for info in infos:
            sockaddr = info[4]
            ip_text = sockaddr[0] if sockaddr else ""
            if not ip_text or ip_text in checked:
                continue
            checked.add(ip_text)
            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError:
                raise PermissionError(
                    f"DNS for {hostname!r} returned invalid IP {ip_text!r}; refused"
                ) from None
            _require_public_ip(ip, tool_name=self.tool_name)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every redirect target before urllib follows it."""

    def __init__(self, policy: NetworkSafetyPolicy):
        self.policy = policy
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        old_url = req.full_url
        safe_url = self.policy.validate_redirect(old_url, newurl)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def build_safe_opener(policy: NetworkSafetyPolicy):
    return urllib.request.build_opener(SafeRedirectHandler(policy))


def decompress_gzip_limited(raw: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """Return at most `max_bytes` decompressed bytes plus a truncation flag."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        data = fh.read(max_bytes + 1)
    return data[:max_bytes], len(data) > max_bytes


def host_patterns_from_env(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(
        item.strip().lower()
        for item in value.split(",")
        if item.strip()
    )


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _require_public_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    tool_name: str,
) -> None:
    if not ip.is_global:
        raise PermissionError(
            f"IP {ip} is not a public global address; {tool_name} refuses local/private targets"
        )


def _normalize_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def _matches_host(hostname: str, patterns: tuple[str, ...]) -> bool:
    host = _normalize_hostname(hostname)
    for raw in patterns:
        pattern = raw.strip().rstrip(".").lower()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if pattern == host:
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != pattern[2:]:
                return True
        elif pattern.startswith("."):
            if host == pattern[1:] or host.endswith(pattern):
                return True
    return False
