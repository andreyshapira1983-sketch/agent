"""Operational Design Domain detector: REFUSE harmful/illegal requests, ESCALATE ones needing human authority; pure regex, no LLM, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

DomainKind = Literal[
    "physical_world",      # drive, phone-call, move a physical object, robot
    "real_money",          # transfer/pay real funds, buy/sell securities
    "regulated_advice",    # authoritative medical diagnosis / legal ruling
    "authority_over_people",  # hire/fire, sign contracts on someone's behalf
    "harmful_illegal",     # weapons, malware, intrusion — never in domain
]

DomainVerdict = Literal["in_domain", "out_of_domain"]
DomainAction = Literal["proceed", "refuse", "escalate"]


@dataclass(frozen=True)
class DomainFinding:
    kind: DomainKind
    evidence: str          # short excerpt that triggered the pattern
    confidence: float      # 0.0–1.0


@dataclass
class DomainResult:
    verdict: DomainVerdict
    action: DomainAction
    findings: list[DomainFinding] = field(default_factory=list)
    message: str = ""      # operator-facing refusal/escalation text

    @property
    def in_domain(self) -> bool:
        return self.verdict == "in_domain"

    @property
    def blocks(self) -> bool:
        """True when the agent must NOT proceed to planning."""
        return self.action in ("refuse", "escalate")


# ---------------------------------------------------------------------------
# Coding / simulation context bypass (precision guard)
# ---------------------------------------------------------------------------

# When any of these appear, the request is about software, simulation, tests or
# examples — never a real-world action — so we never flag it out-of-domain.
_CODE_CONTEXT = re.compile(
    r"\b(код[ае]?|кода|функци\w*|метод\w*|класс\w*|тест\w*|пример\w*|"
    r"скрипт\w*|программ\w*|симул\w*|моков?\w*|заглушк\w*|в\s+базе|"
    r"в\s+бд|в\s+таблиц\w*|алгоритм\w*|сортиров\w*|строк[уаи]|"
    r"code|function|method|class\b|unit\s*test|pytest|example|sample|"
    r"mock|stub|simulat\w*|in\s+the\s+database|sql|api\s+endpoint|"
    r"regex|parser|refactor)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Out-of-domain pattern registry  (high precision, imperative-leaning)
# ---------------------------------------------------------------------------

_PHYSICAL_WORLD = re.compile(
    # `\w*` for the same reason as in `_REAL_MONEY`: without it a Russian accusative form slipped
    # past.
    r"\b(поезжай|съезди|сходи|отвез[иь]|води\s+маши\w*|сядь\s+за\s+руль|"
    r"позвони\s+по\s+телефону|набери\s+номер|нажми\s+физическ\w*|"
    r"подвигай\s+робот\w*|управляй\s+робот\w*|drive\s+(the|a|my)\s+car|"
    r"make\s+a\s+phone\s+call|pick\s+up\s+the\s+phone|physically\s+(go|move)|"
    r"move\s+the\s+robot|operate\s+the\s+(machine|vehicle|drone))\b",
    re.IGNORECASE,
)

# Every truncated Russian stem carries `\w*`: the closing `\b` would otherwise cut off inflected
# forms.
# `рубл\w*|руб\b` instead of `руб\w*`, so that a word like "рубрика" is not a money order.
_REAL_MONEY = re.compile(
    r"\b(переведи\s+(деньги|\d+|средства|сумм\w*|\$|рубл\w*|руб\b|евро|доллар\w*)|"
    r"оплати\s+(счёт|счет|заказ|картой|со\s+счёта)|сделай\s+платёж|"
    r"сделай\s+платеж|купи\s+(акци\w*|биткойн\w*|крипт\w*|валют\w*)|"
    r"продай\s+(акци\w*|биткойн\w*|крипт\w*|валют\w*)|сними\s+деньги\s+со\s+счёта|"
    r"wire\s+(the\s+)?money|send\s+(real\s+)?money|make\s+a\s+payment|"
    r"transfer\s+\$?\d|buy\s+(stocks|shares|bitcoin|crypto)|"
    r"sell\s+(stocks|shares|bitcoin|crypto))\b",
    re.IGNORECASE,
)

_REGULATED_ADVICE = re.compile(
    r"\b(поставь\s+(мне\s+)?диагноз|выпиши\s+(мне\s+)?рецепт|"
    r"назначь\s+(мне\s+)?лечение|какое\s+лекарство\s+(мне\s+)?принять|"
    r"diagnose\s+(me|my|the\s+patient)|prescribe\s+(me|a\s+)|"
    r"give\s+me\s+(legal|medical)\s+advice|is\s+this\s+legal\s+in\s+my\s+case|"
    r"represent\s+me\s+in\s+court)\b",
    re.IGNORECASE,
)

_AUTHORITY_OVER_PEOPLE = re.compile(
    # `соглашен` without `\w*` matched no real spelling; the word exists only as an inflected form.
    r"\b(уволь\s+|прими\s+на\s+работу|подпиши\s+(контракт\w*|договор\w*|соглашен\w*)|"
    r"fire\s+(him|her|them|the\s+employee)|hire\s+(him|her|them)|"
    r"sign\s+the\s+(contract|agreement)\s+on\s+(my|his|her|their)\s+behalf)\b",
    re.IGNORECASE,
)

_HARMFUL_ILLEGAL = re.compile(
    r"\b((сделай|напиши|создай|сгенерируй|разработай|собери)\s+(\w+\s+){0,2}"
    r"(вирус\w*|малвар\w*|троян\w*|ransomware|кейлоггер\w*|шифровальщик\w*|"
    r"бомбу|оружие|эксплойт\w*)|"
    r"взломай\s+(сайт|сервер|аккаунт|сеть|систему)|обойди\s+защит|"
    r"укради\s+(данные|парол)|build\s+(a\s+)?(bomb|weapon|malware)|"
    r"write\s+(me\s+)?malware|create\s+(a\s+)?(virus|ransomware|exploit)|"
    r"hack\s+(into\s+)?(the\s+)?(site|server|account|network|system)|"
    r"bypass\s+(the\s+)?(security|authentication|drm)|steal\s+(the\s+)?"
    r"(data|password|credentials))\b",
    re.IGNORECASE,
)


# kind -> (pattern, confidence, action)
_REGISTRY: tuple[tuple[DomainKind, re.Pattern[str], float, DomainAction], ...] = (
    ("harmful_illegal", _HARMFUL_ILLEGAL, 0.95, "refuse"),
    ("real_money", _REAL_MONEY, 0.85, "escalate"),
    ("physical_world", _PHYSICAL_WORLD, 0.85, "escalate"),
    ("regulated_advice", _REGULATED_ADVICE, 0.80, "escalate"),
    ("authority_over_people", _AUTHORITY_OVER_PEOPLE, 0.80, "escalate"),
)

# A finding must reach this confidence to block.
_BLOCK_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_operational_domain(text: str) -> DomainResult:
    """Evaluate whether *text* lies inside the agent's operational domain.

    Returns a :class:`DomainResult`: in_domain/proceed (normal), out_of_domain/refuse (harmful or illegal), out_of_domain/escalate (needs a human with authority; hand off).
    Pure function: no I/O, no LLM, deterministic.
    """
    # Guard: non-string or empty input is trivially in-domain.
    if not isinstance(text, str) or not text.strip():
        return DomainResult(verdict="in_domain", action="proceed")

    # Guard: truncate before regex to bound worst-case cost.
    if len(text) > 4096:
        text = text[:4096]

    # Precision bypass: a coding/simulation/test framing is always in-domain,
    # EXCEPT for harmful/illegal requests, which stay out-of-domain even when
    # dressed up as "write code that ...".
    code_framed = bool(_CODE_CONTEXT.search(text))

    findings: list[DomainFinding] = []
    for kind, pattern, confidence, _action in _REGISTRY:
        match = pattern.search(text)
        if match:
            findings.append(
                DomainFinding(
                    kind=kind,
                    evidence=match.group(0).strip()[:80],
                    confidence=confidence,
                )
            )

    if not findings:
        return DomainResult(verdict="in_domain", action="proceed", findings=findings)

    # Resolve to the highest-confidence blocking finding.
    blocking = [f for f in findings if f.confidence >= _BLOCK_THRESHOLD]
    if code_framed:
        # Inside a coding/simulation frame, only genuinely harmful/illegal
        # requests survive the bypass; everything else is treated as code.
        blocking = [f for f in blocking if f.kind == "harmful_illegal"]

    if not blocking:
        return DomainResult(verdict="in_domain", action="proceed", findings=findings)

    top = max(blocking, key=lambda f: f.confidence)
    action = _action_for(top.kind)
    return DomainResult(
        verdict="out_of_domain",
        action=action,
        findings=findings,
        message=_build_message(top, action),
    )


def _action_for(kind: DomainKind) -> DomainAction:
    for registered_kind, _pattern, _conf, action in _REGISTRY:
        if registered_kind == kind:
            return action
    return "escalate"


def _build_message(finding: DomainFinding, action: DomainAction) -> str:
    """Build an honest, operator-facing refusal/escalation message."""
    if action == "refuse":
        return (
            "Эта задача вне моей операционной области и относится к вредоносным "
            f"или незаконным действиям («{finding.evidence}»). Я не буду этого "
            "делать и не могу помочь с этим запросом."
        )
    reason = {
        "real_money": "она требует распоряжения реальными деньгами",
        "physical_world": "она требует действия в физическом мире",
        "regulated_advice": "она требует профессионального (медицинского/"
                            "юридического) заключения с ответственностью",
        "authority_over_people": "она требует полномочий в отношении других людей",
    }.get(finding.kind, "она вне моей операционной области")
    return (
        f"Эта задача вне моей операционной области: {reason} "
        f"(«{finding.evidence}»). Я не должен действовать самостоятельно — "
        "нужно решение человека. Передай это оператору или уточни задачу так, "
        "чтобы она оставалась в рамках работы с кодом и данными."
    )
