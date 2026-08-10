"""Одно утверждение, объявленное и Фактом, и Непроверенным — в одном ответе.

ЗАЧЕМ. 2026-08-10 ответ писал в Facts «в ходе текущего выполнения (trace_id
run_860e7ef) записано 92 события» и в Unverified — «не доказано, что trace_id
run_860e7ef соответствует текущему исполнению». Контракт вывода имеет обе
секции и не имел их очной ставки: каждая проверялась сама по себе, а вместе
их не читал никто.

ЧТО ЭТО НЕ ДЕЛАЕТ. Раздел Unverified — признак честности, а не улика. Ответ
обязан называть, чего он не доказал, и детектор, срабатывающий на само наличие
раздела, запрещал бы честность. Поэтому сверяется не факт оговорки, а ПРЕДМЕТ:
конкретный носитель смысла (идентификатор, путь, число с единицей, имя в
кавычках), который в Facts утверждён, а в Unverified объявлен недоказанным.

ГРАНИЦА. Здесь только обнаружение. Что делать с находкой, решает
`core/unsupported_claims.py` — рубеж принятия ответа; допуск в обучение решает
`decide_usage_eligibility` по сигналу дефекта. Обнаружитель ничего не судит.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Заголовки контракта вывода (`core/answer_format.py`). Сверяются два раздела:
#: утверждающий и оговаривающий.
_SECTION_RE = re.compile(
    r"^\s*(Conclusion|Facts|Sources|Confidence|Unverified|Safety)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

#: Носители смысла, по которым имеет смысл сверять два раздела. Каждый — вещь,
#: о которой можно осмысленно сказать «утверждено» и «не доказано» об ОДНОМ И
#: ТОМ ЖЕ. Обычные слова сюда не входят намеренно: «система» в обеих секциях
#: означало бы лишь то, что ответ про систему.
_SUBJECT_RE = re.compile(
    r"\b(?:[a-z]+_[0-9a-f]{6,}"          # идентификаторы: run_…, mem_…, sess_…
    r"|[0-9a-f]{7,40}"                   # SHA
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|cmd|toml|yaml|yml)"  # пути
    r"|\d+(?:[.,]\d+)?\s*(?:событ\w*|строк\w*|тест\w*|байт\w*|events?|lines?|tests?)"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Contradiction:
    """Предмет, утверждённый в одной секции и снятый в другой."""

    subject: str
    asserted_in: str
    denied_in: str

    def to_log_payload(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "asserted_in": self.asserted_in,
            "denied_in": self.denied_in,
        }


def _sections(answer: str) -> dict[str, str]:
    """Текст ответа, разложенный по заголовкам контракта вывода."""
    marks = list(_SECTION_RE.finditer(answer))
    out: dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        out[mark.group(1).lower()] = answer[mark.end():end]
    return out


def _subjects(text: str) -> set[str]:
    return {m.group(0).lower() for m in _SUBJECT_RE.finditer(text)}


def contradicted_claims(answer: str | None) -> tuple[Contradiction, ...]:
    """Предметы, утверждённые в Conclusion/Facts и снятые в Unverified.

    Пустой кортеж означает ровно одно: пересечения предметов нет. Он же
    возвращается, когда разделов нет вовсе — очной ставки без сторон не бывает,
    и выдумывать предмет спора нельзя.
    """
    if not answer:
        return ()
    sections = _sections(answer)
    denied_text = sections.get("unverified", "")
    if not denied_text.strip():
        return ()
    denied = _subjects(denied_text)
    if not denied:
        return ()

    found: list[Contradiction] = []
    seen: set[str] = set()
    for name in ("facts", "conclusion"):
        asserted_text = sections.get(name, "")
        for subject in sorted(_subjects(asserted_text) & denied):
            if subject in seen:
                continue
            seen.add(subject)
            found.append(Contradiction(
                subject=subject, asserted_in=name, denied_in="unverified"
            ))
    return tuple(found)
