"""Verification warnings in human words, next to the claim they qualify.

Machine labels such as `[claim-figure-unverified]` are translated, not dropped (they are warnings: without them the answer looks
more certain than it is): a short hedge in place, the source URL kept. Done only at the display edge (`format_human_response`);
episodes and campaign review keep the labels for counting and source lookup.
"""
from __future__ import annotations

import re

# A short first-person hedge, as a human would say it.
_RU = {
    "topic-only": "не проверял",
    "claim-figure-unverified": "число не сверял",
    "absence-unverifiable": "что этого нет, проверить не могу",
    "claim-refuted": "но проверка говорит обратное",
    "улика-без-этих-слов": "в источнике этого нет",
    "dialogue-supported": "по нашему разговору",
    "subagent-asserted": "со слов помощника",
    "no-receipt": "не проверял",
    "unverified": "не проверял",
    "цитата-не-подтверждает": "не проверял",
}
_EN = {
    "topic-only": "unchecked",
    "claim-figure-unverified": "number unchecked",
    "absence-unverifiable": "can't verify it's absent",
    "claim-refuted": "but the check says otherwise",
    "улика-без-этих-слов": "the source doesn't say this",
    "dialogue-supported": "from our conversation",
    "subagent-asserted": "a helper's word",
    "no-receipt": "unchecked",
    "unverified": "unchecked",
    "цитата-не-подтверждает": "unchecked",
}
#: Виды меток-предупреждений: кто снимает метку со строки, обязан сказать это словами.
WARNING_KINDS: tuple[str, ...] = tuple(_RU)
_MARKER_RE = re.compile(
    r"\s*\[(topic-only|claim-figure-unverified|absence-unverifiable|claim-refuted|"
    r"улика-без-этих-слов|dialogue-supported|subagent-asserted|no-receipt|unverified|"
    r"цитата-не-подтверждает)(?::([^\]]*))?\]")
#: `[unverified:…]` ставится вместе с `[no-receipt]` и сам по себе ничего не добавляет.
_UNVERIFIED_BODY_RE = re.compile(r"\s*\[unverified:[^\]]*\]")
_URL_RE = re.compile(r"https?://\S+")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)


def _russian(text: str) -> bool:
    """Russian if Cyrillic is at least a third of Latin: paths and code names in a Russian answer are Latin."""
    return 3 * len(_CYRILLIC_RE.findall(text)) >= len(_LATIN_RE.findall(text))


def strip_warning_markers(text: str) -> tuple[str, int]:
    """Remove warning labels entirely (chat conversation); return the text and their count."""
    plain = _UNVERIFIED_BODY_RE.sub("", text or "")
    return _MARKER_RE.sub("", plain), len(_MARKER_RE.findall(plain))


def humanize_warning_markers(text: str) -> str:
    """Warning labels -> a short parenthesised hedge; the source address stays."""
    if "[" not in (text or ""):
        return text
    # Language is judged on the text WITHOUT the labels: in a short line the labels' own Latin outweighed the Russian phrase.
    plain = _UNVERIFIED_BODY_RE.sub("", text)
    words = _RU if _russian(_MARKER_RE.sub("", plain)) else _EN
    seen_at_line: set[tuple[int, str]] = set()

    def say(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2) or ""
        line = plain.count("\n", 0, match.start())
        if (line, words[kind]) in seen_at_line:
            return ""
        seen_at_line.add((line, words[kind]))
        url = _URL_RE.search(body)
        source = f"{url.group(0).rstrip('.,;')} — " if url else ""
        return f" ({source}{words[kind]})"

    return _MARKER_RE.sub(say, plain)
