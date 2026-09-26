"""What must EXIST or have CHANGED when this request is done (MIR-067).

Derived from the request BEFORE the work; an unmet obligation forbids `achieved`.
A duty that cannot be read unambiguously becomes an ambiguity to ask, never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from core.file_request_intent import paths_mentioned
from core.lang_match import normalize_text

DeliverableKind = Literal["file_exists", "file_modified", "tests_green"]

#: How each deliverable is checked; part of the contract, so "verified" is explicit.
# `tests_green`, not `tests_pass`: bandit reads names ending in "pass" as
# credentials (B105), and the repo carries no suppressions.
VERIFICATION_METHODS: dict[DeliverableKind, str] = {
    "file_exists": "a successful write artifact targets the path",
    "file_modified": "a successful write artifact targets the path",
    "tests_green": "a run_tests artifact reports success",
}

# Verb stems, matched by prefix on normalized tokens. Kept short: a wrong stem
# manufactures an obligation the operator never gave.
_CREATE_STEMS: tuple[str, ...] = (
    "созда", "напиш", "запиш", "сформир", "сгенерир", "добав",
    "create", "write", "generate", "add",
)
_MODIFY_STEMS: tuple[str, ...] = (
    "исправ", "почин", "измен", "обнов", "поправ", "перепиш", "удали",
    "fix", "change", "update", "modify", "edit", "refactor", "remove", "delete",
)
_READ_STEMS: tuple[str, ...] = (
    "прочит", "прочти", "читай", "перечисл", "покаж", "посмотр", "проверь",
    "изуч", "找", "опиш", "объясн", "сравн", "назов", "исслед", "найд",
    "read", "list", "show", "check", "inspect", "describe", "explain",
    "compare", "review", "analyse", "analyze", "find", "search",
)


# Explicit operator-declared change-sets override incidental path mentions;
# read from the full request, before demanding_text() and the mixed-request check.
_EXPLICIT_CHANGE_TARGET_DECLARATIONS: dict[str, re.Pattern[str]] = {
    # Начало предложения, а не только строки; захват — до конца предложения,
    # чтобы соседнее «Файл Y — только прочитать» не попало в изменяемые.
    "ru": re.compile(
        '(?:^|(?<=[.!?]\\s))[ \\t]*(?:менять|меняется|меняем|изменить|изменяется|изменя(?:ть|йте))\\s*:?\\s*(?:ровно\\s+(?:один|одну|\\d+)\\s+(?:файл|файла|файлов)\\s*[-—:]?\\s*)?(?P<targets>(?:(?![.!?]\\s)[^\\n;])+)',
        re.IGNORECASE | re.MULTILINE,
    ),
    "en": re.compile(
        '^[ \\t]*(?:change|modify|edit)(?:\\s+set)?\\s*:\\s*(?:exactly\\s+(?:one|\\d+)\\s+files?\\s*[-—:]?\\s*)?(?P<targets>[^\\n;]+)',
        re.IGNORECASE | re.MULTILINE,
    ),
}


def _explicit_change_targets(text: str) -> tuple[str, ...]:
    """Return the operator-declared change-set, if one is syntactically valid."""
    for declaration in _EXPLICIT_CHANGE_TARGET_DECLARATIONS.values():
        match = declaration.search(text)
        if match is None:
            continue

        # Keep request order while removing duplicate declarations.
        targets = tuple(dict.fromkeys(paths_mentioned(match.group("targets"))))
        if targets:
            return targets
    return ()

#: A passing test suite is a deliverable in its own right — it names no path.
_TESTS_PASS_RE = re.compile(
    r"(тест\w*\s+(проход|прошл|зелён|зелен))"
    r"|((чтобы|пока)\s+тест)"
    r"|(tests?\s+(pass|are\s+green))"
    r"|(make\s+the\s+tests?\s+pass)",
    re.IGNORECASE,
)


#: Классы затребованного, которые извлекатель распознаёт, но проверять не умеет.
#: Ошибка здесь лишь помечает границу покрытия, а не выдумывает обязательство.
_UNSUPPORTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("report_sections", re.compile(
        r"(отчита\w*|отчёт\w*|отчет\w*|доложи)\s+(отдельно|по\s+раздел|разделами)"
        r"|(report|answer)\s+separately"
        r"|(в\s+конце|at\s+the\s+end)\s+(отчита|report)"
        r"|(перечисли|определи|opredeli)\s*:"
        r"|report\s+separately",
        re.IGNORECASE)),
    ("experiment", re.compile(
        r"(проведи|выполни|поставь)\s+(\w+\s+){0,3}(эксперимент|опыт|замер)"
        r"|(perform|run|conduct)\s+(at\s+least\s+one\s+)?(\w+\s+){0,3}experiment"
        r"|challenge-response",
        re.IGNORECASE)),
    ("prohibition", re.compile(
        r"\b(не\s+(исправляй|меняй|трогай|используй|спрашивай|модифицируй))"
        r"|\bne\s+(ispravlyay|menyay|trogay|ispolzuy)"
        r"|\bdo\s+not\s+(use|modify|change|ask|interpret|merely|stop)"
        r"|\bmust\s+not\s+use",
        re.IGNORECASE)),
    ("verification_requirement", re.compile(
        r"(докажи|обоснуй|подтверди)\s"
        r"|(prove|substantiate|justify)\s+(that|your|the)"
        r"|fail-before"
        r"|(попробуй|try)\s+(опроверг|to\s+falsify)",
        re.IGNORECASE)),
)


#: Единица задания, названная ЗАГОЛОВКОМ: `## 3. …`, `### B. …`, `# U01 — …`.
#: Идентификатор оператора сохраняется дословно; упоминание в прозе — не единица.
_REQUESTED_UNIT_RE = re.compile(
    r"^\s{0,3}#{1,4}\s*("
    r"(?:\d{1,2}|[A-Z])\.\s+\S.*?"
    r"|(?:[A-Za-z\u0410-\u042f\u0430-\u044f]{1,4}\d{1,3})\s*[\u2014\u2013:-]\s+\S.*?"
    r")\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RequestedUnit:
    """Названный оператором раздел работы или отчёта."""

    title: str
    identifier: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {"title": self.title, "identifier": self.identifier}


#: Метка в начале заголовка (`U01`, `R7`, `3`, `B`) — по ней сверяется адресованность.
_UNIT_LABEL_RE = re.compile(
    r"^([A-Za-z\u0410-\u042f\u0430-\u044f]{0,4}\d{1,3}|[A-Z])\s*[.\u2014\u2013:-]"
)


#: Строка «S1 — Ключи» без `#` (вставка теряет решётку) — единица, но только
#: когда таких строк две и больше: одиночная — проза (R1).
_PLAIN_UNIT_RE = re.compile(
    r"^\s{0,3}((?:[A-Za-zА-Яа-я]{1,4}\d{1,3})"
    r"\s*[—–:-]\s+\S.*?)\s*$",
    re.MULTILINE,
)


def requested_units(text: str) -> tuple[RequestedUnit, ...]:
    """Единицы задания в порядке их появления, без повторов."""
    units = _units_from(_REQUESTED_UNIT_RE, text)
    if units:
        return units
    plain = _units_from(_PLAIN_UNIT_RE, text)
    return plain if len(plain) >= 2 else ()


def _units_from(pattern: re.Pattern[str], text: str) -> tuple[RequestedUnit, ...]:
    seen: set[str] = set()
    out: list[RequestedUnit] = []
    for match in pattern.finditer(text or ""):
        title = " ".join(match.group(1).split())
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            label = _UNIT_LABEL_RE.match(title)
            out.append(RequestedUnit(
                title=title, identifier=label.group(1) if label else ""
            ))
    return tuple(out)


def _units_without_body(text: str) -> tuple[str, ...]:
    """Идентификаторы единиц, за строкой которых нет ни одной строки задания."""
    if not requested_units(text):
        return ()
    lines = (text or "").splitlines()

    def _ident(line: str) -> str | None:
        for pattern in (_REQUESTED_UNIT_RE, _PLAIN_UNIT_RE):
            m = pattern.match(line)
            if m:
                label = _UNIT_LABEL_RE.match(" ".join(m.group(1).split()))
                return label.group(1) if label else None
        return None

    empty: list[str] = []
    marks = [(i, _ident(ln)) for i, ln in enumerate(lines)]
    unit_rows = [(i, ident) for i, ident in marks if ident]
    for pos, (row, ident) in enumerate(unit_rows):
        end = unit_rows[pos + 1][0] if pos + 1 < len(unit_rows) else len(lines)
        if not any(ln.strip() for ln in lines[row + 1:end]):
            empty.append(ident)
    return tuple(empty)


def unaddressed_units(contract: Any, answer: str) -> tuple[str, ...]:
    """Названные единицы, следа которых в ответе нет."""
    body = (answer or "").casefold()
    missing: list[str] = []
    for unit in getattr(contract, "requested_units", ()) or ():
        # Односимвольная метка («C») совпала бы с любой буквой — для неё
        # остаётся сверка по заголовку.
        ident = (unit.identifier or "").casefold()
        if len(ident) >= 2 and ident in body:
            continue
        tail = unit.title.split(".", 1)[-1].strip().casefold()
        if tail and tail not in body:
            missing.append(unit.title)
    return tuple(missing)


@dataclass(frozen=True)
class UnsupportedDeliverable:
    """Затребованное, которое извлекатель видит и НЕ умеет проверять."""

    kind: str
    evidence: str

    def to_log_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "evidence": self.evidence}


@dataclass(frozen=True)
class ContractObligation:
    """One thing that must be true after the run, and how that is checked."""

    deliverable: DeliverableKind
    target: str
    verification: str
    derived_from: str

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "deliverable": self.deliverable,
            "target": self.target,
            "verification": self.verification,
            "derived_from": self.derived_from,
        }


@dataclass(frozen=True)
class CompletionContract:
    """The deliverables a request owes, fixed before the work starts."""

    obligations: tuple[ContractObligation, ...] = ()
    ambiguities: tuple[str, ...] = ()
    unsupported_deliverables: tuple[UnsupportedDeliverable, ...] = ()
    #: Единицы, названные оператором заголовками (предметы, а не классы).
    requested_units: tuple[RequestedUnit, ...] = ()
    #: Чек-лист поручения (core/request_checklist.py); None — не составлен.
    checklist: Any = None

    @property
    def coverage(self) -> str:
        """`complete`, пока извлекатель не встретил ничего вне своей области."""
        return "partial" if self.unsupported_deliverables else "complete"

    @property
    def needs_clarification(self) -> bool:
        """True when a duty could not be read; the caller asks instead of guessing."""
        return bool(self.ambiguities)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "obligations": [o.to_log_payload() for o in self.obligations],
            "ambiguities": list(self.ambiguities),
            "needs_clarification": self.needs_clarification,
            "unsupported_deliverables": [
                u.to_log_payload() for u in self.unsupported_deliverables
            ],
            "coverage": self.coverage,
            "requested_units": [u.to_log_payload() for u in self.requested_units],
        }


def _mentions_read(tokens: tuple[str, ...]) -> bool:
    return any(tok.startswith(stem) for tok in tokens for stem in _READ_STEMS)


#: Прошедшее время и второе лицо будущего («добавил», «added», «напишешь») —
#: рассказ или условие, а не поручение. Хвостовая пунктуация («добавил,») допускается.
_NOT_AN_ORDER_RE = re.compile(
    r"(?:"
    r"л|ла|ло|ли|ed"          # прошедшее: «добавил», «added»
    r"|ешь|ёшь|ишь|ашь"      # второе лицо будущего: «напишешь»
    r")[^\w]*$"
)
#: Прежнее имя. Основные правила — приказ стоит в форме приказа
#: (`_ORDER_TAIL_RE`) и называет свой предмет (`_orders_a_path`).
_PAST_TENSE_RE = _NOT_AN_ORDER_RE

#: Имя из кода или данных, а не слово речи: `added_at`, `evidence_kind=source`,
#: `core/loop.py`, `remove()` — иначе имя поля читается как приказ.
_IS_A_NAME_RE = re.compile(r"[_/\\=(]|\d")

#: Хвост после стебля в форме приказа: повелительное («исправь», «исправьте»)
#: или инфинитив («добавить»). У латиницы приказ — сам стебель, хвост пуст.
_ORDER_TAIL_RE = re.compile(
    r"^(?:|и|ь|й|ай|ей|уй)(?:те)?$"           # повелительное
    r"|^(?:ть|ти|ить|ать|ять|еть|уть|овать|ивать|ывать)$"  # инфинитив
)
_TAIL_PUNCT_RE = re.compile(r"[^\w]+$")

#: Границы предложения. Точка — конец только перед пробелом или концом текста,
#: иначе `core/loop.py` разрезался бы и путь пропадал.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;]+(?=\s|$)|\n+")


def _orders_a_path(demanding: str) -> bool:
    """Распоряжается ли просьба ФАЙЛОМ: глагол письма стоит в предложении с путём."""
    parts = [p for p in _SENTENCE_SPLIT_RE.split(demanding or "") if p.strip()]
    near = " . ".join(p for p in parts if paths_mentioned(p))
    if not near:
        return False
    return _action_for(tuple(normalize_text(near).split())) in {"create", "modify"}


def _written_paths_only(named: list[str], demanding: str) -> list[str]:
    """Оставить в долгу только пути из предложений с глаголом записи.

    Если такие предложения называют лишь часть путей, прочие — чтение. Когда
    чтение и запись в одном предложении, подмножества нет и путь остаётся к вопросу.
    """
    if len(named) <= 1:
        return named
    ordered = _ordered_paths(demanding)
    return ordered if 0 < len(ordered) < len(named) else named


def _ordered_paths(demanding: str) -> list[str]:
    """Пути, названные в предложениях с глаголом записи (порядок — как в просьбе)."""
    out: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(demanding or ""):
        paths = paths_mentioned(part) if part.strip() else ()
        if paths and _action_for(tuple(normalize_text(part).split())) in {"create", "modify"}:
            out += [p for p in paths if p not in out]
    return out


def _in_order_form(tok: str, stem: str) -> bool:
    """Стоит ли слово в форме распоряжения, а не названия или рассказа."""
    tail = _TAIL_PUNCT_RE.sub("", tok[len(stem):])
    if stem.isascii():
        return not tail
    return bool(_ORDER_TAIL_RE.match(tail))


def _action_for(tokens: tuple[str, ...]) -> str:
    """`create` / `modify` / `read` / `unknown` for one request's tokens."""
    def _hit(stems: tuple[str, ...], *, strict: bool = False) -> bool:
        return any(
            tok.startswith(stem)
            and not _PAST_TENSE_RE.search(tok)
            and not _IS_A_NAME_RE.search(tok)
            and (not strict or _in_order_form(tok, stem))
            for tok in tokens for stem in stems
        )

    # Долг создаёт только распоряжение — строгость формы нужна лишь там.
    if _hit(_MODIFY_STEMS, strict=True):
        return "modify"
    if _hit(_CREATE_STEMS, strict=True):
        return "create"
    if _hit(_READ_STEMS):
        return "read"
    return "unknown"


