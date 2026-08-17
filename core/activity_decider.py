"""Activity-type decider: the door must not choose the mind.

Classifies operator input into conversation / bounded_action /
persistent_goal / goal_control BEFORE any channel semantics apply.
Deterministic and conservative: launching work needs a start verb AND a
continuity contract; hypothetical framing vetoes everything. Design prose:
docs/CODE_NOTES.md («The door chose the mind»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ActivityType = Literal[
    "conversation", "bounded_action", "persistent_goal", "goal_control",
]

#: Musing / thought-experiment / meta-discussion framing. A veto over every
#: other class: the operator's borders — «а представь…» must never mint a
#: campaign, and DESCRIBING the mechanism («… → C16 создаёт задачу», «если я
#: напишу …, что произойдёт?») is architecture talk, not an order.
_HYPOTHETICAL_RE = re.compile(
    r"(?i)как бы ты|что ты думаешь|что бы ты|расскажи|представь|вообрази"
    r"|если бы|допустим|гипотетически"
    r"|→|=>|что произойдёт|если я (?:напишу|скажу)|должн[аоы]?\s+уметь"
    r"|what do you think|how would you|tell me|imagine|hypothetically"
    r"|what happens if|if i (?:write|say)|should be able",
)

#: Mention is not use (live specimen rtask_7879672a: a pasted diagram's
#: quoted «"начни X и продолжай"» minted a real task). A verb inside any
#: quoted/backticked span is somebody TALKING ABOUT the command.
_QUOTE_SPAN_RE = re.compile(r'"[^"\n]{2,400}"|«[^»\n]{2,400}»|`[^`\n]{2,400}`')


def _unquoted(text: str) -> str:
    """The text with quoted/backticked spans blanked — only top-level words
    can carry a directive."""
    return _QUOTE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)

#: Launching work: BOTH halves must fire. A bare start verb («начни с того,
#: что расскажи…») is not an ongoing contract.
_START_RE = re.compile(
    r"(?i)\bначни|\bначинай|\bзаймись|\bприступай|\bвозьмись"
    r"|\bstart\b|\bbegin\b|\btake up\b",
)
_CONTINUITY_RE = re.compile(
    r"(?i)продолжай|как свою работу|как работу|на постоянной основе"
    r"|постоянно|дальше сам|веди это|это твоя работа|не останавливайся"
    r"|keep going|as your (?:ongoing )?work|and continue|from now on",
)

#: Managing existing work: a control verb AND a work noun.
_CONTROL_RE = re.compile(
    r"(?i)останови|прекрати|приостанови|поставь на паузу|возобнови|отмени"
    r"|\bstop\b|\bpause\b|\bresume\b|\bcancel\b",
)
_WORK_NOUN_RE = re.compile(
    r"(?i)обучени|работ|цель|целью|задач|кампани|изучени"
    r"|\bwork\b|\bgoal\b|\btask\b|\blearning\b|\bcampaign\b",
)

#: Do-it-now imperatives; routing falls through to the loop either way, the
#: label exists for the journal and for future completion contracts.
_IMMEDIATE_RE = re.compile(
    r"(?i)\bпроверь|\bизмерь|\bзапусти|\bвыполни|\bсделай|\bпокажи"
    r"|\bcheck\b|\brun\b|\bverify\b|\bmeasure\b",
)


@dataclass(frozen=True)
class ActivityDecision:
    activity: ActivityType
    reason: str


#: An order LEADS the utterance; a narrative mentions verbs mid-text. Live
#: proof 2026-08-17 (trace_68084781): the console glued a paste tail to a
#: typed order («…выдумка.Начни…») and the whole-text scan minted a task
#: from the chimera; a proof-demand question was likewise hijacked into
#: goal_control by verbs buried in its argument.
_HEAD_CHARS = 30


def _leads(pattern: re.Pattern[str], text: str) -> bool:
    m = pattern.search(text)
    return m is not None and m.start() < _HEAD_CHARS


def decide_activity(text: str) -> ActivityDecision:
    """One decision, deterministic, before any channel semantics.

    Directives are read from the UNQUOTED text only: mention is not use —
    a persistent goal is born from a top-level directive, never from a
    quote, an example, code, or a description of expected behaviour."""
    t = (text or "").strip()
    if not t:
        return ActivityDecision("conversation", "empty input")
    if _HYPOTHETICAL_RE.search(t):
        return ActivityDecision(
            "conversation", "hypothetical/meta framing vetoes launching")
    top = _unquoted(t)
    if _leads(_CONTROL_RE, top) and _WORK_NOUN_RE.search(top):
        return ActivityDecision(
            "goal_control", "control verb leads + work noun")
    if _leads(_START_RE, top) and _CONTINUITY_RE.search(top):
        return ActivityDecision(
            "persistent_goal", "start verb leads + continuity contract")
    if _leads(_IMMEDIATE_RE, top):
        return ActivityDecision(
            "bounded_action", "do-it-now imperative; routed to the loop")
    return ActivityDecision("conversation", "default")
