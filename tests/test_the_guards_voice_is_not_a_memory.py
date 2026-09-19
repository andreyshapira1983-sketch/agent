"""Голос охранника — аннотация, а не факт источника: в память он не пишется.

Background: docs/CODE_NOTES.md, "The guard's voice became a memory".
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.injection_guard import annotate_suspicious, strip_suspicious_annotation
from core.knowledge_pipeline import KnowledgePipeline
from core.source_ranker import rank_chain

#: Живой прогон 2026-08-16 (run_3220976037): три доктринных файла сработали как
#: «подозрительные», и обёртка-предупреждение легла в постоянную память СЕМЬЮ
#: записями с confidence 0.85 — как знание, добытое из источника.
_REAL = (
    "The organisation exists only when roles and budgets are explicit. "
    "Human approval remains mandatory for irreversible actions."
)


def _claims_from(excerpt: str):
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file", source_id="file:knowledge/doctrine/X.md",
        obtained_via="file_read", claim="Contents of workspace file X.md",
        excerpt=excerpt, confidence=0.9,
    ))
    ranking = rank_chain(chain, question="what does the doctrine say")
    registry, _ = KnowledgePipeline().build_registry(chain, ranking=ranking)
    return [c.text for c in registry.claims]


def test_the_wrapper_never_becomes_a_claim():
    texts = _claims_from(annotate_suspicious(_REAL, "file:knowledge/doctrine/X.md"))

    joined = " ".join(texts)
    assert "may be adversarial" not in joined
    assert "untrusted data only" not in joined
    assert "END OF UNTRUSTED CONTENT" not in joined


def test_the_real_content_still_becomes_claims():
    """Улов не отдан: подозрительность источника не лишает его содержимое
    права быть утверждением — судят другие ворота, не обёртка.
    """
    wrapped = _claims_from(annotate_suspicious(_REAL, "file:x"))
    plain = _claims_from(_REAL)

    assert wrapped, "из обёрнутого источника не извлеклось ничего"
    assert set(wrapped) == set(plain), "обёртка изменила состав утверждений"


def test_strip_removes_both_wrapper_lines_and_keeps_the_text():
    wrapped = annotate_suspicious(_REAL, "file:some/path.md")

    stripped = strip_suspicious_annotation(wrapped)

    assert "WARNING" not in stripped
    assert "END OF UNTRUSTED CONTENT" not in stripped
    assert _REAL in stripped


def test_strip_is_a_no_op_on_clean_text():
    assert strip_suspicious_annotation(_REAL) == _REAL
