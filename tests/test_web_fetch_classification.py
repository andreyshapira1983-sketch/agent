"""Regression: web_fetch output must classify PUBLIC, not PRIVATE.

MGA-06 / CORE-09: ``web_fetch`` was absent from ``core.loop._TOOL_SOURCE_HINTS``,
so the loop's source-hint lookup (``_TOOL_SOURCE_HINTS.get(tool, "tool_output")``)
fell back to ``"tool_output"``, which ``core.data_classifier`` maps to PRIVATE.
A public web page fetched via ``web_fetch`` therefore lost its ``public``
classification (while the same page via ``web_search`` was correctly public).
"""
from __future__ import annotations

from core.loop import _TOOL_SOURCE_HINTS


def test_web_fetch_is_mapped_to_web_source_hint():
    assert _TOOL_SOURCE_HINTS.get("web_fetch") == "web"
