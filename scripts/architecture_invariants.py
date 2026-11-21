"""Check the architecture's load-bearing invariants (read-only; non-zero exit on drift)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
DOCS = REPO / "docs"

#: Layers `core/` may never import: the entry points and the layers above the core.
_FORBIDDEN_CORE_IMPORTS = ("cli", "app", "main", "agent_tick")

#: Directories whose imports count as "production reachability" for INV-2.
_PRODUCTION_ROOTS = (
    "core", "cli", "app", "api", "tools", "project_intelligence",
    # docker/daemon_loop.py repeats agent_tick: the continuous-autonomy path.
    "docker",
)
#: Entry points; scripts/market_worker.py is a live polling process, not a one-off script.
_PRODUCTION_FILES = ("agent_tick.py", "main.py", "scripts/market_worker.py")

#: Modules exempt from INV-2; every entry states its reason and is a mechanism that cannot run.
_ORPHAN_ALLOWLIST: dict[str, str] = {
    # Data, not a decider. Readers: scripts/gen_anatomy.py (ast parse, no import) and
    # _sync_anatomy_groups in core/self_build_producer.py (as text).
    "anatomy_groups": ("standing: data literal, parsed not imported; readers "
                       "named above — постоянна по замыслу, пока таблица групп "
                       "остаётся данными без импортёров"),
}

#: Env vars documented on purpose before code reads them; a renamed flag does not belong here.
_ENV_ALLOWLIST: dict[str, str] = {
    # MEMORY_LIFECYCLE_CONTRACT.md §15 is a proposal; its phase P0 is not built.
    "AGENT_MEMORY_LIFECYCLE": "planned — memory lifecycle plan, phase P0 unbuilt",
    # tests/test_network_deny.py pins the flag and its reader; this entry cannot hide a dead flag.
    "AGENT_TESTS_ALLOW_NETWORK": "test-suite flag — read by tests/conftest.py, outside production roots",
}

#: Words that mark a documented flag as not built yet; mirrors `_PLANNED_MARKERS`
#: in docs_code_conformance.py.
_ENV_PLANNED_MARKERS = (
    "proposed", "planned", "will be", "would be", "not yet", "to be added",
)

#: Files that must recognise every verdict `verifier_core` can assign.
_VERDICT_CONSUMERS = (
    "core/low_evidence_policy.py",
    "core/unsupported_claims.py",
    "core/loop.py",
    "core/verifier_models.py",
)

#: Default verdicts every consumer already handles by falling through.
_VERDICT_EXEMPT = frozenset({"unverified"})


def _core_modules() -> list[Path]:
    return sorted(p for p in CORE.glob("*.py") if p.name != "__init__.py")


def _iter_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # relative import — stays inside the package
                continue
            if node.module:
                yield node.lineno, node.module


def check_core_layering() -> tuple[list[str], int]:
    problems: list[str] = []
    modules = _core_modules()
    for path in modules:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:            # pragma: no cover - unparseable file
            problems.append(f"core/{path.name}:{exc.lineno}  unparseable: {exc.msg}")
            continue
        for lineno, module in _iter_imports(tree):
            root = module.split(".", 1)[0]
            if root in _FORBIDDEN_CORE_IMPORTS:
                problems.append(
                    f"core/{path.name}:{lineno}  imports `{module}` — the core "
                    f"layer may not depend on `{root}`"
                )
    return problems, len(modules)


def check_no_orphaned_modules() -> tuple[list[str], int]:
    names = {p.stem for p in _core_modules()}
    sources: list[Path] = []
    for root in _PRODUCTION_ROOTS:
        sources.extend((REPO / root).rglob("*.py"))
    sources.extend(REPO / name for name in _PRODUCTION_FILES)
    referenced: set[str] = set()
    for path in sources:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        referenced |= set(re.findall(r"(?:from|import)\s+core\.([a-z0-9_]+)", text))
        if path.parent == CORE:
            # Sibling relative imports (`from .verifier_models import ...`).
            referenced |= set(re.findall(r"from\s+\.([a-z0-9_]+)\s+import", text))
    orphans = sorted(names - referenced - set(_ORPHAN_ALLOWLIST))
    return [
        f"core/{name}.py  imported by no production module — a decider that "
        f"cannot run (self-audit-lessons #6)"
        for name in orphans
    ], len(names)


#: The lookahead skips file names such as `AGENT_ROLE_CONTRACT.md`: a token followed by
#: a short extension is not an environment variable.
_ENV_FLAG_RE = re.compile(r"\bAGENT_[A-Z0-9_]{3,}\b(?!\.[A-Za-z0-9]{1,5})")


def check_documented_env_flags() -> tuple[list[str], int]:
    code_text_parts: list[str] = []
    for root in _PRODUCTION_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            code_text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    for name in _PRODUCTION_FILES:
        path = REPO / name
        if path.is_file():
            code_text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    for extra in ("compose.yaml", "Dockerfile"):
        path = REPO / extra
        if path.is_file():
            code_text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
    code_text = "\n".join(code_text_parts)

    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    checked: set[str] = set()
    for doc in sorted(DOCS.rglob("*.md")):
        rel = doc.relative_to(DOCS).as_posix()
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            for flag in _ENV_FLAG_RE.findall(line):
                checked.add(flag)
                if flag in _ENV_ALLOWLIST or flag in code_text:
                    continue
                if any(marker in lowered for marker in _ENV_PLANNED_MARKERS):
                    continue
                key = (rel, flag)
                if key in seen:
                    continue
                seen.add(key)
                problems.append(
                    f"{rel}:{lineno}  documents env flag `{flag}` that no code reads"
                )
    return problems, len(checked)   # type: ignore[return-value]


def check_verdict_vocabulary() -> tuple[list[str], int]:
    src = (CORE / "verifier_core.py").read_text(encoding="utf-8")
    verdicts = set(re.findall(r'verdict\s*=\s*"([a-z_]+)"', src))
    verdicts |= set(re.findall(r'verdict="([a-z_]+)"', src))
    verdicts -= _VERDICT_EXEMPT
    consumers = {
        rel: (REPO / rel).read_text(encoding="utf-8") for rel in _VERDICT_CONSUMERS
    }
    problems: list[str] = []
    for verdict in sorted(verdicts):
        # `<verdict>_chunks` is the report's bucket name; either spelling counts.
        if any(
            verdict in text or f"{verdict}_chunks" in text
            for text in consumers.values()
        ):
            continue
        problems.append(
            f"core/verifier_core.py assigns verdict `{verdict}` that no consumer "
            f"buckets ({', '.join(_VERDICT_CONSUMERS)})"
        )
    return problems, len(verdicts)


_CHECKS = (
    ("INV-1 core layer imports downward only", check_core_layering, "core modules"),
    ("INV-2 no orphaned core modules", check_no_orphaned_modules, "core modules"),
    ("INV-3 documented env flags exist in code", check_documented_env_flags, "flags"),
    ("INV-4 verifier verdicts are all bucketed", check_verdict_vocabulary, "verdicts"),
)


def main() -> int:
    print("Architecture invariants check (read-only)")
    failed = False
    for title, check, unit in _CHECKS:
        problems, checked = check()
        status = (
            f"{len(problems)} violation(s) of {checked} {unit}"
            if problems
            else f"ok ({checked} {unit} checked)"
        )
        print(f"  {title}: {status}")
        if problems:
            failed = True
            for item in problems:
                print(f"    {item}")

    print("\n  RESULT:", "DRIFT FOUND" if failed else "every invariant holds.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
