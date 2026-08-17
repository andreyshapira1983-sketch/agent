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

#: Musing / thought-experiment framing. A veto over every other class: the
#: operator's own safety border — «а представь…» must never mint a campaign.
_HYPOTHETICAL_RE = re.compile(
    r"(?i)как бы ты|что ты думаешь|что бы ты|расскажи|представь|вообрази"
    r"|если бы|допустим|гипотетически"
    r"|what do you think|how would you|tell me|imagine|hypothetically",
)

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


def decide_activity(text: str) -> ActivityDecision:
    """One decision, deterministic, before any channel semantics."""
    t = (text or "").strip()
    if not t:
        return ActivityDecision("conversation", "empty input")
    if _HYPOTHETICAL_RE.search(t):
        return ActivityDecision(
            "conversation", "hypothetical/musing framing vetoes launching")
    if _CONTROL_RE.search(t) and _WORK_NOUN_RE.search(t):
        return ActivityDecision(
            "goal_control", "control verb + work noun")
    if _START_RE.search(t) and _CONTINUITY_RE.search(t):
        return ActivityDecision(
            "persistent_goal", "start verb + continuity contract")
    if _IMMEDIATE_RE.search(t):
        return ActivityDecision(
            "bounded_action", "do-it-now imperative; routed to the loop")
    return ActivityDecision("conversation", "default")