#: Кому просьба отдаёт выбор, не называя его: «ему», «клиенту», «заказчику».
_THIRD = r"(?:ему|ей|им|клиенту|заказчику|пользователю|him|her|them|the\s+client|the\s+customer)"
#: Формат отдан на усмотрение третьего лица: «в нужном ему формате», «как
#: клиенту удобно», «in the format the client needs».
_DEFERRED_FORMAT_RE = re.compile(
    rf"(?:нужн|удобн|подходящ)\w*\s+{_THIRD}\s+(?:формат|вид)"
    rf"|(?:формат|вид)\w*,?\s+(?:который|какой|что)\s+{_THIRD}\s+(?:нуж|удоб|подход)"
    rf"|(?:формат|вид)\w*,?\s+(?:который|какой|что)\s+(?:нуж|удоб|подход)\w*\s+{_THIRD}"
    rf"|как\s+{_THIRD}\s+(?:нужно|надо|удобно|хочется)"
    rf"|(?:format|form)\s+(?:that\s+)?{_THIRD}\s+(?:needs?|wants?|prefers?)",
    re.IGNORECASE,
)
#: Отправка третьему лицу без адреса и канала: «отправь ему», «send it to the client».
_DEFERRED_SEND_RE = re.compile(
    rf"(?:отправ|пришл|перешл|отошл)\w*\s+(?:\w+\s+)?{_THIRD}\b|send\s+(?:it\s+)?to\s+{_THIRD}\b",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(r"@|https?://|на\s+адрес|по\s+адресу|в\s+(?:telegram|телеграм|slack|чат)", re.IGNORECASE)


def _deferred_choices(demanding: str) -> list[str]:
    """Выбор, который просьба отдаёт неназванному человеку, — вопрос, а не догадка.

    Правило узкое намеренно: только прямая отсылка к предпочтению третьего лица.
    Текст неясности — готовый вопрос заказчику, ворота показывают его как есть.
    """
    out: list[str] = []
    if _DEFERRED_FORMAT_RE.search(demanding):
        out.append("в каком формате нужен результат? В просьбе выбор формата отдан "
                   "заказчику, но сам формат не назван")
    if _DEFERRED_SEND_RE.search(demanding) and not _ADDRESS_RE.search(demanding):
        out.append("кому и каким способом отправить результат? Адрес или канал в "
                   "просьбе не названы")
    return out


#: Закавыченный кусок просьбы вместе с глаголом письма перед ним: без фразы
#: «напиши» всё равно читалось бы как «создай».
_QUOTED_SPAN_RE = re.compile(
    r"(?:\b(?:напиш|напис|скаж|ответ|отвеч|укаж|пиши|write|say|reply|answer|"
    r"state|print)\w*\s+(?:что\s+|это\s+)?)?"
    r"(«[^»\n]*»|\"[^\"\n]*\"|“[^”\n]*”)",
    re.IGNORECASE,
)


def _without_quoted_prose(text: str) -> str:
    """Просьба без закавыченных ФРАЗ; закавыченные имена файлов остаются.

    «Напиши «данных нет»» — речь, «создай файл «report.md»» — работа.
    """
    def keep(match: re.Match[str]) -> str:
        quoted = match.group(1)
        return match.group(0) if paths_mentioned(quoted[1:-1]) else " "

    return _QUOTED_SPAN_RE.sub(keep, text or "")


def derive_completion_contract(
    question: str,
    *,
    file_hint: str | None = None,
) -> CompletionContract:
    """Read the deliverables out of the REQUEST, before any work happens.

    Blind to plan, artifacts and answer by signature; anything unclear becomes an ambiguity.
    """
    text = question or ""
    # Долги — только из требующих предложений: `Do not create X` не долг.
    # Сам запрет остаётся в `unsupported_deliverables` (по полному тексту).
    demanding = _without_quoted_prose(demanding_text(text))
    tokens = tuple(normalize_text(demanding).split())
    action = _action_for(tokens)
    # Распоряжение называет свой предмет: если ни один путь не стоит в
    # предложении с глаголом письма, просьба про чтение.
    if (
        action in {"create", "modify"}
        and paths_mentioned(demanding)
        and not _orders_a_path(demanding)
    ):
        action = "read" if _mentions_read(tokens) else "unknown"

    obligations: list[ContractObligation] = []
    ambiguities: list[str] = []

    # Explicit scope is authoritative: it is not an ambiguity merely because
    # the request names additional read-only/context paths.
    declared_change_set = _explicit_change_targets(text)
    if declared_change_set:
        action = "modify"
        named = list(declared_change_set)
    else:
        named = list(paths_mentioned(demanding))

    # Read + change over MORE THAN ONE path cannot be attributed ("прочитай A.py
    # и исправь B.py"); an invented duty would block `achieved`. One path is safe.
    if not declared_change_set and action in {"create", "modify"}:
        named = _written_paths_only(named, demanding)
    if (
        not declared_change_set
        and action in {"create", "modify"}
        and _mentions_read(tokens)
        and len(named) > 1
    ):
        ambiguities.append(
            "the request mixes reading and changing over several paths; "
            "which of them must change cannot be read from the wording"
        )
        named = []
    hint = (file_hint or "").strip()
    # A --file hint is an explicit pointer, not a request to change it.
    # It only carries a deliverable when the request itself asks for one.
    if hint and not declared_change_set and hint not in named and action in {"create", "modify"}:
        named.append(hint)

    for path in named:
        if action == "create":
            obligations.append(ContractObligation(
                deliverable="file_exists",
                target=path,
                verification=VERIFICATION_METHODS["file_exists"],
                derived_from=path,
            ))
        elif action == "modify":
            obligations.append(ContractObligation(
                deliverable="file_modified",
                target=path,
                verification=VERIFICATION_METHODS["file_modified"],
                derived_from=path,
            ))

    # No "change verb without a target" ambiguity: it was wrong every time —
    # the target lived in prose. Such a request yields an empty contract instead.

    if _TESTS_PASS_RE.search(demanding):
        obligations.append(ContractObligation(
            deliverable="tests_green",
            target="",
            verification=VERIFICATION_METHODS["tests_green"],
            derived_from="the request requires the tests to pass",
        ))

    ambiguities.extend(_deferred_choices(demanding))

    # Единица без содержания (обрезанная вставка) — вопрос оператору (R1).
    for ident in _units_without_body(text):
        ambiguities.append(
            f"единица «{ident}» объявлена, но не содержит задания — "
            "спросить, что в ней требуется"
        )

    return CompletionContract(
        obligations=tuple(obligations),
        ambiguities=tuple(ambiguities),
        unsupported_deliverables=_unsupported_in(text),
        # По ПОЛНОМУ тексту: единицей бывает и запрещающий раздел.
        requested_units=requested_units(text),
    )


#: Запрещающее предложение. Маркер стоит ПЕРЕД глаголом, иначе «создай файл,
#: если он не существует» прочиталось бы как запрет.
_PROHIBITING_CLAUSE_RE = re.compile(
    r"^\s*(?:"
    r"(?:do\s+not|don't|never)"
    r"|(?:не|ни)\s+(?:\w+\s+){0,2}?(?:созда|напиш|измен|исправ|почин|трог|"
    r"меняй|модифиц|удал|добав|коммить|запуска|обновля|правь)"
    r"|(?:ne)\s+(?:\w+\s+){0,2}?(?:sozda|napish|izmen|isprav|pochin|trog|menyay)"
    r")",
    re.IGNORECASE,
)

#: Границы предложений и однородных частей: «сделай A; не трогай B».
# Якорь на самом тире, а не `\s+—`: иначе стена пробелов даёт квадратичное время.
_CLAUSE_SPLIT_RE = re.compile(r"(?:(?<=[.!?;\n])\s+|(?<=\s)—\s+)")


def demanding_text(text: str) -> str:
    """Текст без запрещающих предложений — из него и читаются долги."""
    kept = [
        part.strip() for part in _CLAUSE_SPLIT_RE.split(text or "")
        if part.strip() and not _PROHIBITING_CLAUSE_RE.match(part.strip())
    ]
    return " ".join(kept)


def _unsupported_in(text: str) -> tuple[UnsupportedDeliverable, ...]:
    """Затребованное, которое видно в тексте и непроверяемо этим модулем."""
    found: list[UnsupportedDeliverable] = []
    for kind, pattern in _UNSUPPORTED_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            found.append(UnsupportedDeliverable(
                kind=kind, evidence=match.group(0).strip()
            ))
    return tuple(found)


#: Only an explicit write proves a file deliverable: a `shell_exec` receipt does
#: not reliably say which file it touched.
_WRITE_TOOL = "file_write"

#: The gateway can admit an effect and not perform it. A simulated write is
#: not a deliverable.
_SIMULATED = "gateway simulate"


def _executed(meta: dict[str, Any]) -> bool:
    issues = meta.get("issues") or ()
    return not any(_SIMULATED in str(i).casefold() for i in issues)


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().strip("./").casefold()


def _tests_really_passed(output: Any) -> bool:
    """A `run_tests` receipt that actually reports a green run."""
    if not isinstance(output, dict):
        return False
    if output.get("timed_out"):
        return False
    if output.get("exit_code") != 0:
        return False
    return int(output.get("failed") or 0) == 0 and int(output.get("errors") or 0) == 0


def unmet_obligations(
    contract: CompletionContract,
    *,
    artifacts: dict[str, Any] | None = None,
) -> tuple[ContractObligation, ...]:
    """Which contract obligations the run's artifacts do not satisfy (never the answer text)."""
    artifacts = artifacts or {}
    entries = [(str(label), meta or {}) for label, meta in artifacts.items()]

    unmet: list[ContractObligation] = []
    for obligation in contract.obligations:
        if obligation.deliverable == "tests_green":
            met = any(
                str(meta.get("tool") or "") == "run_tests"
                and _executed(meta)
                and _tests_really_passed(meta.get("output"))
                for _label, meta in entries
            )
        else:
            target = _norm(obligation.target)
            met = any(
                str(meta.get("tool") or "") == _WRITE_TOOL
                and _executed(meta)
                and _norm(_written_path(label, meta)) == target
                for label, meta in entries
            )
        if not met:
            unmet.append(obligation)
    return tuple(unmet)


def _written_path(label: str, meta: dict[str, Any]) -> str:
    """The path a write receipt claims, preferring the receipt over the label."""
    output = meta.get("output")
    if isinstance(output, dict) and output.get("path"):
        return str(output["path"])
    return str(label).split(":", 1)[-1]
