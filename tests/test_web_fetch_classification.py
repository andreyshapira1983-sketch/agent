"""Regression: web_fetch output must classify PUBLIC, not PRIVATE.

MGA-06 / CORE-09: ``web_fetch`` was absent from ``core.loop._TOOL_SOURCE_HINTS``,
so the loop's source-hint lookup (``_TOOL_SOURCE_HINTS.get(tool, "tool_output")``)
fell back to ``"tool_output"``, which ``core.data_classifier`` maps to PRIVATE.
A public web page fetched via ``web_fetch`` therefore lost its ``public``
classification (while the same page via ``web_search`` was correctly public).
"""
from __future__ import annotations

from core.data_classifier import DataClass, classify
from core.loop import _TOOL_SOURCE_HINTS


def _hint_for(tool: str) -> str:
    # Mirrors the loop's real lookup at core/loop.py (the `.get(..., "tool_output")`).
    return _TOOL_SOURCE_HINTS.get(tool, "tool_output")


def test_web_fetch_is_mapped_to_web_source_hint():
    assert _TOOL_SOURCE_HINTS.get("web_fetch") == "web"


def test_web_fetch_page_classifies_public_not_private():
    text = "The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris."
    result = classify(text, source=_hint_for("web_fetch"))
    assert result.cls == DataClass.PUBLIC, (
        f"a public web_fetch page should classify PUBLIC, got {result.cls}"
    )


def test_controls_unchanged():
    # The fix must not disturb the existing mappings.
    assert classify("x", source=_hint_for("web_search")).cls == DataClass.PUBLIC
    assert classify("x", source=_hint_for("file_read")).cls == DataClass.PRIVATE
