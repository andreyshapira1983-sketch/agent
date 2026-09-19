"""A self-build lesson records where its content came from — MIR-121's write side.

WHY THIS EXISTS. MIR-121 asked whether web-derived text can reach a
`lesson`-tagged episode, and the trace came back UNANSWERABLE: all 127
lesson-tagged episodes carried no `source_labels` at all — the self-build
writer recorded no provenance, so «no web-derived lesson was found» was a
statement about the instrument, not the store. The guard built against
poisoned lessons (`_lesson_provenance_disqualified`, refusing `memory:`
labels) was filtering a label the writer never wrote.

THE RULE. The producer records what it actually read — the target file, and
whether recalled memory lessons were injected into the Builder prompt — and
the episode writer carries those labels into the record, the way chat and
campaign episodes already do. From now on, an absent label is evidence of
absence, because the writer provably writes them.

THE `memory:` LABEL IS THE POINT. Injected lessons come FROM episodic memory,
so a lesson minted from a run that consumed lessons is memory-derived content
— exactly the laundering channel MIR-121's attack class uses. Labelling it
`memory:self-build-lessons` is what lets `_lesson_provenance_disqualified`
finally bite on the population it was written for.
"""
from __future__ import annotations

from core.self_build_memory import build_self_build_episode


def test_a_produced_lesson_names_the_file_it_read() -> None:
    ep = build_self_build_episode("self-build-produce", {
        "status": "proposed", "reason": "split", "target_path": "core/x.py",
        "sources": ["file:core/x.py"],
    })
    assert "file:core/x.py" in (ep.source_labels or ()), (
        "the lesson does not say which file the run read — the MIR-121 trace "
        "stays unanswerable for every new episode too"
    )


def test_injected_memory_lessons_are_labelled_as_memory() -> None:
    """The laundering channel, made visible: content that flowed in from
    episodic memory is declared, so the provenance guard can bite."""
    ep = build_self_build_episode("self-build-produce", {
        "status": "critic_veto", "reason": "confidence 0.1",
        "target_path": "core/x.py",
        "sources": ["file:core/x.py", "memory:self-build-lessons"],
    })
    assert "memory:self-build-lessons" in (ep.source_labels or ())


def test_an_apply_episode_labels_its_changed_files() -> None:
    """The apply side has no `sources` from the producer; its reads ARE the
    files it changed, and they were already tagged — now they are labelled."""
    ep = build_self_build_episode("self-apply-run", {
        "status": "committed_local", "reason": "ok",
        "files_changed": ["core/a.py", "core/b.py"],
    })
    labels = set(ep.source_labels or ())
    assert "file:core/a.py" in labels and "file:core/b.py" in labels


def test_a_target_only_result_still_gets_a_file_label() -> None:
    """Older callers pass no `sources`; the target is still a read the writer
    can see, and one honest label beats none."""
    ep = build_self_build_episode("self-build-produce", {
        "status": "no_grounded_target", "reason": "too large",
        "target_path": "core/big.py",
    })
    assert "file:core/big.py" in (ep.source_labels or ())


def test_the_producer_reports_what_it_read() -> None:
    """The other half of the wire: ProducerReport carries `sources`, so the
    label chain starts where the reading happens rather than being invented
    at the writer."""
    from core.self_build_producer import ProducerReport

    report = ProducerReport(status="proposed", sources=["file:core/x.py"])
    assert report.to_dict()["sources"] == ["file:core/x.py"]
