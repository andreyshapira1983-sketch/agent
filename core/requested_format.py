"""Форма ответа, заданная человеком, — это и есть сдача работы.

Замер 2026-09-25, GAIA (arXiv 2311.12983), первые 54 ответа полного прогона:
в 17 (31 %) нет строки «FINAL ANSWER: …», которую вопрос требовал ПОСЛЕДНЕЙ.
Причины три, и все наши:

* свой контракт вывода (Conclusion … Safety) кончается разделом «Safety», а
  просьба — «закончи ответ строкой»: модель выбирала контракт;
* проверка дописывала в саму строку метки (`FINAL ANSWER: 34689 [unverified]`),
  и ответ переставал быть тем, что просили;
* ворота слабых улик переписывали тело целиком, и строка пропадала вместе с
  догадками вокруг неё.

Заказчик тоже задаёт форму («ответ одной строкой: СУММА: <число>»), поэтому
это не подгонка под мерку. Путь — как в «Let Me Speak Freely?» (Tam и др.,
EMNLP 2024 Industry, arXiv 2408.02442): рассуждать свободно, а в заданную форму
переводить отдельным шагом (их «NL-to-Format»): жёсткая форма во время
рассуждения ухудшает само рассуждение.

Предупреждение при этом не теряется: снятая со строки метка «не проверено»
становится строкой-оговоркой прямо над ней («Silence Is Endorsement»,
arXiv 2609.20211 — без пометки непроверенное читается как проверенное).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.warning_words import WARNING_KINDS

#: «МЕТКА: [ЗАПОЛНИТЕЛЬ]» или «МЕТКА: <заполнитель>». Метка — заглавными (так
#: пишут шаблоны); заполнитель в [] — заглавными, в <> — до пяти слов без цифр.
#: Узко нарочно: «ERROR: [Errno 2]» из вставленного журнала — не шаблон.
_TEMPLATE_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])([A-ZА-ЯЁ][A-ZА-ЯЁ0-9 _-]{1,40}?)\s*[:：]\s*"
    r"(?:\[[A-ZА-ЯЁ][A-ZА-ЯЁ _-]*\]|<[^<>\d]{1,60}>)"
)

_CITATION_KINDS = (
    "verified:", "declared:", "general-knowledge", "web:", "file:", "file_read:", "file_write:",
    "search:", "test:", "log:", "shell:", "tool:", "diff:", "memory:", "sensor:", "runtime:",
    "dialogue:", "artifact:", "prior_turn:", "user",
)
_TAG_RE = re.compile(
    r"\s*\[(?:" + "|".join(re.escape(k) for k in (*WARNING_KINDS, *_CITATION_KINDS)) + r")[^\]]*\]",
    re.IGNORECASE,
)
_WARNING_RE = re.compile(r"\[(?:" + "|".join(re.escape(k) for k in WARNING_KINDS) + r")", re.IGNORECASE)

MISSING_PREFIX = "⚠️ Заданную строку "
UNCONFIRMED_PREFIX = "⚠️ Значение заданной строки "
_VALUE_LINE_RE = re.compile(r"^[A-ZА-ЯЁ][A-ZА-ЯЁ0-9 _-]{1,40}[:：]\s*\S")


def is_tail_line(line: str) -> bool:
    """Строка хвоста `honor` — чтобы человеческая печать её не выбросила."""
    return line.startswith((MISSING_PREFIX, UNCONFIRMED_PREFIX)) or bool(_VALUE_LINE_RE.match(line))


CONVERT_SYSTEM = (
    "You copy an answer into a required line. You get the user's request and the answer text. "
    "Reply with exactly one line: the required label, a colon, and the value the ANSWER TEXT "
    "gives. Follow the request's own rules for how the value is written. Never compute, guess "
    "or add anything the answer text does not say. If the answer text gives no value, reply "
    "with the single word NONE."
)


def requested_labels(question: str) -> tuple[str, ...]:
    """Метки строк, которые человек потребовал в ответе, в порядке появления."""
    out: list[str] = []
    for match in _TEMPLATE_RE.finditer(question or ""):
        label = " ".join(match.group(1).split())
        if sum(ch.isalpha() for ch in label) >= 2 and label not in out:
            out.append(label)
    return tuple(out)


def _line_re(label: str) -> re.Pattern[str]:
    # «Conclusion: FINAL ANSWER: 42» — строка внутри заголовка контракта; заголовок
    # остаётся на месте (`_drop_line`), уходит только заданная строка.
    return re.compile(
        r"^(?P<head>[\s>*_`#-]*(?:conclusion\s*:)?[\s*_`]*)"
        + r"\s+".join(map(re.escape, label.split())) + r"[*_`]*\s*[:：]\s*(?P<value>.*)$",
        re.IGNORECASE | re.MULTILINE,
    )


def _drop_line(match: re.Match[str]) -> str:
    head = match.group("head")
    return head.strip() if "conclusion" in head.lower() else ""


def _clean(value: str) -> str:
    value = _TAG_RE.sub("", value)
    return value.strip().strip("*_`").strip()


def _last_value(text: str, label: str) -> tuple[str, bool] | None:
    """Значение последней строки «label: …» и была ли на ней метка-предупреждение."""
    found = list(_line_re(label).finditer(text or ""))
    for match in reversed(found):
        value = _clean(match.group("value"))
        if value:
            return value, bool(_WARNING_RE.search(match.group("value")))
    return None


@dataclass
class Honored:
    text: str
    #: Откуда взята строка: answer | draft | converted | missing — для журнала.
    origins: dict[str, str] = field(default_factory=dict)


def honor(
    answer: str,
    *,
    question: str,
    draft: str = "",
    convert: Callable[[str, str], str] | None = None,
) -> Honored:
    """Заданные строки — последними, чистыми, с оговоркой над ними, если нужна.

    Порядок поиска значения: окончательный ответ → черновик синтеза (ворота
    могли его вырезать) → один перевод отдельным шагом (`convert(label, text)`).
    Строка не выдумывается: не нашлось значения — об этом сказано словами.
    """
    labels = requested_labels(question)
    if not labels or not answer:
        return Honored(answer)
    body, tail, origins = answer, [], {}
    for label in labels:
        got, origin = _last_value(answer, label), "answer"
        if got is None:
            got, origin = _last_value(draft, label), "draft"
            got = (got[0], True) if got else None  # ворота это вырезали: не проверено
        if got is None and convert is not None:
            origin = "converted"
            try:
                raw = convert(label, draft or answer)
            except Exception:  # noqa: BLE001 — перевод не валит ответ; ниже скажем, что строки нет
                raw = ""
            got = _last_value(raw, label) or ((_clean(raw), True) if raw.strip() else None)
            if got and (not got[0] or got[0].upper() == "NONE"):
                got = None
        if got is None:
            origins[label] = "missing"
            # Без двоеточия после метки: иначе сама оговорка читается как строка ответа.
            tail.append(f"{MISSING_PREFIX}«{label}» заполнить нечем: в ответе нет её значения.")
            continue
        origins[label] = origin
        body = _line_re(label).sub(_drop_line, body)
        # Перевод только переписывает сказанное в ответе: его пометки остаются в теле.
        if got[1]:
            tail.append(f"{UNCONFIRMED_PREFIX}«{label}» не подтверждено источником этого хода.")
        tail.append(f"{label}: {got[0]}")
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip()
    return Honored(body + "\n\n" + "\n".join(tail), origins)


def llm_converter(llm: object, question: str) -> Callable[[str, str], str] | None:
    """`convert` для `honor` поверх модели с `.complete(system=, user=, …)`."""
    complete = getattr(llm, "complete", None)
    if complete is None:
        return None

    def convert(label: str, text: str) -> str:
        user = (f"REQUIRED LINE: {label}: <value>\n\nREQUEST:\n{question[-3000:]}\n\n"
                f"ANSWER TEXT:\n{text[-6000:]}")
        return str(complete(system=CONVERT_SYSTEM, user=user, max_tokens=200, temperature=0.0))

    return convert
