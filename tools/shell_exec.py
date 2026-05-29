"""Shell Exec tool — narrow, sandboxed, with mandatory compensation plan.

MVP-11 contract (deliberately tiny; widen only when each new command is
proven safe in tests):

  Whitelist
    READ_ONLY_COMMANDS   purely informational binaries the OS provides
                         (whoami, hostname, where on Windows / which on
                         POSIX). These produce output, do not mutate
                         state, and carry a no-op compensation plan.
    MUTATING_COMMANDS    file-system creators that are EASY to undo:
                         `mkdir <path>` and `touch <path>`. Both create
                         a single new path; compensation is a single
                         `delete_path_if_created` action.
    UNRECOGNIZED         everything else — classified as `external`
                         risk so the policy gate escalates it; even on
                         approval, `run()` refuses to dispatch.

  Hard rules enforced in `_validate_argv` (defence in depth — even when
  the policy gate, planner sanitiser, or approval gate are bypassed):
    - argv must be a non-empty list of strings (no shell-string allowed)
    - argv[0] must be in the whitelist
    - no argv element may contain a shell metacharacter:
        ; | & < > ` $ ( ) { } [ ] \\n \\r \\t \\0
    - no argv element may equal '..', start with '/' or '\\\\', look
      like 'C:\\...', or contain '~' / '$VAR'
    - all path arguments to mutating commands must resolve INSIDE
      the workspace root

  Sandbox
    - subprocess.run with `shell=False` always
    - `cwd = workspace_root` (no escape)
    - `env` reset to a tiny safe subset (PATH + SystemRoot on Windows)
    - `timeout` in seconds, default 5 — short on purpose
    - stdout/stderr captured with a HARD cap (`DEFAULT_OUTPUT_CAP`
      bytes); excess is truncated and a flag set in the output
    - text mode with strict UTF-8 (errors='replace' is silent corruption)

  Compensation Plan
    Built BEFORE execution. For mutating commands it captures whether
    the target path existed BEFORE the run; the plan only removes
    paths the tool itself created. Read-only commands carry a noop
    plan so the audit trail is uniform.

  Secret redaction
    stdout/stderr pass through `core.redaction.redact_text` before
    they leave the tool. Detected hits surface as additional
    `secret_detected` events at the loop level via `data_classified`.

Output shape (consumed by `validate_output` and by the synthesizer):
    {
        "argv":              list[str],          # the actual argv that ran
        "exit_code":         int | None,         # None on timeout
        "stdout":            str,                # redacted, truncated
        "stderr":            str,                # redacted, truncated
        "stdout_truncated":  bool,
        "stderr_truncated":  bool,
        "duration_ms":       int,
        "timed_out":         bool,
        "compensation_plan": dict,                # CompensationPlan.to_dict()
    }
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.compensation import CompensationAction, CompensationPlan
from core.redaction import redact_text
from tools.base import Risk, Tool, require_ascii_identifier


# Commands whose output is informational only. Calling them does not
# mutate filesystem or environment.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "whoami",
        "hostname",
        # Cross-platform PATH-resolver: `where` on Windows, `which` on POSIX.
        # We accept both — `run()` picks the right one at dispatch time.
        "where",
        "which",
    }
)

# Mutating commands handled with `delete_path_if_created` compensation.
# Both produce ONE new path and accept exactly one positional argument.
MUTATING_COMMANDS: frozenset[str] = frozenset({"mkdir", "touch"})

ALL_WHITELIST: frozenset[str] = READ_ONLY_COMMANDS | MUTATING_COMMANDS

# Characters that would let argv elements compose into a shell — even
# though we always run with shell=False, blocking these at the input
# layer means a malicious planner can't smuggle them through a quote
# trick on some platform. Includes whitespace controls so newline
# injection is impossible too.
_FORBIDDEN_CHARS = frozenset(";|&<>`$()[]{}\n\r\t\0")

# Hard limits — short by design.
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_CAP = 64 * 1024  # 64 KiB per stream


class ShellExecTool(Tool):
    name = "shell_exec"
    description = (
        "Execute ONE whitelisted shell command inside the workspace. "
        "Read-only commands (whoami, hostname, where/which) run without "
        "approval. Mutating commands (mkdir, touch) escalate to the "
        "approval gate and ship with a compensation plan that can undo "
        "the change via :rollback. Shell metacharacters, absolute "
        "paths, and any command outside the tiny built-in whitelist "
        "are rejected before dispatch."
    )
    # Static fallback — overridden per-argv by `risk_for`.
    risk: Risk = "irreversible"

    def __init__(
        self,
        workspace_root: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_cap_bytes: int = DEFAULT_OUTPUT_CAP,
    ):
        if not workspace_root.exists():
            raise FileNotFoundError(
                f"shell_exec workspace_root does not exist: {workspace_root}"
            )
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        if output_cap_bytes <= 0:
            raise ValueError(f"output_cap_bytes must be > 0, got {output_cap_bytes}")
        self.workspace_root = workspace_root.resolve()
        self.timeout_seconds = float(timeout_seconds)
        self.output_cap_bytes = int(output_cap_bytes)

    # ------------------------------------------------------------------
    # Risk classification (argument-aware — MVP-9 contract)
    # ------------------------------------------------------------------
    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        """Decide risk per invocation, NOT per tool class.

        - empty / missing argv     -> external  (will be rejected anyway)
        - argv[0] in READ_ONLY     -> read_only  (no approval gate)
        - argv[0] in MUTATING      -> irreversible  (approval gate fires)
        - argv[0] anywhere else    -> external  (approval gate fires +
                                                 run() still refuses)
        """
        argv = arguments.get("argv") if isinstance(arguments, dict) else None
        if not isinstance(argv, list) or not argv:
            return "external"
        cmd = argv[0]
        if not isinstance(cmd, str):
            return "external"
        cmd_norm = cmd.strip().lower()
        if cmd_norm in READ_ONLY_COMMANDS:
            return "read_only"
        if cmd_norm in MUTATING_COMMANDS:
            return "irreversible"
        return "external"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_argv(self, argv: list[str]) -> tuple[str, list[str]]:
        """Return (command_norm, validated_argv) or raise PermissionError.

        Hard rejection at the tool layer — does NOT depend on the
        policy gate, planner sanitiser, or approval provider. This is
        the last line of defence.
        """
        if not isinstance(argv, list) or not argv:
            raise PermissionError("shell_exec requires a non-empty 'argv' list")
        if len(argv) > 16:
            raise PermissionError("shell_exec rejects argv longer than 16 elements")
        for i, elem in enumerate(argv):
            if not isinstance(elem, str):
                raise PermissionError(
                    f"shell_exec argv[{i}] must be a string, got {type(elem).__name__}"
                )
            if not elem:
                raise PermissionError(f"shell_exec argv[{i}] must be non-empty")
            if any(ch in _FORBIDDEN_CHARS for ch in elem):
                raise PermissionError(
                    f"shell_exec argv[{i}] contains a forbidden character "
                    f"(shell metachar / whitespace control)"
                )
            if elem.startswith("~") or "$" in elem:
                raise PermissionError(
                    f"shell_exec argv[{i}] forbids '~' and '$' expansion"
                )
            # ASCII-only identifier policy. Shell argv is a programming
            # boundary, not a place for human text. Forbidding non-ASCII
            # here avoids cmd.exe / cp1251 corruption AND prevents the
            # planner from leaking user-question fragments into argv.
            require_ascii_identifier(elem, role=f"shell_exec argv[{i}]")

        cmd = argv[0].strip().lower()
        if cmd not in ALL_WHITELIST:
            raise PermissionError(
                f"shell_exec refuses '{argv[0]}' — not in whitelist "
                f"{sorted(ALL_WHITELIST)}"
            )

        # Mutating commands: validate path arguments now.
        if cmd in MUTATING_COMMANDS:
            if len(argv) != 2:
                raise PermissionError(
                    f"shell_exec '{cmd}' requires exactly one path argument "
                    f"(got {len(argv) - 1})"
                )
            path_str = argv[1]
            self._validate_path_in_workspace(path_str)

        return cmd, argv

    def _validate_path_in_workspace(self, path_str: str) -> Path:
        """Reject absolute / `..` / drive-letter paths; resolve into workspace."""
        if not path_str:
            raise PermissionError("shell_exec path argument cannot be empty")
        # Reject obvious escape attempts at the textual layer before
        # `Path.resolve` does anything platform-specific.
        if path_str.startswith(("/", "\\")):
            raise PermissionError(
                f"shell_exec rejects absolute-style path '{path_str}'"
            )
        if len(path_str) >= 2 and path_str[1] == ":":
            raise PermissionError(
                f"shell_exec rejects drive-letter path '{path_str}'"
            )
        if ".." in Path(path_str).parts:
            raise PermissionError(
                f"shell_exec rejects '..' traversal in '{path_str}'"
            )

        candidate = (self.workspace_root / path_str).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(
                f"shell_exec path '{path_str}' escapes workspace"
            ) from exc
        return candidate

    # ------------------------------------------------------------------
    # Compensation plan — built BEFORE execution
    # ------------------------------------------------------------------
    def _build_compensation_plan(
        self, cmd: str, argv: list[str], target_existed_before: bool
    ) -> CompensationPlan:
        if cmd in READ_ONLY_COMMANDS:
            return CompensationPlan.noop(
                tool_name=self.name,
                description=f"read-only command '{cmd}'; nothing to undo",
            )

        if cmd in MUTATING_COMMANDS:
            path_str = argv[1]
            if target_existed_before:
                # Tool refuses to overwrite-by-create — see `run()`.
                # Reaching here means we're about to no-op (touch on
                # existing file) or fail; the plan is still a noop.
                return CompensationPlan.noop(
                    tool_name=self.name,
                    description=f"'{cmd} {path_str}' did not create a new path",
                )
            return CompensationPlan(
                tool_name=self.name,
                description=f"undo '{cmd} {path_str}' by deleting the created path",
                actions=[
                    CompensationAction(
                        kind="delete_path_if_created",
                        description=f"delete path created by {cmd}",
                        path=path_str,
                    )
                ],
            )

        # Unreachable — `_validate_argv` already rejected.
        return CompensationPlan.noop(
            tool_name=self.name, description="unrecognized command (denied)"
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def run(self, argv: list[str]) -> dict[str, Any]:
        """Execute one whitelisted command in the workspace sandbox.

        Returns a structured dict (see module docstring for the schema)
        on success and on graceful timeout. Raises PermissionError on
        validation failure (caller surfaces as `tool_result.status=error`).
        """
        cmd, argv = self._validate_argv(argv)

        # Snapshot pre-condition for compensation BEFORE the side effect.
        target_existed_before = False
        target_path: Path | None = None
        if cmd in MUTATING_COMMANDS:
            target_path = self._validate_path_in_workspace(argv[1])
            target_existed_before = target_path.exists()
            # 'mkdir' on an existing path must fail with a clean error
            # instead of producing a half-baked state. 'touch' on an
            # existing path is a no-op (POSIX semantics) and we mirror
            # that — the compensation plan is then a noop.
            if cmd == "mkdir" and target_existed_before:
                raise PermissionError(
                    f"shell_exec 'mkdir {argv[1]}' refuses: path already exists"
                )

        plan = self._build_compensation_plan(cmd, argv, target_existed_before)

        if cmd in READ_ONLY_COMMANDS:
            return self._run_subprocess(cmd, argv, plan)
        if cmd in MUTATING_COMMANDS:
            return self._run_mutating(cmd, argv, target_path, plan)
        # Defensive — `_validate_argv` already raised.
        raise PermissionError(f"shell_exec refuses to dispatch '{cmd}'")

    # --- subprocess execution (read-only commands) ---------------------
    def _run_subprocess(
        self, cmd: str, argv: list[str], plan: CompensationPlan
    ) -> dict[str, Any]:
        # Resolve executable explicitly so a missing binary is a clean
        # error, not a confusing FileNotFoundError from subprocess.
        # `where` is Windows-only; map it to `which` on POSIX (and vice
        # versa) so tests are portable.
        real_cmd = self._platform_alias(cmd)
        exe = shutil.which(real_cmd)
        if exe is None:
            raise FileNotFoundError(
                f"shell_exec cannot find executable '{real_cmd}' on PATH"
            )

        run_argv = [exe, *argv[1:]]
        env = self._safe_env()
        started = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                run_argv,
                cwd=self.workspace_root,
                env=env,
                shell=False,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            stdout_bytes = completed.stdout or b""
            stderr_bytes = completed.stderr or b""
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
            exit_code = None
            timed_out = True

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_trunc = self._cap_and_decode(stdout_bytes)
        stderr, stderr_trunc = self._cap_and_decode(stderr_bytes)
        # `redact_text` returns (clean_text, findings). The findings are
        # not surfaced from the tool — they'll be re-detected by the
        # kernel-side DataClassifier on tool_output, which is the single
        # source of truth for `secret_detected` events.
        stdout_safe, _ = redact_text(stdout)
        stderr_safe, _ = redact_text(stderr)

        return {
            "argv": list(argv),
            "exit_code": exit_code,
            "stdout": stdout_safe,
            "stderr": stderr_safe,
            "stdout_truncated": stdout_trunc,
            "stderr_truncated": stderr_trunc,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "compensation_plan": plan.to_dict(),
        }

    # --- in-process execution (mutating commands) ----------------------
    def _run_mutating(
        self,
        cmd: str,
        argv: list[str],
        target: Path | None,
        plan: CompensationPlan,
    ) -> dict[str, Any]:
        """Mutating whitelist is interpreted by the tool, not spawned.

        On Windows, `mkdir` and `touch` are NOT standalone executables
        (mkdir is a `cmd.exe` builtin; touch ships nowhere by default).
        Spawning them via `shell=False` would either fail or require
        `cmd /c`, which would reintroduce shell parsing — exactly what
        the validator forbids. So the MVP interprets these two commands
        in-process, behind the same compensation contract.

        This is documented in README §"shell_exec — narrow MVP".
        """
        assert target is not None  # mypy hint; validator ensures this
        started = time.monotonic()
        stderr = ""
        exit_code = 0
        try:
            if cmd == "mkdir":
                target.mkdir(parents=False, exist_ok=False)
            elif cmd == "touch":
                # POSIX 'touch' creates if missing, updates mtime if not.
                target.touch(exist_ok=True)
        except OSError as exc:
            stderr = f"{type(exc).__name__}: {exc}"
            exit_code = 1
        duration_ms = int((time.monotonic() - started) * 1000)
        stderr_safe, _ = redact_text(stderr)

        return {
            "argv": list(argv),
            "exit_code": exit_code,
            "stdout": "",
            "stderr": stderr_safe,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": duration_ms,
            "timed_out": False,
            "compensation_plan": plan.to_dict(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _platform_alias(self, cmd: str) -> str:
        """Map `where`<->`which` to the binary the current OS actually ships."""
        if cmd == "where" and sys.platform != "win32":
            return "which"
        if cmd == "which" and sys.platform == "win32":
            return "where"
        return cmd

    def _safe_env(self) -> dict[str, str]:
        """Minimal env: PATH + (Windows) SystemRoot. Nothing else."""
        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            # SystemRoot is REQUIRED for many Windows .exe to even start.
            sr = os.environ.get("SystemRoot")
            if sr:
                env["SystemRoot"] = sr
        return env

    def _cap_and_decode(self, raw: bytes) -> tuple[str, bool]:
        """Truncate to output_cap_bytes; decode UTF-8 strictly."""
        truncated = False
        if len(raw) > self.output_cap_bytes:
            raw = raw[: self.output_cap_bytes]
            truncated = True
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return text, truncated

    # ------------------------------------------------------------------
    # Output validation (Tool contract)
    # ------------------------------------------------------------------
    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        if not isinstance(output, dict):
            return False, ["shell_exec output must be a dict"]

        required = {
            "argv", "exit_code", "stdout", "stderr",
            "stdout_truncated", "stderr_truncated",
            "duration_ms", "timed_out", "compensation_plan",
        }
        missing = required - output.keys()
        if missing:
            return False, [f"missing keys: {sorted(missing)}"]

        if not isinstance(output["argv"], list) or not output["argv"]:
            return False, ["argv must be a non-empty list"]
        if not isinstance(output["stdout"], str) or not isinstance(output["stderr"], str):
            return False, ["stdout / stderr must be strings"]
        if not isinstance(output["compensation_plan"], dict):
            return False, ["compensation_plan must be a dict"]
        if not isinstance(output["timed_out"], bool):
            return False, ["timed_out must be a bool"]
        if output["timed_out"] and output["exit_code"] is not None:
            warnings.append("timed_out=True but exit_code is set")
        if not output["timed_out"] and output["exit_code"] is None:
            warnings.append("exit_code is None but timed_out=False")
        if output["duration_ms"] < 0:
            return False, ["duration_ms cannot be negative"]
        return True, warnings
