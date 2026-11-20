"""Select which cited claims get a model entailment check: only derived claims the arithmetic check did not resolve, capped per answer."""
from __future__ import annotations

import re
from typing import Any

from core.claim_arithmetic import evaluate as evaluate_claim_arithmetic
from core.verifier_utils import truth_excerpt

JUDGE_CALLS_PER_ANSWER = 6

_DERIVED_RE = re.compile(
    r"больше|меньше|выше|ниже|раньше|позже|быстрее|медленнее|чаще|реже|равн|столько же"
    r"|значит|следовательно|поэтому|потому что|так как|отсюда|итого|всего|кажд|ни один"
    r"|никогда|всегда|единствен|только|втор|трет|перв"
    r"|\b(?:все|всех|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\b"
    r"|\b(?:more|less|fewer|greater|smaller|larger|higher|lower|faster|slower|before|after"
    r"|equal|same as|therefore|thus|hence|because|so|implies|means|total|every|all|none"
    r"|never|always|only|first|second|third|one|two|three|four|five)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_CITATION_RE = re.compile(r"\[[^\]]*\]")


def needs_entailment(claim: str, excerpts: list[str]) -> bool:
    """A derived claim that recomputation did not resolve itself."""
    claim = _CITATION_RE.sub(" ", claim)  # a digit in a source name is not an inference
    joined = "\n".join(truth_excerpt(e or "") for e in excerpts)
    if any(evaluate_claim_arithmetic(claim, truth_excerpt(e or "")).outcome != "silent" for e in excerpts):
        return False
    if _DERIVED_RE.search(claim):
        return True
    return any(n not in joined for n in _NUMBER_RE.findall(claim))


class BudgetedJudge:
    """Judge model with a per-answer call limit; every call is logged."""

    def __init__(self, llm: Any, log: Any = None, max_calls: int = JUDGE_CALLS_PER_ANSWER) -> None:
        self.llm, self.log, self.remaining, self.spent = llm, log, max_calls, 0

    def complete(self, **kwargs: Any) -> Any:
        self.remaining -= 1
        self.spent += 1
        answer = self.llm.complete(**kwargs)
        if self.log is not None:
            self.log.log("entailment_check", {"call": self.spent, "remaining": self.remaining,
                                              "answer": str(answer)[:40]})
        return answer


def judge_for(router: Any, log: Any = None) -> BudgetedJudge | None:
    """Judge for a live answer: the cheap `verifier` role with a fresh per-answer limit; a shared single client (ModelRouter.single) is never used."""
    if router is None or getattr(router, "_static_llm", None) is not None:
        return None
    return BudgetedJudge(router.for_role("verifier"), log)
