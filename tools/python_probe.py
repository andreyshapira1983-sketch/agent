"""Python Probe — a lab: a small experiment that measures the agent's own runtime."""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from core.redaction import redact_text
from tools.base import Tool
from tools.file_read import _is_credential_path

_INPUT_MAX_FILES = 20
#: Per-file input cap, large enough for the agent's own journals.
_INPUT_MAX_BYTES = 16 * 1024 * 1024
_INPUT_MAX_TOTAL = 32 * 1024 * 1024

#: Modules whose import turns a measurement into an action; matched by root, not substring.
_FORBIDDEN_MODULES = frozenset({
    "subprocess", "multiprocessing", "socket", "shutil", "ctypes",
    "urllib", "http", "requests", "httpx", "ftplib", "smtplib", "paramiko",
})

#: Effectful os.* calls; os itself stays allowed (os.name, os.environ are fair measurements).
_FORBIDDEN_OS_CALLS = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
    "system", "popen", "spawnl", "spawnv", "kill", "chmod", "chown",
})

#: Dynamic-import names: as an attribute, a string or an import, each one bypasses the gate.
_DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module"})

_OUTPUT_CAP = 16 * 1024  # 16 KiB per stream: an experiment, not a dump

#: Operator-only switch (default off) letting experiments import the agent's own modules.
#: The AST gate cannot judge those modules, so this is the operator's call, never the agent's.
WORKSPACE_IMPORT_ENV = "PYTHON_PROBE_WORKSPACE_IMPORT"
_WORKSPACE_PACKAGES = ("core", "tools", "app", "cli")


def _is_glob(value: str) -> bool:
    """True when the string is a glob pattern (contains * or ?), not a path."""
    return "*" in value or "?" in value


def _with_workspace_on_path(code: str, root: Path) -> str:
    """Wrap the experiment so it sees the workspace root.

    The path goes into the code, not PYTHONPATH (-I ignores the environment); compiling as
    <string> keeps error line numbers those of the experiment itself.
    """
    return (
        f"import sys\nsys.path.insert(0, {str(root)!r})\n"
        f"exec(compile({code!r}, '<string>', 'exec'))\n"
    )


#: Python audit events that are actions (process, network); file removal, renames and writes
#: are judged by path. No ctypes here: numpy and Python itself load libraries through it.
_RUNTIME_ACTIONS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn", "os.kill", "os.startfile",
    "socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo",
})
_RUNTIME_PATH_EVENTS = frozenset({"os.remove", "os.rmdir", "shutil.rmtree", "os.rename"})


def _with_runtime_tripwire(code: str) -> str:
    """Prepend an audit-hook tripwire that refuses actions even if the text gate is bypassed.

    It catches honest bypasses (importlib.import_module, builtins.__import__, write_text);
    audit hooks are not a sandbox, so the wall is the separate nobody user.
    """
    guard = (
        "import sys as _pg_sys, os as _pg_os, tempfile as _pg_tmp\n"
        "_pg_sys.dont_write_bytecode = True\n"
        "_pg_roots = {_pg_os.path.realpath(_pg_os.getcwd()),"
        " _pg_os.path.realpath(_pg_tmp.gettempdir())}\n"
        f"_pg_actions = {sorted(_RUNTIME_ACTIONS)!r}\n"
        f"_pg_paths = {sorted(_RUNTIME_PATH_EVENTS)!r}\n"
        "_pg_write = (_pg_os.O_WRONLY | _pg_os.O_RDWR | _pg_os.O_APPEND | _pg_os.O_CREAT"
        " | _pg_os.O_TRUNC)\n"
        "def _pg_inside(p):\n"
        "    try:\n"
        "        raw = _pg_os.fsdecode(p)\n"
        "        r = _pg_os.path.realpath(raw)\n"
        "    except (TypeError, ValueError):\n"
        "        return isinstance(p, int)\n"
        "    if raw.lower() == _pg_os.devnull.lower():\n"
        "        return True\n"
        "    return r == _pg_os.devnull or any(r == t or r.startswith(t + _pg_os.sep)"
        " for t in _pg_roots)\n"
        "def _pg_hook(event, args):\n"
        "    if event in _pg_actions:\n"
        "        raise RuntimeError('refused at run time: ' + event + ' is an action, not a measurement')\n"
        "    if event in _pg_paths and not all(_pg_inside(a) for a in args[:2] if a is not None"
        " and not isinstance(a, int)):\n"
        "        raise RuntimeError('refused at run time: ' + event + ' outside the experiment folder')\n"
        "    if event == 'open' and len(args) > 2 and isinstance(args[2], int)"
        " and args[2] & _pg_write and not _pg_inside(args[0]):\n"
        "        raise RuntimeError('refused at run time: writing outside the experiment folder')\n"
        "_pg_sys.addaudithook(_pg_hook)\n"
    )
    return guard + f"exec(compile({code!r}, '<string>', 'exec'))\n"


