"""The sieve learns the attribute subtype: obj.attr is verified like kwargs.

Measured 2026-08-17 (meas_8fd5b4e5, meas_be27ac23): Stage B twice produced
the identical invented attribute — `getattr(claim, "state", None)` on a
CausalClaim, whose state is COMPUTED by state_of() and exists on no field —
and the call-kwargs sieve could not see it. The phantom class recurred in
an attribute-access subtype; the lesson's scope was measurably narrower
than its instrument. Same law as the kwargs sieve: judge structure against
the RUNNING code, and doubt = silence — the sieve only subtracts garbage,
it never blocks on uncertainty.
"""
from __future__ import annotations

from core.attribute_sieve import phantom_attribute_reason

# Condensed verbatim from the twice-denied proposal (ain_af04f2d9 /
# ain_1bc4a0cd): the type of `claim` is only reachable through
# load_claims' return annotation.
_LIVE_SPECIMEN = '''
from core.causal_claim_store import load_claims

def pick(workspace):
    claims = load_claims(workspace)
    return [
        extra["key"]
        for claim, extra in claims
        if getattr(claim, "state", None) == "LESSON"
    ]
'''


def test_the_live_specimen_is_caught() -> None:
    reason = phantom_attribute_reason(_LIVE_SPECIMEN)
    assert reason is not None
    assert "state" in reason
    assert "CausalClaim" in reason


def test_the_honest_form_passes() -> None:
    code = '''
from core.causal_claim_store import load_claims
from core.causal_lesson import state_of

def pick(workspace):
    return [
        extra["key"]
        for claim, extra in load_claims(workspace)
        if state_of(claim) == "LESSON"
    ]
'''
    assert phantom_attribute_reason(code) is None


def test_a_real_field_on_a_constructed_object_passes() -> None:
    code = '''
from core.causal_lesson import Observation

def f():
    obs = Observation(episode_id="e", trace_id="t", run_id="r")
    return obs.episode_id
'''
    assert phantom_attribute_reason(code) is None


def test_an_invented_field_on_a_constructed_object_is_caught() -> None:
    code = '''
from core.causal_lesson import Observation

def f():
    obs = Observation(episode_id="e", trace_id="t", run_id="r")
    return obs.provenance_grade
'''
    reason = phantom_attribute_reason(code)
    assert reason is not None
    assert "provenance_grade" in reason


def test_methods_and_properties_are_not_phantoms() -> None:
    code = '''
from core.causal_lesson import Observation

def f():
    obs = Observation(episode_id="e", trace_id="t", run_id="r")
    return obs.to_log_payload()
'''
    assert phantom_attribute_reason(code) is None


def test_an_unknown_variable_is_silence() -> None:
    code = '''
def f(mystery):
    return mystery.whatever_field
'''
    assert phantom_attribute_reason(code) is None


def test_a_non_literal_getattr_is_silence() -> None:
    code = '''
from core.causal_lesson import Observation

def f(name):
    obs = Observation(episode_id="e", trace_id="t", run_id="r")
    return getattr(obs, name, None)
'''
    assert phantom_attribute_reason(code) is None


def test_an_unimportable_module_is_silence() -> None:
    code = '''
from nowhere.at.all import Ghost

def f():
    g = Ghost()
    return g.anything
'''
    assert phantom_attribute_reason(code) is None


def test_broken_code_is_silence_not_a_crash() -> None:
    assert phantom_attribute_reason("def broken(:") is None


# ── the sieve in both critics' hands, and in the measurement ledger ────────


def test_the_stage_a_critic_vetoes_and_measures_the_subtype(tmp_path) -> None:
    """A generated test carrying an invented FIELD is vetoed, and the
    critic-instrument signs a defect_recurred measurement for the lesson."""
    import json

    from tests.test_the_measurement_has_its_own_writer import (
        _measurements,
        _producer_run,
        _saved_lesson,
    )

    key = _saved_lesson(tmp_path)
    report = _producer_run(tmp_path, [
        "from core.sample_module import add",
        "from core.causal_lesson import Observation",
        "",
        "def test_add_reproduces_the_defect():",
        "    obs = Observation(episode_id='e', trace_id='t', run_id='r')",
        "    assert obs.ghost_field == 1",
        "    assert add(1, 2) == 4",
    ])
    assert report.status == "task_veto"
    assert any("ghost_field" in r for r in report.veto_reasons), report.veto_reasons
    rows = [m for m in _measurements(tmp_path) if m["lesson_key"] == key]
    assert rows and rows[0]["outcome"] == "defect_recurred"
    assert "ghost_field" in json.dumps(rows)


def test_the_stage_b_critic_vetoes_the_live_specimen() -> None:
    """The twice-denied generation would now die in the machine, not in my
    eyes: _critic_review names the phantom."""
    from core.self_build_producer import _critic_review

    out = _critic_review(
        "tools/lesson_provenance_tool.py",
        "def old():\n    return 1\n",
        {"content": _LIVE_SPECIMEN, "confidence": 0.95,
         "test_paths": ["tests/test_lesson_provenance_tool.py"]},
        confidence_threshold=0.6,
    )
    assert out.decision == "veto"
    reasons = list(out.data.get("veto_reasons", []))
    assert any("state" in r and "CausalClaim" in r for r in reasons), reasons
