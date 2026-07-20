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
# Exit-code semantics, per command FAMILY (MIR-010).
#
# A command that ran and answered "no" is not a command that failed, and the
# live defect came from having only one word for both: `shell_exec` reported
# `status=success` whenever the subprocess started, so `grep` failing to open
# a file read exactly like `grep` finding nothing.
#
# A blanket `exit != 0 -> failure` is the obvious fix and it is wrong. Measured
# on Windows + git-bash while designing this:
#
#     grep found         exit 0   stderr empty
#     grep no match      exit 1   stderr empty       <- a legitimate ANSWER
#     grep missing file  exit 2   stderr non-empty
#     where not found    exit 1   stderr NON-empty   <- also a legitimate answer
#     which bad option   exit 255
#
# So `where`/`which` write diagnostics on a perfectly ordinary negative
# result. Sharing grep's "stderr means trouble" rule would turn every "not
# found" into a failure. Each family therefore carries its own contract, and
# only the ones that were measured or documented are listed. Anything absent
# gets the conservative unknown contract.
_FAMILY_GREP = frozenset({"grep", "egrep", "fgrep"})
_FAMILY_FINDSTR = frozenset({"findstr"})
_FAMILY_RIPGREP = frozenset({"rg"})
_FAMILY_DIFF = frozenset({"diff", "cmp"})
_FAMILY_TEST = frozenset({"test", "["})
_FAMILY_WHERE = frozenset({"where"})
_FAMILY_WHICH = frozenset({"which"})


def classify_shell_result(
    command: str, *, exit_code: int | None, stderr: str
) -> tuple[str, str]:
    """Return `(execution_status, answer_result)` for one finished command.

    `execution_status` is `success` or `failure`: did the command run
    correctly. `answer_result` is `positive`, `negative` or `not_applicable`:
    what it answered. They are separate facts on purpose — "grep found
    nothing" is a successful execution with a negative answer, and calling
    that a failure is as wrong as calling a crash a success.

    `answer_result` is telemetry about ONE command. It is deliberately not a
    completion signal: it says nothing about whether the task was done,
    whether an episode may be reused, or what a procedure deserves.

    `command` must be the binary that ACTUALLY RAN. `_platform_alias` swaps
    `grep`→`findstr` on Windows, and reading one tool's exit code against
    another tool's contract is the same class of error this function exists
    to remove.

    A family is applied only when the executable is unambiguous. `git` is a
    multiplexer whose exit semantics differ per subcommand, so it takes the
    unknown contract rather than a guess from `argv[1]`.
    """
    if exit_code is None:
        # A timeout killed the process. Absence of an exit code is not a pass.
        return "failure", "not_applicable"
    if exit_code == 0:
        if command in (
            _FAMILY_GREP | _FAMILY_FINDSTR | _FAMILY_RIPGREP
            | _FAMILY_DIFF | _FAMILY_TEST | _FAMILY_WHERE | _FAMILY_WHICH
        ):
            return "success", "positive"
        return "success", "not_applicable"

    has_stderr = bool((stderr or "").strip())

    if command in _FAMILY_GREP or command in _FAMILY_FINDSTR:
        # 1 means "no match" — but only when nothing was written to stderr.
        # The live case arrived as exit 1 + "FINDSTR: Cannot open loop.py".
        if exit_code == 1 and not has_stderr:
            return "success", "negative"
        return "failure", "not_applicable"

    if command in _FAMILY_RIPGREP:
        # ripgrep documents 2 as its error code, so the exit code alone
        # decides; no stderr heuristic is invented for it without a measurement.
        return ("success", "negative") if exit_code == 1 else ("failure", "not_applicable")

    if command in _FAMILY_DIFF or command in _FAMILY_TEST:
        # 1 is the answer "they differ" / "the predicate is false".
        return ("success", "negative") if exit_code == 1 else ("failure", "not_applicable")

    if command in _FAMILY_WHERE or command in _FAMILY_WHICH:
        # Measured: both write diagnostics to stderr on a normal "not found",
        # so stderr is deliberately ignored here.
        return ("success", "negative") if exit_code == 1 else ("failure", "not_applicable")

    return "failure", "not_applicable"


READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "whoami",
        "hostname",
        # Cross-platform PATH-resolver: `where` on Windows, `which` on POSIX.
        # We accept both — `run()` picks the right one at dispatch time.
        "where",
        "which",
        # Source-control inspection. Only the subcommands listed in
        # READ_ONLY_SUBCOMMANDS['git'] are accepted; anything else
        # (push, rm, reset, checkout) is rejected at validation time.
        "git",
        # File-content search. `findstr` is Windows; `grep` is POSIX.
        # `_platform_alias` swaps them so callers can use either.
        "findstr",
        "grep",
    }
)

