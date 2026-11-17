"""Находка поиска, стоящая в списке дальше выдержки, не теряется для сверки — а чужая не появляется."""
from __future__ import annotations

from core.evidence import MAX_EXCERPT_CHARS, ProvenanceChain, evidence_from_tool_result
from core.verifier_absence import literals_absent_from_excerpt
from core.verifier_utils import best_run_of_source

_HITS = [{"title": f"Обзор складских программ, часть {n}: " + "подробный разбор " * 6,
          "url": f"https://example.org/reviews/warehouse_part_{n}.md"} for n in range(1, 11)]


def _view(claim: str):
    ev = evidence_from_tool_result(tool_name="web_search", arguments={"query": "складские программы"},
                                   output=_HITS)
    assert ev is not None and "warehouse_part_9" not in ev.excerpt
    assert len(ev.source_text) > MAX_EXCERPT_CHARS and "source_text" not in ev.to_dict()
    return best_run_of_source(ev, ProvenanceChain([ev]), claim)


def test_a_late_hit_is_visible_to_the_literal_gate() -> None:
    claim = "Поиск вернул страницу warehouse_part_9.md"
    view = _view(claim)
    assert not literals_absent_from_excerpt(claim, view.excerpt, view.source_id)


def test_a_hit_the_search_did_not_return_stays_absent() -> None:
    claim = "Поиск вернул страницу depot_part_3.md"
    view = _view(claim)
    assert literals_absent_from_excerpt(claim, view.excerpt, view.source_id)
