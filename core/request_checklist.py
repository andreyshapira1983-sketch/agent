"""Чек-лист поручения: требования человека как вопросы «да/нет».

Зачем, замер 2026-09-23: договор выполненности (`core/completion_contract.py`)
проверяет ровно три вещи — файл создан, файл изменён, тесты зелёные. Всё, чем
живёт обычная (и платная) задача, — «не больше 8 предложений», «минимум три
источника», «не меняй код», «строка VERDICT в конце» — не проверялось вовсе.
Живой случай того же дня: поручение требовало в конце короткий ответ
оператору не длиннее 8 предложений, ответ его не дал, а ход считался
выполненным.

Как это делают в литературе (оба источника прочитаны):

- TICK (Cook et al., «TICKing All the Boxes: Generated Checklists Improve LLM
  Evaluation and Generation», arXiv 2410.03608): модель раскладывает поручение
  на вопросы «да/нет», по одному на каждое требование; «да» значит «выполнено»;
  покрываются явные требования и подразумеваемые важные для такой задачи;
  вопрос так же конкретен, как требование («3 предложения» -> «ответ в 3
  предложения?»). Качество — доля «да» (Pass Rate). Та же модель может и
  составлять, и отвечать.
- RLCF (Viswanathan et al., «Checklists Are Better Than Reward Models For
  Aligning Language Models», NeurIPS 2025, arXiv 2507.18624): у пункта вес
  важности 0–100; пункт проверяется программой ТОЛЬКО если программа проверяет
  его точно — «никогда не приближай», иначе отвечает судья-модель; ко всем
  спискам добавляется общее требование «отвечает прямо на запрос, без лишнего»,
  иначе проверку учатся обходить.

Наши отличия, названные прямо: программу, написанную моделью, мы не исполняем —
точные проверки взяты из короткого белого списка (подстрока есть / подстроки
нет). Подсчёт предложений, язык и прочее уходят судье: на ответе с
перечнями, сокращениями и служебным хвостом это не точная проверка, а
приближение, которое RLCF запрещает.

Дальше — моё инженерное решение по замеру, не литература. Чек-лист «как в
TICK» на 16 живых парах чата 24.09 дал важное «нет» в 14, и большинство — ложные:
подразумеваемые требования (извинись, пообещай, разговорный тон), пересланная
вакансия или пост, принятые за поручение, разбор прошлого ответа, принятый за
список требований, и разброс судьи на одном и том же ответе (2 из 3). Поэтому:
только явные указания об ЭТОМ ответе; у пункта дословная цитата из поручения,
и программа проверяет, что она там есть; судья может воздержаться (N/A) на
вопрос не о тексте ответа; важное «нет» спрашивается второй раз и засчитывается
при совпадении; общий пункт RLCF — ниже порога. После правок на 18 прогонах
(15 пар): ложных «нет» 0; настоящие пойманы в #538 (метки FACT/INFERENCE,
итог ≤8 предложений), #544, #546. Цена: пустой список ~1 с, полный 3–8 с.

Сторож односторонний, как все в `assemble_completion_verdict`: невыполненный
важный пункт может понизить «выполнено» до «выполнено частично», но ничего не
повышает. Сбой модели, пустой или неразборчивый ответ — пустой чек-лист и
никакого сигнала: отсутствие проверки не становится обвинением.

Включено по умолчанию (решение оператора 24.09: «включи, пусть работает»).
AGENT_REQUEST_CHECKLIST=0 в .env — аварийный выключатель, не способ включать.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

ENV = "AGENT_REQUEST_CHECKLIST"
MAX_ITEMS = 12
#: Пункт с такой важностью и выше, отвеченный «нет», понижает вердикт.
IMPORTANT = 50
#: Общее требование RLCF — добавляется к каждому списку. Вес ниже порога: на
#: живых парах 24.09 судья отвечал на него «нет» в 6 ответах из 16, и половина —
#: вкусовщина; пусть пишется в журнал, а понижать вердикт не может.
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
    answer: str          # yes | no | unknown
    how: str             # program | judge
    why: str = ""


@dataclass(frozen=True)
class ChecklistVerdict:
    verdicts: tuple[ItemVerdict, ...] = ()
    reason: str = ""
    unmet: tuple[str, ...] = field(default=())

    @property
    def pass_rate(self) -> float | None:
        """Доля «да», взвешенная важностью (TICK Pass Rate с весами RLCF)."""
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
    """Составить чек-лист ДО работы. Любой сбой — пустой список с причиной."""
    text = " ".join((request or "").split())
    if llm is None or not text:
        return Checklist(reason="no model or empty request")
    try:
        raw = llm.complete(BUILD_SYSTEM, f"REQUEST:\n{text[:6000]}", max_tokens=1200, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — без чек-листа ход идёт, как прежде
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
    """Пункт держится, только если его цитата дословно стоит в поручении.

    Точная проверка, которую делает программа, а не модель: пункт без опоры в
    тексте человека — выдуманное требование, его «нет» было бы обвинением ни
    за что. Пробелы и регистр не в счёт; цитата короче 3 знаков — не цитата.
    """
    words = " ".join(str(quote or "").split()).casefold()
    return len(words) >= 3 and words in " ".join(request.split()).casefold()


def _program(item: ChecklistItem, answer: str) -> str:
    present = (item.arg or "").casefold() in (answer or "").casefold()
    if item.rule == "must_include":
        return "yes" if present else "no"
    return "no" if present else "yes"


def check_answer(llm: Any, request: str, checklist: Checklist, answer: str) -> ChecklistVerdict:
    """Ответить на чек-лист по готовому ответу: точное — программой, остальное — судьёй."""
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
    # Второй взгляд на важное «нет»: замер 24.09 (#542) — один и тот же ответ
    # судья при temperature 0 признавал то выполненным, то нет (2 из 3). «Нет»,
    # которое понижает вердикт, засчитывается, только если судья повторил его.
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
    """Один вызов судьи: номер пункта -> (yes | no | unknown, почему). Сбой — пусто."""
    listing = "\n".join(f"{n}. {item.question}" for n, item in judged)
    user = (f"REQUEST:\n{' '.join((request or '').split())[:6000]}\n\n"
            f"RESPONSE:\n{(answer or '')[:12000]}\n\nQUESTIONS:\n{listing}")
    try:
        raw = llm.complete(JUDGE_SYSTEM, user, max_tokens=1500, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — судья упал: «не знаю», не «нет»
        return {}, f"judge failed: {type(exc).__name__}"
    out: dict[int, tuple[str, str]] = {}
    for a in _json(raw).get("answers") or []:
        if isinstance(a, dict) and str(a.get("n", "")).isdigit():
            word = {"YES": "yes", "NO": "no"}.get(str(a.get("answer") or "").strip().upper(), "unknown")
            out[int(a["n"])] = (word, " ".join(str(a.get("why") or "").split())[:300])
    return out, "ok"


def attach_checklist(loop: Any, contract: Any, request: str) -> Any:
    """Составить чек-лист ДО работы и положить его в договор хода; выключено — договор как был.

    Журнал `request_checklist` пишется до первого инструмента, как и сам
    договор: критерий не может быть подогнан под работу, которую он судит.
    """
    if not enabled():
        return contract
    from dataclasses import replace

    checklist = build_checklist(getattr(loop, "llm", None), request)
    loop.log.log("request_checklist", checklist.to_log_payload())
    return replace(contract, checklist=checklist)


def judge_contract_checklist(loop: Any, request: str, answer: str, contract: Any) -> bool:
    """Проверить ответ по чек-листу договора; True — важный пункт не выполнен."""
    checklist = getattr(contract, "checklist", None)
    if checklist is None or not checklist.items:
        return False
    verdict = check_answer(getattr(loop, "llm", None), request, checklist, answer)
    loop.log.log("checklist_verdict", verdict.to_log_payload())
    return bool(verdict.unmet)
