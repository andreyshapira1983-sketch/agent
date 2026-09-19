"""The HTTPS handler's TLS context reaches the connection — and it verifies.

Web exam 2026-09-19, second run, N07: `peps.python.org` failed with
«CERTIFICATE_VERIFY_FAILED: unable to get issuer certificate» — the Windows
store lacked an intermediate; the certifi roots open the same address. Setting
a context was not enough: the guarded `https_open` called `do_open` without
`context=`, so any context given to the handler was silently dropped. Checked
live the same day: expired, wrong-host and self-signed certificates are still
refused. Offline here.
"""
from __future__ import annotations

import ssl
import urllib.request

from tools.network_safety import NetworkSafetyPolicy, _guarded_handlers, _tls_context


def test_the_context_verifies() -> None:
    ctx = _tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_the_handler_passes_its_context_on(monkeypatch) -> None:
    https = next(h for h in _guarded_handlers(NetworkSafetyPolicy(tool_name="web_fetch"))
                 if isinstance(h, urllib.request.HTTPSHandler))
    seen = {}
    monkeypatch.setattr(https, "do_open", lambda cls, req, **kw: seen.update(kw))
    https.https_open(urllib.request.Request("https://example.org/"))
    assert isinstance(seen.get("context"), ssl.SSLContext), "the context was dropped on the way"
