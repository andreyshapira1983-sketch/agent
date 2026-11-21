"""MVP-14.4 — unit tests for the Verifier.

These pin the contract Verifier presents to the rest of the loop:

  * every claim either resolves to an Evidence record (verified) or
    gets an [unverified] tag — there is no third state where a claim
    silently sneaks through;
  * a fully unverified answer ALWAYS carries a disclaimer;
  * citation grammar parsing is deterministic and bracket-safe;
  * matching is substring-on-source_id with same-kind preference.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    is_structural_chunk,
    verify,
)

# ============================================================
# match_citation
# ============================================================

def _chain_with(*evidences):
    c = ProvenanceChain()
    for ev in evidences:
        c.add(ev)
    return c


# ============================================================
# verify() — overall behaviour
# ============================================================

def _file_ev(path: str, excerpt: str = "content") -> object:
    return make_evidence(
        kind="file", source_id=f"file:{path}", obtained_via="file_read",
        claim=f"file {path}", excerpt=excerpt,
    )


class TestVerifyEmptyAnswer:
    def test_empty_answer_yields_zero_chunks(self):
        report = verify(answer="", chain=_chain_with(_file_ev("a.txt")))
        assert report.total_chunks == 0
        assert report.verified_chunks == 0
        # Empty answer is by definition fully unverified.
        assert report.fully_unverified is True


# ============================================================
# MVP-14.4.x — Structural-chunk skipping
# ============================================================

class TestIsStructuralChunk:
    @pytest.mark.parametrize("header", [
        "Conclusion:", "Facts:", "Sources:", "Confidence:",
        "Unverified:", "Safety:",
        "conclusion:", "FACTS:",          # case-insensitive
        "Conclusion", "Facts",            # colon optional
        "  Conclusion:  ",                # whitespace tolerant
        "# Conclusion", "## Facts:",      # markdown-prefixed
        "**Conclusion:**", "**Facts:**",  # markdown-bold headers
    ])
    def test_output_contract_headers_are_structural(self, header: str):
        assert is_structural_chunk(header)

    @pytest.mark.parametrize("md", [
        "# Title", "## Subtitle", "### h3", "#### h4",
        "##### h5", "###### h6",
    ])
    def test_markdown_headings_without_citation_are_structural(self, md: str):
        assert is_structural_chunk(md)


    def test_empty_is_structural(self):
        assert is_structural_chunk("")
        assert is_structural_chunk("   ")


# ============================================================
# MVP-14.5.x — malformed_output: Russian / translated headers
# ============================================================

class TestMalformedOutputDetection:
    """Regression tests: LLM sometimes generates Russian headers
    (e.g. '# Заключение') instead of the required English markers.
    The verifier must detect this as malformed so loop.py can log
    output_contract_violation and the fix to SYSTEM_ANSWER can be
    validated.
    """

    def test_russian_h1_headers_are_malformed(self):
        """# Заключение / # Факты are markdown headings, not Output
        Contract headers — answer must be flagged malformed."""
        answer = (
            "# Заключение\n"
            "Ответ на вопрос [general-knowledge].\n"
            "# Факты\n"
            "- Факт первый [general-knowledge].\n"
            "# Источники\n"
            "1. general-knowledge\n"
            "# Уверенность\n"
            "medium\n"
            "# Неподтверждённое\n"
            "nothing\n"
            "# Безопасность\n"
            "nothing"
        )
        report = verify(answer=answer, chain=ProvenanceChain())
        assert report.malformed_output is True
