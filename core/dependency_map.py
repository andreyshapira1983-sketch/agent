"""Project import/dependency map for self-build changes.

Before the agent modifies or splits a module it should know the file's real
consumers: which project files import it, exactly which symbols they take from
it, and which test files exercise it. This turns "don't break the public API"
from a guess into a measured contract:

* the Builder receives the list of symbols that are ACTUALLY imported elsewhere
  (with the importing files), so it knows what it must keep importable;
* the Critic can veto a split that drops a symbol some other module really
  imports — naming the importer, not just the symbol;
* the targeted-test selection can include the test files that import the
  target, so a break is caught by the lane instead of by a full-suite surprise.

Pure standard library + AST; read-only; never raises out of the public helpers
(best-effort: unparseable or unreadable files are skipped).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

# Directories never scanned for importers: VCS internals, caches, virtualenvs,
# and the agent's own data/state stores (never project source).
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "data",
    ".pytest_cache",
}

# Hard cap on scanned files so a pathological workspace cannot stall a produce
# run; typical project size here is a few hundred files.
_MAX_SCAN_FILES = 3000


@dataclass
class ImporterInfo:
    """One project file that imports the target module."""

    path: str  # repo-relative, forward slashes
    symbols: list[str] = field(default_factory=list)  # from-imported names
    imports_module: bool = False  # plain `import core.target` style


@dataclass
class DependencyMap:
    """Who consumes the target module, and how."""

    target: str
    module_name: str
    importers: list[ImporterInfo] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)

    @property
    def imported_symbols(self) -> dict[str, list[str]]:
        """Map of symbol -> importer paths, for every from-imported name."""
        out: dict[str, list[str]] = {}
        for imp in self.importers:
            for sym in imp.symbols:
                out.setdefault(sym, []).append(imp.path)
        return out

    def summary_lines(self, *, max_symbols: int = 20) -> list[str]:
        """Short human/LLM-readable evidence lines."""
        lines = [
            f"importers={len(self.importers)}",
            f"related_tests={len(self.related_tests)}",
        ]
        symbols = self.imported_symbols
        if symbols:
            shown = sorted(symbols)[:max_symbols]
            extra = len(symbols) - len(shown)
            suffix = f" (+{extra} more)" if extra > 0 else ""
            lines.append("imported_symbols=" + ", ".join(shown) + suffix)
        return lines

    def builder_context(self, *, max_symbols: int = 40) -> str:
        """Prompt block telling the Builder which symbols form the contract."""
        if not self.importers:
            return (
                f"PROJECT IMPORT MAP: no other project file imports "
                f"{self.module_name}; only tests or external callers may rely "
                f"on it."
            )
        parts = [
            f"PROJECT IMPORT MAP for {self.module_name} -- these symbols are "
            f"imported by other project files and MUST remain importable from "
            f"the target after your change:"
        ]
        symbols = self.imported_symbols
        for sym in sorted(symbols)[:max_symbols]:
            users = ", ".join(sorted(symbols[sym])[:3])
            parts.append(f"- {sym} (used by {users})")
        if len(symbols) > max_symbols:
            parts.append(f"- ... and {len(symbols) - max_symbols} more")
        module_importers = sorted(
            i.path for i in self.importers if i.imports_module
        )
        if module_importers:
            parts.append(
                "Whole-module importers (attribute access possible on ANY "
                "top-level name): " + ", ".join(module_importers[:5])
            )
        if self.related_tests:
            parts.append(
                "Related test files: " + ", ".join(self.related_tests[:10])
            )
        return "\n".join(parts)


def _module_name_for(target: str) -> str:
    """core/verifier.py -> core.verifier; main.py -> main."""
    norm = target.replace("\\", "/").strip().lstrip("/")
    if norm.lower().endswith(".py"):
        norm = norm[:-3]
    if norm.endswith("/__init__"):
        norm = norm[: -len("/__init__")]
    return norm.replace("/", ".")


def _resolve_relative(module: str | None, level: int, importer_rel: Path) -> str:
    """Resolve a relative `from .x import y` to an absolute dotted module."""
    base_parts = list(importer_rel.parent.parts)
    if level > 1:
        base_parts = base_parts[: len(base_parts) - (level - 1)]
    prefix = ".".join(base_parts)
    if module:
        return f"{prefix}.{module}" if prefix else module
    return prefix


def _iter_python_files(workspace: Path):
    count = 0
    for path in sorted(workspace.rglob("*.py")):
        rel = path.relative_to(workspace)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        count += 1
        if count > _MAX_SCAN_FILES:
            return
        yield path, rel


def build_dependency_map(workspace: Path, target: str) -> DependencyMap:
    """Scan the workspace and return the import map for ``target``.

    Best-effort and read-only: files that cannot be read or parsed are
    skipped silently; the function itself never raises for those.
    """
    module_name = _module_name_for(target)
    norm_target = target.replace("\\", "/").strip()
    dep = DependencyMap(target=norm_target, module_name=module_name)
    if not module_name:
        return dep

    for path, rel in _iter_python_files(workspace):
        rel_str = str(rel).replace("\\", "/")
        if rel_str == norm_target:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        symbols: list[str] = []
        imports_module = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module_name or alias.name.startswith(
                        module_name + "."
                    ):
                        imports_module = True
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    resolved = _resolve_relative(node.module, node.level, rel)
                else:
                    resolved = node.module or ""
                if resolved == module_name:
                    for alias in node.names:
                        if alias.name == "*":
                            imports_module = True
                        else:
                            symbols.append(alias.name)
                elif resolved.startswith(module_name + "."):
                    imports_module = True
        if symbols or imports_module:
            info = ImporterInfo(
                path=rel_str,
                symbols=sorted(dict.fromkeys(symbols)),
                imports_module=imports_module,
            )
            dep.importers.append(info)
            first = rel.parts[0].lower() if rel.parts else ""
            if first == "tests" or rel.name.startswith("test_"):
                dep.related_tests.append(rel_str)

    dep.related_tests = sorted(dict.fromkeys(dep.related_tests))
    return dep
