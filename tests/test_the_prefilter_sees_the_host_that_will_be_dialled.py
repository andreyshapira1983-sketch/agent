"""The step pre-filter must parse a URL the way the connection will.

Found by mutation 2026-08-20: changing `authority.split("@", 1)` to
`split("@", 2)` in `core/step_sanitizer.py` survived the entire suite — and the
mutant was the more correct of the two.

A URL may carry more than one '@' in its authority. Per the URL parsers
everyone actually uses, the userinfo ends at the LAST one:

    http://user:pass@evil.com@169.254.169.254/latest/meta-data/
    urlsplit(...).hostname  ->  169.254.169.254
    the pre-filter computed ->  evil.com@169.254.169.254

That second string is not an IP literal, so the local-network check said no,
and a step aimed at the cloud metadata endpoint read as ordinary traffic.

Severity, measured rather than assumed: `tools.network_safety` parses the same
URL correctly and refuses all three variants below, so the defence in depth
held and this was a pre-filter bypass, not an open door. The defect worth
naming is two parsers in one codebase disagreeing about what the host is —
which is how filter bypasses are built.

The invariant is therefore not a list of tricky URLs but agreement: whatever
the pre-filter thinks the host is, it must be what the URL parser will dial.
"""
from __future__ import annotations

import urllib.parse

import pytest

from core.step_sanitizer import _is_local_network_host, _url_host

_ADVERSARIAL = [
    "http://user:pass@evil.com@169.254.169.254/latest/meta-data/",
    "http://evil.com@127.0.0.1/x",
    "http://a@b@c@10.0.0.1/x",
    "http://user@example.com/x",
    "http://example.com/x",
    "http://example.com:8080/x",
    "http://[::1]:8080/x",
    "http://user:pass@[fd00::1]/x",
]


@pytest.mark.parametrize("url", _ADVERSARIAL)
def test_the_prefilter_host_is_the_host_the_url_parser_returns(url: str) -> None:
    expected = urllib.parse.urlsplit(url).hostname or ""
    assert _url_host(url) == expected.strip("."), (
        f"the pre-filter would judge {_url_host(url)!r} while the request goes "
        f"to {expected!r} — two parsers, one URL, and the guard is on the "
        "wrong one"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@evil.com@169.254.169.254/latest/meta-data/",
        "http://evil.com@127.0.0.1/x",
        "http://a@b@c@10.0.0.1/x",
    ],
)
def test_a_second_at_sign_does_not_hide_a_local_target(url: str) -> None:
    assert _is_local_network_host(_url_host(url)) is True


def test_an_ordinary_public_url_is_still_not_local() -> None:
    """Boundary pin: a filter that called everything local would satisfy the
    tests above and block all outbound work."""
    assert _is_local_network_host(_url_host("http://example.com/x")) is False
