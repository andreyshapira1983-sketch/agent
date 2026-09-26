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

#: Предметы сверки: идентификаторы, SHA, пути, числа с единицей. Обычные слова
#: не берутся — совпадение «система» ничего не значит. SHA обязан содержать
#: hex-букву (иначе дата — SHA), путь — кончаться расширением (не `.json.bak`).
_SUBJECT_RE = re.compile(
    r"\b(?:[a-z]+_[0-9a-f]{6,}"          # идентификаторы: run_…, mem_…, sess_…
    r"|(?=[0-9]*[a-f])[0-9a-f]{7,40}\b"  # SHA
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|cmd|toml|yaml|yml)(?![\w-]|\.\w)"  # пути
    r"|\d+(?:[.,]\d+)?\s*(?:событ\w*|строк\w*|тест\w*|байт\w*|events?|lines?|tests?)"
    r")",
    re.IGNORECASE,
)

#: Граница знания или метода («не читал», «не проверено отдельно», «подтверждён
#: только…») не снимает утверждение — иначе наказывается честность (R9).
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


#: Буквальные объекты суждения: код в кавычках, путь, идентификатор, число —
#: надёжнее слов, которые русская морфология не даёт сравнить.
_OBJECT_RE = re.compile(
    r"`[^`]+`"
    r"|\b[\w./\\-]+\.(?:py|md|json|jsonl|txt|log|cmd|toml|yaml|yml)\b"
    r"|\b[a-z]+_[a-z0-9_]{3,}\b"
    r"|\b\d{2,}\b",
    re.IGNORECASE,
)

