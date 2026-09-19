"""The claim gate's protocol: where a verdict came from, and what it means.

Two failures found by mutation are made durable here.

PROVENANCE. `gate` used to read a subprocess exit code as if it necessarily came
from the verdict protocol. A missing validator, a crashing one, a malformed one and
one exiting 1 in silence all reported UNAVAILABLE -- "the certificate refused" said
where no verdict was produced at all.

SEMANTICS. Every declared verdict now maps explicitly. Two of them used to fall
through to UNAVAILABLE: PRECEDENCE_VIOLATED says the certificate's own dependency
model was contradicted, and UNREADABLE says no verdict was formed. Neither is a
refusal, so neither may look like one.

The tests drive the real `gate` against a FAKE validator in a temporary root, so
nothing in the repository is mutated to make them run.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "qm_gate_claim.py"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("qm_gate_claim_under_test", _GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sandbox(tmp_path: Path, validator_source: str | None) -> tuple[object, Path]:
    """A temporary ROOT with a fake validator, plus a certificate that resolves."""
    module = _load_gate_module()
    module.ROOT = tmp_path
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    if validator_source is not None:
        (tmp_path / "scripts" / "qm_claim_check.py").write_text(
            validator_source, encoding="utf-8")
    certificate = tmp_path / "x.claim.qm"
    certificate.write_text(json.dumps({"claim_id": "CX"}), encoding="utf-8")
    return module, certificate


_EMITS = (
    "import sys\n"
    "print('QM-VERDICT: {verdict} exit={code}')\n"
    "sys.exit({code})\n"
)


@pytest.mark.parametrize(
    "label,source",
    [
        ("validator missing", None),
        ("validator crashes", "raise RuntimeError('boom')\n"),
        ("validator malformed", "def (:\n"),
        ("validator emits no protocol verdict", "import sys\nsys.exit(1)\n"),
        ("verdict disagrees with exit code",
         "import sys\nprint('QM-VERDICT: VALID exit=0')\nsys.exit(1)\n"),
        ("unknown verdict declared",
         "import sys\nprint('QM-VERDICT: SPLENDID exit=0')\nsys.exit(0)\n"),
    ],
)
def test_a_verdict_that_was_never_produced_is_never_a_refusal(
    tmp_path: Path, label: str, source: str | None
) -> None:
    module, certificate = _sandbox(tmp_path, source)
    status, detail = module.gate(certificate, "CX")
    assert status == "UNRESOLVABLE", f"{label} produced {status}: {detail}"


@pytest.mark.parametrize(
    "verdict,code,expected",
    [
        ("VALID", 0, "USABLE"),
        ("INVALID", 1, "UNAVAILABLE"),
        ("OUT_OF_DOMAIN", 2, "UNAVAILABLE"),
        ("PRECEDENCE_VIOLATED", 3, "UNRESOLVABLE"),
        ("UNREADABLE", 4, "UNRESOLVABLE"),
    ],
)
def test_each_declared_verdict_maps_where_it_belongs(
    tmp_path: Path, verdict: str, code: int, expected: str
) -> None:
    module, certificate = _sandbox(
        tmp_path, _EMITS.format(verdict=verdict, code=code))
    status, detail = module.gate(certificate, "CX")
    assert status == expected, f"{verdict} produced {status}: {detail}"


def test_the_two_unsound_verdicts_are_not_refusals(tmp_path: Path) -> None:
    """Stated separately because collapsing these was the original defect."""
    for verdict, code in (("PRECEDENCE_VIOLATED", 3), ("UNREADABLE", 4)):
        module, certificate = _sandbox(
            tmp_path, _EMITS.format(verdict=verdict, code=code))
        status, _ = module.gate(certificate, "CX")
        assert status != "UNAVAILABLE", (
            f"{verdict} means no trustworthy verdict exists; UNAVAILABLE would "
            "present a broken mechanism as a decision"
        )


def test_a_mismatched_claim_id_is_unresolvable_before_any_validator_runs(
    tmp_path: Path,
) -> None:
    module, certificate = _sandbox(tmp_path, _EMITS.format(verdict="VALID", code=0))
    status, detail = module.gate(certificate, "C-OTHER")
    assert status == "UNRESOLVABLE" and "C-OTHER" in detail
