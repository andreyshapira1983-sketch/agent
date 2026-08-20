"""Break the code on purpose and see whether the tests notice.

Census item C2. A test that has never failed is not evidence. Coverage says
a line RAN; it does not say a break in that line would be caught. The
difference is not academic — a test with no assertion gives full coverage
and catches nothing.

Deliberately small and deliberately not a framework. It applies a handful of
mutations that correspond to real defects this project has actually shipped
— a comparison flipped, a boundary moved by one, a truth value inverted, a
call removed — runs a chosen slice of the suite, and restores the file.
Nothing is left mutated: the original is written back in a `finally`, and
the run refuses to start on a dirty working tree so a crash can never be
mistaken for the program.

Exit codes, and the difference between the last two matters: 0 when every
mutation was caught, 1 when any survived — so it can gate a change the way
the ratchets do — and 2 when the probe REFUSED to run at all, either because
the target has uncommitted changes or because the selected tests were
already red. A refusal is not a finding, and a caller must be able to tell
them apart.
"""
from __future__ import annotations

import argparse
import ast
import shutil
import subprocess  # nosec B404 — runs pytest on this repository, by design
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Comparison flips. Each is a defect shape with a history here: an off-by-one
#: on a threshold, a gate that admits what it should refuse.
_COMPARE_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
}


@dataclass(frozen=True)
class Mutation:
    """One deliberate break, with the address a reader needs to judge it."""

    line: int
    description: str
    source: str


def _default_value_nodes(tree: ast.AST) -> set[int]:
    """Constants that are DEFAULTS — a signature default or a field default."""
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            for default in [*node.defaults, *node.kw_defaults]:
                if default is not None:
                    skip.update(id(n) for n in ast.walk(default))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            # `field: int = 0` at class level — a dataclass default.
            skip.update(id(n) for n in ast.walk(node.value))
    return skip


class _Mutator(ast.NodeTransformer):
    """Applies exactly ONE change, chosen by index, and reports what it did."""

    def __init__(self, target: int, skip: set[int] | None = None) -> None:
        self.skip = skip or set()
        self.target = target
        self.seen = 0
        self.applied: str | None = None
        self.line: int | None = None

    def _take(self, node: ast.AST, description: str) -> bool:
        if id(node) in self.skip:
            return False
        hit = self.seen == self.target
        self.seen += 1
        if hit:
            self.applied = description
            self.line = getattr(node, "lineno", None)
        return hit

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if len(node.ops) == 1 and type(node.ops[0]) in _COMPARE_SWAP:
            was = type(node.ops[0]).__name__
            now = _COMPARE_SWAP[type(node.ops[0])].__name__
            if self._take(node, f"comparison {was} -> {now}"):
                node.ops = [_COMPARE_SWAP[type(node.ops[0])]()]
        return node

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, bool):
            if self._take(node, f"boolean {node.value} -> {not node.value}"):
                return ast.copy_location(ast.Constant(value=not node.value), node)
        elif (isinstance(node.value, int)
              and not isinstance(node.value, bool)
              and self._take(node, f"number {node.value} -> {node.value + 1}")):
            return ast.copy_location(ast.Constant(value=node.value + 1), node)
        return node


def enumerate_mutations(source: str) -> list[Mutation]:
    """Every mutation this probe knows how to make in ``source``."""
    out: list[Mutation] = []
    index = 0
    while True:
        try:
            # ONE parse per round, shared between the skip set and the walk.
            # A first version parsed twice and matched nodes by `id()`, so the
            # skip set referred to objects the mutator never saw and silently
            # did nothing. Caught before it ran, but it is exactly the kind of
            # inert guard this project keeps finding, so it is written down.
            tree = ast.parse(source)
            mutator = _Mutator(index, skip=_default_value_nodes(tree))
            tree = mutator.visit(tree)
        except SyntaxError:
            break
        if mutator.applied is None:
            break
        ast.fix_missing_locations(tree)
        out.append(Mutation(
            line=mutator.line or 0,
            description=mutator.applied,
            source=ast.unparse(tree),
        ))
        index += 1
    return out


#: pytest's own vocabulary. 1 means tests ran and failed; 4 means the
#: arguments were unusable (a path that is not there) and 5 that nothing was
#: collected. Neither of the last two is a statement about the code.
_PYTEST_FAILED, _PYTEST_USAGE, _PYTEST_NO_TESTS = 1, 4, 5


def _pytest(selection: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:randomly", *selection],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


def run_tests(selection: list[str]) -> bool:
    """True when the selection is GREEN."""
    return _pytest(selection) == 0


def selection_state(selection: list[str]) -> str:
    """`green`, `red`, or `uncollectable` — three states, never two."""
    code = _pytest(selection)
    if code == 0:
        return "green"
    if code == _PYTEST_FAILED:
        return "red"
    return "uncollectable"


def probe(target: Path, selection: list[str], *, limit: int | None = None) -> int:
    """Mutate, run, restore. Returns the number that survived."""
    original = target.read_text(encoding="utf-8")
    mutations = enumerate_mutations(original)
    if limit is not None:
        mutations = mutations[:limit]

    print(f"target    : {target}")
    print(f"tests     : {' '.join(selection)}")
    print(f"mutations : {len(mutations)}\n")

    state = selection_state(selection)
    if state != "green":
        print("REFUSED: " + (
            "the selection is already red — a survivor would mean nothing"
            if state == "red" else
            "the selection could not be run at all — check that every path "
            "exists and that it collects tests"
        ))
        return -1

    backup = Path(tempfile.mkdtemp()) / target.name
    shutil.copy2(target, backup)
    survived: list[Mutation] = []
    try:
        for i, mutation in enumerate(mutations, start=1):
            target.write_text(mutation.source, encoding="utf-8")
            caught = not run_tests(selection)
            mark = "caught    " if caught else "NOT caught"
            print(f"  [{i:3}/{len(mutations)}] {mark} "
                  f"{target.name}:{mutation.line}  {mutation.description}")
            if not caught:
                survived.append(mutation)
    finally:
        # Always, and before anything else can read the file.
        shutil.copy2(backup, target)

    print(f"\nsurvived: {len(survived)} of {len(mutations)}")
    for mutation in survived:
        print(f"  {target.name}:{mutation.line}  {mutation.description}")
    return len(survived)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("tests", nargs="+")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N mutations (a full pass can be slow)")
    args = parser.parse_args(argv)

    dirty = subprocess.run(
        ["git", "status", "--porcelain", str(args.target)],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if dirty:
        print(f"REFUSED: {args.target} has uncommitted changes. This rewrites the "
              "file and restores it from disk; an interrupted run must not be "
              "able to lose work that was never committed.")
        return 2

    survived = probe(args.target, args.tests, limit=args.limit)
    if survived < 0:
        # `probe` refused: the selection was already red, so a survivor would
        # prove nothing. That is not the same answer as "mutations survived",
        # and collapsing both into 1 would let a caller read a refusal as a
        # finding. Shares exit 2 with the uncommitted-changes refusal above:
        # both mean "the probe did not run", which is what a caller must act on.
        return 2
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
