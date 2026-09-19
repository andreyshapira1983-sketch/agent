"""Two runs of one tool are one source — a citation must not land on the wrong run.

Drive run 2026-09-19 (Tong, inflation): python_probe ran twice. Both outputs sit
in the chain under the same source_id `tool_output:python_probe`; the answer's
`[tool:python_probe]` resolved to the FIRST run, which never printed
`1.1420073898156842e+26`, and the true line «a_f = e^60 = 1.142e+26» was stamped
[claim-refuted]. That evening 12 of 15 refutations were this gate on tool
outputs (python_probe, find_in_files). The citation names the source; every run
of that source in the turn is that source. Other sources still do not count.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _probe(excerpt: str):
    return make_evidence(kind="tool_output", source_id="tool_output:python_probe", obtained_via="python_probe",
                         claim="Measured outcome of a live experiment", excerpt=excerpt, confidence=0.7)


def _answer(fact: str) -> str:
    return ("Conclusion: N = 60. [tool:python_probe]\nFacts:\n- " + fact + " [tool:python_probe]\n"
            "Sources:\n1. tool:python_probe - run\nConfidence: high\nUnverified: nothing\n")


def _report(fact: str, *excerpts: str):
    chain = ProvenanceChain()
    for text in excerpts:
        chain.add(_probe(text))
    return verify(answer=_answer(fact), chain=chain, user_question="посчитай число e-фолдов")


FIRST = "N = 60.0\nN == 60: True\n"
SECOND = "a_i = 1.0\na_f = e^60 = 1.1420073898156842e+26\nN = ln(a_f/a_i) = 60.0\nN == 60: True\n"


def test_the_second_run_supports_its_own_numbers() -> None:
    report = _report("Расчёт: a_f = e^60 = 1.1420073898156842e+26, N = 60.0", FIRST, SECOND)
    assert report.refuted_chunks == 0, [(c.verdict, getattr(c.reason, "expected", None)) for c in report.chunks]


def test_a_number_no_run_printed_is_still_refuted() -> None:
    report = _report("Расчёт: a_f = e^60 = 9.9990073898156842e+26, N = 60.0", FIRST, SECOND)
    assert report.refuted_chunks == 1


def test_the_place_searched_is_not_the_thing_missing() -> None:
    """Same run: «Поиск `N = ln` по файлу Tong_Cosmology.txt: совпадений нет» got
    absence_refuted_by_evidence — the search output names the file it searched."""
    from core.verifier_absence import absence_subjects

    assert absence_subjects("Поиск `N = ln` по файлу Tong_Cosmology.txt: совпадений нет") == set()
    assert "data_v2" in absence_subjects("Файла data_v2.csv нет в workspace")