#: Строка с отрицанием ничего не утверждает, и снимать с неё нечего.
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

    Сверяются пары строк: утверждающая строка без отрицания, отрицающая — с
    отрицанием и без нового буквального объекта (новый объект — новое
    суждение, а не снятие). Имя в обоих разделах само по себе ничего не снимает.
    """
    if not answer:
        return ()
    sections = _sections(answer)
    # Именная строка («Точное содержимое X») — открытый вопрос, а не снятие.
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
            # Среди предметов строки, не подстрокой: `report.json` ≠ `report.json.bak`.
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


#: Заголовок утверждает НАЛИЧИЕ предмета…
_PRESENCE_CLAIM_RE = re.compile(
    r"присутству|\bесть\b|содерж|реализован|определ[её]н|добавлен|"
    r"\bpresent\b|\bexists?\b|\bcontains?\b|\bimplemented\b|\bdefined\b|\badded\b",
    re.IGNORECASE,
)
#: …а факт того же ответа — его ОТСУТСТВИЕ.
_ABSENCE_CLAIM_RE = re.compile(
    r"отсутству|\bнет\b|не\s+(?:реализован|определ|найден|существу|содерж|добавлен)|"
    r"\babsent\b|\bmissing\b|\bnot\s+(?:present|implemented|defined|found|added)\b|"
    r"\bdoes\s+not\s+(?:exist|contain)\b",
    re.IGNORECASE,
)
#: Предмет спора: код в апострофах, составной идентификатор, путь.
_IDENT_RE = re.compile(
    r"`([^`\n]{3,})`|\b([A-Za-z]\w*_\w+)\b|\b([\w./\\-]+\.(?:py|md|json|jsonl|txt))\b")


def _identifiers(line: str) -> set[str]:
    return {next(g for g in m.groups() if g).strip().lower()
            for m in _IDENT_RE.finditer(line)}


def headline_contradicts_facts(answer: str | None) -> tuple[Contradiction, ...]:
    """Заголовок утверждает наличие того, чьё отсутствие называют факты ответа.

    Сигнал только наблюдающий (не в `DISQUALIFYING_DEFECT_SIGNALS`), пока не
    откалиброван на живых ответах.
    """
    if not answer:
        return ()
    sections = _sections(answer)
    headlines = [ln for ln in sections.get("conclusion", "").splitlines()
                 if _PRESENCE_CLAIM_RE.search(ln) and not _ABSENCE_CLAIM_RE.search(ln)]
    denials = [ln for ln in sections.get("facts", "").splitlines()
               if _ABSENCE_CLAIM_RE.search(ln) and not _BOUNDARY_RE.search(ln)]
    found: dict[str, Contradiction] = {}
    for head in headlines:
        claimed = _identifiers(head)
        for fact in denials:
            for subject in sorted(claimed & _identifiers(fact)):
                found.setdefault(subject, Contradiction(
                    subject=subject, asserted_in="conclusion", denied_in="facts"))
    return tuple(found.values())


#: Доклад о записи и его отрицание; сверяются со списком реально выполненных
#: инструментов, а не с уликами в тексте ответа.
_CLAIMS_WRITE_RE = re.compile(
    r"(?i)\b(?:записа(?:л|н[оаы]?|ла)|переписа(?:л|н[оаы]?)|сохран(?:ил|ён|ено)|дописа(?:л|н[оаы]?)|"
    r"создал\w*\s+файл|wrote|written|saved)\b")
_DENIES_WRITE_RE = re.compile(
    r"(?i)\b(?:не\s+(?:записа|переписа|сохран)\w*|не\s+вызывал\w*\s+file_write|ни\s+одного\s+файла|"
    r"did\s+not\s+write|no\s+files?\s+(?:were\s+)?written)\b")


#: Названный предмет записи (файл). Страдательная форма без него («правило
#: записано») — о состоянии, возможно давнем, а не о действии в этом ходе.
_NAMES_WRITTEN_THING_RE = re.compile(
    r"(?i)[\w./-]+\.(?:md|txt|py|jsonl|json|csv|xlsx|docx|pdf|log|ya?ml|toml)\b"
    r"|\b(?:файл|file)\w*"
)
#: Первое лицо («записал») — доклад о своём действии в этом ходе.
_FIRST_PERSON_WRITE_RE = re.compile(
    r"(?i)\b(?:записал|переписал|сохранил|дописал|создал|wrote|saved)\b"
)


def _claims_a_write_now(head: str) -> bool:
    """True, когда вывод докладывает о записи, сделанной В ЭТОМ ХОДЕ."""
    if _FIRST_PERSON_WRITE_RE.search(head):
        return True
    if not _CLAIMS_WRITE_RE.search(head):
        return False
    return _NAMES_WRITTEN_THING_RE.search(head) is not None


#: Инструменты, которые пишут. `patch_check` не входит: он работает на клоне и
#: рабочую папку не меняет.
_WRITING_TOOLS: frozenset[str] = frozenset({
    "file_write", "journal_append", "memory_bank",
})


#: Доклад о любом действии — чтении, замере, пробе, счёте, запуске — и его отрицание.
_CLAIMS_ACTION_RE = re.compile(
    r"(?i)(?:\b(?:прочита(?:л[аи]?|н[оаы]?)|открыл[аи]?|измерил[аи]?|посчитал[аи]?|"
    r"пересчитал[аи]?|запустил[аи]?|выполнил[аи]?|проверил[аи]?)\b|"
    r"\bI\s+(?:read|measured|counted|ran|executed)\b|"
    r"\bпо\s+(?:измерени|замер|подсчёт|выдач)\w*|"
    r"\b(?:проб[аы]|probe|замер)\w*\s+(?:показал|напечатал|вернул|дал|showed|printed|returned)\w*)")
_DENIES_ACTION_RE = re.compile(
    r"(?i)\b(?:не\s+(?:прочита|открыва|открыл|измеря|измерил|посчита|запуска|запустил|выполня|выполнил|"
    r"вызывал|провер)\w*|ни\s+одно(?:го|й)\s+(?:инструмент|проб|шаг|файл)\w*|"
    r"did\s+not\s+(?:read|run|measure)|no\s+tools?\s+(?:were\s+)?(?:run|executed))")


def _report_head(answer: str) -> str:
    """Вывод ответа: раздел Conclusion, иначе первый абзац (с учётом `\\r\\n`)."""
    return (_sections(answer).get("conclusion")
            or re.split(r"\r?\n[ \t]*\r?\n", answer, maxsplit=1)[0])


def action_report_mismatch(answer: str | None, executed_tools: list[str]) -> str | None:
    """Строка-поправка, когда доклад о действиях расходится с тем, что выполнено.

    Сверяется только ВЫВОД (первый абзац / Conclusion): там доклад, который
    читают. Возвращает None, если расхождения нет.
    """
    head = _report_head(answer or "")
    if (not executed_tools and (_CLAIMS_ACTION_RE.search(head) or _claims_a_write_now(head))
            and not (_DENIES_ACTION_RE.search(head) or _DENIES_WRITE_RE.search(head))):
        return ("⚠️ По журналу хода: в этом ходе НЕ выполнено ни одного инструмента "
                "(инструментов: 0, записей: 0) — утверждения о прочитанном, измеренном "
                "или записанном в выводе не подтверждены.")
    writes = sum(1 for tool in executed_tools if tool in _WRITING_TOOLS)
    if writes == 0 and _claims_a_write_now(head) and not _DENIES_WRITE_RE.search(head):
        return ("⚠️ По журналу хода: в этом ходе НЕ выполнено ни одной записи "
                "(file_write, journal_append, memory_bank: 0) — утверждение о записи "
                "в выводе не подтверждено.")
    if writes > 0 and _DENIES_WRITE_RE.search(head):
        return (f"⚠️ По журналу хода: в этом ходе выполнено записей: {writes} "
                "(file_write / journal_append / memory_bank) — утверждение «не записал» "
                "в выводе неверно.")
    if writes > 0:
        # Факт записи — всегда, без разбора слов: переформулировок отрицания
        # бесконечно, журнал — один.
        made = ", ".join(sorted({t for t in executed_tools if t in _WRITING_TOOLS}))
        return f"ℹ️ По журналу хода: записей в этом ходе: {writes} ({made})."
    return None
