"""The answer under construction — an object the deciders contribute to.

Until now the response was a plain `str` that every post-synthesis decider
mutated in turn, and the last writer won. That is how three subsystems could
each report `applied: true` while only one of them reached the operator.
Measured on the real loop, default configuration: a `replan_exhausted` turn
whose evidence was thin produced

* `output_policy applied=True` — a warning that the verdict is unreliable
  because the replan budget ran out,
* `clarification_gate` — three concrete questions, the gate's whole purpose
  being that *when the loop is stuck, the mature response is to ASK*,
* `answer_enforcement outcome=insufficient_evidence applied=True` — which
  rebuilt the answer from scratch,

and the 426 characters the operator received contained none of the first two.
They were not overruled. They were overwritten, because they were bytes in the
same variable as the claims.

The fix is to stop pretending those are the same thing. A response has two
kinds of content, and only one of them is a claim:

``body``
    The claim-bearing text. Deciders may rewrite it wholesale — that is what
    low-evidence truncation legitimately does, and what it is *for*.

``notice``
    Something a decider says *about* the answer: a clarifying question, a
    caveat, a scope note. It is not a claim, it is never evidence, and no
    verdict about the claims can make it untrue. Truncating the claims must
    leave it standing.

Three notice channels, matching the places the loop puts them:

``prepend``          rendered above the body, in registration order, so the
                     first-registered ends up closest to the body — which
                     reproduces the existing nesting exactly.
``unverified_note``  merged into the Output Contract's ``Unverified:`` section
                     of whatever body survives, via the same helper
                     ``core.output_policy`` has always used.
``append``           rendered below the body (MIR-069: the five-point
                     verification tail). Below, because it is a statement
                     about the claims and must read after them — and in this
                     ledger, because a body rewrite must not delete it.

Composition happens once, at the end, in :meth:`ResponseDraft.render`. That is
the single arbitration point the cycle did not have: before, "who wins" was
decided by the order of statements in a 4000-line function; now it is decided by
what kind of content each decider produced.

The draft also keeps the ledger. :meth:`ResponseDraft.to_log_payload` reports
every contribution and whether it survived into the rendered text, so a
contribution that goes missing is visible in the journal instead of having to be
found by reading the code — which is how this defect was found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.output_policy import _merge_unverified

Channel = Literal["body", "prepend", "unverified_note", "append"]

#: Channels that carry something *about* the answer rather than a claim in it.
NOTICE_CHANNELS: frozenset[str] = frozenset({"prepend", "unverified_note", "append"})


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

    # ── contributing ─────────────────────────────────────────────────────

    def add_notice(self, *, author: str, channel: Channel, text: str) -> bool:
        """Attach something the deciders say *about* the answer.

        Returns whether it was attached. A blank notice, or one already present
        verbatim in the body, is skipped — both are the checks the call sites
        performed inline before this object existed.
        """
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
        """Rewrite the claims. Records who superseded whom.

        Rewriting is a legitimate act — it is what truncation does. What was
        not legitimate was doing it to a variable that also held other
        deciders' notices.
        """
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
                # Prepending in registration order leaves the first-registered
                # notice closest to the body, which is the nesting the loop
                # produced when each call site prepended in turn.
                text = f"{notice.text}\n\n{text}"
        return text

    # ── auditing ─────────────────────────────────────────────────────────

    def missing_from(self, rendered: str) -> list[Contribution]:
        """Notices whose text did not make it into *rendered*.

        The invariant this object exists to hold: a decider that contributed
        something either sees it in the answer or sees itself recorded as
        superseded. Anything this returns is a contribution that vanished with
        neither, which is a defect by construction rather than by opinion.
        """
        return [n for n in self.notices if n.text not in rendered]

    def to_log_payload(self, rendered: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "body_author": self.body_author,
            "body_chars": len(self.body),
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
