"""run_tests tool: run pytest in the workspace and return structured results."""
from __future__ import annotations

import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.code_state import describe_code_state
from tools.base import Risk, Tool, require_ascii_identifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bounds a hang, not the duration: about twice the slowest measured full-suite run.
DEFAULT_TIMEOUT_SECONDS = 900.0
MAX_STDOUT_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_STDERR_BYTES = 1 * 1024 * 1024
MAX_PATHS = 16                       # planner shouldn't submit huge path lists
MAX_PATTERN_LEN = 200                # pytest -k filter
MAX_FAILED_NAMES = 50                # cap on the captured failed test list

_TIMEOUT_NOTE = (
    "NOTE: run_tests TIMED OUT before pytest finished. "
    "Progress characters (including F) from a partial run are NOT a final "
    "failure list. Treat timed_out=True as inconclusive; re-run a narrower "
    "paths/pattern subset or raise AGENT_TEST_TIMEOUT_SECONDS."
)


# pytest's terminal summary lines like:
#   ===== 12 passed, 1 failed, 2 errors, 3 skipped in 2.45s =====
_SUMMARY_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|warnings?|deselected)",
    re.IGNORECASE,
)

# pytest-cov TOTAL line:
#   TOTAL                              1234    456    63%
_COVERAGE_TOTAL_RE = re.compile(
    r"^TOTAL\s+\d+\s+\d+\s+(\d+)%",
    re.MULTILINE,
)
# Individual module line:
#   core/llm.py                         302     12    96%   45-50, 120
_COVERAGE_LINE_RE = re.compile(
    r"^(\S+\.py)\s+\d+\s+\d+\s+(\d+)%",
    re.MULTILINE,
)


#: Files whose change alters what a pytest run executes: code and pytest config.
_CODE_SUFFIXES = (".py", ".pth", ".ini", ".cfg", ".toml")
#: Folders pytest does not descend into by default (`norecursedirs`).
_UNCOLLECTED_DIRS = frozenset({"__pycache__", "node_modules", "venv"})
_GIT_TIMEOUT_SECONDS = 10.0


def _is_code(git_path: str) -> bool:
    """True for a path, as git prints it, that names code or pytest config."""
    return git_path.strip().strip('"').lower().endswith(_CODE_SUFFIXES)


