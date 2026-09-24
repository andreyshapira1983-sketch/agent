"""Предупреждения проверки — человеческими словами у самого утверждения.

24.09, чат оператора: в каждом ответе стояли `[topic-only:tool:python_probe]`,
`[claim-figure-unverified]`, `[absence-unverifiable]` — метки для машины,
показанные человеку. Вычистить их нельзя: это предупреждения (граница
`_strip_verification_markers`, H-43), без них ответ увереннее, чем есть.

Как показывать неуверенность, измерено: Kim, Liao и др., «I'm Not Sure,
But...», FAccT 2024 (N=404) — высказанная словами неуверенность снижает
излишнее согласие с системой и повышает точность людей. Поэтому метка не
исчезает, а переводится: короткая оговорка на месте утверждения, источник
(адрес страницы) сохраняется. Делается только на краю показа
(`format_human_response`): в эпизоде и в разборе кампании метки остаются —
по ним считают и ищут источники.
"""
from __future__ import annotations

import re

_RU = {
    "topic-only": "источник по теме, но этого прямо не подтверждает",
    "claim-figure-unverified": "число не сверено с источником",
    "absence-unverifiable": "что этого нет — проверить нельзя",
    "claim-refuted": "проверка этому противоречит",
    "улика-без-этих-слов": "в источнике этих слов нет",
    "dialogue-supported": "по нашему разговору, не по источнику",
    "subagent-asserted": "со слов помощника, не проверено",
    "no-receipt": "у инструмента нет квитанции — не подтверждено",
}
_EN = {
    "topic-only": "the source is on topic but does not confirm this",
    "claim-figure-unverified": "number not checked against a source",
    "absence-unverifiable": "absence cannot be verified",
    "claim-refuted": "the check contradicts this",
    "улика-без-этих-слов": "the source does not contain these words",
    "dialogue-supported": "from our conversation, not a source",
    "subagent-asserted": "a helper's word, unchecked",
    "no-receipt": "no tool receipt — unconfirmed",
}
_MARKER_RE = re.compile(
    r"\s*\[(topic-only|claim-figure-unverified|absence-unverifiable|claim-refuted|"
    r"улика-без-этих-слов|dialogue-supported|subagent-asserted|no-receipt)(?::([^\]]*))?\]")
#: `[unverified:…]` ставится вместе с `[no-receipt]` и сам по себе ничего не добавляет.
_UNVERIFIED_BODY_RE = re.compile(r"\s*\[unverified:[^\]]*\]")
_URL_RE = re.compile(r"https?://\S+")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)


def _russian(text: str) -> bool:
    """Русский, если кириллицы хотя бы треть от латиницы: пути и имена кода в
    русском ответе латинские («Файл data/lesson_injections.jsonl не существует»)."""
    return 3 * len(_CYRILLIC_RE.findall(text)) >= len(_LATIN_RE.findall(text))


def humanize_warning_markers(text: str) -> str:
    """Метки-предупреждения -> короткая оговорка в скобках; адрес источника остаётся."""
    if "[" not in (text or ""):
        return text
    # Язык — по тексту БЕЗ меток: в короткой строке латиница самих меток
    # перевешивала русскую фразу.
    plain = _UNVERIFIED_BODY_RE.sub("", text)
    words = _RU if _russian(_MARKER_RE.sub("", plain)) else _EN
    seen_at_line: set[tuple[int, str]] = set()

    def say(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2) or ""
        line = plain.count("\n", 0, match.start())
        if (line, kind) in seen_at_line:
            return ""
        seen_at_line.add((line, kind))
        url = _URL_RE.search(body)
        source = f"{url.group(0).rstrip('.,;')} — " if url else ""
        return f" ({source}{words[kind]})"

    return _MARKER_RE.sub(say, plain)
