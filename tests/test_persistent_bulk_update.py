"""Updating N records must cost one rewrite, not N.

Census item A5. `PersistentMemoryStore.update` rewrites the whole file per
record — its own docstring says so, "Replace an existing record in-place (full
rewrite)". Calling it in a loop is therefore quadratic. Measured on five
records:

    loop  : 5 rewrites, 25 rows written
    bulk  : 1 rewrite,   5 rows written

Three callers wanted the same thing and each improvised, because the store
offered no public bulk update — `save_many` APPENDS, it does not replace:

    core/loop_memory_read.py        looped over `update`
    core/loop_memory_write.py       looped over `update`
    core/loop_response_deciders.py  reached past the API into `_rewrite`

The third one had already found the right shape — "one load, all increments in
memory, ONE rewrite" (review round #294) — and had to break the boundary to get
it. That is the real finding: not three sloppy call sites but one missing
operation, and a fix applied at one site while the class stayed open at the
other two.

`update_many` is that operation. These tests hold the cost, the semantics, and
the lock discipline it inherits from `update`.
"""
from __future__ import annotations

from pathlib import Path

import core.persistent_memory as pm
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore


def _store(tmp_path: Path, n: int) -> tuple[PersistentMemoryStore, list[MemoryRecord]]:
    store = PersistentMemoryStore(path=tmp_path / "mem.jsonl")
    made = []
    for i in range(n):
        rec = MemoryRecord(
            type="semantic", content=f"fact {i}", tags=["t"],
            owner="user", source="user-explicit",
        )
        store.save(rec)
        made.append(rec)
    return store, made


class _CountingWriter:
    """Counts real rewrites, and how many rows each one carried."""

    def __init__(self, monkeypatch) -> None:
        self.rewrites = 0
        self.rows = 0
        self._real = pm.rewrite_state_jsonl_unlocked
        monkeypatch.setattr(pm, "rewrite_state_jsonl_unlocked", self)

    def __call__(self, path, rows):
        self.rewrites += 1
        self.rows += len(rows)
        return self._real(path, rows)


# ---------------------------------------------------------------------------
# The cost, which is the whole point
# ---------------------------------------------------------------------------

def test_a_bulk_update_rewrites_the_file_once(tmp_path: Path, monkeypatch):
    store, _ = _store(tmp_path, 5)
    counter = _CountingWriter(monkeypatch)

    landed = store.update_many(
        r.model_copy(update={"access_count": r.access_count + 1})
        for r in store.load()
    )

    assert landed == 5
    assert counter.rewrites == 1, "one rewrite, whatever the record count"
    assert counter.rows == 5


def test_the_loop_it_replaces_was_quadratic(tmp_path: Path, monkeypatch):
    """The measurement that made this item real, kept as a test.

    Not a guard on behaviour anyone should write — a record of WHY the bulk
    operation exists, so a future reader can see the cost rather than take the
    claim on trust.
    """
    store, _ = _store(tmp_path, 5)
    counter = _CountingWriter(monkeypatch)

    for rec in store.load():
        store.update(rec.model_copy(update={"access_count": rec.access_count + 1}))

    assert counter.rewrites == 5
    assert counter.rows == 25


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------

def test_the_new_values_actually_land(tmp_path: Path):
    store, _ = _store(tmp_path, 3)

    store.update_many(
        r.model_copy(update={"access_count": 7}) for r in store.load()
    )

    assert [r.access_count for r in store.load()] == [7, 7, 7]


def test_records_not_named_are_left_alone(tmp_path: Path):
    store, made = _store(tmp_path, 3)
    target = made[1]

    store.update_many([target.model_copy(update={"access_count": 9})])

    by_id = {r.id: r for r in store.load()}
    assert by_id[target.id].access_count == 9
    assert [by_id[m.id].access_count for m in made] == [0, 9, 0]
    assert len(by_id) == 3, "nothing was dropped by the rewrite"


def test_an_unknown_id_is_skipped_rather_than_raising(tmp_path: Path):
    """A caller updating what it just retrieved must not fail on a race.

    Hygiene can archive a record between the read and the write. Raising there
    would turn an ordinary retrieval into a failed turn; the return value is how
    the caller learns it happened.
    """
    store, made = _store(tmp_path, 2)
    ghost = MemoryRecord(
        type="semantic", content="archived meanwhile", tags=[],
        owner="user", source="user-explicit",
    )

    landed = store.update_many([
        made[0].model_copy(update={"access_count": 4}),
        ghost,
    ])

    assert landed == 1
    assert len(store.load()) == 2, "the unknown record was not inserted"


def test_an_empty_call_writes_nothing(tmp_path: Path, monkeypatch):
    store, _ = _store(tmp_path, 2)
    counter = _CountingWriter(monkeypatch)

    assert store.update_many([]) == 0
    assert counter.rewrites == 0


def test_nothing_matching_writes_nothing(tmp_path: Path, monkeypatch):
    """Zero matches must not rewrite either — an empty diff is not a write."""
    store, _ = _store(tmp_path, 2)
    ghost = MemoryRecord(
        type="semantic", content="nowhere", tags=[],
        owner="user", source="user-explicit",
    )
    counter = _CountingWriter(monkeypatch)

    assert store.update_many([ghost]) == 0
    assert counter.rewrites == 0


# ---------------------------------------------------------------------------
# The three callers no longer improvise
# ---------------------------------------------------------------------------

def test_no_caller_loops_over_update_or_reaches_past_the_api():
    """Guards the class, not the three instances.

    A5's lesson is that one missing operation produced three different
    workarounds, and that fixing one site left the other two open — the same
    shape as review #294. Checking the class is what keeps a fourth caller from
    inventing a fourth workaround.
    """
    import ast
    import pathlib

    offenders: list[str] = []
    for path in sorted(pathlib.Path("core").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = ast.unparse(node.func.value)
                if "persistent_store" not in receiver:
                    continue
                if node.func.attr == "_rewrite":
                    offenders.append(f"{path.name}:{node.lineno} reaches _rewrite")
            if isinstance(node, (ast.For, ast.While)):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and inner.func.attr == "update"
                            and "persistent_store" in ast.unparse(inner.func.value)):
                        offenders.append(
                            f"{path.name}:{inner.lineno} calls update() in a loop"
                        )

    assert offenders == [], offenders
