"""MVP-13.1 — `run_tests` tool: sandboxed pytest runner.

Lets the agent verify its own changes by running the project's test
suite (or a filtered subset). This is the cornerstone of the
self-repair loop: every proposed code change must come with a test
run, and the loop rolls back if the new tests don't pass.

Safety model:
  - never `shell=True`; argv built from a fixed prefix + user-supplied
    paths/pattern. No metacharacters get to the OS shell because there
    IS no shell in the pipeline.
  - cwd is the workspace root; pytest cannot escape it.
  - timeout (default 90 s) — pytest can be long; capped via subprocess.
  - output capped at 1 MiB stdout / 1 MiB stderr to keep audit logs
    bounded; oversized output is truncated with a marker.
  - stdout/stderr pass through `redact_text` before being returned, so
    a credential printed inside a test never lands on disk or LLM.
  - paths are ASCII-only and validated to be relative + inside the
    workspace.

Risk:
  Reversible. Tests SHOULD be read-only in a healthy project, but the
  tool runs arbitrary Python code and conftest.py fixtures can have
  side effects. Mapping to `reversible` forces a Policy Gate review
  without making every test invocation require explicit human approval
  (read_only would let the agent run untrusted code unsupervised).
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.base import Risk, Tool, require_ascii_identifier


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_STDOUT_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_STDERR_BYTES = 1 * 1024 * 1024
MAX_PATHS = 16                       # planner shouldn't submit huge path lists
MAX_PATTERN_LEN = 200                # pytest -k filter
MAX_FAILED_NAMES = 50                # cap on the captured failed test list


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
        "Risk: reversible (subprocess executes Python code under the "
        "workspace; requires approval)."
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
    # risk_for: always reversible. No dynamic downgrade — running tests
    # always involves subprocess and arbitrary user-test code.
    # ------------------------------------------------------------------

    def risk_for(self, arguments: dict[str, Any]) -> Risk:  # noqa: ARG002
        return "reversible"

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
            completed = subprocess.run(
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

        # Late-stage redaction so a credential leaked by a test never
        # lands in audit logs / planner context / model prompts.
        from core.redaction import redact_text  # avoid module-level cycle

        stdout_safe, _ = redact_text(stdout["text"])
        stderr_safe, _ = redact_text(stderr["text"])

        counts = self._parse_counts(stdout_safe + "\n" + stderr_safe)
        failed_tests = self._parse_failed_names(stdout_safe)

        # Bound the audit-log size: only keep the last few KB of stdout
        # (mostly pytest's "short test summary" + traceback tails).
        tail_chars = 4000
        stdout_tail = stdout_safe[-tail_chars:] if len(stdout_safe) > tail_chars else stdout_safe

        coverage_summary = self._parse_coverage(stdout_safe) if coverage else None

        return {
            "command": argv,
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
            # MVP-11 contract: every tool that runs returns a compensation
            # plan (even if noop) so the registry stays uniform.
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
        # total >= passed + failed + errors + skipped (errors may be
        # double-counted by pytest as failed; allow >=, not strict ==)
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
                # Reject any path containing parent-traversal segments.
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
            argv.extend(["-k", pattern])

        argv.extend(cleaned_paths)
        return argv

    @staticmethod
    def _build_env() -> dict[str, str]:
        """Minimal env: keep PATH for the python interpreter to find
        plugins, force UTF-8 I/O so Windows test output isn't mojibake.
        Also forwards all AGENT_* variables so the subprocess inherits
        the provider / budget settings from the parent process."""
        import os

        keep_keys = (
            "PATH",
            "PYTHONPATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
        )
        env = {k: os.environ[k] for k in keep_keys if k in os.environ}
        # Forward all AGENT_* env vars (provider, model, budget, etc.)
        # Exclude AGENT_TEST_TIMEOUT_SECONDS — it's a meta-setting for the
        # tool itself and should not propagate into nested test subprocesses
        # (otherwise tests that check the default timeout would break).
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
        """Extract `FAILED tests/foo.py::Klass::test_bar` lines.

        pytest in -q mode emits these under the "short test summary
        info" footer. We grep for the FAILED token at line start.
        """
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
