"""Intent understanding: translates plain human language into one of the agent's real actions, or plain conversation.

Grounded: the model may only choose an ``action`` from the real capability list passed in; an invented action is rejected. Safe by default: malformed output, an ungrounded action or a low-confidence guess falls back to ``conversation``. Side-effect free: reads a message, returns a decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

IntentKind = Literal["conversation", "action"]

# Below this confidence the action guess is too weak to act on; talk instead (a wrong command is worse than a plain reply).
_MIN_ACTION_CONFIDENCE = 0.5


@dataclass(frozen=True)
class IntentDecision:
    kind: IntentKind
    action: str | None
    confidence: float
    reasoning: str
    # "model": a genuine, parseable verdict. "fallback": no clear verdict (error / unparseable / ungrounded / low confidence), defaulted to conversation; callers that must not override deterministic behaviour on uncertainty check this.
    source: Literal["model", "fallback"] = "model"

    @classmethod
    def conversation(cls, reasoning: str = "") -> IntentDecision:
        return cls(kind="conversation", action=None, confidence=1.0,
                   reasoning=reasoning, source="fallback")


_SYSTEM_TEMPLATE = """You are the intent router for a LOCAL autonomous agent.
Decide what the user actually WANTS from this one message.

The agent can perform exactly these actions (and NOTHING else):
{actions}

Rules:
- If the user is asking the agent to DO one of the actions above, return that action.
- If the user is greeting, chatting, reflecting, venting, or merely MENTIONING /
  quoting a phrase (not requesting it), return "conversation".
- Judge the MEANING, not keywords. "Что ты умеешь?" is a request; "я лишь написал
  предложение что ты умеешь делать" is conversation.
- Never invent an action that is not in the list. When unsure, choose conversation.

Reply with ONE JSON object and nothing else:
{{"kind": "action" | "conversation", "action": "<one of the actions, or null>",
  "confidence": 0.0-1.0, "reasoning": "<short>"}}"""


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    # Strip ```json fences if present.
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)
        cleaned = cleaned[1] if len(cleaned) >= 2 else text
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def understand_intent(
    message: str,
    *,
    available_actions: tuple[str, ...] | list[str],
    llm: Any,
) -> IntentDecision:
    """Classify *message* into a grounded action or plain conversation."""
    actions = tuple(str(a) for a in available_actions if str(a).strip())
    if not message or not message.strip():
        return IntentDecision.conversation("empty message")

    action_lines = "\n".join(f"- {a}" for a in actions) or "- (none)"
    system = _SYSTEM_TEMPLATE.format(actions=action_lines)
    try:
        raw = llm.complete(system, message.strip(), temperature=0.0)
    except Exception:  # noqa: BLE001 — a model failure must never break the turn
        return IntentDecision.conversation("llm error")

    obj = _extract_json(raw)
    if obj is None:
        return IntentDecision.conversation("unparseable model output")

    kind = str(obj.get("kind") or "").strip().lower()
    action = obj.get("action")
    action = str(action).strip() if action not in (None, "", "null") else None
    try:
        confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reasoning = str(obj.get("reasoning") or "")[:300]

    # Only an EXPLICIT verdict is a genuine model signal (source="model"); a missing kind, ungrounded action or low confidence is uncertainty (source="fallback"). An empty "{}" is not a "conversation" verdict.
    if kind == "conversation":
        return IntentDecision(kind="conversation", action=None, confidence=confidence,
                              reasoning=reasoning or "model chose conversation", source="model")
    if kind != "action":
        return IntentDecision.conversation(f"no/unknown kind in model output: {kind!r}")
    if action is None or action not in actions:
        return IntentDecision.conversation(f"ungrounded/invented action: {action!r}")
    if confidence < _MIN_ACTION_CONFIDENCE:
        return IntentDecision(kind="conversation", action=None, confidence=confidence,
                              reasoning=f"low confidence ({confidence:.2f})", source="fallback")
    return IntentDecision(kind="action", action=action, confidence=confidence,
                          reasoning=reasoning, source="model")
