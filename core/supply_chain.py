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
_LOCK_START_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+\-]+)\b")
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    version: str
    hashes: tuple[str, ...] = ()

    @property
    def normalized_name(self) -> str:
        return normalize_package_name(self.name)

    def to_component(self) -> dict[str, Any]:
        return {
            "type": "library",
            "name": self.name,
            "version": self.version,
            "purl": f"pkg:pypi/{self.normalized_name}@{self.version}",
            "hashes": [
                {"alg": "SHA-256", "content": value}
                for value in self.hashes
            ],
        }


@dataclass(frozen=True)
class SupplyChainReport:
    workspace: str
    ok: bool
    requirements_file: str | None
    requirements_in_file: str | None
    requirements_lock_file: str | None
    sbom_file: str | None
    ci_workflow: str | None
    pinned_requirements: tuple[str, ...] = ()
    unpinned_requirements: tuple[str, ...] = ()
    locked_packages: int = 0
    unhashed_lock_entries: tuple[str, ...] = ()
    sbom_components: int = 0
    sbom_matches_lock: bool = False
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
            "requirements_lock_file": self.requirements_lock_file,
            "sbom_file": self.sbom_file,
            "ci_workflow": self.ci_workflow,
            "pinned_requirements": list(self.pinned_requirements),
            "unpinned_requirements": list(self.unpinned_requirements),
            "locked_packages": self.locked_packages,
            "unhashed_lock_entries": list(self.unhashed_lock_entries),
            "sbom_components": self.sbom_components,
            "sbom_matches_lock": self.sbom_matches_lock,
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
            f"requirements.lock: {self.requirements_lock_file or 'missing'}",
            f"sbom: {self.sbom_file or 'missing'}",
            f"ci_workflow: {self.ci_workflow or 'missing'}",
            f"hash_checking_mode: {self.hash_checking_mode}",
        ]
        lines.append(
            f"dependency pins: pinned={len(self.pinned_requirements)} "
            f"unpinned={len(self.unpinned_requirements)}"
        )
        lines.append(
            f"lock: packages={self.locked_packages} "
            f"unhashed={len(self.unhashed_lock_entries)}"
        )
        lines.append(
            f"sbom: components={self.sbom_components} "
            f"matches_lock={self.sbom_matches_lock}"
        )
        if self.unpinned_requirements:
            lines.append("unpinned requirements:")
            lines.extend(f"  - {item}" for item in self.unpinned_requirements)
        if self.unhashed_lock_entries:
            lines.append("unhashed lock entries:")
            lines.extend(f"  - {item}" for item in self.unhashed_lock_entries)
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
    requirements_lock = root / "requirements.lock"
    sbom = root / "sbom.cdx.json"
    workflow = root / ".github" / "workflows" / "ci.yml"

    pinned, unpinned = _classify_requirements(requirements)
    locked = parse_requirements_lock(requirements_lock)
    unhashed_lock_entries = tuple(
        f"{item.name}=={item.version}"
        for item in locked
        if not item.hashes
    )
    expected_sbom = build_cyclonedx_sbom(locked) if locked else {}
    loaded_sbom = _load_json(sbom)
    sbom_matches_lock = bool(loaded_sbom) and loaded_sbom == expected_sbom
    sbom_components = len(loaded_sbom.get("components", [])) if isinstance(loaded_sbom, dict) else 0
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.exists() else ""
    ci_gates = {
        "pip_install_hash_lock": "pip install --require-hashes -r requirements.lock" in workflow_text,
        "pip_check": "pip check" in workflow_text,
        "release_audit": "scripts/audit_release.py" in workflow_text,
        "sbom_check": "scripts/generate_sbom.py --check" in workflow_text,
        "pytest": "pytest" in workflow_text,
        "coverage_branch": "coverage run --branch" in workflow_text,
        "coverage_report": "coverage report" in workflow_text,
        "coverage_threshold": "--fail-under=85" in workflow_text,
    }
    missing_ci_gates = tuple(name for name, present in ci_gates.items() if not present)
    hash_checking = (
        requirements_lock.exists()
        and bool(locked)
        and not unhashed_lock_entries
        and "--require-hashes" in workflow_text
        and "requirements.lock" in workflow_text
    )

    warnings: list[str] = []
    if not requirements.exists():
        warnings.append("requirements.txt is missing")
    if not requirements_in.exists():
        warnings.append("requirements.in is missing; direct dependency intent is not documented")
    if not requirements_lock.exists():
        warnings.append("requirements.lock is missing; hash-locked rebuilds are not possible")
    if not sbom.exists():
        warnings.append("sbom.cdx.json is missing")
    if not workflow.exists():
        warnings.append("CI workflow is missing")
    if unpinned:
        warnings.append("requirements.txt contains unpinned/range dependencies")
    if unhashed_lock_entries:
        warnings.append("requirements.lock contains entries without sha256 hashes")
    if sbom.exists() and not sbom_matches_lock:
        warnings.append("sbom.cdx.json is out of sync with requirements.lock")
    if not hash_checking:
        warnings.append("hash-checking mode is not enforced by requirements.lock + CI")

    ok = (
        requirements.exists()
        and requirements_in.exists()
        and requirements_lock.exists()
        and sbom.exists()
        and workflow.exists()
        and not unpinned
        and bool(locked)
        and not unhashed_lock_entries
        and sbom_matches_lock
        and hash_checking
        and not missing_ci_gates
    )
    return SupplyChainReport(
        workspace=str(root),
        ok=ok,
        requirements_file=_rel(root, requirements) if requirements.exists() else None,
        requirements_in_file=_rel(root, requirements_in) if requirements_in.exists() else None,
        requirements_lock_file=_rel(root, requirements_lock) if requirements_lock.exists() else None,
        sbom_file=_rel(root, sbom) if sbom.exists() else None,
        ci_workflow=_rel(root, workflow) if workflow.exists() else None,
        pinned_requirements=tuple(pinned),
        unpinned_requirements=tuple(unpinned),
        locked_packages=len(locked),
        unhashed_lock_entries=unhashed_lock_entries,
        sbom_components=sbom_components,
        sbom_matches_lock=sbom_matches_lock,
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


def parse_requirements_lock(path: Path) -> tuple[LockedRequirement, ...]:
    if not path.exists():
        return ()
    items: list[LockedRequirement] = []
    current_name: str | None = None
    current_version: str | None = None
    current_hashes: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_version, current_hashes
        if current_name and current_version:
            items.append(
                LockedRequirement(
                    name=current_name,
                    version=current_version,
                    hashes=tuple(sorted(set(current_hashes))),
                )
            )
        current_name = None
        current_version = None
        current_hashes = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        clean = line.rstrip("\\").strip()
        match = _LOCK_START_RE.match(clean)
        if match:
            flush()
            current_name = match.group(1)
            current_version = match.group(2)
            current_hashes.extend(_HASH_RE.findall(clean))
            continue
        current_hashes.extend(_HASH_RE.findall(clean))
    flush()
    return tuple(items)


def build_cyclonedx_sbom(items: tuple[LockedRequirement, ...]) -> dict[str, Any]:
    components = [
        item.to_component()
        for item in sorted(items, key=lambda entry: entry.normalized_name)
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "autonomous-agent",
            },
            "properties": [
                {"name": "source", "value": "requirements.lock"},
                {"name": "generator", "value": "scripts/generate_sbom.py"},
            ],
        },
        "components": components,
    }


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
