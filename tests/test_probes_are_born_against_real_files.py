"""Probes must be born against files that exist, and the explainer must see them.

Measured 2026-08-30, reproduced 2/2: every autonomous campaign died the same
way. The explain rung invented journal names (`logs/loop_44.log`,
`logs/blocked_events.log` — none exist), the discriminate rung honestly said
"ignorance is not a verdict" three times, and the loop sensor stopped the
campaign. The lesson about remembered identifiers was IN the planner prompt,
receipts and all — advice does not change behaviour; gates do.

The design is the agent's (GUARD_DESIGN, chat round): drop a pair whose probe
targets a file that does not exist at claim birth, and let the EXISTING
two-survivors quality gate decline the claim; feed the explainer a live
inventory of logs/ and data/ so the model writes probes against reality.
Scenario names and fake-response scripts are his (delivery round 3); the
harness is courier work from the measured hand-run, after four rounds in which
every rewrite mutated a different fixture detail — harness files are past his
one-delivery ceiling, and that boundary is banked in the ledger.

Expected TODAY (gate and feed not yet built): tests 1 and 4 green, 2 and 3 red.
"""
from __future__ import annotations

from core.causal_claim_store import load_claims
from core.causal_climb_action import explain_causal_observation
from core.causal_lesson import Observation
from core.causal_store import CausalObservationStore


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeLog:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload):
        self.events.append((event, payload))


class _FakeAgent:
    def __init__(self, response: str):
        self.llm = _FakeLLM(response)
        self.log = _FakeLog()


def _seed_observation(tmp_path):
    store = CausalObservationStore(tmp_path / "data" / "causal_observations.jsonl")
    store.record(Observation(
        episode_id="ep1", trace_id="t1", run_id="r1",
        defect_signals=("sig_a",), evidence_refs=("ev1",),
        observed_mismatch="mismatch text",
    ))


def _write_log(tmp_path, name, content):
    p = tmp_path / "logs" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _pairs(first_probe: str, second_probe: str) -> str:
    second = f"в журнале видно Y {second_probe}" if second_probe else "в журнале видно Y"
    return (
        "ОБЪЯСНЕНИЕ 1: причина А\n"
        f"ПРЕДСКАЗАНИЕ 1: в журнале видно X {first_probe}\n"
        "ОБЪЯСНЕНИЕ 2: причина Б\n"
        f"ПРЕДСКАЗАНИЕ 2: {second}\n"
    )


def test_both_files_created_yields_one_claim_with_two_explanations(tmp_path):
    _seed_observation(tmp_path)
    _write_log(tmp_path, "real.log", "needle_a present")
    _write_log(tmp_path, "second.log", "needle_b present")
    agent = _FakeAgent(_pairs(
        "[probe: logs/real.log | needle_a | есть]",
        "[probe: logs/second.log | needle_b | есть]",
    ))

    explain_causal_observation(agent=agent, workspace=tmp_path)

    claims = load_claims(tmp_path)
    assert len(claims) == 1
    claim, _extra = claims[0]
    assert len(claim.explanations) == 2


def test_ghost_log_not_created_declines_claim(tmp_path):
    _seed_observation(tmp_path)
    _write_log(tmp_path, "real.log", "needle_a present")
    agent = _FakeAgent(_pairs(
        "[probe: logs/real.log | needle_a | есть]",
        "[probe: logs/ghost.log | needle_b | есть]",
    ))

    explain_causal_observation(agent=agent, workspace=tmp_path)

    assert len(load_claims(tmp_path)) == 0
    assert any(
        event == "causal_climb_declined" and "выжило" in str(payload)
        for event, payload in agent.log.events
    )


def test_real_journal_log_created_uses_it_in_first_call(tmp_path):
    _seed_observation(tmp_path)
    _write_log(tmp_path, "real_journal.log", "needle_a present")
    agent = _FakeAgent(_pairs(
        "[probe: logs/real_journal.log | needle_a | есть]",
        "[probe: logs/real_journal.log | needle_b | нет]",
    ))

    explain_causal_observation(agent=agent, workspace=tmp_path)

    assert "real_journal.log" in agent.llm.calls[0]["user"]


def test_pair_with_probe_and_pair_without_probe_yields_one_claim(tmp_path):
    _seed_observation(tmp_path)
    _write_log(tmp_path, "real.log", "needle_a present")
    agent = _FakeAgent(_pairs(
        "[probe: logs/real.log | needle_a | есть]",
        "",
    ))

    explain_causal_observation(agent=agent, workspace=tmp_path)

    claims = load_claims(tmp_path)
    assert len(claims) == 1
    claim, _extra = claims[0]
    assert len(claim.explanations) == 2
