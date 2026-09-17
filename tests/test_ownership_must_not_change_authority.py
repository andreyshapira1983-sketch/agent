"""Proof 8 of 8: changing ownership must not change effective authority.

The other seven proofs describe a mechanism that does not exist yet and are
banked. This one is different by design: it is GREEN today. It records what the
unattended organs are actually allowed to do, measured at the sites that refuse,
so that after any consolidation the same contract must still hold. If an organ
comes out of that work holding one right it did not hold before, this goes red.

Two premises were corrected by measurement before this test was written, and
both matter more than the assertions below.

FIRST: there are not four different organ envelopes. `agent_tick.py` builds the
queue drain (:946), the self-build producer (:684), hygiene (:1165) and the
campaign lane (:1352) with identical arguments —
`build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)`.
Probing all four returns the same 52 observables with zero differences. The
per-organ differences people assume exist are not in the construction; they are
made later by runtime mutation of the shared policy, planner and gateway
objects, which is exactly the mechanism that becomes unsafe once one host is
shared (MIR-114).

SECOND: the real envelope boundary today is unattended versus interactive, and
it is sharp — seven observables differ, every one of them a right.

So consolidation is cheaper than it looked in one way and more dangerous in
another: there is one envelope to preserve, not four, and the thing that must
never happen is an organ inheriting the interactive one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agent_tick
from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import build_agent
from core.approval import AutoApprover
from tests.support.authority_probe import probe_authority

#: The observables on which the two profiles genuinely differ. Measured, not
#: chosen: every other observable is equal between them, so a test that pinned
#: all 52 would fail on an unrelated change and teach nothing.
_AUTHORITY_AXES = (
    "effective_sinks",
    "permitted_sinks",
    "episodic_replay",
    "escalation_terminal",
    "escalating_probes",
    "knowledge_auto_write_effective",
    "knowledge_refusal_rule",
)


def _unattended(workspace: Path):
    return build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)


#: Every place in `agent_tick.py` that constructs an agent, found by parsing the
#: file rather than by listing line numbers that rot. The four are the queue
#: drain, the self-build producer, hygiene and the campaign lane.
_BUILDER_NAMES = {"build_agent", "_build_agent"}


def _production_build_sites() -> list[dict]:
    """Each real construction site with the keyword arguments it passes.

    This is the half that the behavioural probe cannot see. The probe answers
    "what does THIS envelope allow"; only reading the call sites answers
    "is this the envelope the organs are actually given".
    """
    tree = ast.parse((Path(__file__).resolve().parents[1] / "agent_tick.py")
                     .read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in _BUILDER_NAMES:
            continue
        keywords = {}
        unresolved = []
        for kw in node.keywords:
            expr = ast.unparse(kw.value)
            if kw.arg is None:
                keywords[f"**{expr}"] = expr
            elif isinstance(kw.value, ast.Constant):
                keywords[kw.arg] = kw.value.value
            else:
                unresolved.append(f"{kw.arg}={expr}")
        sites.append({"line": node.lineno, "keywords": keywords,
                      "unresolved": unresolved})
    return sorted(sites, key=lambda s: s["line"])


def _resolve_profile(expr: str, workspace: Path) -> dict:
    """Развернуть `**`-аргумент площадки сборки: имя или вызов на agent_tick.

    Форм ровно две и обе объявлены здесь нарочно: любая третья (выражение,
    которое этот свидетель не умеет исполнить) должна упасть, а не быть
    молча пропущена — иначе конверт полномочий перестанет проверяться.
    """
    if expr.endswith(")"):
        name, _, _ = expr.partition("(")
        return dict(getattr(agent_tick, name)(workspace))
    return dict(getattr(agent_tick, expr))


def test_all_four_production_organs_are_built_the_same_way() -> None:
    """The membership half: every organ is handed the same envelope.

    An earlier version of this test called one helper four times and labelled
    the results 'queue', 'self_build', 'hygiene', 'campaign'. It proved that
    four calls to one helper agree — which is true of any helper — and would
    have stayed green if a single call site had been changed. This one reads
    the call sites.
    """
    sites = _production_build_sites()
    assert len(sites) == 4, (
        f"agent_tick.py now constructs an agent in {len(sites)} places, not 4 "
        f"(lines {[s['line'] for s in sites]}). A new construction site is a "
        "new cognitive instance — decide deliberately whether it belongs."
    )
    for site in sites:
        assert not site["unresolved"], (
            f"the site at line {site['line']} passes {site['unresolved']}, which "
            "this witness cannot resolve — read it by hand rather than trusting "
            "a green run"
        )
    signatures = {frozenset(s["keywords"].items()) for s in sites}
    assert len(signatures) == 1, (
        "the four organs are no longer built alike: "
        + "; ".join(f"line {s['line']}: {sorted(s['keywords'])}" for s in sites)
    )


def test_what_those_sites_pass_yields_the_unattended_envelope(
    workspace: Path,
) -> None:
    """The behavioural half of the same link: whatever those sites pass must
    produce the envelope the contract below pins. Reading the arguments is not
    enough — a renamed profile constant with different contents would pass the
    membership test and fail this one.

    2026-09-17: the profile stopped being a bare constant and became
    `unattended_memory_profile(workspace)`, so that the burn-in sandbox can
    widen its OWN copy without touching the production envelope. This witness
    now resolves either form — a name or a one-argument call on `agent_tick` —
    and it got STRONGER, not weaker: it no longer reads a dict, it runs the
    very expression the production site runs and probes what comes out.
    """
    for site in _production_build_sites():
        kwargs = {}
        for key, value in site["keywords"].items():
            if key.startswith("**"):
                kwargs.update(_resolve_profile(key[2:], workspace))
            else:
                kwargs[key] = value
        observed = probe_authority(build_agent(workspace, **kwargs))
        assert observed["permitted_sinks"] == ("episode", "hygiene"), (
            f"the organ built at agent_tick.py:{site['line']} holds "
            f"{observed['permitted_sinks']} — not the unattended envelope"
        )
        assert observed["escalation_terminal"] == "refuse:no_provider", (
            f"the organ built at agent_tick.py:{site['line']} escalates to "
            f"{observed['escalation_terminal']} — it has acquired a human"
        )


def test_the_unattended_envelope_is_what_it_is(workspace: Path) -> None:
    """The contract a consolidation must reproduce exactly."""
    observed = probe_authority(_unattended(workspace))
    assert observed["effective_sinks"] == ("episode", "hygiene")
    assert observed["permitted_sinks"] == ("episode", "hygiene")
    assert observed["episodic_replay"] == "refuses_replay"
    assert observed["escalation_terminal"] == "refuse:no_provider"
    assert observed["knowledge_auto_write_effective"] is False


def test_the_unattended_envelope_is_narrower_than_the_interactive_one(
    workspace: Path,
) -> None:
    """The direction of the difference is the point. If an organ were ever run
    under an interactive host it would gain every right on this list, silently:
    six more durable sinks, a stored answer served in place of a real cycle, an
    approval provider that says yes, and automatic knowledge writes."""
    unattended = probe_authority(_unattended(workspace))
    interactive = probe_authority(
        build_agent(workspace, with_memory=True,
                    approval_provider=AutoApprover(default="approve"))
    )

    differing = sorted(k for k in unattended if interactive.get(k) != unattended.get(k))
    assert differing == sorted(_AUTHORITY_AXES), (
        f"the two profiles now differ on {differing}; the axes this test guards "
        f"are {sorted(_AUTHORITY_AXES)}. A new axis is not a failure — it is a "
        "right that nobody has pinned yet, so add it here deliberately."
    )

    assert set(unattended["permitted_sinks"]) < set(interactive["permitted_sinks"])
    assert unattended["episodic_replay"] == "refuses_replay"
    assert interactive["episodic_replay"] == "serves_stored_answer"
    assert unattended["escalation_terminal"].startswith("refuse")
    assert interactive["escalation_terminal"].startswith("ask")


@pytest.mark.parametrize("axis", _AUTHORITY_AXES)
def test_each_guarded_axis_is_actually_observable(workspace: Path, axis: str) -> None:
    """Boundary pin: a probe that silently stopped reporting an axis would make
    every assertion above vacuous."""
    assert axis in probe_authority(_unattended(workspace))
