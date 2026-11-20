"""The answer under construction: deciders contribute a claim-bearing ``body`` and claim-free notices.

Notice channels: ``prepend`` (above the body, registration order), ``unverified_note`` (merged into
the ``Unverified:`` section) and ``append`` (below the body). Notices survive body rewrites;
:meth:`ResponseDraft.render` composes once and :meth:`ResponseDraft.to_log_payload` records the ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Channel = Literal["body", "prepend", "unverified_note", "append"]

#: Channels that carry something *about* the answer rather than a claim in it.
NOTICE_CHANNELS: frozenset[str] = frozenset({"prepend", "unverified_note", "append"})


def _merge_unverified(answer: str, note: str) -> str:
    if re.search(r"(?im)^Unverified\s*:\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^Unverified\s*:\s*nothing\s*$",
            f"Unverified: {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^Unverified\s*:\s*(?!nothing\s*$).+\S\s*$", answer):
        return re.sub(
            r"(?im)^(Unverified\s*:\s*.+\S)\s*$",
            rf"\1; {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^\*\*Unverified:\*\*\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^\*\*Unverified:\*\*\s*nothing\s*$",
            f"**Unverified:** {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^\*\*Unverified\*\*\s*:?\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^\*\*Unverified\*\*\s*:?\s*nothing\s*$",
            f"**Unverified:** {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^Unverified\s*:\s*$", answer):
        return re.sub(
            r"(?im)^Unverified\s*:\s*$",
            f"Unverified:\n- {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^#+\s*Unverified\s*$", answer):
        return re.sub(
            r"(?im)^(#+\s*Unverified\s*)$",
            rf"\1\n- {note}",
            answer,
            count=1,
        )
    return answer.rstrip() + f"\nUnverified: {note}\n"


@dataclass(frozen=True)
class Contribution:
    """One decider's addition to the response, and who made it."""

    author: str
    channel: Channel
    text: str

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "channel": self.channel,
            "chars": len(self.text),
        }


@dataclass
class ResponseDraft:
    """The response as it is built, plus the ledger of who built what."""

    body: str
    body_author: str = "synthesizer"
    notices: list[Contribution] = field(default_factory=list)
    #: Body rewrites, oldest first: ``{"author": …, "superseded_by": …}``.
    body_history: list[dict[str, str]] = field(default_factory=list)
    #: A refusal replaced the claims; verification still describes the draft.
    answer_withheld: bool = False

    # ── contributing ─────────────────────────────────────────────────────

    def add_notice(self, *, author: str, channel: Channel, text: str) -> bool:
        """Attach something the deciders say *about* the answer."""
        if channel not in NOTICE_CHANNELS:
            raise ValueError(f"not a notice channel: {channel!r}")
        if not text or not text.strip():
            return False
        if text in self.body:
            return False
        if any(n.text == text and n.channel == channel for n in self.notices):
            return False
        self.notices.append(Contribution(author=author, channel=channel, text=text))
        return True

    def set_body(self, text: str, *, by: str) -> None:
        """Rewrite the claims. Records who superseded whom."""
        if text == self.body:
            return
        self.body_history.append(
            {"author": self.body_author, "superseded_by": by}
        )
        self.body = text
        self.body_author = by

    # ── composing ────────────────────────────────────────────────────────

    def render(self) -> str:
        """Compose the final text. The only place the pieces are joined."""
        text = self.body
        for notice in self.notices:
            if notice.channel == "unverified_note":
                text = _merge_unverified(text, notice.text)
        for notice in self.notices:
            if notice.channel == "append":
                text = f"{text}\n\n{notice.text}"
        for notice in self.notices:
            if notice.channel == "prepend":
                # registration order leaves the first-registered notice closest to the body
                text = f"{notice.text}\n\n{text}"
        return text

    # ── auditing ─────────────────────────────────────────────────────────

    def missing_from(self, rendered: str) -> list[Contribution]:
        """Notices whose text did not make it into *rendered*."""
        return [n for n in self.notices if n.text not in rendered]

    def to_log_payload(self, rendered: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "body_author": self.body_author,
            "body_chars": len(self.body),
            "answer_withheld": self.answer_withheld,
            "contributions": [n.to_log_payload() for n in self.notices],
            "body_history": list(self.body_history),
        }
        if rendered is not None:
            payload["rendered_chars"] = len(rendered)
            payload["missing"] = [
                {"author": n.author, "channel": n.channel}
                for n in self.missing_from(rendered)
            ]
        return payload
