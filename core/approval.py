"""Approval providers: ask a human to authorise escalated actions and return a typed ApprovalDecision."""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Literal, TextIO

from core.models import ApprovalDecision, ApprovalRequest

# Accepted user strings (case-insensitive); anything else aborts.
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
    """Render the arguments and report what the rendering hid; returns (preview, notice)."""
    full = str(arguments)
    if len(full) <= _PREVIEW_LIMIT:
        return full, ""
    preview = full[: _PREVIEW_LIMIT - 1] + "…"
    hidden = len(full) - (_PREVIEW_LIMIT - 1)
    # A key counts as lost when its NAME is missing from the visible text: the reader cannot tell the argument was passed.
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
    """Synchronous stdin/stderr prompt; blocks until the user answers. `input_fn` and `out` are injectable for tests."""

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
                # Own prefixed line, so the operator reads it as the renderer speaking, not as part of the request.
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
    """Deterministic provider for tests and `--auto-approve` runs; logs every request on `self.calls`."""

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
