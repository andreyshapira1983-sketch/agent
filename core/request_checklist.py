"""Request checklist: the human's requirements as yes/no questions (TICK / RLCF style), checked against the finished answer.

Only explicit instructions about THIS answer; each item carries a verbatim quote from the request that a program verifies; exact checks come from a short whitelist (substring present / absent), the rest goes to a judge model that may abstain (N/A); an important "no" is asked a second time and counts only if repeated. One-sided guard like everything in `assemble_completion_verdict`: it may lower "done" to "done partly", never raise it; a model failure or unparseable reply gives an empty checklist and no signal.
On by default; AGENT_REQUEST_CHECKLIST=0 in .env is an emergency off switch.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

ENV = "AGENT_REQUEST_CHECKLIST"
MAX_ITEMS = 12
#: An item with this importance or higher answered "no" lowers the verdict.
IMPORTANT = 50
#: The general RLCF requirement, added to every list; weight below the threshold: the judge said "no" to it in 6 of 16 answers and half were taste, so it is logged but cannot lower the verdict.
UNIVERSAL = "Does the response directly address the request, without long off-topic content?"
UNIVERSAL_IMPORTANCE = 40
_PROGRAM_RULES = ("must_include", "must_not_include")

BUILD_SYSTEM = """You turn a person's REQUEST into an evaluation checklist (TICK method).
Write YES/NO questions about the RESPONSE the agent will give. YES must mean the requirement is met.
- Only requirements the request states EXPLICITLY about the response: instructions on what THIS response must contain or look like. Statements, assessments, and critique of a previous answer or of facts are not requirements. Do not add implied ones: no apology, promise, tone, politeness or "acknowledge the user" unless the request literally asks for it.
- Every question carries "quote": the exact words of the request that state this requirement, copied character for character.
- If the request only forwards material (a job ad, a post, someone else's message) or is conversation without instructions to the agent about its answer, return {"items": []}. Requirements written for someone else are not requirements for this response.
- Phrase every question positively, so that YES means met ("Does the answer mark the guess as a hypothesis?", not "Does the answer avoid presenting a guess as fact?").
- One question per distinct requirement; do not repeat a requirement in two questions.
- Make each question exactly as specific as its requirement (request "at most 8 sentences" -> question "Is the final answer at most 8 sentences long?").
- Give each question an importance from 0 to 100: 100 means the response fails without it.
- Only if a requirement is EXACTLY checkable as "the response contains this literal text" or "the response does not contain this literal text", set rule "must_include" or "must_not_include" and arg to that literal text. Never approximate: counts, language, tone, structure and anything else get rule null.
- Do not write questions about actions — tool calls, files on disk, test runs, "do not change the code": those are checked elsewhere.
- At most 10 questions. Use the language of the request.
Answer with ONE JSON object only:
{"items": [{"question": "...", "quote": "exact words of the request", "importance": 0-100, "rule": null | "must_include" | "must_not_include", "arg": null | "literal text"}]}"""

JUDGE_SYSTEM = """You check an agent's RESPONSE against a checklist made from the person's REQUEST.
For each numbered question answer YES or NO about the RESPONSE, with one short reason.
- YES only if the response itself satisfies the requirement. If the response says it could not do something, that requirement is NO.
- Judge the agent's own text. Ignore the trailing verification report the system appends (lines beginning with "Проверка:" or "⚠️").
- Count sentences, sections and items literally when a question asks for a count.
- When the request allows an alternative ("if X is not proven, say so"), a response that takes the alternative meets the requirement: YES.
- Answer N/A when the question is not about the text of the response (for example an action such as "do not change the code"), or when the request did not ask for it.
Answer with ONE JSON object only:
{"answers": [{"n": 1, "answer": "YES" | "NO" | "N/A", "why": "..."}]}"""


def enabled() -> bool:
    return os.environ.get(ENV, "").strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class ChecklistItem:
    question: str
    importance: int
    rule: str | None = None
    arg: str | None = None

    def to_log_payload(self) -> dict[str, Any]:
        return {"question": self.question, "importance": self.importance,
                "rule": self.rule, "arg": self.arg}


@dataclass(frozen=True)
class Checklist:
    items: tuple[ChecklistItem, ...] = ()
    reason: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {"items": [i.to_log_payload() for i in self.items], "reason": self.reason}


@dataclass(frozen=True)
class ItemVerdict:
    item: ChecklistItem
    answer: str  # yes | no | unknown
    how: str  # program | judge
    why: str = ""


@dataclass(frozen=True)
class ChecklistVerdict:
    verdicts: tuple[ItemVerdict, ...] = ()
    reason: str = ""
    unmet: tuple[str, ...] = field(default=())

    @property
    def pass_rate(self) -> float | None:
        """Share of "yes" weighted by importance (TICK Pass Rate with RLCF weights)."""
        known = [v for v in self.verdicts if v.answer in ("yes", "no")]
        total = sum(v.item.importance for v in known)
        if not total:
            return None
        return sum(v.item.importance for v in known if v.answer == "yes") / total

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "pass_rate": self.pass_rate,
            "unmet_important": list(self.unmet),
            "reason": self.reason,
            "items": [{**v.item.to_log_payload(), "answer": v.answer, "how": v.how, "why": v.why}
                      for v in self.verdicts],
        }


def _json(raw: Any) -> dict[str, Any]:
    from core.plan_parsing import extract_json_object

    return extract_json_object(raw if isinstance(raw, str) else str(raw)) or {}


def build_checklist(llm: Any, request: str) -> Checklist:
    """Build the checklist BEFORE the work; any failure gives an empty list with a reason."""
    text = " ".join((request or "").split())
    if llm is None or not text:
        return Checklist(reason="no model or empty request")
    try:
        raw = llm.complete(BUILD_SYSTEM, f"REQUEST:\n{text[:6000]}", max_tokens=1200, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — without a checklist the turn goes as before
        return Checklist(reason=f"model call failed: {type(exc).__name__}")
    items: list[ChecklistItem] = []
    dropped = 0
    for entry in (_json(raw).get("items") or [])[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        question = " ".join(str(entry.get("question") or "").split())
        if not question:
            continue
        if not _quoted_from(entry.get("quote"), text):
            dropped += 1
            continue
        try:
            importance = max(0, min(100, int(entry.get("importance", 50))))
        except (TypeError, ValueError):
            importance = 50
        rule = entry.get("rule") if entry.get("rule") in _PROGRAM_RULES else None
        arg = str(entry.get("arg")) if rule and entry.get("arg") else None
        items.append(ChecklistItem(question, importance, rule if arg else None, arg))
    if not items:
        return Checklist(reason=f"model returned no usable items (unquoted: {dropped})")
    items.append(ChecklistItem(UNIVERSAL, UNIVERSAL_IMPORTANCE))
    return Checklist(items=tuple(items), reason=f"model (unquoted dropped: {dropped})")


def _quoted_from(quote: Any, request: str) -> bool:
    """An item holds only if its quote stands verbatim in the request: a program check, not the model's; whitespace and case ignored, a quote under 3 chars is not a quote."""
    words = " ".join(str(quote or "").split()).casefold()
    return len(words) >= 3 and words in " ".join(request.split()).casefold()


def _program(item: ChecklistItem, answer: str) -> str:
    present = (item.arg or "").casefold() in (answer or "").casefold()
    if item.rule == "must_include":
        return "yes" if present else "no"
    return "no" if present else "yes"


def check_answer(llm: Any, request: str, checklist: Checklist, answer: str) -> ChecklistVerdict:
    """Answer the checklist from a finished answer: exact items by program, the rest by the judge."""
    if not checklist.items:
        return ChecklistVerdict(reason=checklist.reason or "empty checklist")
    verdicts: dict[int, ItemVerdict] = {}
    judged: list[tuple[int, ChecklistItem]] = []
    for n, item in enumerate(checklist.items, 1):
        if item.rule in _PROGRAM_RULES and item.arg:
            verdicts[n] = ItemVerdict(item, _program(item, answer), "program")
        else:
            judged.append((n, item))
    first, reason = _judge(llm, request, answer, judged)
    # Second look at an important "no": the judge at temperature 0 flipped on the same answer (2 of 3), so a "no" that lowers the verdict counts only if repeated.
    doubtful = [(n, item) for n, item in judged
                if first.get(n, ("", ""))[0] == "no" and item.importance >= IMPORTANT]
    second = _judge(llm, request, answer, doubtful)[0] if doubtful else {}
    for n, item in judged:
        word, why = first.get(n, ("unknown", ""))
        if (n, item) in doubtful and second.get(n, ("", ""))[0] != "no":
            word, why = "unknown", f"судья разошёлся с собой: {why}"[:300]
        verdicts[n] = ItemVerdict(item, word, "judge", why)
    ordered = tuple(verdicts[n] for n in sorted(verdicts))
    unmet = tuple(v.item.question for v in ordered if v.answer == "no" and v.item.importance >= IMPORTANT)
    return ChecklistVerdict(verdicts=ordered, reason=reason, unmet=unmet)


def _judge(llm: Any, request: str, answer: str,
           judged: list[tuple[int, ChecklistItem]]) -> tuple[dict[int, tuple[str, str]], str]:
    """One judge call: item number -> (yes | no | unknown, why). Failure gives empty."""
    listing = "\n".join(f"{n}. {item.question}" for n, item in judged)
    user = (f"REQUEST:\n{' '.join((request or '').split())[:6000]}\n\n"
            f"RESPONSE:\n{(answer or '')[:12000]}\n\nQUESTIONS:\n{listing}")
    try:
        raw = llm.complete(JUDGE_SYSTEM, user, max_tokens=1500, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — the judge failed: "unknown", not "no"
        return {}, f"judge failed: {type(exc).__name__}"
    out: dict[int, tuple[str, str]] = {}
    for a in _json(raw).get("answers") or []:
        if isinstance(a, dict) and str(a.get("n", "")).isdigit():
            word = {"YES": "yes", "NO": "no"}.get(str(a.get("answer") or "").strip().upper(), "unknown")
            out[int(a["n"])] = (word, " ".join(str(a.get("why") or "").split())[:300])
    return out, "ok"


def attach_checklist(loop: Any, contract: Any, request: str) -> Any:
    """Build the checklist BEFORE the work and put it in the turn's contract; off leaves the contract as it was.

    The `request_checklist` journal entry is written before the first tool, like the contract itself, so the criterion cannot be fitted to the work.
    """
    if not enabled():
        return contract
    from dataclasses import replace

    checklist = build_checklist(getattr(loop, "llm", None), request)
    loop.log.log("request_checklist", checklist.to_log_payload())
    return replace(contract, checklist=checklist)


def judge_contract_checklist(loop: Any, request: str, answer: str, contract: Any) -> bool:
    """Check the answer against the contract's checklist; True means an important item was not met."""
    checklist = getattr(contract, "checklist", None)
    if checklist is None or not checklist.items:
        return False
    from core.answer_format import format_human_response

    # The text the human sees (the sections hidden behind a failed "one number").
    try:
        shown = format_human_response(answer)
    except Exception:  # noqa: BLE001 — no human view: judge the raw answer as before
        shown = answer
    verdict = check_answer(getattr(loop, "llm", None), request, checklist, shown)
    loop.log.log("checklist_verdict", verdict.to_log_payload())
    return bool(verdict.unmet)
