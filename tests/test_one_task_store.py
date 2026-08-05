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

#: Modules that name a task-queue file. Adding one is fine; naming a DIFFERENT
#: file in it is the defect this file exists to prevent.
_QUEUE_USERS = (
    "agent_tick.py",
    "app/bootstrap.py",
    "app/task_scheduler_cli.py",
    "cli/commands_health.py",
)

_JSONL_RE = re.compile(r"([\w/]*(?:task|queue)[\w]*\.jsonl)", re.IGNORECASE)


def _named_queue_files(path: Path) -> set[str]:
    """Task-store filenames a module names in a string literal.

    Read from string constants rather than from the whole text, so a filename
    mentioned in a comment — this file's own history, for instance — is not
    mistaken for a second store.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(m.group(1).split("/")[-1] for m in _JSONL_RE.finditer(node.value))
    return found


def test_every_module_that_queues_tasks_names_the_same_file():
    """Two constants, two files, and a daemon that cannot see its own work."""
    named: dict[str, set[str]] = {}
    for rel in _QUEUE_USERS:
        path = _REPO / rel
        assert path.is_file(), f"модуль пропал: {rel} — обнови список"
        files = _named_queue_files(path)
        if files:
            named[rel] = files

    all_files = set().union(*named.values()) if named else set()
    assert all_files == {"runtime_tasks.jsonl"}, (
        "очередь задач снова разъехалась по нескольким файлам:\n  "
        + "\n  ".join(f"{mod}: {sorted(files)}" for mod, files in named.items())
        + "\nОдно место для всех задач: то, которое видит и оператор, и демон."
    )


def test_the_daemon_takes_its_path_from_the_shared_constant():
    """Imported, not re-declared — a copy drifts the moment one side moves."""
    source = (_REPO / "agent_tick.py").read_text(encoding="utf-8")
    assert "DEFAULT_RUNTIME_TASKS_PATH" in source, (
        "agent_tick.py снова объявляет путь очереди сам"
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
