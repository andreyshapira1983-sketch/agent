"""Admission rules for the planner-level SSRF pre-filter.

The pre-filter in core.step_sanitizer is defense-in-depth: the real boundary
is tools/network_safety.py, which parses hostnames and refuses non-public
targets at fetch time.  These tests pin the pre-filter itself: it must judge
the URL's actual host, not a substring of the whole URL, so userinfo tricks
(``http://evil.com@127.0.0.1/``) cannot slip a local target into a plan.
"""

import pytest

from core.step_sanitizer import sanitize_step


def _admit_url(tool_name: str, url: str) -> tuple[dict | None, list[str]]:
    warnings: list[str] = []
    step = sanitize_step(tool_name, {"url": url}, None, 0, warnings)
    return step, warnings


@pytest.mark.parametrize("tool_name", ["web_fetch", "rss_fetch"])
@pytest.mark.parametrize(
    "url",
    [
        # The userinfo bypass Copilot flagged on PR #241: the substring
        # check saw "://evil.com@127." and admitted the step.
        "http://evil.com@127.0.0.1/",
        "http://user:pass@10.0.0.1/feed",
        # Plain local targets the old substring check already caught —
        # parity must hold after the rewrite.
        "http://localhost/admin",
        "http://localhost.evil.com/",
        "http://127.0.0.1:8080/x",
        "http://0.0.0.0/",
        "http://192.168.1.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::1]:8080/",
    ],
)
def test_local_network_urls_are_dropped(tool_name, url):
    step, warnings = _admit_url(tool_name, url)
    assert step is None, f"{url} was admitted"
    assert any("targets local network" in w for w in warnings), warnings


@pytest.mark.parametrize("tool_name", ["web_fetch", "rss_fetch"])
@pytest.mark.parametrize(
    "url",
    [
        # Public hosts must stay admitted, including ones whose URL merely
        # *contains* a local-looking substring outside the host.
        "https://en.wikipedia.org/wiki/127.0.0.1",
        "https://site.io/docs?redirect=http://localhost/",
        "http://user@public-mirror.org/feed",
    ],
)
def test_public_urls_stay_admitted(tool_name, url):
    step, warnings = _admit_url(tool_name, url)
    assert step is not None, f"{url} was dropped: {warnings}"
    assert warnings == []
