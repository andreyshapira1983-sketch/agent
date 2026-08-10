"""What must EXIST or have CHANGED when this request is done (MIR-067).

The gap this closes, in the operator's words (ruling of 2026-08-02):

    The result of a task must be represented by a separate structured
    completion contract. The contract is derived from the original request
    BEFORE the work is performed, and carries verifiable obligations together
    with the way each one is verified. A plan and a good textual answer do not
    by themselves prove completion. An unmet obligation forbids the status
    `achieved`, forbids banking a successful episode, and forbids procedural
    success credit. If an obligation cannot be unambiguously derived from the
    request, the agent must ask for clarification rather than guess.

Until now nothing in the system carried the *deliverable*. The recorded goal of
every run is ``f"Answer the question: {question}"``, and every completion check
measured the ANSWER — its citations, its chunks, its shape. So a run that was
asked to change a file, and only read it, succeeded by the system's own
definition. `core/completion_obligation.py` named this missing fourth source
`acceptance_criteria` and reported it as `not_wired`. This module is that
source.

## Derived before the work — structurally, not by convention

:func:`derive_completion_contract` takes the request and the file hint. It
cannot see the plan, the artifacts or the answer, because it is not given
them. A contract that could read the work it judges would be a description of
what happened, not a criterion for it.

## Small vocabulary on purpose; ambiguity is an ASK, never a guess

Only three deliverables are recognised, each with a mechanical check:

===================  ==============================================
`file_exists`        a path named for creation must exist afterwards
`file_modified`      a path named for change must have been written
`tests_green`         the run must carry a passing test result
===================  ==============================================

`core/completion_obligation.py` keys its `intent` source on the OBJECT and
avoids verb lists, for a measured reason: wording is a weak proxy for duty. A
deliverable cannot be read off the object alone — "прочитай core/foo.py" and
"почини core/foo.py" name the same object and owe different things — so a
narrow verb vocabulary is unavoidable here. What keeps it honest is that an
unreadable request yields an EMPTY contract, never an invented duty.

## Ambiguity is OBSERVED, not yet acted on — and here is why

The operator's clause 6 says an obligation that cannot be unambiguously
derived must be asked about. `ambiguities` records those cases and the loop
journals them, but nothing stops the run to ask. That restraint is measured,
not timid. Two candidate rules were tried against the 48 real requests in the
live agent's episodic memory:

* *a path is named under no recognised verb* → 4 clarifications, of which 2
  were ordinary discussion turns that merely cited a file ("твоя гипотеза
  неверна, доказательство: core/evidence.py строка 522"). Stopping a
  conversation to ask what should happen to a quoted file is a defect, not
  caution.
* *a change verb with no path* (the rule kept here) → 7 clarifications, and
  all 7 were genuine change requests whose target was named in PROSE rather
  than as a path ("сделай так, чтобы эпизод сохранял, что пошло не так").
  Asking "what should I change?" there is obtuse.

So with this vocabulary the signal cannot yet tell "the operator was vague"
from "the operator was clear in words this module does not parse". Wiring it
to the stop-and-ask path would trade a silent wrong answer for a loud wrong
question. It stays observational until the numbers justify power — the
standing sensor policy for this repository.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from core.file_request_intent import paths_mentioned
from core.lang_match import normalize_text

DeliverableKind = Literal["file_exists", "file_modified", "tests_green"]

#: How each deliverable is checked. Named, not free text: the verification
#: method is part of the contract, so a later reader can tell what "verified"
#: meant without re-deriving it from the code.
# `tests_green`, not `tests_pass`: bandit reads any name ending in "pass" as a
# credential (B105) and flagged this table as a hardcoded password. This
# repository carries no suppressions — a false positive is answered by a name
# that is not ambiguous, the same way `_TOKEN_EDGE_PUNCT` became
# `_FILENAME_EDGE_PUNCT` in the secret scanner.
VERIFICATION_METHODS: dict[DeliverableKind, str] = {
    "file_exists": "a successful write artifact targets the path",
    "file_modified": "a successful write artifact targets the path",
    "tests_green": "a run_tests artifact reports success",
}

# Verb stems, matched on normalized whole tokens by prefix. Kept deliberately
# short: every stem here is a claim that this word unambiguously signals the
# action, and a wrong claim manufactures an obligation the operator never gave.
_CREATE_STEMS: tuple[str, ...] = (
    "созда", "напиш", "сформир", "сгенерир", "добав",
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

#: A passing test suite is a deliverable in its own right — it names no path.
_TESTS_PASS_RE = re.compile(
    r"(тест\w*\s+(проход|прошл|зелён|зелен))"
    r"|((чтобы|пока)\s+тест)"
    r"|(tests?\s+(pass|are\s+green))"
    r"|(make\s+the\s+tests?\s+pass)",
    re.IGNORECASE,
)


#: Классы затребованного, которые извлекатель РАСПОЗНАЁТ, но проверять не умеет.
#: Каждый образец — заявление «эти слова однозначно просят вот это»; ложное
#: заявление здесь не выдумывает обязательство (их по-прежнему строят только
#: пути к файлам), а лишь помечает границу, и цена ошибки соответственно ниже.
#: Латиница и кириллица порознь: оператор пишет и транслитом тоже.
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


#: Единица задания, названная ЗАГОЛОВКОМ. Три формы метки:
#:
#:   `## 3. Найди границу`      — номер с точкой
#:   `### B. Образец`           — буква с точкой
#:   `# U01 — Verifier`         — ПОМЕЧЕННАЯ единица: буквы с цифрами,
#:                                 разделитель — тире, длинное тире или двоеточие
#:
#: Третья форма добавлена 2026-08-10 по живому провалу: задание объявило
#: «Each unit has a stable identifier U01 through U12 … must survive
#: unchanged», а извлекатель взял семь разделов ФИНАЛЬНОГО ОТЧЁТА (`A.`…`G.`)
#: и ни одной из двенадцати рабочих единиц. Идентификатор оператора — часть
#: контракта, а не наша перефразировка, и он сохраняется дословно.
#:
#: Только ЗАГОЛОВОК. Упоминание в прозе («сделай как в U01») раздела не
#: объявляет, и считать его единицей значило бы выдумать пункт.
_REQUESTED_UNIT_RE = re.compile(
    r"^\s{0,3}#{1,4}\s*("
    r"(?:\d{1,2}|[A-Z])\.\s+\S.*?"
    r"|(?:[A-Za-z\u0410-\u042f\u0430-\u044f]{1,4}\d{1,3})\s*[\u2014\u2013:-]\s+\S.*?"
    r")\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RequestedUnit:
    """Названный оператором раздел работы или отчёта.

    `identifier` — метка, которую оператор объявил САМ (`U01`, `R7`, `B`).
    Она часть контракта, а не наша перефразировка, и по ней же считается
    адресованность: ответ «U01: точка входа — verify()» покрывает единицу, не
    повторяя её заголовок дословно. Без этого сверка помечала непокрытыми все
    единицы разом и была бесполезна (замер 2026-08-10).

    Существует, потому что `unsupported_deliverables` группирует по КЛАССАМ:
    четырнадцать названных единиц живого задания давали две записи
    (`report_sections`, `prohibition`). `partial` сообщал, что часть контракта
    не представлена, и не сообщал какая — в конце хода сверять было не с чем.
    """

    title: str
    identifier: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {"title": self.title, "identifier": self.identifier}


#: Метка в начале заголовка: `U01`, `R7`, `3`, `B`. Ровно та строка, которую
#: написал оператор, — по ней и сверяется адресованность.
_UNIT_LABEL_RE = re.compile(
    r"^([A-Za-z\u0410-\u042f\u0430-\u044f]{0,4}\d{1,3}|[A-Z])\s*[.\u2014\u2013:-]"
)


def requested_units(text: str) -> tuple[RequestedUnit, ...]:
    """Единицы задания в порядке их появления, без повторов."""
    seen: set[str] = set()
    out: list[RequestedUnit] = []
    for match in _REQUESTED_UNIT_RE.finditer(text or ""):
        title = " ".join(match.group(1).split())
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            label = _UNIT_LABEL_RE.match(title)
            out.append(RequestedUnit(
                title=title, identifier=label.group(1) if label else ""
            ))
    return tuple(out)


def unaddressed_units(contract: Any, answer: str) -> tuple[str, ...]:
    """Названные единицы, следа которых в ответе нет.

    Присутствие, а не качество. Единица считается адресованной, если в ответе
    встречается её содержательная часть — заголовок без номера. Судить, ХОРОШО
    ли раздел раскрыт, эта функция не берётся: для этого нужно понимание, а
    выдуманный судья здесь был бы тем же дефектом, что и выдуманный долг.
    """
    body = (answer or "").casefold()
    missing: list[str] = []
    for unit in getattr(contract, "requested_units", ()) or ():
        # Метка засчитывается только начиная с двух знаков: односимвольная
        # («C») совпала бы с любой буквой в тексте и объявила бы покрытым
        # всё подряд. Для таких единиц остаётся сверка по заголовку.
        ident = (unit.identifier or "").casefold()
        if len(ident) >= 2 and ident in body:
            continue
        tail = unit.title.split(".", 1)[-1].strip().casefold()
        if tail and tail not in body:
            missing.append(unit.title)
    return tuple(missing)


@dataclass(frozen=True)
class UnsupportedDeliverable:
    """Затребованное, которое извлекатель видит и НЕ умеет проверять.

    Существует ради одного различия: пустой список обязательств раньше означал
    и «запрос ничего не должен», и «запрошенное я представить не умею».
    Потребитель читал второе как первое (живой случай 2026-08-10).
    """

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
    #: Единицы, названные оператором заголовками. Отдельно от
    #: `unsupported_deliverables`: там классы, здесь предметы.
    requested_units: tuple[RequestedUnit, ...] = ()

    @property
    def coverage(self) -> str:
        """`complete`, пока извлекатель не встретил ничего вне своей области.

        Отвечает на вопрос, который раньше задать было негде: «этот ноль —
        про запрос или про меня?». Потребитель, увидевший `partial`, знает,
        что отсутствие обязательств не означает отсутствия долга.
        """
        return "partial" if self.unsupported_deliverables else "complete"

    @property
    def needs_clarification(self) -> bool:
        """True when the request named an object whose duty could not be read.

        The operator's rule: ask, do not guess. This flag is what a caller
        acts on; this module never turns an ambiguity into an obligation.
        """
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


def _action_for(tokens: tuple[str, ...]) -> str:
    """`create` / `modify` / `read` / `unknown` for one request's tokens.

    Modify wins over create when both appear ("исправь и добавь"): the stricter
    duty is the safer one to owe, since a modification check also fails when
    nothing was written at all.
    """
    def _hit(stems: tuple[str, ...]) -> bool:
        return any(tok.startswith(stem) for tok in tokens for stem in stems)

    if _hit(_MODIFY_STEMS):
        return "modify"
    if _hit(_CREATE_STEMS):
        return "create"
    if _hit(_READ_STEMS):
        return "read"
    return "unknown"


def derive_completion_contract(
    question: str,
    *,
    file_hint: str | None = None,
) -> CompletionContract:
    """Read the deliverables out of the REQUEST, before any work happens.

    Deliberately blind to the plan, the artifacts and the answer: they are not
    parameters. Anything it cannot read unambiguously becomes an ambiguity for
    the caller to raise with the operator.
    """
    text = question or ""
    # Долги читаются ТОЛЬКО из требующих предложений. Запрещающие вырезаются
    # первыми: 2026-08-10 измерено шесть из шести, где `Do not create X` давало
    # долг «X обязан существовать», а `Не исправляй Y` — «Y обязан измениться».
    # Единственное обязательство того живого прогона (`tests_green`) пришло из
    # фразы «…merely to make the test pass», стоявшей внутри запрета. Запрет при
    # этом никуда не девается — он остаётся в `unsupported_deliverables`, и
    # читается по ПОЛНОМУ тексту, а не по этому усечению.
    demanding = demanding_text(text)
    tokens = tuple(normalize_text(demanding).split())
    action = _action_for(tokens)

    obligations: list[ContractObligation] = []
    ambiguities: list[str] = []

    named = list(paths_mentioned(demanding))

    # A request that both reads and changes, over MORE THAN ONE path, cannot
    # be attributed: "прочитай A.py и исправь B.py" would otherwise owe a
    # change on A.py too, and an invented duty blocks `achieved` on its own
    # (Copilot, PR #258). One path is safe — "прочитай core/foo.py и исправь
    # его" names a single object and the stricter action wins.
    if action in {"create", "modify"} and _mentions_read(tokens) and len(named) > 1:
        ambiguities.append(
            "the request mixes reading and changing over several paths; "
            "which of them must change cannot be read from the wording"
        )
        named = []
    hint = (file_hint or "").strip()
    if hint and hint not in named:
        # A --file hint is an explicit pointer, not a request to change it.
        # It only carries a deliverable when the request itself asks for one.
        if action in {"create", "modify"}:
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

    # ── the TARGETLESS-CHANGE ambiguity rule is RETIRED (2026-08-02) ────────
    #
    # Scope of this retirement, precisely: only the "a change verb names no
    # target" rule is gone. The mixed read+change rule above still raises an
    # ambiguity, and `ambiguities` is still a populated field — it fired 0
    # times across the same 62 live requests, so it is unexercised rather than
    # disproven, and there is no evidence on which to retire it.
    #
    # Two candidate TARGETLESS rules were tried and BOTH measured at zero
    # precision:
    #
    # * "a path is named under an unrecognised verb" — 4 firings on 48 live
    #   requests, 2 of them ordinary discussion turns that merely cited a file.
    # * "a change verb with no path" — 8 firings on 62 live requests, and ALL
    #   EIGHT were wrong. Every one named its target in prose the vocabulary
    #   cannot parse ("сделай так, чтобы эпизод сохранял, что пошло не так",
    #   "почини так, чтобы разные команды давали разные label"), and the eighth
    #   was a long numbered experiment procedure whose step 7 happened to
    #   contain "внеси минимальное исправление" — one future, conditional verb
    #   in a flat bag of tokens, and the whole task was declared targetless.
    #
    # A signal that is wrong 8 times out of 8 is not a signal to tune, it is a
    # claim to withdraw. The operator's clause 6 — ask, do not guess — remains
    # the goal, and the mixed-request rule still serves it; what is gone is the
    # rule that could not tell a vague operator from a clear one this module
    # fails to parse. A request whose target lives in prose now yields an empty
    # contract that says exactly what is true — no deliverable could be
    # derived — without adding a false claim about the operator's clarity.
    #
    # What this does NOT fix: the vocabulary still cannot express "run an
    # experiment", so a multi-step procedure still yields no obligations. That
    # limit is recorded in MIR-067 and is a derivation problem, not a rule to
    # bolt on here.

    if _TESTS_PASS_RE.search(demanding):
        obligations.append(ContractObligation(
            deliverable="tests_green",
            target="",
            verification=VERIFICATION_METHODS["tests_green"],
            derived_from="the request requires the tests to pass",
        ))

    return CompletionContract(
        obligations=tuple(obligations),
        ambiguities=tuple(ambiguities),
        unsupported_deliverables=_unsupported_in(text),
        # По ПОЛНОМУ тексту: единицы называют и запрещающие разделы
        # тоже («7. Do not repeat the imagined-API failure»).
        requested_units=requested_units(text),
    )


#: Запрещающее предложение. Маркер обязан стоять ПЕРЕД глаголом действия,
#: иначе «создай файл, если он не существует» прочиталось бы как запрет.
#: Латиница, кириллица и транслит порознь: оператор пишет всеми тремя.
_PROHIBITING_CLAUSE_RE = re.compile(
    r"^\s*(?:"
    r"(?:do\s+not|don't|never)"
    r"|(?:не|ни)\s+(?:\w+\s+){0,2}?(?:созда|напиш|измен|исправ|почин|трог|"
    r"меняй|модифиц|удал|добав|коммить|запуска|обновля|правь)"
    r"|(?:ne)\s+(?:\w+\s+){0,2}?(?:sozda|napish|izmen|isprav|pochin|trog|menyay)"
    r")",
    re.IGNORECASE,
)

#: Границы предложений и однородных частей. Точка с запятой и тире разделяют
#: «сделай A; не трогай B» — без них запрет утащил бы за собой и требование.
_CLAUSE_SPLIT_RE = re.compile(r"(?:(?<=[.!?;\n])\s+|\s+—\s+)")


def demanding_text(text: str) -> str:
    """Текст без запрещающих предложений — из него и читаются долги.

    Отрицание не входило в область видимости извлекателя, и это давало не
    неполноту, а ИНВЕРСИЮ: запрет становился ровно тем долгом, который
    запрещает. Последствие поведенческое — послушание запрету доходило до
    `assemble_completion_verdict` как `obligation_silently_missing` и понижало
    вердикт прогона.

    Режется по предложениям, а не по всему тексту: «Создай A. Не трогай B.»
    обязано сохранить долг по A.
    """
    kept = [
        part for part in _CLAUSE_SPLIT_RE.split(text or "")
        if part.strip() and not _PROHIBITING_CLAUSE_RE.match(part)
    ]
    return " ".join(kept)


def _unsupported_in(text: str) -> tuple[UnsupportedDeliverable, ...]:
    """Затребованное, которое видно в тексте и непроверяемо этим модулем.

    Возвращает по одной записи на класс, с ЦИТАТОЙ из запроса: запись без
    улики недоказуема, а потребителю нужно не «где-то там просили отчёт», а
    место, по которому он это проверит сам.
    """
    found: list[UnsupportedDeliverable] = []
    for kind, pattern in _UNSUPPORTED_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            found.append(UnsupportedDeliverable(
                kind=kind, evidence=match.group(0).strip()
            ))
    return tuple(found)


#: Only an explicit write proves a file deliverable. `shell_exec` was here in
#: the first draft and is not: a shell receipt does not reliably encode which
#: file it touched, so a read-only command that merely mentions the name would
#: have satisfied the contract (Codacy, PR #258 — rated a security finding,
#: and correctly: it is a way to claim a change that never happened).
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
    """A `run_tests` receipt that actually reports a green run.

    The first draft accepted ANY run_tests artifact, so a run whose tests
    failed satisfied "make the tests pass" (Copilot, PR #258). The tool
    returns structured counts; they are what the contract reads.
    """
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
    """Which contract obligations the run's EVIDENCE does not satisfy.

    Satisfaction is judged against artifacts — what the run actually did —
    never against the answer text. That is the operator's "a good textual
    answer does not prove completion", made mechanical: the answer is not a
    parameter here, so it cannot satisfy anything.

    Paths are compared as whole normalized paths, taken from the write
    receipt's own ``output["path"]``. The first draft matched the BASENAME
    inside the artifact label, which let `tests/test_auth.py` satisfy a duty
    owed about `auth.py` (Codacy, PR #258).
    """
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
