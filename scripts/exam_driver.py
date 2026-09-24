"""Drive one REPL session of the agent for an examination: questions arrive
as files, answers leave as files; the process stays alive across turns so
working memory carries the dialogue.

Two lessons carved into it on 2026-09-05:
* every question is sent inside a `:task-begin … :task-end` block — the
  keyboard intent router hijacked the word «модели» into `:models`;
* the end of a turn is the turn's own closing marker, NEVER silence: the
  first version ended a turn after 40 s of quiet output, the planner was
  silent for 44 s, and the stop that followed killed the process before
  synthesis — a live provenance check was lost to the harness, not to the
  agent (ACTUATION_TEST_2026-09-05.md in git history, raw timeline).

Protocol (paths under ``--dir``): ``q/next.txt`` = the next question;
``a/turn_<n>.md`` = the answer with the journals' deltas; ``q/stop`` =
finish the session. Run from the repository root.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

#: The line the loop prints when a turn is fully over (episodic + procedural
#: memory written). Everything the operator reads comes before it or right
#: after it; the REPL then waits for the next line of stdin.
END_OF_TURN_MARKERS: tuple[str, ...] = ("procedural_memory_update", "[PROC]")
#: After the marker, the answer text is flushed; this short grace collects it.
GRACE_AFTER_MARKER = 6.0
TURN_TIMEOUT = 900


def _count(path: str) -> int:
    try:
        from core.state_integrity import read_state_jsonl

        return len(read_state_jsonl(path))
    except Exception:  # noqa: BLE001 — a missing journal is 0 rows for the delta
        return 0


def _usage_rows() -> list[dict]:
    try:
        from core.state_integrity import read_state_jsonl

        return read_state_jsonl("data/model_usage.jsonl")
    except Exception:  # noqa: BLE001
        return []


def turn_is_over(lines: list[str], start: int) -> bool:
    """A turn is over when its closing marker has been printed. Silence is
    not a signal: a 44-second planner call prints nothing."""
    return any(any(m in line for m in END_OF_TURN_MARKERS) for line in lines[start:])


def wait_for_turn_end(
    buf: list[str], start: int, *, proc_alive, timeout: float = TURN_TIMEOUT,
    grace: float = GRACE_AFTER_MARKER, sleep=time.sleep, clock=time.monotonic,
) -> str:
    """Block until the closing marker appears (then a short grace for the
    answer text), the process dies, or the timeout. Returns why it returned:
    'marker' | 'exit' | 'timeout'."""
    t0 = clock()
    while True:
        if turn_is_over(buf, start):
            sleep(grace)
            return "marker"
        if not proc_alive():
            return "exit"
        if clock() - t0 > timeout:
            return "timeout"
        sleep(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="exchange directory (q/, a/)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument(
        "--approve", action="store_true",
        help="run the REPL with --auto-approve approve (the operator's word «дай ему "
             "починить до конца»); default is deny — the agent may read, not write",
    )
    args = ap.parse_args(argv)
    root = Path(args.dir)
    qdir, adir = root / "q", root / "a"
    qdir.mkdir(parents=True, exist_ok=True)
    adir.mkdir(parents=True, exist_ok=True)
    stop = qdir / "stop"

    # Measured 2026-09-05 11:45: pytest spawned by his run_tests tool inherits
    # this driver's OPEN stdin pipe and never starts (0 s CPU) until the tool's
    # ceiling — 14 s with stdin closed, 70 s with it open under a 60-s ceiling.
    # The defect is the tool's (a child should get stdin=DEVNULL) and is his
    # cargo; the driver only caps the loss at one minute instead of fifteen.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "AGENT_TEST_TIMEOUT_SECONDS": os.environ.get("AGENT_TEST_TIMEOUT_SECONDS", "60")}
    proc = subprocess.Popen(  # nosec B603 — fixed argv, our own entry point
        [args.python, "-u", "main.py", "--auto-approve", "approve" if args.approve else "deny"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
    )
    buf: list[str] = []

    def _pump() -> None:
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover — Popen was given a pipe
            return
        for line in stdout:
            buf.append(line)

    threading.Thread(target=_pump, daemon=True).start()
    log = adir / "driver.log"
    log.write_text(f"started pid {proc.pid} at {time.strftime('%H:%M:%S')}\n", encoding="utf-8")
    time.sleep(8)
    turn = 0
    while True:
        if stop.exists():
            try:
                if proc.stdin is not None:
                    proc.stdin.write(":quit\n")
                    proc.stdin.flush()
            except Exception as exc:  # noqa: BLE001 — the process may already be gone; say so
                log.open("a", encoding="utf-8").write(f"quit not delivered: {exc!r}\n")
            time.sleep(3)
            proc.terminate()
            stop.unlink(missing_ok=True)
            log.open("a", encoding="utf-8").write(f"stopped at {time.strftime('%H:%M:%S')}\n")
            return 0
        if proc.poll() is not None:
            log.open("a", encoding="utf-8").write(f"agent process exited {proc.returncode}\n")
            return 1
        nxt = qdir / "next.txt"
        if not nxt.exists():
            time.sleep(2)
            continue
        turn += 1
        question = nxt.read_text(encoding="utf-8").strip()
        nxt.unlink()
        start = len(buf)
        usage_before = len(_usage_rows())
        pol_before = _count("data/model_routing_policy.jsonl")
        mem_before = _count("data/persistent_memory.jsonl")
        t0 = time.time()
        if proc.stdin is None:  # pragma: no cover — Popen was given a pipe
            return 1
        proc.stdin.write(":task-begin\n" + question + "\n:task-end\n")
        proc.stdin.flush()
        why = wait_for_turn_end(buf, start, proc_alive=lambda: proc.poll() is None)
        answer = "".join(buf[start:])
        rows = _usage_rows()[usage_before:]
        report = {
            "turn": turn, "ended_by": why, "seconds": round(time.time() - t0),
            "calls": len(rows), "tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "routes": sorted({f"{r.get('role')}:{r.get('provider')}/{r.get('model')} [{r.get('route_reason')}]" for r in rows}),
            "policy_records": [pol_before, _count("data/model_routing_policy.jsonl")],
            "persistent_records": [mem_before, _count("data/persistent_memory.jsonl")],
        }
        (adir / f"turn_{turn}.md").write_text(
            f"## Turn {turn}\n\n**Q:**\n\n{question}\n\n**A:**\n\n{answer}\n\n_journals:_ {json.dumps(report, ensure_ascii=False)}\n",
            encoding="utf-8",
        )
        log.open("a", encoding="utf-8").write(f"turn {turn} ended_by={why} at {time.strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    raise SystemExit(main())
