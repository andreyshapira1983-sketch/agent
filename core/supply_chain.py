"""Release/supply-chain audit helpers.

This is a local, deterministic gate: it does not contact package indexes or
model providers. It checks whether the repository has the minimum mechanics
needed to rebuild from GitHub with controlled dependencies and CI release
checks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_REQ_NAME_RE = r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?"
_PINNED_RE = re.compile(rf"^\s*{_REQ_NAME_RE}==[A-Za-z0-9_.!+\-]+(?:\s*#.*)?$")
_UNPINNED_OPERATOR_RE = re.compile(r"(>=|<=|~=|!=|>|<)")


@dataclass(frozen=True)
class SupplyChainReport:
    workspace: str
    ok: bool
    requirements_file: str | None
    requirements_in_file: str | None
    ci_workflow: str | None
    pinned_requirements: tuple[str, ...] = ()
    unpinned_requirements: tuple[str, ...] = ()
    ci_gates: dict[str, bool] = field(default_factory=dict)
    missing_ci_gates: tuple[str, ...] = ()
    hash_checking_mode: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "ok": self.ok,
            "requirements_file": self.requirements_file,
            "requirements_in_file": self.requirements_in_file,
            "ci_workflow": self.ci_workflow,
            "pinned_requirements": list(self.pinned_requirements),
            "unpinned_requirements": list(self.unpinned_requirements),
            "ci_gates": dict(self.ci_gates),
            "missing_ci_gates": list(self.missing_ci_gates),
            "hash_checking_mode": self.hash_checking_mode,
            "warnings": list(self.warnings),
        }

    def user_summary(self) -> str:
        lines = [
            "=== supply-chain audit ===",
            f"ok: {self.ok}",
            f"requirements: {self.requirements_file or 'missing'}",
            f"requirements.in: {self.requirements_in_file or 'missing'}",
            f"ci_workflow: {self.ci_workflow or 'missing'}",
            f"hash_checking_mode: {self.hash_checking_mode}",
        ]
        lines.append(
            f"dependency pins: pinned={len(self.pinned_requirements)} "
            f"unpinned={len(self.unpinned_requirements)}"
        )
        if self.unpinned_requirements:
            lines.append("unpinned requirements:")
            lines.extend(f"  - {item}" for item in self.unpinned_requirements)
        if self.ci_gates:
            lines.append("ci gates:")
            for name, present in self.ci_gates.items():
                lines.append(f"  - {name}: {present}")
        if self.missing_ci_gates:
            lines.append("missing ci gates:")
            lines.extend(f"  - {gate}" for gate in self.missing_ci_gates)
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return "\n".join(lines)


def audit_supply_chain(workspace: Path) -> SupplyChainReport:
    root = workspace.resolve()
    requirements = root / "requirements.txt"
    requirements_in = root / "requirements.in"
    workflow = root / ".github" / "workflows" / "ci.yml"

    pinned, unpinned = _classify_requirements(requirements)
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.exists() else ""
    ci_gates = {
        "pip_install_requirements": "pip install -r requirements.txt" in workflow_text,
        "pip_check": "pip check" in workflow_text,
        "release_audit": "scripts/audit_release.py" in workflow_text,
        "pytest": "pytest" in workflow_text,
        "coverage_branch": "coverage run --branch" in workflow_text,
        "coverage_report": "coverage report" in workflow_text,
        "coverage_threshold": "--fail-under=85" in workflow_text,
    }
    missing_ci_gates = tuple(name for name, present in ci_gates.items() if not present)
    hash_checking = "--require-hashes" in workflow_text and "--hash=sha256:" in (
        requirements.read_text(encoding="utf-8") if requirements.exists() else ""
    )

    warnings: list[str] = []
    if not requirements.exists():
        warnings.append("requirements.txt is missing")
    if not requirements_in.exists():
        warnings.append("requirements.in is missing; direct dependency intent is not documented")
    if not workflow.exists():
        warnings.append("CI workflow is missing")
    if unpinned:
        warnings.append("requirements.txt contains unpinned/range dependencies")
    if not hash_checking:
        warnings.append(
            "hash-checking mode is not enabled yet; add generated hashes in a later hardening pass"
        )

    ok = (
        requirements.exists()
        and requirements_in.exists()
        and workflow.exists()
        and not unpinned
        and not missing_ci_gates
    )
    return SupplyChainReport(
        workspace=str(root),
        ok=ok,
        requirements_file=_rel(root, requirements) if requirements.exists() else None,
        requirements_in_file=_rel(root, requirements_in) if requirements_in.exists() else None,
        ci_workflow=_rel(root, workflow) if workflow.exists() else None,
        pinned_requirements=tuple(pinned),
        unpinned_requirements=tuple(unpinned),
        ci_gates=ci_gates,
        missing_ci_gates=missing_ci_gates,
        hash_checking_mode=hash_checking,
        warnings=tuple(warnings),
    )


def _classify_requirements(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [], []
    pinned: list[str] = []
    unpinned: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--", "-c ")):
            continue
        if _PINNED_RE.match(line):
            pinned.append(line)
            continue
        if _UNPINNED_OPERATOR_RE.search(line) or "==" not in line:
            unpinned.append(line)
    return pinned, unpinned


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
