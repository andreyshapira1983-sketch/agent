"""What the operator reads before authorising must admit what it is hiding.

The mutation sweep found the truncation logic in `CLIApprovalProvider._render`
unwitnessed: breaking it left the whole suite green. It was rated cosmetic at the
time. The OWASP 2026 agentic list rates the same surface ASI09 —
Human-Agent Trust Exploitation, "agents whose outputs manipulate humans into
unsafe actions, where approval steps target human judgement" — and the Replit
incident of July 2025 is the concrete version: an agent that fabricated data and
falsely claimed rollback was impossible.

Measured before writing this, on a plausible `file_write` request:

    full arguments      397 characters
    shown to the human  199, then an ellipsis
    hidden              198 characters, contents unstated
    the `mode` key      absent from the preview entirely

So an operator could approve an overwrite while the word "overwrite" never
appeared on screen. That is not cosmetic; it is the gate's only human-facing
surface lying by omission.

What this fixes and what it deliberately does not. It makes the preview HONEST
about its own truncation — how much was cut and which argument keys were lost —
so a reader can tell they are not seeing everything. It does NOT reorder or
rank arguments by "importance": deciding which argument matters most is a
judgement, and inventing one here would put a developer's opinion between the
agent and the human at exactly the boundary this project is auditing.
"""
from __future__ import annotations

import io

from core.approval import CLIApprovalProvider
from core.models import ApprovalRequest


def _render(arguments: dict) -> str:
    """Render one request and return what the operator would see."""
    out = io.StringIO()
    provider = CLIApprovalProvider(input_fn=lambda _prompt: "n", out=out)
    req = ApprovalRequest(
        action_id="act_probe", step_id="step_probe",
        tool_name="file_write", risk="irreversible",
        arguments=arguments, reasons=["probe"], summary="probe",
    )
    provider.request(req)
    return out.getvalue()


def test_a_short_request_is_shown_whole_and_says_nothing_about_hiding() -> None:
    """The control: no truncation, therefore no truncation notice. A preview
    that always warns is as useless as one that never does."""
    text = _render({"path": "a.md", "mode": "new"})
    assert "a.md" in text and "new" in text
    assert "hidden" not in text.lower()


def test_a_truncated_preview_says_how_much_it_hid() -> None:
    """An ellipsis alone tells the reader that something is missing, not that
    half the request is missing."""
    long_content = "x" * 800
    text = _render({"path": "a.md", "content": long_content, "mode": "overwrite"})
    assert "…" in text or "..." in text
    assert "hidden" in text.lower(), (
        "the preview truncated silently — the reader cannot tell how much of "
        "the request they are approving unseen"
    )


def test_an_argument_key_lost_to_truncation_is_named() -> None:
    """The measured failure: `mode: overwrite` vanished entirely from a preview
    of an overwrite. A key that falls off the end must still be named, because
    its ABSENCE from the visible text reads as its absence from the request."""
    text = _render({"path": "a.md", "content": "x" * 800, "mode": "overwrite"})
    assert "mode" in text, (
        "an argument key was cut away without a trace — the operator would "
        "approve an overwrite with the word 'overwrite' never on screen"
    )


def test_the_notice_names_every_lost_key_not_just_the_first() -> None:
    text = _render({
        "path": "a.md", "content": "x" * 800,
        "mode": "overwrite", "encoding": "utf-8", "backup": False,
    })
    for key in ("mode", "encoding", "backup"):
        assert key in text, f"{key} was lost without being named"


def test_the_visible_part_is_still_the_beginning_of_the_arguments() -> None:
    """Honesty about truncation must not cost the preview its content: the
    reader still sees the request, just with an accurate footnote."""
    text = _render({"path": "very-specific-name.md", "content": "y" * 800})
    assert "very-specific-name.md" in text


def test_the_notice_cannot_be_confused_with_the_arguments_themselves() -> None:
    """A request whose own content contains the word 'hidden' must not make the
    preview look like it truncated when it did not — otherwise the signal is
    forgeable by the very text it describes."""
    text = _render({"path": "a.md", "note": "the word hidden appears here"})
    assert "characters hidden" not in text.lower(), (
        "a request forged the truncation notice with its own content"
    )
