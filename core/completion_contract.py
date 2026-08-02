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
`tests_pass`         the run must carry a passing test result
===================  ==============================================

`core/completion_obligation.py` keys its `intent` source on the OBJECT and
avoids verb lists, for a measured reason: wording is a weak proxy for duty. A
deliverable cannot be read off the object alone — "прочитай core/foo.py" and
"почини core/foo.py" name the same object and owe different things — so a
narrow verb vocabulary is unavoidable here. The escape hatch is what keeps it
honest: when a request names a path but no recognised action, this module
records an **ambiguity**, never an invented obligation. Ambiguity is the
operator's clarification case, and an empty contract is safer than a confident
wrong one.

Read verbs are recognised precisely so they are NOT ambiguous: reading is
already a duty of the `intent` source, and owes no deliverable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from core.completion_obligation import paths_mentioned
from core.lang_match import normalize_text

DeliverableKind = Literal["file_exists", "file_modified", "tests_pass"]

#: How each deliverable is checked. Named, not free text: the verification
#: method is part of the contract, so a later reader can tell what "verified"
#: meant without re-deriving it from the code.
VERIFICATION_METHODS: dict[DeliverableKind, str] = {
    "file_exists": "a successful write artifact targets the path",
    "file_modified": "a successful write artifact targets the path",
    "tests_pass": "a run_tests artifact reports success",
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
        }


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
    tokens = tuple(normalize_text(text).split())
    action = _action_for(tokens)

    obligations: list[ContractObligation] = []
    ambiguities: list[str] = []

    named = list(paths_mentioned(text))
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
        elif action == "unknown":
            ambiguities.append(
                f"'{path}' is named but the request does not say what must "
                "happen to it (read it? create it? change it?)"
            )

    if _TESTS_PASS_RE.search(text):
        obligations.append(ContractObligation(
            deliverable="tests_pass",
            target="",
            verification=VERIFICATION_METHODS["tests_pass"],
            derived_from="the request requires the tests to pass",
        ))

    return CompletionContract(
        obligations=tuple(obligations),
        ambiguities=tuple(ambiguities),
    )


_WRITING_TOOLS: frozenset[str] = frozenset({"file_write", "shell_exec"})


def unmet_obligations(
    contract: CompletionContract,
    *,
    artifacts: dict[str, Any] | None = None,
) -> tuple[ContractObligation, ...]:
    """Which contract obligations the run's EVIDENCE does not satisfy.

    Judged against artifacts — what the run actually did — never against the
    answer text. That is the operator's "a good textual answer does not prove
    completion", made mechanical: an answer cannot satisfy anything here
    because it is not read.
    """
    artifacts = artifacts or {}
    entries = [(str(label), meta or {}) for label, meta in artifacts.items()]

    def _succeeded(meta: dict[str, Any]) -> bool:
        status = str(meta.get("status") or "").casefold()
        return status in {"", "success", "ok"}

    unmet: list[ContractObligation] = []
    for obligation in contract.obligations:
        if obligation.deliverable == "tests_pass":
            met = any(
                str(meta.get("tool") or "") == "run_tests" and _succeeded(meta)
                for _label, meta in entries
            )
        else:
            base = obligation.target.replace("\\", "/").rsplit("/", 1)[-1]
            met = any(
                str(meta.get("tool") or "") in _WRITING_TOOLS
                and _succeeded(meta)
                and base.casefold() in label.casefold()
                for label, meta in entries
            )
        if not met:
            unmet.append(obligation)
    return tuple(unmet)
