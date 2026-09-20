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


#: Буквальные объекты спора: код в обратных кавычках, путь, составной
#: идентификатор, число. По ним видно, О ЧЁМ суждение, — в отличие от русской
#: морфологии, которую подсчётом слов не берут («выполнения» / «исполнению»).
_OBJECT_RE = re.compile(
    r"`[^`]+`"
    r"|\b[\w./\\-]+\.(?:py|md|json|jsonl|txt|log|cmd|toml|yaml|yml)\b"
    r"|\b[a-z]+_[a-z0-9_]{3,}\b"
    r"|\b\d{2,}\b",
    re.IGNORECASE,
)

#: Строка, которая сама отрицает, ничего не утверждает: снимать с неё нечего.
#: В живом корпусе такие строки стояли в Facts («содержимое не было
#: прочитано») и ловили обвинение от собственного повтора в Unverified.
_NEGATION_RE = re.compile(
    r"\bне\b|\bнет\b|отсутств|\bnot\b|\bno\b|missing|\bбез\s",
    re.IGNORECASE,
)

_OBJECT_SPLIT_RE = re.compile(r"""[\s=,()"':]+""")


def _objects(line: str) -> set[str]:
    """Буквальные объекты строки, разобранные до отдельных имён."""
    out: set[str] = set()
    for m in _OBJECT_RE.finditer(line):
        for piece in _OBJECT_SPLIT_RE.split(m.group(0).strip("`").lower()):
            if len(piece) >= 3:
                out.add(piece)
    return out


def contradicted_claims(answer: str | None) -> tuple[Contradiction, ...]:
    """Суждение, утверждённое в Conclusion/Facts и снятое в Unverified.

    Сверяются ПАРЫ СТРОК, а не множества предметов. Замер 2026-09-20 на 420
    живых ответах: прежняя сверка обвиняла 154 ответа (37%), и ни одно
    обвинение при разборе не оказалось противоречием — все были формой
    честного ответа, где Facts утверждает одно свойство предмета («каталог
    содержит файл X»), а Unverified называет другое («содержимое X не
    прочитано»). Раздел Unverified ПО КОНТРАКТУ перечисляет непроверенное;
    имя, попавшее в оба раздела, само по себе ничего не снимает.

    Цена ошибки измерена там же: обвинение закрывает эпизоду вход в опыт
    (`_answer_disqualified`, core/smart_memory.py) прежде всех прочих осей —
    за четверо суток так отсечены 29 эпизодов из 133, годных по всем трём
    осям, то есть пятая часть всего, чему прогоны могли научить.

    Три условия сверх совпадения предмета, ни одно не подбиралось порогом:
    утверждающая строка должна утверждать, отрицающая — отрицать (именная
    строка «Точное содержимое X» называет открытый вопрос, а не снимает
    факт), и отрицание не вправе вводить НОВЫЙ буквальный объект: новый
    объект значит новое суждение о том же предмете, а не снятие прежнего.
    На том же корпусе остаётся 24 обвинения из 420, и живой случай
    2026-08-10, ради которого детектор построен, по-прежнему ловится.
    """
    if not answer:
        return ()
    sections = _sections(answer)
    # Строка, которая ничего не отрицает, ничего и не снимает. Большинство
    # строк Unverified — именные: «Точное содержимое X», «Номер строки в
    # файле». Это открытый вопрос, а не снятие факта; снятие произносится
    # («не доказано, что…»), и именно так выглядел живой случай 2026-08-10.
    denied_lines = [
        ln for ln in sections.get("unverified", "").splitlines()
        if ln.strip() and _NEGATION_RE.search(ln) and not _BOUNDARY_RE.search(ln)
    ]
    if not denied_lines:
        return ()

    found: list[Contradiction] = []
    seen: set[str] = set()
    for name in ("facts", "conclusion"):
        for asserted in sections.get(name, "").splitlines():
            if not asserted.strip() or _NEGATION_RE.search(asserted):
                continue
            # Предмет ищется среди предметов СТРОКИ, а не подстрокой: иначе
            # `report.json` находился внутри `report.json.bak.…` в соседней.
            subjects = _subjects(asserted)
            if not subjects:
                continue
            allowed = _objects(asserted) | subjects
            for denial in denied_lines:
                if _objects(denial) - allowed:
                    continue  # отрицание о другом: оно назвало новый объект
                for subject in sorted(subjects & _subjects(denial)):
                    if subject in seen:
                        continue
                    seen.add(subject)
                    found.append(Contradiction(
                        subject=subject, asserted_in=name, denied_in="unverified"
                    ))
    return tuple(found)
