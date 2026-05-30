"""Network safety helpers for read-only HTTP tools.

The fetch tools are intentionally small, but their network boundary has to be
strict: a URL that looks public can redirect to localhost, or a hostname can
resolve to a private address. This module centralizes those checks so
`web_fetch` and `rss_fetch` enforce the same SSRF policy.
"""
from __future__ import annotations

import ipaddress
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
        self.validate_host(parsed.hostname, parsed.port)
        return parsed

    def validate_redirect(self, from_url: str, to_url: str) -> str:
        absolute = urllib.parse.urljoin(from_url, to_url)
        self.validate_url(absolute, role=f"{self.tool_name} redirect url")
        return absolute

    def validate_host(self, hostname: str | None, port: int | None = None) -> None:
        if hostname is None:
            raise ValueError("url has no hostname")
        host_lower = hostname.rstrip(".").lower()
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
