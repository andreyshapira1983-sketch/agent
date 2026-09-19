"""Одно утверждение, объявленное и Фактом, и Непроверенным — в одном ответе."""
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
#
#: Замер 2026-09-19 (внешний экзамен, одна задача пять раз подряд): дата
#: «20260919» из имени бэкапа читалась как SHA, а `report.json` вырезался из
#: `report.json.bak.20260919T…Z`. SHA теперь обязан содержать hex-букву, путь —
#: кончаться на своём расширении.
_SUBJECT_RE = re.compile(
    r"\b(?:[a-z]+_[0-9a-f]{6,}"          # идентификаторы: run_…, mem_…, sess_…
    r"|(?=[0-9]*[a-f])[0-9a-f]{7,40}\b"  # SHA
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|cmd|toml|yaml|yml)(?![\w-]|\.\w)"  # пути
    r"|\d+(?:[.,]\d+)?\s*(?:событ\w*|строк\w*|тест\w*|байт\w*|events?|lines?|tests?)"
    r")",
    re.IGNORECASE,
)

#: R9 (2026-08-13, живой ac75fc92): заявление о ГРАНИЦЕ знания — «не читал»,
#: «данные не передавались» — не оспаривает утверждение о существовании.
#: Противоречие — снятие того же суждения; предмет в строке-границе предметом
#: спора не является. Иначе честность наказывается: лучший эпистемический ответ
#: дня получил self_contradicted×4 и карантин.
#:
#: Замер 2026-09-19: из десяти запусков одной задачи (сумма по именам → report.json)
#: девять ответов получили self_contradiction и ни один эпизод не стал опытом.
#: Все девять — граница МЕТОДА, а не снятие факта: «прочитано не было —
#: подтверждён только факт записи (87 байт)», «не перечитывал», «не подтверждено
#: отдельным чтением», «кодировка не проверена отдельно», «область доказательств
#: ограничена data.csv». Строка, которая называет, что ИМЕННО подтверждено
#: («подтверждён только…», «известны только…»), тоже граница: она утверждает
#: свои предметы, а не снимает их.
_BOUNDARY_RE = re.compile(
    r"не\s+(?:пере|про)?чит|(?:про|пере)?чита\w*\s+не\s+был|чтени|не\s+открыва|"
    r"не\s+передав|данные\s+не|нет\s+данных|недоступ|не\s+запраш|"
    r"не\s+видн|не\s+показан|пропущен|не\s+предоставл|не\s+целиком|усеч|"
    r"отдельно|косвенн|напрямую|побайтов|не\s+сверя|не\s+сверен|"
    r"не\s+удал\w*\s+(?:прочита|открыт|найти|получить)|"
    r"област\w*\s+(?:доказательств|проверенн)|"
    r"(?:подтвержд|известн)\w*\s+только|"
    r"omitted|not\s+read|re-?read|not\s+provided|no\s+data|not\s+shown|"
    r"separately|indirectly|only\s+(?:the\s+)?(?:fact|size)|evidence\s+scope",
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
    """Предметы, утверждённые в Conclusion/Facts и снятые в Unverified."""
    if not answer:
        return ()
    sections = _sections(answer)
    denied_text = sections.get("unverified", "")
    if not denied_text.strip():
        return ()
    denied = _subjects(denied_text)
    if not denied:
        return ()
    # Предмет ищется среди предметов строки, а не подстрокой: иначе
    # `report.json` находился внутри `report.json.bak.…` в соседней строке.
    denied = set().union(*(
        _subjects(ln) for ln in denied_text.splitlines()
        if ln.strip() and not _BOUNDARY_RE.search(ln)
    )) & denied
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
