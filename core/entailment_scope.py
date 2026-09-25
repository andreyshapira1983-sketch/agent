"""Какие утверждения со ссылкой сверять по смыслу моделью (MIR-060).

Решение оператора 25.09 — промежуточный путь. Найденная ссылка ещё не
подтверждение: по ALCE (Gao et al., 2023) утверждение подтверждено, если
процитированное его ВЛЕЧЁТ. Но вызов модели на каждое утверждение — деньги и
время на каждом ответе. Поэтому модель зовём только для ВЫВЕДЕННОГО — со
сравнением, счётом, обобщением, выводом или числом, которого в улике нет — и
только если пересчёт (core/claim_arithmetic) его не распознал: распознанное
он уже решил сам, без модели. Дословный пересказ бесплатен. И не больше
`JUDGE_CALLS_PER_ANSWER` вызовов на ответ: сверх лимита — прежнее поведение.
"""
from __future__ import annotations

import re
from typing import Any

from core.claim_arithmetic import evaluate as evaluate_claim_arithmetic
from core.verifier_utils import truth_excerpt

JUDGE_CALLS_PER_ANSWER = 6

_DERIVED_RE = re.compile(
    r"больше|меньше|выше|ниже|раньше|позже|быстрее|медленнее|чаще|реже|равн|столько же"
    r"|значит|следовательно|поэтому|потому что|так как|отсюда|итого|всего|кажд|ни один"
    r"|никогда|всегда|единствен|только|втор|трет|перв|\b(?:все|всех|один|одна|два|две|три)\b"
    r"|\b(?:more|less|fewer|greater|smaller|larger|higher|lower|faster|slower|before|after"
    r"|equal|same as|therefore|thus|hence|because|so|implies|means|total|every|all|none"
    r"|never|always|only|first|second|third|one|two|three|four|five)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_CITATION_RE = re.compile(r"\[[^\]]*\]")


def needs_entailment(claim: str, excerpts: list[str]) -> bool:
    """Выведенное утверждение, которое пересчёт не решил сам."""
    claim = _CITATION_RE.sub(" ", claim)  # цифра в имени источника — не вывод
    joined = "\n".join(truth_excerpt(e or "") for e in excerpts)
    if any(evaluate_claim_arithmetic(claim, truth_excerpt(e or "")).outcome != "silent" for e in excerpts):
        return False
    if _DERIVED_RE.search(claim):
        return True
    return any(n not in joined for n in _NUMBER_RE.findall(claim))


class BudgetedJudge:
    """Модель-судья с лимитом вызовов на один ответ; каждый вызов — в журнал."""

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
    """Судья для живого ответа: дешёвая роль `verifier`, свежий лимит на ответ.

    Один общий клиент (`ModelRouter.single` — REPL и тесты с поддельной
    моделью) судьёй не становится: он ел бы ответы, заготовленные для синтеза.
    """
    if router is None or getattr(router, "_static_llm", None) is not None:
        return None
    return BudgetedJudge(router.for_role("verifier"), log)