# Some whitelisted commands are themselves multiplexers (one binary,
# many subcommands). For those we further restrict argv[1] to a tight
# set of read-only subcommands. Anything else is rejected.
#
# Rule of thumb: the subcommand must NOT mutate the working tree, the
# index, refs, the config, or the network. `fetch`/`pull`/`push`/`clone`
# touch the network or refs and are intentionally absent.
READ_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "log", "diff", "status", "show", "branch", "tag",
            "blame", "rev-parse", "describe", "ls-files", "ls-tree",
            "cat-file", "shortlog", "reflog", "name-rev",
        }
    ),
}

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
        "Read-only commands (whoami, hostname, where/which, git "
        "log/diff/status/show/branch/tag/blame, findstr/grep) run "
        "without approval. Mutating commands (mkdir, touch) escalate "
        "to the approval gate and ship with a compensation plan that "
        "can undo the change via :rollback. Shell metacharacters, "
        "absolute paths, and any command outside the tiny built-in "
        "whitelist are rejected before dispatch."
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

        # Subcommand whitelist (e.g. git log/diff/status only).
        sub_allowed = READ_ONLY_SUBCOMMANDS.get(cmd)
        if sub_allowed is not None:
            if len(argv) < 2:
                raise PermissionError(
                    f"shell_exec '{cmd}' requires a subcommand "
                    f"(allowed: {sorted(sub_allowed)})"
                )
            sub = argv[1].strip().lower()
            if sub not in sub_allowed:
                raise PermissionError(
                    f"shell_exec '{cmd} {argv[1]}' — subcommand not in "
                    f"whitelist {sorted(sub_allowed)}"
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
        real_cmd, substituted = self._resolve_binary(cmd)
        exe = shutil.which(real_cmd)
        if exe is None:
            raise FileNotFoundError(
                f"shell_exec cannot find executable '{real_cmd}' on PATH"
            )

        # Arguments are adapted only when the PROGRAM changed underneath the
        # caller; the requested binary already understands its own dialect.
        run_argv = [exe, *self._normalise_argv_for(list(argv), substituted=substituted)[1:]]
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
            # Normalised beside the raw facts, never instead of them: the exit
            # code, stdout and stderr above stay exactly as observed (MIR-010).
            # Classified on `real_cmd` — the binary that actually ran.
            **dict(zip(
                ("execution_status", "answer_result"),
                classify_shell_result(real_cmd, exit_code=exit_code, stderr=stderr_safe),
            )),
            # The live log showed `argv: ['grep', ...]` beside `FINDSTR:` in
            # stderr with nothing connecting them. A substitution that cannot
            # be seen is one nobody can debug.
            "executed_command": real_cmd,
            "binary_substituted": substituted,
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
            # `mkdir`/`touch` are dispatched directly — no platform alias is
            # involved, so the requested command IS the one that ran.
            **dict(zip(
                ("execution_status", "answer_result"),
                classify_shell_result(cmd, exit_code=exit_code, stderr=stderr_safe),
            )),
            "compensation_plan": plan.to_dict(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_binary(self, cmd: str) -> tuple[str, bool]:
        """Pick the binary to run: `(name, substituted)`.

        The requested command WINS when it is installed. The platform
        equivalent is a fallback for when it is absent, not a rewrite applied
        regardless — which is what it used to be, and what made a live cycle
        fail. Measured on the machine where that happened:

            shutil.which("grep")     -> C:\\Program Files\\Git\\usr\\bin\\grep.EXE
            grep -c ^ core/loop.py   -> exit 0, "4093"  (either slash style)
            findstr /C:x core/loop.py-> exit 1, "FINDSTR: Cannot open loop.py"

        Real grep was installed, worked, and was swapped away for a tool that
        could not read the path it was handed.
        """
        if shutil.which(cmd) is not None:
            return cmd, False
        alias = self._platform_alias(cmd)
        if alias != cmd and shutil.which(alias) is not None:
            return alias, True
        return cmd, False

    @staticmethod
    def _normalise_argv_for(argv: list[str], *, substituted: bool) -> list[str]:
        """Make the arguments readable by the program that will actually run.

        Only when a SUBSTITUTION happened: if the requested binary is the one
        executing, its own dialect is already correct and rewriting would be
        damage. On Windows `findstr` reads `/` as a switch prefix, so
        `core/loop.py` parses as `core` plus `/l /o /o /p` — the exact live
        failure.

        Separators only. Flags are NOT translated: `grep -c` has no findstr
        equivalent worth guessing at, and a half-built dialect mapper turns
        every unmapped flag into a new silent failure. After MIR-010 a
        fallback that cannot read its arguments fails visibly, which is the
        honest outcome and leaves the planner able to see it.
        """
        if not substituted or sys.platform != "win32":
            return argv
        out = [argv[0]]
        for elem in argv[1:]:
            # A switch is not a path. `/C:x` and `-c` pass through untouched.
            if elem.startswith(("/", "-")) or "/" not in elem:
                out.append(elem)
            else:
                out.append(elem.replace("/", "\\"))
        return out

    def _platform_alias(self, cmd: str) -> str:
        """Map `where`<->`which` and `findstr`<->`grep` to the binary the
        current OS actually ships.

        Consulted by `_resolve_binary` as a FALLBACK only — see there.
        """
        if cmd == "where" and sys.platform != "win32":
            return "which"
        if cmd == "which" and sys.platform == "win32":
            return "where"
        if cmd == "findstr" and sys.platform != "win32":
            return "grep"
        if cmd == "grep" and sys.platform == "win32":
            return "findstr"
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
    def execution_status(self, output: Any) -> str:
        """The command's own verdict, not the subprocess's.

        `run()` returning means the process started and was captured. Whether
        the COMMAND worked is a separate fact, and reporting only the first
        is what let `exit_code: 1` with `FINDSTR: Cannot open` reach the log
        as `status=success` (MIR-010).
        """
        if isinstance(output, dict):
            return str(output.get("execution_status") or "success")
        return "success"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        """Answers ONE question: does this object match the tool's schema?

        A command that failed produces a perfectly well-formed report OF that
        failure, so it validates. Rejecting it here would conflate "the tool
        returned a correct message saying the command failed" with "the tool
        returned something malformed", and only the second is a validation
        problem. The failure travels in `execution_status` and surfaces as the
        tool-result status (MIR-010).
        """
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
