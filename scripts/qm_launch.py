"""EXPERIMENTAL minimal QM launcher. Experiment 2 of the entry-point test.

    main.qm  ->  this launcher  ->  the existing implementation

It consumes `main.qm` and MUST NOT consume `main.py`. That is not a promise in
a docstring. Three mechanisms enforce it: a pre-flight proving the forbidden
module is unresolvable, a meta-path finder that aborts any attempt to import it,
and an audit hook for the subprocess route. A hidden fallback is a crash with
exit code 4, not a silent success.

The first draft enforced it by auditing `open` for a file named `main.py`, and a
liveness probe proved that guard DEAD: imports are served from `__pycache__`, so
the `.py` is never opened. That failure is why the guard below works on module
resolution instead of on file names.

The launcher contains no knowledge of the agent. Everything it does is read out
of the specimen's `entry` block: which module, which callable, what argv[0], and
what to do with the returned value. Change the specimen and the launch changes;
break the specimen and the launch REFUSES rather than guessing.

Exit codes of the launcher itself (distinct from the agent's own):
    3  the specimen is missing, unreadable, or its entry cannot be resolved
    4  the launch path touched a forbidden artifact
Anything else is the agent's own exit code, passed through unchanged.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

REFUSED, FORBIDDEN_TOUCH = 3, 4

ROOT = Path(__file__).resolve().parent.parent


def _abort(why: str) -> None:
    sys.stderr.write(f"\nQM LAUNCH ABORTED: {why}\n")
    raise SystemExit(FORBIDDEN_TOUCH)


class _ForbiddenModuleBlocker:
    """A meta-path finder that refuses the forbidden module names.

    The FIRST draft of this guard audited `open` events for a file named
    `main.py`. A liveness probe killed it: forbidding `app.py`, which the launch
    path genuinely imports, changed nothing, because CPython served the import
    from `cli/__pycache__/app.cpython-311.pyc` and never opened the `.py` at
    all. A path-name guard is therefore STRUCTURALLY unable to prove that a
    module was not loaded -- a stale `main.pyc` would have walked straight past
    it. sys.meta_path is consulted before any loader, bytecode included.
    """

    def __init__(self, module_names: tuple[str, ...]) -> None:
        self.module_names = module_names

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        # EXACT match only. The first sound version also matched on the last
        # dotted component, and its first live run aborted the launch on
        # `dotenv.main` -- a legitimate third-party submodule that merely ends
        # in the same word. "the module main" means the top-level `main`.
        if fullname in self.module_names:
            _abort(f"the launch path tried to import the forbidden module {fullname!r}")
        return None


def _install_forbidden_artifact_guard(names: list[str]) -> tuple[str, ...]:
    """Make "no hidden fallback" machine-enforced, on both routes it can take.

    Returns the forbidden module names so the caller can assert afterwards.
    """
    module_names = tuple(
        n[:-3] if n.lower().endswith(".py") else n for n in names
    )
    file_names = tuple(n.lower() for n in names)

    sys.meta_path.insert(0, _ForbiddenModuleBlocker(module_names))

    def _hook(event: str, args: tuple) -> None:
        # The other route a hidden fallback could take: handing the artifact to
        # another process. Subprocess arguments are strings, so a name check is
        # sound here in a way it is not for imports.
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn"):
            blob = " ".join(str(a) for a in args).lower()
            for name in file_names:
                if name in blob:
                    _abort(f"the launch path spawned a process naming {name!r} ({event})")

    sys.addaudithook(_hook)
    return module_names


def _refuse(why: str) -> int:
    sys.stderr.write(f"QM LAUNCH REFUSED: {why}\n")
    return REFUSED


def main() -> int:
    specimen_path = ROOT / "main.qm"
    if not specimen_path.is_file():
        return _refuse(f"no specimen at {specimen_path}")
    try:
        spec = json.loads(specimen_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _refuse(f"main.qm is not readable -- {exc}")

    entry = spec.get("entry")
    if not isinstance(entry, dict):
        return _refuse("main.qm declares no `entry` -- nothing describes how to start")

    delegate = entry.get("delegate") or {}
    module_name, callable_name = delegate.get("module"), delegate.get("callable")
    if not module_name or not callable_name:
        return _refuse("entry.delegate must name both a module and a callable")

    forbidden = _install_forbidden_artifact_guard(
        entry.get("forbidden_in_the_launch_path") or []
    )

    sys.path.insert(0, str(ROOT))

    # Pre-flight, printed so the claim is evidence rather than assertion: the
    # forbidden modules must be unresolvable BEFORE anything is imported.
    for name in forbidden:
        blocker = sys.meta_path[0]
        sys.meta_path.remove(blocker)
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, AttributeError, ValueError):
            found = None
        finally:
            sys.meta_path.insert(0, blocker)
        sys.stderr.write(
            f"[qm] pre-flight: module {name!r} resolvable -> "
            f"{found.origin if found else 'NO'}\n"
        )
        if found is not None:
            _abort(f"{name!r} is still resolvable; this is not a replacement test")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return _refuse(f"entry.delegate.module {module_name!r} is not importable -- {exc}")

    target = getattr(module, callable_name, None)
    if target is None:
        return _refuse(
            f"entry.delegate.callable {callable_name!r} does not exist in {module_name!r}"
        )
    if not callable(target):
        return _refuse(f"{module_name}.{callable_name} is not callable")

    argv0 = entry.get("argv0")
    if not argv0:
        return _refuse("entry.argv0 is required -- the entry artifact must name itself")
    sys.argv[0] = argv0

    # The termination contract, carried from the specimen: the delegate's return
    # value becomes the process's exit code. A SystemExit raised deeper simply
    # propagates through, exactly as it did through the removed launcher.
    result = target()

    # Post-run: the forbidden modules must still be absent. A guard that only
    # looked forward could miss a late import inside the agent itself.
    for name in forbidden:
        if name in sys.modules:
            _abort(f"module {name!r} was loaded during the run after all")
    sys.stderr.write(f"[qm] post-run: {forbidden} absent from sys.modules\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
