"""Предупреждающая пометка переживает чистку ответа, остальные — нет.

Замер, отвергнутые варианты и границы: H-43 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pytest

from core.answer_format import _strip_verification_markers


@pytest.mark.parametrize("marker", [
    "[unverified]",
    "[verified:web:1]",
    "[declared:runtime:mode]",
])
def test_a_neutral_marker_is_removed(marker: str) -> None:
    assert marker not in _strip_verification_markers(f"Ответ {marker} хвост.")


def test_the_warning_marker_is_kept() -> None:
    text = "Утверждение [topic-only:web:3] и хвост."
    assert "[topic-only:web:3]" in _strip_verification_markers(text), (
        "предупреждение о том, что источник утверждения не подтверждает, "
        "вычищено из ответа — человек видит более уверенный ответ, чем есть"
    )


def test_the_docstring_states_the_boundary() -> None:
    """Растяжка на объяснение: молчаливая граница будет «дополнена» кем-нибудь.

    Проверяется не текст ради текста: именно отсутствие объяснения заставило
    прочесть полную реализацию как неполную.
    """
    doc = _strip_verification_markers.__doc__ or ""
    assert "topic-only" in doc, "граница не названа в самом месте, где живёт"
