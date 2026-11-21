"""Убийство дерева процессов по таймауту не оставляет в живых ни одного потомка."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.bounded_subprocess import kill_process_tree

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="дерево процессов читается из /proc — только Linux",
)

_DEADLINE_SECONDS = 10.0


def _ppid_and_state(pid: int) -> tuple[int, str] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2:].split()
    return int(fields[1]), fields[0]


def _descendants(root: int) -> set[int]:
    parent_of: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            info = _ppid_and_state(int(entry))
            if info is not None:
                parent_of[int(entry)] = info[0]
    found: set[int] = set()
    frontier = {root}
    while frontier:
        frontier = {pid for pid, ppid in parent_of.items() if ppid in frontier} - found
        found |= frontier
    return found


def _alive(pid: int) -> bool:
    info = _ppid_and_state(pid)
    return info is not None and info[1] != "Z"


def test_the_whole_tree_dies_with_its_root() -> None:
    """Процесс, запустивший ребёнка, убивается вместе с ребёнком."""
    started = time.monotonic()
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 60 & sleep 60"],  # noqa: S607
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    tree: set[int] = set()
    try:
        while time.monotonic() - started < 3.0:
            tree = _descendants(proc.pid)
            if len(tree) >= 2:
                break
            time.sleep(0.05)
        assert len(tree) >= 2, f"у корня {proc.pid} не появились два сна: {tree}"

        kill_process_tree(proc)

        survivors: set[int] = set()
        while time.monotonic() - started < _DEADLINE_SECONDS:
            survivors = {pid for pid in tree | {proc.pid} if _alive(pid)}
            if not survivors:
                break
            time.sleep(0.05)
        assert not survivors, f"пережили убийство дерева: {sorted(survivors)}"
        assert time.monotonic() - started < _DEADLINE_SECONDS
    finally:
        for pid in tree | {proc.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        proc.wait(timeout=5)