def _forbidden_reason(code: str) -> str | None:
    """One named refusal reason, or None; judges structure, not strings."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        # Gate bypasses: import_module in any form, builtins.__import__,
        # getattr(..., "__import__"), exec/eval of a string (the gate cannot see inside).
        if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_IMPORT_NAMES:
            return f"'{node.attr}' bypasses the import gate"
        if isinstance(node, ast.Constant) and node.value in _DYNAMIC_IMPORT_NAMES:
            return f"the name '{node.value}' in a string bypasses the import gate"
        if isinstance(node, ast.ImportFrom) and any(
                a.name in _DYNAMIC_IMPORT_NAMES for a in node.names):
            return "importing import_module bypasses the import gate"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("exec", "eval", "compile", "import_module")):
            return f"{node.func.id}() hides code from the import gate"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                root = name.split(".", 1)[0]
                if root in _FORBIDDEN_MODULES:
                    return f"import of '{root}' turns a measurement into an action"
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "__import__":
                return "'__import__' bypasses the import gate"
            if isinstance(fn, ast.Name) and fn.id == "open":
                for arg in list(node.args[1:2]) + [
                    kw.value for kw in node.keywords if kw.arg == "mode"
                ]:
                    if isinstance(arg, ast.Constant) and any(
                        ch in str(arg.value) for ch in "wax+"
                    ):
                        return "open() with a write mode is an action, not a measurement"
            if (isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name) and fn.value.id == "os"
                    and fn.attr in _FORBIDDEN_OS_CALLS):
                return f"call os.{fn.attr}() is an action, not a measurement"
    return None


def _as_nobody(argv: list[str], cwd: str) -> tuple[list[str], str]:
    """Run the experiment as user nobody: a wall, not a layer (Python cannot sandbox itself).

    Linux root without the sandbox refuses; elsewhere it runs as the current user, marked so.
    """
    from tools.convert_file import _NOBODY, sandbox_available

    why = sandbox_available()
    if why is None:
        for base, _dirs, files in os.walk(cwd):
            os.chown(base, _NOBODY, _NOBODY)
            for name in files:
                os.chown(os.path.join(base, name), _NOBODY, _NOBODY)
        return (["setpriv", f"--reuid={_NOBODY}", f"--regid={_NOBODY}", "--clear-groups",
                 "--no-new-privs", *argv], "nobody")
    if sys.platform.startswith("linux") and os.geteuid() == 0:
        raise ValueError(f"python_probe does not run as root without the sandbox: {why}")
    return argv, f"none ({why})"


class PythonProbeTool(Tool):
    name = "python_probe"
    description = (
        "Run a SMALL Python experiment in THIS runtime to MEASURE behaviour: "
        "versions, real signatures, whether a feature exists here — or to "
        "COMPUTE over workspace files listed in `inputs` (copied read-only into "
        "the experiment's cwd, open them by the same relative path). The code "
        "runs as the unprivileged user nobody (no access to keys, agent data, logs or "
        "chat) in an isolated interpreter with a temp cwd, a hard "
        "timeout and an output cap. A failing snippet is a valid measurement "
        "(the ImportError IS the data). Process/network/write operations are "
        "refused. Args: code (str), timeout_seconds (int, default 10), "
        "inputs (list of workspace-relative paths, optional). "
        "The agent's own modules (core, tools, app, cli) are importable only "
        "when the operator set PYTHON_PROBE_WORKSPACE_IMPORT=1; the result's "
        "`workspace_import` says which, so a ModuleNotFoundError for them is "
        "the switch being off, not a fact about the code."
    )
    risk = "read_only"

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def _copy_inputs(self, inputs: list[str], cwd: str) -> list[str]:
        """Copy workspace files into the experiment folder; every refusal names its reason."""
        if self.workspace_root is None:
            raise ValueError("inputs need a workspace; this probe was built without one")
        if not isinstance(inputs, list) or len(inputs) > _INPUT_MAX_FILES:
            raise ValueError(f"inputs must be a list of at most {_INPUT_MAX_FILES} paths")
        total, copied = 0, []
        for rel in inputs:
            if not isinstance(rel, str) or not rel.strip():
                raise ValueError("inputs: every entry must be a non-empty path")
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise ValueError(f"inputs: {rel!r} must be relative and stay inside the workspace")
            # Same denylist as file_read: keys copied into the lab would leak through stdout.
            if _is_credential_path(rel):
                raise ValueError(f"inputs: {rel!r} names a credential store and is not copied")
            src = (self.workspace_root / rel).resolve()
            if not src.is_relative_to(self.workspace_root) or not src.is_file():
                raise ValueError(f"inputs: {rel!r} is not a file in the workspace")
            size = src.stat().st_size
            total += size
            if size > _INPUT_MAX_BYTES or total > _INPUT_MAX_TOTAL:
                raise ValueError(
                    f"inputs: {rel!r} is {size} bytes; the limit is "
                    f"{_INPUT_MAX_BYTES} per file and {_INPUT_MAX_TOTAL} in total — "
                    "narrow it first (find_in_files for the lines, file_read with "
                    "start_line/end_line for a window) and compute over that"
                )
            dest = Path(cwd) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            copied.append(Path(rel).as_posix())
        return copied

    def _auto_inputs(
        self, code: str, inputs: list[str] | None, cwd: str, given: list[str],
    ) -> list[str]:
        """Copy files the code names but inputs omitted; over-limit ones stay in missing_inputs."""
        copied: list[str] = []
        total = sum((Path(cwd) / rel).stat().st_size for rel in given)
        for rel in self._missing_inputs(code, inputs):
            if _is_glob(rel) or _is_credential_path(rel) or len(given) + len(copied) >= _INPUT_MAX_FILES:
                continue
            size = (self.workspace_root / rel).stat().st_size
            if total + size > _INPUT_MAX_TOTAL:
                continue
            try:
                copied += self._copy_inputs([rel], cwd)
            except ValueError:
                continue
            total += size
        return copied

    def _enveloped_inputs(self, rels: list[str]) -> list[str]:
        """Passed .jsonl journals whose lines are wrapped in the state integrity envelope."""
        found: list[str] = []
        for rel in rels:
            if self.workspace_root is None or not rel.endswith(".jsonl"):
                continue
            try:
                with (self.workspace_root / rel).open(encoding="utf-8", errors="replace") as fh:
                    head = fh.readline(4096)
            except OSError:
                continue
            if '"_integrity"' in head and '"payload"' in head:
                found.append(rel)
        return found

    def _missing_inputs(self, code: str, inputs: list[str] | None) -> list[str]:
        """Workspace paths the code names but inputs did not pass (the lab cannot see them)."""
        if self.workspace_root is None:
            return []
        given = {Path(p).as_posix() for p in inputs or ()}
        named = {
            n.value for n in ast.walk(ast.parse(code))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and 0 < len(n.value) < 260 and "\n" not in n.value
        }
        missing = []
        patterns = [v for v in named if _is_glob(v) and "/" not in v]
        for value in sorted(named):
            rel = Path(value)
            if rel.is_absolute() or ".." in rel.parts or rel.as_posix() in given:
                continue
            if _is_glob(value):
                # A glob counts like a directory: in the empty lab it matches nothing, so
                # a zero would pass for a measurement.
                try:
                    hits = [p.relative_to(self.workspace_root).as_posix()
                            for p in self.workspace_root.glob(value) if p.is_file()]
                except (OSError, ValueError, NotImplementedError):
                    continue
                if hits and not all(h in given for h in hits):
                    missing.append(value)
                continue
            try:
                target = self.workspace_root / rel
                # A directory counts like a file: an empty lab folder turns a count into a
                # fake zero. It counts as passed only when ALL its files are in inputs.
                if target.is_dir():
                    for pattern in patterns:
                        hits = {p.relative_to(self.workspace_root).as_posix()
                                for p in target.glob(pattern) if p.is_file()}
                        if hits - given:
                            missing.append(f"{rel.as_posix()}/{pattern}")
                    inside = {p.relative_to(self.workspace_root).as_posix()
                              for p in target.rglob("*") if p.is_file()}
                    if inside and inside <= given:
                        continue
                if target.is_file() or (target.is_dir() and any(target.iterdir())):
                    missing.append(rel.as_posix())
            except (OSError, ValueError):
                continue
        return missing

    def run(
        self, *, code: str, timeout_seconds: int = 10, inputs: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("python_probe needs non-empty code")
        try:
            reason = _forbidden_reason(code)
        except SyntaxError as exc:
            raise ValueError(f"experiment does not parse: {exc.msg}") from exc
        if reason:
            raise ValueError(f"refused before execution: {reason}")

        workspace_import = (os.environ.get(WORKSPACE_IMPORT_ENV) == "1"
                            and self.workspace_root is not None)
        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            for key in ("SystemRoot", "PATHEXT", "SYSTEMDRIVE"):
                val = os.environ.get(key)
                if val:
                    env[key] = val
        started = time.monotonic()
        timed_out = False
        with tempfile.TemporaryDirectory(prefix="python_probe_") as cwd:
            copied = self._copy_inputs(inputs, cwd) if inputs else []
            # Files the code names are copied too (read-only, same limits, never keys).
            auto = self._auto_inputs(code, inputs, cwd, copied)
            # -X utf8: the child prints UTF-8 on every OS (-I ignores PYTHONIOENCODING).
            argv, sandbox = _as_nobody(
                [sys.executable, "-I", "-X", "utf8", "-c",
                 _with_runtime_tripwire(
                     _with_workspace_on_path(code, self.workspace_root)
                     if workspace_import else code)], cwd)
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, shell=False
                    argv,
                    cwd=cwd, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=max(1, int(timeout_seconds)), check=False,
                )
                stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = (exc.stdout or b"")
                stderr = (exc.stderr or b"")
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                exit_code = None

        def _cap(text: str) -> tuple[str, bool]:
            if len(text.encode("utf-8", errors="replace")) <= _OUTPUT_CAP:
                return redact_text(text)[0], False
            return redact_text(text[:_OUTPUT_CAP])[0], True

        stdout, stdout_truncated = _cap(stdout)
        stderr, stderr_truncated = _cap(stderr)
        missing = [p for p in self._missing_inputs(code, inputs) if p not in auto]
        notes = []
        if missing:
            dirs = [p for p in missing
                    if self.workspace_root is not None and (self.workspace_root / p).is_dir()]
            notes.append(f"workspace paths named in the code but not passed in "
                         f"inputs: {missing}; the lab cannot see them — add them to inputs")
            if patterns := [p for p in missing if _is_glob(p)]:
                notes.append(f"{patterns} are GLOB PATTERNS matching files in the workspace, "
                             "but the lab starts in an empty temp folder: the glob returns "
                             "NOTHING there, so any count or 'empty' result is not a "
                             "measurement — pass the files in inputs, or search them with "
                             "find_in_files instead")
            if dirs:
                # A directory cannot be one input, only file by file; without this note the
                # advice above cannot be followed.
                notes.append(f"{dirs} are DIRECTORIES: the lab starts in an empty "
                             "temp folder, so a count over them is not a measurement "
                             "of the code — pass the individual files in inputs, or "
                             "read the directory with file_read/find_in_files instead")
        if enveloped := self._enveloped_inputs(copied + auto):
            # Fields read outside the envelope look empty; the tool warns at the moment of use.
            notes.append(f"{enveloped}: every line is an envelope "
                         '{"_integrity": ..., "payload": {...}} — the fields live INSIDE '
                         'payload: use row.get("payload", row) before reading them; '
                         "an 'empty' field read outside payload is not a measurement")
        if not workspace_import and any(
                f"No module named '{pkg}" in stderr for pkg in _WORKSPACE_PACKAGES):
            notes.append(f"the agent's own modules are not importable here because "
                         f"{WORKSPACE_IMPORT_ENV} is off — this ModuleNotFoundError "
                         f"measures the switch, not the code; say so, do not call it a wall")
        return {
            "code": code,
            "inputs": copied + auto,
            **({"auto_inputs": auto} if auto else {}),
            # Non-empty: the output is no result, the code named files it could not see.
            "missing_inputs": missing,
            # Whether agent modules were importable, so an import error is not taken as fact.
            "workspace_import": workspace_import,
            # "nobody": the wall stands; "none (...)": a developer machine without the wall.
            "sandbox": sandbox,
            **({"note": " | ".join(notes)} if notes else {}),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "timed_out": timed_out,
        }

    def execution_status(self, output: Any) -> str:
        """Only a timeout fails: a crashed experiment has measured the crash."""
        if isinstance(output, dict) and output.get("timed_out"):
            return "failed"
        return "success"
