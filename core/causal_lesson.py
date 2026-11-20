"""Causal lesson ladder: states from observed deviation to learned rule, advanced by evidence, never by author identity.

States: OBSERVED -> EXPLAINED (>=2 competing explanations) -> ATTRIBUTED (proven by intervention) -> GENERALIZED (checked on a case other than the origin) -> LESSON; REFUTED is terminal.
Invariants: no skipped states; the author is in no promotion condition; generalization evidence must differ from the origin case; `retrieval_eligible` is computed, not stored; refutation is terminal. The machine produces only OBSERVED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CausalState = Literal[
    "OBSERVED", "EXPLAINED", "ATTRIBUTED", "GENERALIZED", "LESSON", "REFUTED"
]

#: Possible authors of hypotheses. The list exists for PROVENANCE and measuring author accuracy and deliberately takes no part in promotion.
CausalAuthor = Literal["operator", "agent", "external_engineer"]

_ORDER: tuple[str, ...] = ("OBSERVED", "EXPLAINED", "ATTRIBUTED", "GENERALIZED", "LESSON")


@dataclass(frozen=True)
class Observation:
    """A noticed deviation; the only thing the machine produces by itself."""

    episode_id: str
    trace_id: str
    run_id: str
    defect_signals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observed_mismatch: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id, "trace_id": self.trace_id,
            "run_id": self.run_id, "defect_signals": list(self.defect_signals),
            "evidence_refs": list(self.evidence_refs),
            "observed_mismatch": self.observed_mismatch,
        }


@dataclass(frozen=True)
class Explanation:
    """One of the competing explanations of a deviation."""

    statement: str
    author: CausalAuthor
    #: What would be observed if it were true, and what would not be observed otherwise.
    predicts: str = ""
    refuted_by: str = ""

    @property
    def alive(self) -> bool:
        return not self.refuted_by.strip()


@dataclass(frozen=True)
class Intervention:
    """Cause shown by intervention, not coincidence."""

    mutated: str
    predicted: str
    observed: str
    restored: bool = False

    @property
    def proves_cause(self) -> bool:
        return bool(
            self.mutated.strip() and self.predicted.strip() and self.observed.strip()
        )


@dataclass(frozen=True)
class GeneralizationTest:
    """Check of the rule on a case other than the origin."""

    case_ref: str
    origin_ref: str
    held: bool = False

    @property
    def independent(self) -> bool:
        """The evidence must NOT be the originating case."""
        a, b = self.case_ref.strip(), self.origin_ref.strip()
        return bool(a) and bool(b) and a != b


@dataclass(frozen=True)
class CausalClaim:
    """A hypothesis about a cause with everything brought for it so far."""

    observation: Observation
    explanations: tuple[Explanation, ...] = ()
    chosen: str = ""
    violated_invariant: str = ""
    intervention: Intervention | None = None
    generalized_rule: str = ""
    scope: str = ""
    falsifiers: tuple[str, ...] = ()
    generalization: GeneralizationTest | None = None
    refuted_reason: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            **self.observation.to_log_payload(),
            "state": state_of(self),
            "explanations": [
                {"statement": e.statement, "author": e.author, "alive": e.alive}
                for e in self.explanations
            ],
            "chosen": self.chosen,
            "violated_invariant": self.violated_invariant,
            "intervention_proves_cause": bool(
                self.intervention and self.intervention.proves_cause
            ),
            "generalized_rule": self.generalized_rule,
            "generalization_independent": bool(
                self.generalization and self.generalization.independent
            ),
            "generalization_held": bool(
                self.generalization and self.generalization.held
            ),
            "retrieval_eligible": state_of(self) == "LESSON",
        }


def blocking_reason(claim: CausalClaim) -> str:
    """What the NEXT state still lacks, as one named reason."""
    if claim.refuted_reason.strip():
        return ""
    alive = [e for e in claim.explanations if e.alive]
    gen = claim.generalization
    checks: tuple[tuple[bool, str], ...] = (
        (not claim.observation.evidence_refs,
         "нет улик наблюдения"),
        (len(claim.explanations) < 2,
         "нужны конкурирующие объяснения, минимум два"),
        (not claim.chosen.strip(),
         "не выбрано объяснение"),
        (len(alive) > 1,
         "соперники не разобраны: живых объяснений больше одного"),
        (not claim.violated_invariant.strip(),
         "не назван нарушенный инвариант"),
        (claim.intervention is None or not claim.intervention.proves_cause,
         "причина не доказана вмешательством"),
        (not claim.generalized_rule.strip(),
         "не сформулировано обобщаемое правило"),
        (gen is None,
         "правило не проверено на новом случае"),
        (gen is not None and not gen.independent,
         "проверка обобщения идёт по исходному случаю"),
        (gen is not None and not gen.held,
         "правило не выдержало проверку на новом случае"),
        # Scope is the LAST rung: it can be named honestly only after the rule held elsewhere.
        (not claim.scope.strip(),
         "не названа область применимости правила"),
    )
    for failed, reason in checks:
        if failed:
            return reason
    return ""


def state_of(claim: CausalClaim) -> CausalState:
    """State is a function of what was brought, not of who brought it."""
    if claim.refuted_reason.strip():
        return "REFUTED"
    reason = blocking_reason(claim)
    if not reason:
        return "LESSON"
    if reason.startswith("не названа область"):
        return "GENERALIZED"
    if reason.startswith(("не сформулировано", "правило не проверено",
                          "проверка обобщения", "правило не выдержало")):
        return "ATTRIBUTED"
    if reason.startswith(("не назван", "причина не доказана")):
        return "EXPLAINED"
    return "OBSERVED"


def is_lesson(claim: CausalClaim) -> bool:
    """Whether the claim has earned the right to influence future plans."""
    return state_of(claim) == "LESSON"



def proven_cases(claim: CausalClaim) -> tuple[str, ...]:
    """Cases on which the rule is shown; the rest is transfer."""
    if claim.refuted_reason.strip():
        return ()
    gen = claim.generalization
    if gen is None:
        return ()
    return tuple(dict.fromkeys(
        ref for ref in (gen.origin_ref.strip(), gen.case_ref.strip()) if ref
    ))


def applies_to(claim: CausalClaim, *, case_ref: str) -> bool:
    """Whether the lesson has been checked on THIS case."""
    if not is_lesson(claim):
        return False
    return case_ref.strip() in proven_cases(claim)


def claim_tags(claim: CausalClaim) -> tuple[str, ...]:
    """`lesson` appears ONLY in the final state; it is the sole bypass of the quarantine."""
    state = state_of(claim)
    if state == "LESSON":
        return ("lesson", "causal", "generalization-tested")
    return (f"causal:{state.lower()}", "unverified")


#: Bridge from a stop to the bottom rung of the causal ladder.
#: 
#: Separate from `record_self_stop`: that narrow organ writes exactly one journal by contract and must not get a second address. The observation is only ASSEMBLED here; the stream's existing owner, `CausalObservationStore`, writes it.
_STOP_SIGNAL_PREFIX = "self_stop"


def observation_from_self_stop(
    *, kind: str, reason: str, signature: str, source: str,
    run_id: str = "", trace_id: str = "", episode_id: str = "",
) -> Observation | None:
    """Observation about the agent's own stop, or None when there is no cause.

    Signals are built from the STABLE parts of the wall (kind and canonical reason) so the same wall accumulates a repeat counter instead of new records.
    """
    kind = (kind or "").strip()
    reason = (reason or "").strip()
    source = (source or "").strip()
    if not kind or not reason or not source:
        return None
    return Observation(
        episode_id=episode_id,
        trace_id=trace_id,
        run_id=run_id,
        defect_signals=(f"{_STOP_SIGNAL_PREFIX}:{kind}", f"wall:{reason}"),
        evidence_refs=(source, f"signature:{signature}" if signature else source),
        observed_mismatch=(
            f"A self-launched run produced no work: {kind} at wall '{reason}'. "
            f"The run intended to work and stopped instead; the primary event "
            f"is recorded in {source}."
        ),
    )


def trace_log_path(trace_id: str) -> str:
    """Trace log path from a trace id; the id already starts with "trace_", so do not prefix it again."""
    name = trace_id if trace_id.startswith("trace_") else f"trace_{trace_id}"
    return f"logs/{name}.jsonl"


def observation_from_episode(episode: Any, *, trace_id: str) -> Observation | None:
    """Deviation from an episode, built only from derivable parts; `None` when the detectors were silent."""
    signals = tuple(getattr(episode, "defect_signals", None) or ())
    if not signals:
        return None
    completion = getattr(episode, "completion_state", None) or "unknown"
    return Observation(
        episode_id=str(getattr(episode, "id", "") or ""),
        trace_id=trace_id,
        run_id=str(getattr(episode, "run_id", "") or ""),
        defect_signals=signals,
        evidence_refs=tuple(getattr(episode, "source_labels", None) or ()),
        # The signals' address lives in the observation itself, so the agent does not guess a data file.
        observed_mismatch=(
            f"детекторы {', '.join(signals)} при завершении {completion}; "
            "объяснения не выдвинуты, причина не доказана. Сигналы записаны в "
            "data/episodic_memory.jsonl (payload.defect_signals) у эпизода "
            f"{getattr(episode, 'id', '') or ''!s}; событие детектора — в "
            f"{trace_log_path(trace_id)}"
        ),
    )