def _collected(file_path: str, targets: list[str]) -> bool:
    """True when pytest, given `targets`, would collect or load `file_path`."""
    file_path = file_path.strip().strip('"')
    for target in targets:
        if target == ".":
            inner = file_path
        elif file_path == target:
            return True
        elif file_path.startswith(target + "/"):
            inner = file_path[len(target) + 1:]
        else:
            continue
        folders = inner.split("/")[:-1]
        if not any(d.startswith(".") or d in _UNCOLLECTED_DIRS for d in folders):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class RunTestsTool(Tool):
    """Run `pytest` inside the workspace and return structured results."""

    name = "run_tests"
    description = (
        "Run the project's pytest suite (or a filtered subset). "
        "Returns a structured summary including pass/fail counts, "
        "names of failed tests, exit code, and a tail of stdout. "
        "Use this to verify code changes before applying them. "
        "Risk: reversible while the workspace code is what git HEAD holds; "
        "a run that would execute new or changed code (untracked, modified "
        "or git-ignored test targets) requires approval."
    )
    risk: Risk = "reversible"

    def __init__(
        self,
        workspace_root: Path,
        *,
        timeout_seconds: float | None = None,
        python_executable: str | None = None,
    ):
        if not workspace_root.is_dir():
            raise ValueError(
                f"workspace_root must be an existing directory, got {workspace_root}"
            )
        # Allow override via env var (useful for daemon / CI with large suites)
        if timeout_seconds is None:
            import os as _os
            env_val = _os.environ.get("AGENT_TEST_TIMEOUT_SECONDS")
            timeout_seconds = float(env_val) if env_val else DEFAULT_TIMEOUT_SECONDS
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        self.workspace_root = workspace_root.resolve()
        self.timeout_seconds = float(timeout_seconds)
        # Allow injection in tests; default to the interpreter running us.
        self.python_executable = python_executable or sys.executable

    # ------------------------------------------------------------------
    # risk_for: reversible only while the code is what git HEAD holds.
    # ------------------------------------------------------------------

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        """`irreversible` (approval) when the run would execute new or changed code."""
        paths = arguments.get("paths") if isinstance(arguments, dict) else None
        try:
            unreviewed = self._runs_unreviewed_code(paths)
        except Exception:  # noqa: BLE001 — a gate that cannot tell asks
            unreviewed = True
        return "irreversible" if unreviewed else "reversible"

    def _runs_unreviewed_code(self, paths: Any) -> bool:
        """True if git shows untracked, changed or ignored-but-targeted code; False without git."""
        from tools.shell_exec import trusted_which

        git = trusted_which("git", self.workspace_root)
        if git is None:
            return False
        changed = self._git_lines(git, "status", "--porcelain", "--untracked-files=all", "--", ".")
        if changed is None:
            return False
        if any(_is_code(line[3:]) for line in changed):
            return True
        targets = ["tests"] if paths is None else [
            posixpath.normpath(p.replace("\\", "/")) for p in paths if isinstance(p, str) and p
        ]
        if not targets:
            return False
        # A target git ignores is invisible to `status` yet runs all the same.
        ignored = self._git_lines(
            git, "ls-files", "--others", "--ignored", "--exclude-standard", "--", *targets
        )
        if ignored is None:
            return True
        return any(_is_code(line) and _collected(line, targets) for line in ignored)

    def _git_lines(self, git: str, *args: str) -> list[str] | None:
        """Output lines of one read-only git call; None when there is no repository."""
        from core.bounded_subprocess import run_with_tree_kill

        env = {**self._build_env(), "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
        stdout, stderr, exit_code, timed_out = run_with_tree_kill(
            [git, *args], cwd=self.workspace_root, env=env, timeout=_GIT_TIMEOUT_SECONDS,
        )
        if timed_out or exit_code != 0:
            if not timed_out and b"not a git repository" in stderr.lower():
                return None
            raise RuntimeError(f"git {args[0]} gave no answer (exit {exit_code})")
        return stdout.decode("utf-8", errors="replace").splitlines()

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        paths: list[str] | None = None,
        pattern: str | None = None,
        coverage: bool = False,
    ) -> dict[str, Any]:
        argv = self._build_argv(paths=paths, pattern=pattern, coverage=coverage)
        env = self._build_env()
        started = time.monotonic()

        timed_out = False
        try:
            completed = subprocess.run(  # noqa: S603 — _build_argv validates: workspace-relative paths, no leading dash, ASCII
                argv,
                cwd=str(self.workspace_root),
                env=env,
                capture_output=True,
                shell=False,
                timeout=self.timeout_seconds,
                check=False,
            )
            stdout_b = completed.stdout
            stderr_b = completed.stderr
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_b = exc.stdout or b""
            stderr_b = exc.stderr or b""
            exit_code = None

        duration_ms = int((time.monotonic() - started) * 1000)

        stdout = self._decode_and_cap(stdout_b, MAX_STDOUT_BYTES)
        stderr = self._decode_and_cap(stderr_b, MAX_STDERR_BYTES)

        # Redact so a credential printed by a test never reaches logs or model prompts.
        from core.redaction import redact_text  # avoid module-level cycle

        stdout_safe, _ = redact_text(stdout["text"])
        stderr_safe, _ = redact_text(stderr["text"])

        counts = self._parse_counts(stdout_safe + "\n" + stderr_safe)
        failed_tests = self._parse_failed_names(stdout_safe)
        # On timeout the progress-bar F glyphs are unreliable; keep only named failures.
        if timed_out and not failed_tests:
            counts = {
                "passed": counts["passed"],
                "failed": 0,
                "errors": 0,
                "skipped": counts["skipped"],
                "total": counts["passed"] + counts["skipped"],
            }

        # Keep only the stdout tail (summary and tracebacks) to bound the audit log.
        tail_chars = 4000
        stdout_tail = stdout_safe[-tail_chars:] if len(stdout_safe) > tail_chars else stdout_safe
        if timed_out:
            stdout_tail = _TIMEOUT_NOTE + "\n" + stdout_tail

        coverage_summary = self._parse_coverage(stdout_safe) if coverage else None

        return {
            "command": argv,
            # Which code was tested: a failure in a stale copy is not a project defect.
            "code_state": describe_code_state(self.workspace_root),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "errors": counts["errors"],
            "skipped": counts["skipped"],
            "total": counts["total"],
            "failed_tests": failed_tests[:MAX_FAILED_NAMES],
            "coverage": coverage_summary,
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_safe[-tail_chars:] if len(stderr_safe) > tail_chars else stderr_safe,
            # Every tool returns a compensation plan, here a no-op, so the registry stays uniform.
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "test run has no compensation"}],
                "tool_name": self.name,
                "description": "run_tests is read-only-ish; no rollback needed",
            },
        }

    # ------------------------------------------------------------------
    # validate_output
    # ------------------------------------------------------------------

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["run_tests output must be a dict"]
        required = {
            "command", "exit_code", "timed_out", "duration_ms",
            "passed", "failed", "errors", "skipped", "total",
            "failed_tests", "stdout_truncated", "stderr_truncated",
            "stdout_tail", "stderr_tail", "compensation_plan",
        }
        missing = required - output.keys()
        if missing:
            return False, [f"missing keys: {sorted(missing)}"]

        if not isinstance(output["command"], list) or not output["command"]:
            return False, ["command must be a non-empty list"]
        if not isinstance(output["timed_out"], bool):
            return False, ["timed_out must be a bool"]
        if output["duration_ms"] < 0:
            return False, ["duration_ms cannot be negative"]
        for k in ("passed", "failed", "errors", "skipped", "total"):
            if not isinstance(output[k], int) or output[k] < 0:
                return False, [f"{k} must be a non-negative int"]
        # Errors may be double-counted as failures, so only require total >= the counted outcomes.
        if output["total"] < output["passed"] + output["failed"] + output["skipped"]:
            return False, ["total smaller than counted outcomes"]
        if not isinstance(output["failed_tests"], list):
            return False, ["failed_tests must be a list"]
        if not isinstance(output["compensation_plan"], dict):
            return False, ["compensation_plan must be a dict"]

        warnings: list[str] = []
        if output["timed_out"] and output["exit_code"] is not None:
            warnings.append("timed_out=True but exit_code is set")
        return True, warnings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_argv(
        self, *, paths: list[str] | None, pattern: str | None, coverage: bool = False
    ) -> list[str]:
        argv: list[str] = [
            self.python_executable, "-m", "pytest",
            "-q", "--tb=short", "--no-header",
        ]
        if coverage:
            argv += ["--cov=.", "--cov-report=term-missing", "--cov-config=pytest.ini"]
        # Paths: must each resolve INSIDE the workspace; ASCII-only.
        cleaned_paths: list[str] = []
        if paths is None:
            cleaned_paths.append("tests")  # safe default
        else:
            if not isinstance(paths, list):
                raise ValueError("paths must be a list of strings")
            if len(paths) > MAX_PATHS:
                raise ValueError(f"too many paths (max {MAX_PATHS})")
            for i, p in enumerate(paths):
                require_ascii_identifier(p, role=f"run_tests paths[{i}]")
                # A leading dash is read as an option (`-p <module>` imports code); refuse by shape.
                if p.startswith("-"):
                    raise PermissionError(
                        f"paths[{i}]={p!r} starts with '-' — refused"
                    )
                if ".." in Path(p).parts:
                    raise PermissionError(
                        f"paths[{i}] contains '..' — refused"
                    )
                resolved = (self.workspace_root / p).resolve()
                try:
                    resolved.relative_to(self.workspace_root)
                except ValueError:
                    raise PermissionError(
                        f"paths[{i}]={p!r} resolves outside workspace"
                    ) from None
                cleaned_paths.append(p)
        # Pattern: keep it short and ASCII; pytest passes it to -k.
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ValueError("pattern must be a string or None")
            if len(pattern) > MAX_PATTERN_LEN:
                raise ValueError(
                    f"pattern too long ({len(pattern)} > {MAX_PATTERN_LEN})"
                )
            require_ascii_identifier(pattern, role="run_tests pattern")
            if pattern.startswith("-"):
                raise PermissionError(f"pattern {pattern!r} starts with '-' — refused")
            argv.extend(["-k", pattern])

        argv.extend(cleaned_paths)
        return argv

    @staticmethod
    def _build_env() -> dict[str, str]:
        """Minimal env: PATH and friends, all AGENT_* settings, and forced UTF-8 output."""
        import os

        keep_keys = (
            "PATH",
            # Without PATHEXT, Windows cannot resolve `python` to `python.exe` in the subprocess.
            "PATHEXT",
            "PYTHONPATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
        )
        env = {k: os.environ[k] for k in keep_keys if k in os.environ}
        # Forward AGENT_* settings, except the tool's own timeout, which would break
        # tests that check the default.
        _skip = {"AGENT_TEST_TIMEOUT_SECONDS"}
        for k, v in os.environ.items():
            if k.startswith("AGENT_") and k not in _skip:
                env[k] = v
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    @staticmethod
    def _decode_and_cap(data: bytes, cap: int) -> dict[str, Any]:
        truncated = False
        if len(data) > cap:
            data = data[:cap]
            truncated = True
        text = data.decode("utf-8", errors="replace")
        return {"text": text, "truncated": truncated}

    @staticmethod
    def _parse_counts(text: str) -> dict[str, int]:
        """Read pytest's summary line into structured counts."""
        out = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}
        for n_str, kind in _SUMMARY_RE.findall(text):
            n = int(n_str)
            k = kind.lower()
            if k.startswith("pass"):
                out["passed"] = n
            elif k.startswith("fail"):
                out["failed"] = n
            elif k.startswith("error"):
                out["errors"] = n
            elif k.startswith("skip"):
                out["skipped"] = n
        out["total"] = out["passed"] + out["failed"] + out["errors"] + out["skipped"]
        return out

    @staticmethod
    def _parse_coverage(text: str) -> dict[str, Any] | None:
        """Extract total coverage % and the 10 worst-covered modules."""
        m = _COVERAGE_TOTAL_RE.search(text)
        if not m:
            return None
        total_pct = int(m.group(1))
        modules: list[dict[str, Any]] = []
        for line_m in _COVERAGE_LINE_RE.finditer(text):
            modules.append({"file": line_m.group(1), "coverage_pct": int(line_m.group(2))})
        # Sort ascending so the worst-covered files come first.
        modules.sort(key=lambda d: d["coverage_pct"])
        return {
            "total_pct": total_pct,
            "worst_covered": modules[:10],
        }

    @staticmethod
    def _parse_failed_names(text: str) -> list[str]:
        """Names from the `FAILED ...` and `ERROR ...` lines of pytest's short summary."""
        names: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("FAILED "):
                # `FAILED tests/foo.py::test_x - reason...`
                rest = stripped[len("FAILED "):]
                name = rest.split(" - ", 1)[0].strip()
                if name:
                    names.append(name)
            elif stripped.startswith("ERROR "):
                rest = stripped[len("ERROR "):]
                name = rest.split(" - ", 1)[0].strip()
                if name:
                    names.append(name)
        return names
