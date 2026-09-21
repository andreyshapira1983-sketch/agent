"""Integrity of the four entities the node/edge census cannot hold.

WHY A SEPARATE FILE FROM THE CENSUS. `tests/test_cns_census.py` guards what a
discovery rule finds — nodes and call edges. This guards what no rule finds by
walking calls: state carriers, signal flows, decision boundaries and the place
state is created. The operator's map names them as separate entities precisely
because a call edge cannot carry a claim about a shared mutable list, and a
node cannot carry a claim about a transition.

WHAT THIS PROVES. Only that the model's references still point at things that
exist and that its verdicts come from the agreed vocabulary. It does NOT prove
any claim inside the model — those are proven by the experiments named in
`docs/NERVE_PROTOCOL.ru.md`, one arc at a time, and recorded here afterwards.

WHAT IT CANNOT PROVE. A `file:line` anchor that drifted onto a different line
of the same file still passes: the check is that the line exists, not that it
still says what it said. Anchoring by content would redden on every unrelated
edit above it; that trade is deliberate and this is its cost.
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / "knowledge" / "maps" / "cns_model.json"
CENSUS = ROOT / "knowledge" / "maps" / "cns_census.json"

#: Verdicts the model may use, from docs/NERVE_PROTOCOL.ru.md. `UNDER_QUESTION`
#: exists so a disproven explanation can be marked without silently replacing
#: it with its rival — both stay visible until one is measured.
#:
#: `PARTIAL` is deliberately NOT here, and the story is worth keeping: it was
#: added for one revision on 2026-08-08 because a partial arc had written
#: `certified.verdict = "PARTIAL — ..."` and this ratchet reddened. Widening
#: the enum was the lazy fix — the operator refused it, and the file already
#: held the right shape in `prompt_to_model_family`: PARTIAL is an ARC STATUS
#: (`status`, from `_status_vocabulary.path_statuses`, which this ratchet does
#: not scan), while `certified.verdict` answers a different question — whether
#: the four conditions are all met. For a partial arc that answer is UNPROVEN.
#: Two vocabularies, two questions; collapsing them would have let "partly
#: proven" pass as a certification verdict.
VERDICTS = frozenset({"PROVEN", "UNPROVEN", "NEGATIVE", "UNDER_QUESTION"})

ENTITIES = ("state_carriers", "signal_flows", "decision_boundaries",
            "state_initialization")


def _model() -> dict:
    return json.loads(MODEL.read_text(encoding="utf-8"))


def _walk(obj, key: str):
    """Yield every value stored under `key`, at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)


def test_the_model_carries_all_four_entities() -> None:
    """A missing section is indistinguishable from 'nothing there'."""
    missing = [e for e in ENTITIES if e not in _model()]
    assert not missing, f"entities absent from the model: {missing}"


def test_every_referenced_node_exists_in_the_census() -> None:
    """The model may not name a node the census does not know."""
    known = set(json.loads(CENSUS.read_text(encoding="utf-8"))["nodes"])
    named = {n for n in _walk(_model(), "node") if isinstance(n, str)}
    named |= {n for n in _walk(_model(), "created_by") if isinstance(n, str)}
    unknown = sorted(named - known)
    assert not unknown, (
        f"nodes named by the model but absent from cns_census.json: {unknown}"
    )


def test_every_code_anchor_points_at_a_real_place() -> None:
    """`path:line` anchors must resolve — file present, line within it."""
    broken: list[str] = []
    anchors: list[str] = []
    for key in ("site", "created_at"):
        anchors += [a for a in _walk(_model(), key) if isinstance(a, str)]
    for anchor in anchors:
        head, _, tail = anchor.rpartition(":")
        if not head or not tail.isdigit():
            continue  # not a file:line anchor (e.g. a symbol reference)
        path = ROOT / head
        if not path.exists():
            broken.append(f"{anchor} (no such file)")
            continue
        total = len(path.read_text(encoding="utf-8").splitlines())
        if int(tail) > total:
            broken.append(f"{anchor} (file has {total} lines)")
    assert not broken, f"anchors that no longer resolve: {broken}"


def test_every_verdict_comes_from_the_protocol_vocabulary() -> None:
    """Ad-hoc statuses are how 'probably fine' re-enters through the back door."""
    bad: list[str] = []
    for section in ("properties", "certified"):
        for block in _walk(_model(), section):
            if not isinstance(block, dict):
                continue
            for prop, value in block.items():
                if not isinstance(value, str) or prop.endswith(("_note", "_evidence")):
                    continue
                first = value.split()[0].rstrip(",.") if value else ""
                if first not in VERDICTS and not value.startswith(("NOT CERTIFIED", "N/A", "probe", "two poles", "mutation")):
                    bad.append(f"{prop}={value!r}")
    assert not bad, (
        f"verdicts outside {sorted(VERDICTS)}: {bad}. Evidence goes in a "
        "`*_evidence` or `*_note` field; the verdict itself stays in the vocabulary."
    )


def test_the_acceptance_case_is_described_whole() -> None:
    """`failure_history` is the operator's test of this schema.

    If the schema cannot hold it without smearing facts across nodes, the
    schema is wrong — so the fields that make it whole are named here, and
    dropping any one of them fails rather than quietly shrinking the record.
    """
    fh = _model()["state_carriers"]["failure_history"]
    required = ("created_at", "transport", "mutation_mode", "writers",
                "readers", "lifetime", "owner", "properties", "semantic_defects")
    missing = [f for f in required if f not in fh]
    assert not missing, f"the acceptance case lost fields: {missing}"
    assert len(fh["writers"]) == 6, (
        "six write sites were found by reading the code (loop_attempt:481, "
        "loop_synthesis:609, :676, verify_replan:149, :255, :406); a different "
        "number means the line moved or a writer appeared — walk it, do not "
        "adjust the number"
    )
    # Шестой — `_read_what_it_left_unverified` (2026-09-21): черновик вынес в
    # «Не подтверждено» файлы своей папки, они дочитаны, и триггер
    # `unverified_own_file` ведёт одну пересборку. Пройдено по коду.
    # Пройдено ногами 2026-09-20, а не подогнано. Пятый писатель —
    # `loop_synthesis.py::AgentLoopSynthesis._rewrite_if_off_topic`: нерв
    # вшит коммитом c0eaf7b в тот же день и в карту тогда не внесён. Сайты
    # прочих четырёх тоже уехали (:472→:481, :228→:255, :379→:406) — здесь
    # они названы заново, чтобы сообщение об ошибке не врало следующему.
    assert fh["owner"] == "UNPROVEN", (
        "ownership was never proven; an assignment site is not an owner"
    )
