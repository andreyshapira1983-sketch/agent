"""One queue for tasks. A second one is invisible work, not redundancy.

Until 2026-08-05 there were two. The daemon read `data/task_queue.jsonl`,
named in `agent_tick.py` and nowhere else. Everything an operator touches —
`:task-add` through `app/task_scheduler_cli.py`, the default in
`app/bootstrap.py`, the count in `cli/commands_health.py` — used
`data/runtime_tasks.jsonl`.

So a task queued by hand was durable, visible in the REPL, counted by the
health command, and **invisible to the process meant to run it**. Measured on
the live workspace through the read-only observer: 27 consecutive ticks logged
`no_pending_tasks` while an `auto_run` task sat `pending` in the other store.
The daemon was not refusing the work. It could not see it.

Nothing was broken enough to fail: both stores parsed, both held valid rows,
both were written by the same `TaskQueueStore` class. The defect lived in the
gap between two constants, which is precisely the shape no test catches by
accident — hence this file.

`runtime_tasks.jsonl` is the survivor because three modules already used it
against the daemon's one, and because it holds the work: the discarded store
contained a single `done` row.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

#: Where to look. A hand-written list was the first version and it MISSED
#: `tests/test_budget_kill_switch.py`, which had the old path hard-coded —
#: found by reading the file before deleting it, not by this guard. A guard
#: whose scope is a list of names only catches what its author remembered,
#: so the scope is now the tree.
_SEARCH_ROOTS = ("agent_tick.py", "app", "cli", "core", "tools", "tests")
_SKIP = {"__pycache__", ".venv", "node_modules"}


def _python_files() -> list[Path]:
    out: list[Path] = []
    for rel in _SEARCH_ROOTS:
        target = _REPO / rel
        if target.is_file():
            out.append(target)
        elif target.is_dir():
            out.extend(
                f for f in target.rglob("*.py")
                if not _SKIP & set(f.parts) and f.name != "test_one_task_store.py"
            )
    return out

#: Only the PRODUCTION store counts: a path rooted at `data/`. Tests build
#: isolated queues as `tmp_path / "tasks.jsonl"` all over the suite, and
#: those are fixtures, not a second place where work can hide. The first
#: version matched any `*task*.jsonl` and flagged eight such files.
_JSONL_RE = re.compile(r"data[/\\]+([\w]*(?:task|queue)[\w]*\.jsonl)", re.IGNORECASE)


def _named_queue_files(path: Path) -> set[str]:
    """Production task-store filenames a module names in a string literal.

    Docstrings are skipped. They ARE string constants, which is how the first
    version flagged a test whose docstring merely recounts the old two-store
    history — the check would have demanded that history be erased to stay
    green. A guard that punishes writing down what happened is worse than the
    drift it looks for.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node not in docstrings):
            found.update(m.group(1) for m in _JSONL_RE.finditer(node.value))
    return found


def test_every_module_that_queues_tasks_names_the_same_file():
    """Two constants, two files, and a daemon that cannot see its own work."""
    named: dict[str, set[str]] = {}
    for path in _python_files():
        files = _named_queue_files(path)
        if files:
            named[path.relative_to(_REPO).as_posix()] = files

    all_files = set().union(*named.values()) if named else set()
    assert all_files <= {"runtime_tasks.jsonl"}, (
        "очередь задач снова разъехалась по нескольким файлам:\n  "
        + "\n  ".join(f"{mod}: {sorted(files)}" for mod, files in sorted(named.items()))
        + "\nОдно место для всех задач: то, которое видит и оператор, и демон."
    )


def test_the_daemon_takes_its_path_from_the_shared_constant():
    """Imported and ASSIGNED, not merely mentioned somewhere in the file.

    The first version searched the source for the constant's name, so a
    comment, a docstring or an unused import would have satisfied it while
    `TASK_QUEUE_PATH` went back to a literal. Judging code by a substring is
    the mistake this whole branch keeps finding in other places; it had no
    business in the guard against it.

    Also pins WHERE the constant comes from. `core.task_queue` owns the store
    and costs 73 ms to import; `app.bootstrap` costs 365 ms and pulls 404
    modules, and `agent_tick` lazily imports `build_agent` from it in three
    places precisely so `--status` does not build an agent. A module-level
    import of bootstrap here would undo that silently.
    """
    tree = ast.parse((_REPO / "agent_tick.py").read_text(encoding="utf-8"))

    sources = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(a.name == "DEFAULT_RUNTIME_TASKS_PATH" for a in node.names)
    }
    assert sources == {"core.task_queue"}, (
        f"путь очереди берётся не оттуда, откуда должен: {sources or None}"
    )

    assigned = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(x, ast.Name) and x.id == "TASK_QUEUE_PATH"
                for x in node.targets)
    ]
    assert len(assigned) == 1, f"TASK_QUEUE_PATH присвоен {len(assigned)} раз"
    assert "DEFAULT_RUNTIME_TASKS_PATH" in ast.unparse(assigned[0].value), (
        f"TASK_QUEUE_PATH снова строится сам: {ast.unparse(assigned[0].value)}"
    )


def test_paused_checkpoints_are_not_swept_up_by_the_merge(tmp_path: Path):
    """Sharing the store must not make the daemon adopt suspended work.

    Five `paused` `resume_checkpoint` rows were sitting in the surviving store
    when the daemon was pointed at it, so "the daemon now sees everything" was
    the real risk of the merge. `pending()` selects on `status`, and this
    proves it on a store it builds itself.

    Built rather than read from `data/`: that file is absent in CI, where the
    check would have passed by iterating nothing — §32, a test that passes
    because of where you ran it. Twice today was enough.
    """
    from core.task_queue import TaskQueueStore

    store = TaskQueueStore(tmp_path / "runtime_tasks.jsonl")
    wanted = store.add(kind="auto_run", goal="project health")
    for i in range(5):
        parked = store.add(kind="resume_checkpoint", goal=f"suspended {i}")
        # `_update_one` on purpose: the store exposes no public `pause`,
        # because nothing pauses a task through it — `core/checkpoint.py`
        # writes the phase. The fixture needs the row shape, not the route.
        store._update_one(parked.id,
                          lambda task: task.with_updates(status="paused"))

    pending = store.pending()

    assert [t.id for t in pending] == [wanted.id], (
        f"демон подхватил не только ждущую задачу: "
        f"{[(t.id, t.status) for t in pending]}"
    )
    assert len(store.load()) == 6, "приостановленные должны остаться в хранилище"
