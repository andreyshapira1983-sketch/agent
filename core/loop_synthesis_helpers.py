"""Helper functions used by `core.loop_synthesis.AgentLoopSynthesis`.

Split out to keep `core/loop_synthesis.py` small while preserving behavior.
"""

from __future__ import annotations

from typing import Any


def _organ_map(agent: object) -> dict[str, object]:
    """Органы, о наличии которых агент вправе сообщить как о факте."""
    names = (
        "memory", "persistent_store", "episodic_store", "procedural_store",
        "source_registry_store", "user_profile_store", "approval_provider",
    )
    out: dict[str, object] = {}
    for name in names:
        key = "working_memory" if name == "memory" else name
        out[key] = getattr(agent, name, None)
    return out


def _artifact_blocks(
    artifacts: dict[str, dict], *, question: str,
) -> list[tuple[str, str]]:
    """Render each artifact for the prompt, sparing the agent's own description."""
    from core.planner import LLMPlanner
    from core.tool_output_render import format_artifact

    self_doc = {p.rstrip("/") for p in LLMPlanner.DEFAULT_SELF_DOCUMENTATION_PATHS}
    blocks: list[tuple[str, str]] = []
    for label, art in artifacts.items():
        target = str(label).split(":", 1)[-1].strip()
        blocks.append(
            (
                label,
                format_artifact(
                    art["tool"],
                    art["output"],
                    question=question,
                    self_documentation=target in self_doc,
                ),
            )
        )
    return blocks


def _log_budget_trim(
    log: Any,
    *,
    trimmed_blocks: list[tuple[str, str]],
    was_trimmed: bool,
    memory_trimmed: bool,
    memory_payload: str,
    memory_label: str,
    long_term_block: str,
    memory_has_records: bool,
    surviving_memory_ids: set[str] | None,
    artifacts: dict[str, Any],
) -> None:
    """Journal what the total budget cut, and who disagreed about it."""
    from core.evidence_budget import total_trims

    if was_trimmed:
        _trims = total_trims(trimmed_blocks)
        log.log(
            "evidence_budget_trim",
            {
                "labels": [lbl for lbl, _ in trimmed_blocks],
                "total_chars": sum(len(c) for _, c in trimmed_blocks),
                "memory_trimmed": memory_trimmed,
                "memory_chars": len(memory_payload),
                "memory_chars_kept": (
                    len(long_term_block.strip()) if memory_has_records else 0
                ),
                "memory_ids_kept": sorted(surviving_memory_ids)
                if surviving_memory_ids is not None
                else None,
                "trims": [
                    {"label": lbl, "kept": kept, "original": orig}
                    for lbl, kept, orig in _trims
                ],
            },
        )
        try:
            from core.subsystem_disagreement import detect_budget_starvation

            for _ev in detect_budget_starvation(
                _trims,
                planned_labels=set(artifacts.keys()),
                memory_label=memory_label,
            ):
                log.log("subsystem_disagreement", _ev)
        except Exception as _sd_exc:  # noqa: BLE001 — the failure is recorded and logged
            try:
                log.log(
                    "subsystem_disagreement_error",
                    {
                        "error_type": type(_sd_exc).__name__,
                        "error": str(_sd_exc)[:300],
                    },
                )
            except Exception:  # noqa: BLE001, S110 — the logger must never break the answer path
                pass
