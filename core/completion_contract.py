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
    tokens = tuple(normalize_text(text).split())
    action = _action_for(tokens)

    obligations: list[ContractObligation] = []
    ambiguities: list[str] = []

    named = list(paths_mentioned(text))

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

    # An ambiguity is a request that ASKS for a change and does not say of
    # what. That is the case where guessing is dangerous and asking is cheap.
    #
    # A path named under an unrecognised verb is deliberately NOT one.
    # Measured against the 48 real requests in the live agent's episodic
    # memory: flagging those raised 4 clarifications, and 2 of them were
    # ordinary discussion turns that merely cited a file ("твоя гипотеза
    # неверна, доказательство: core/evidence.py строка 522"). Stopping a
    # conversation to ask what should happen to a file the operator was only
    # quoting is not caution, it is a defect. "No deliverable could be
    # derived" and "the request is ambiguous" are different facts.
    if action in {"create", "modify"} and not named:
        ambiguities.append(
            f"the request asks to {action} something but names no target"
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
    if output.get("exit_code") not in (0,):
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
        if obligation.deliverable == "tests_pass":
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
