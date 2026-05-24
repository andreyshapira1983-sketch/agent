"""
tools/builtins/text_summarizer.py — Summarize long text

Brain uses this to condense large blocks of text before
storing them in memory or passing them to the LLM.

This is a local, rule-based summarizer — no LLM call inside.
(A real implementation would inject an LLM, but the tool contract
keeps the interface clean regardless of the backend.)
"""

from __future__ import annotations

from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_MAX_INPUT_CHARS = 50_000
_DEFAULT_MAX_SENTENCES = 5


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences (simple heuristic)."""
    import re
    # Split on . ! ? followed by whitespace or end
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _score_sentence(sentence: str, word_freq: dict[str, int]) -> float:
    """Score a sentence by sum of word frequencies (extractive summarization)."""
    words = sentence.lower().split()
    if not words:
        return 0.0
    return sum(word_freq.get(w, 0) for w in words) / len(words)


class TextSummarizerTool(ToolBase):
    """
    Extract top N most informative sentences from a block of text.

    params:
        text          (str): The text to summarize.
        max_sentences (int, optional): How many sentences to return (default 5).
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="text_summarizer",
            description=(
                "Extract the most informative sentences from a long text "
                "(extractive summarization, no LLM required)"
            ),
            parameters={
                "text":          "str — text to summarize (max 50 000 chars)",
                "max_sentences": "int (optional, default 5) — how many sentences to return",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        text: str = params.get("text", "")
        max_sentences: int = int(params.get("max_sentences", _DEFAULT_MAX_SENTENCES))

        if not text or not isinstance(text, str):
            return self._fail("'text' param is required and must be a string")
        if len(text) > _MAX_INPUT_CHARS:
            return self._fail(f"Text too long (max {_MAX_INPUT_CHARS} chars)")
        if max_sentences < 1 or max_sentences > 50:
            return self._fail("'max_sentences' must be between 1 and 50")

        def _run():
            sentences = _extract_sentences(text)
            if not sentences:
                return []

            # Build word frequency table (stopword-free enough for scoring)
            import re
            stopwords = {
                "the","a","an","is","in","of","and","or","to","it","that",
                "this","for","on","with","at","be","was","are","were","by",
                "as","from","but","not","have","has","had","he","she","they",
                "we","you","i","его","она","они","мы","вы","я","и","в","на",
            }
            all_words = re.findall(r'\b\w+\b', text.lower())
            word_freq: dict[str, int] = {}
            for w in all_words:
                if w not in stopwords and len(w) > 2:
                    word_freq[w] = word_freq.get(w, 0) + 1

            scored = sorted(
                enumerate(sentences),
                key=lambda pair: _score_sentence(pair[1], word_freq),
                reverse=True,
            )
            # Pick top sentences, restore original order
            top_indices = sorted(idx for idx, _ in scored[:max_sentences])
            return [sentences[i] for i in top_indices]

        try:
            summary, elapsed = self._timed(_run)
            return self._ok(
                output=summary,
                sentence_count=len(summary),
                original_length=len(text),
                duration_ms=round(elapsed, 2),
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"Summarization error: {exc}")
