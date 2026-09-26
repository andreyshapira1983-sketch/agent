"""Предупреждающая пометка переживает чистку ответа, остальные — нет.

Замер, отвергнутые варианты и границы: H-43 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pytest

from core.answer_format import _strip_verification_markers


@pytest.mark.parametrize("marker", [
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


def test_an_uncited_claim_keeps_its_warning_and_the_human_reads_words() -> None:
    """2026-09-25: a bare `[unverified]` sits on ONE claim and says it has no
    source. Stripped, the claim looked checked — "Silence Is Endorsement"
    (arXiv 2609.20211): without the mark a monitor approves 5% -> 60%."""
    from core.answer_format import format_human_response

    kept = _strip_verification_markers("Утверждение без источника [unverified].")
    assert "[unverified]" in kept
    shown = format_human_response(
        "Conclusion: Итог [unverified]\nFacts:\n- Без источника [unverified]\nConfidence: low")
    assert "[unverified]" not in shown, "a machine tag reached the human"
    assert shown.count("не проверял") == 2, shown


def test_the_docstring_states_the_boundary() -> None:
    """Растяжка на объяснение: молчаливая граница будет «дополнена» кем-нибудь.

    Проверяется не текст ради текста: именно отсутствие объяснения заставило
    прочесть полную реализацию как неполную.
    """
    doc = _strip_verification_markers.__doc__ or ""
    assert "topic-only" in doc, "граница не названа в самом месте, где живёт"
