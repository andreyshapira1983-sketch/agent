"""Approval Providers (§7 Security, Policy & Autonomy Governance — Human
Approval).

PolicyGate decides whether an action carries enough risk that a human must
authorise it (`escalate`). The Approval Provider is the surface that asks
the human and brings back a typed `ApprovalDecision`.

The loop is provider-agnostic: it never reads stdin itself, it never times
out itself. All policy-on-human surface area lives here.
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Literal, TextIO

from core.models import ApprovalDecision, ApprovalRequest

# Strings accepted from the user (case-insensitive). Anything else => abort.
_YES_TOKENS: frozenset[str] = frozenset({"y", "yes", "д", "да", "approve", "ok", "okay"})
_NO_TOKENS: frozenset[str] = frozenset({"n", "no", "н", "нет", "deny", "cancel"})


def _classify(raw: str | None) -> tuple[Literal["approve", "deny", "abort"], str]:
    """Map raw human input to a typed decision + a one-line reason."""
    if raw is None:
        return "abort", "no input (EOF / timeout)"
    stripped = raw.strip().lower()
    if not stripped:
        return "abort", "empty input"
    if stripped in _YES_TOKENS:
        return "approve", f"input='{stripped}'"
    if stripped in _NO_TOKENS:
        return "deny", f"input='{stripped}'"
    return "abort", f"unrecognised input='{stripped}'"


#: How much of the arguments the operator sees before the preview truncates.
_PREVIEW_LIMIT = 200


def _preview_arguments(arguments: dict) -> tuple[str, str]:
    """Render the arguments, and say plainly what the rendering hid.

    Measured 2026-08-22 on a plausible `file_write`: 397 characters of
    arguments, 199 shown, and `mode: overwrite` gone entirely — an operator
    could approve an overwrite with the word never on screen. An ellipsis says
    "something is missing"; it does not say "half of it, including the mode".

    Returns `(preview, notice)`. The notice is empty when nothing was hidden,
    because a preview that always warns teaches the reader to ignore it.

    Deliberately NOT done here: reordering or ranking the arguments by
    importance. Which argument matters most is a judgement, and inventing one
    would place a developer's opinion between the agent and the human at
    exactly the boundary this project is auditing.
    """
    full = str(arguments)
    if len(full) <= _PREVIEW_LIMIT:
        return full, ""
    preview = full[: _PREVIEW_LIMIT - 1] + "…"
    hidden = len(full) - (_PREVIEW_LIMIT - 1)
    # A key counts as lost when its NAME is absent from the visible text: the
    # reader has no way to know the argument was passed at all.
    lost = [str(key) for key in arguments if str(key) not in preview]
    notice = f"{hidden} characters hidden"
    if lost:
        notice += "; arguments not shown at all: " + ", ".join(lost)
    return preview, notice


class ApprovalProvider(ABC):
    """Anything that can answer an ApprovalRequest."""

    @abstractmethod
    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        raise NotImplementedError


class CLIApprovalProvider(ApprovalProvider):
    """Synchronous stdin/stderr prompt. Blocks until the user answers.

    `input_fn` is injectable so tests can drive the prompt without touching
    the real stdin; `out` is the stream the human-readable prompt is written
    to (defaults to stderr so it never interferes with --ask output).
    """

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        out: TextIO | None = None,
    ):
        self._input = input_fn
        self._out = out or sys.stderr

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        self._render(req)
        try:
            raw = self._input("approve? [y/n] ")
        except (EOFError, KeyboardInterrupt):
            raw = None
        decision, reason = _classify(raw)
        return ApprovalDecision(
            request_id=req.id,
            decision=decision,
            responder="user" if decision != "abort" else "timeout",
            reasons=[reason],
            raw_input=raw,
        )

    def _render(self, req: ApprovalRequest) -> None:
        print(file=self._out)
        print("=== APPROVAL REQUIRED ===", file=self._out)
        print(f"  request_id : {req.id}", file=self._out)
        print(f"  tool       : {req.tool_name}", file=self._out)
        print(f"  risk       : {req.risk}", file=self._out)
        if req.arguments:
            preview, notice = _preview_arguments(req.arguments)
            print(f"  arguments  : {preview}", file=self._out)
            if notice:
                # On its own line and prefixed, so the operator reads it as the
                # renderer speaking rather than as more of the request.
                print(f"  ! preview  : {notice}", file=self._out)
        if req.reasons:
            print(f"  reasons    : {'; '.join(req.reasons)}", file=self._out)
        if req.summary:
            print(f"  summary    : {req.summary}", file=self._out)
        print(
            "  y/yes/да → approve   |   n/no/нет → deny   |   anything else → abort",
            file=self._out,
        )


class AutoApprover(ApprovalProvider):
    """Deterministic provider for tests and `--auto-approve` smoke runs.

    Pass `default` to choose the verdict (approve / deny / abort). Every
    request is logged on `self.calls` so tests can assert what was asked.
    """

    def __init__(
        self,
        default: Literal["approve", "deny", "abort"] = "approve",
        responder: str = "auto",
        reason: str = "auto-approval policy",
    ):
        self.default = default
        self.responder = responder
        self.reason = reason
        self.calls: list[ApprovalRequest] = []

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        self.calls.append(req)
        return ApprovalDecision(
            request_id=req.id,
            decision=self.default,
            responder=self.responder,
            reasons=[self.reason],
        )
