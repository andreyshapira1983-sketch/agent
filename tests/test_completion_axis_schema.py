"""Task completion is a separate axis from evidence quality (MIR-057), schema.

The defect: `outcome` is derived from chunk counts alone, so a cycle that was
blocked — the evidence budget truncated the file it needed — banks `success`,
becomes `usage_eligible`, and credits a procedure. Verification measures
whether claims are grounded; it cannot measure whether the goal was reached.

This commit adds the axis and nothing else: two durable fields, an assembly
rule, serialization. No reader consults `completion_state` yet, so behaviour is
unchanged — the gates land in later commits.

Two properties are pinned here because they are the ones that cannot be fixed
later without rewriting history:

*   **The state is frozen at banking.** A state re-derived on every read would
    reclassify old episodes whenever the rule changes, and procedural feedback
    for those episodes has already been applied under the old rule.
*   **Legacy is never reconstructed.** A row written before this field simply
    has no key; it reads as `unknown` and no abort tag or `replan_exhausted`
    flag is retro-fitted into a verdict. `None` (never classified) stays
    distinguishable from an explicit state, exactly as `usage_eligible` does.
"""
from __future__ import annotations

from core.smart_memory import (
    assemble_completion_state,
)


def test_any_other_abort_is_failed() -> None:
    assert assemble_completion_state(
        aborted_reason="TypeError", replan_exhausted=False, declared=None
    ) == "failed"


def test_no_declaration_and_no_override_is_unknown() -> None:
    """The honest default: nothing observed, so nothing is claimed."""
    assert assemble_completion_state(
        aborted_reason="", replan_exhausted=False, declared=None
    ) == "unknown"
