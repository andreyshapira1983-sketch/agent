"""Shell Exec tool — narrow, sandboxed, with mandatory compensation plan.

MVP-11 contract (deliberately tiny; widen only when each new command is
proven safe in tests):

Hard rules enforced in `_validate_argv` (defence in depth — they hold even
if the policy gate, planner sanitiser or approval gate are bypassed):

- argv is a non-empty list of strings; no shell-string form is accepted
- argv[0] is in the whitelist
- no element carries a shell metacharacter: ; | & < > ` $ ( ) [ ] \\n \\r \\t \\0
  (braces are allowed: they compose nothing with shell=False, and the agent's
  own `{{step:N.output}}` syntax must be searchable)
- no element equals '..', starts with '/' or '\\\\', looks like 'C:\\...',
  or contains '~' / '$VAR'
- every path argument to a mutating command resolves INSIDE the workspace

Sandbox:

- `subprocess.Popen` with `shell=False`, always; stdin is /dev/null
- `cwd = workspace_root`, no escape
- `env` reset to a tiny safe subset (PATH + SystemRoot on Windows)
- `timeout` in seconds, default 5 — short on purpose; on expiry the WHOLE
  process tree is killed and the tool returns at once (see
  `core.bounded_subprocess` for why `subprocess.run` cannot do this)
- stdout/stderr capped at `DEFAULT_OUTPUT_CAP` bytes; excess truncated and
  flagged in the output
- text mode with strict UTF-8 (`errors='replace'` is silent corruption)

Compensation Plan Built BEFORE execution. For mutating commands it captures
whether the target path existed BEFORE the run; the plan only removes paths
the tool itself created. Read-only commands carry a noop plan so the audit
trail is uniform.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.bounded_subprocess import (
    TREE_KILL_GRACE_SECONDS,
    kill_process_tree,
    run_with_tree_kill,
)
from core.compensation import CompensationAction, CompensationPlan
from core.control_files import control_file_hit, refuse_message
from core.redaction import redact_text
from tools.base import Risk, Tool, require_ascii_identifier
from tools.file_read import _is_credential_path

# Commands whose output is informational only: calling them mutates neither
# the filesystem nor the environment. Exit-code semantics are per command
# FAMILY, because a command that ran and answered "no" is not a command that
# failed — with one word for both, `grep` failing to open a file read exactly
# like `grep` finding nothing.
#
# A blanket `exit != 0 -> failure` is the obvious fix and it is wrong.
# Measured on Windows + git-bash:
#
#     grep found         exit 0   stderr empty
#     grep no match      exit 1   stderr empty       <- a legitimate ANSWER
#     grep missing file  exit 2   stderr non-empty
#     where not found    exit 1   stderr NON-empty   <- also a legitimate answer
#     which bad option   exit 255
#
# So `where`/`which` write diagnostics on an ordinary negative result, and
# sharing grep's "stderr means trouble" rule would turn every "not found"
# into a failure. Each family carries its own contract; only measured or
# documented ones are listed, and anything absent gets the conservative
# unknown contract.
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


#: Установка пакетов себе. Открыто словом оператора 2026-09-23.
#:
#: Почему `uv`, а не `pip`: в окружении агента pip НЕТ вовсе — оно создано
#: uv, и в `.venv/bin` лежат только python и uvicorn. Системный `pip` ставит
#: в ДРУГОЙ интерпретатор: замер 2026-09-23 — `pdfplumber` есть в окружении
#: агента и отсутствует в системном. Открыть `pip` значило бы дать агенту
#: ставить пакеты, которых он потом не увидит: тихая ловушка того самого
#: класса, который мы весь день и чиним.
_FAMILY_UV = frozenset({"uv"})

#: Подкоманды `uv pip`, которые ничего не меняют.
_UV_PIP_READ = frozenset({"list", "show", "freeze", "tree"})
#: Подкоманда, которая ставит. Обратима: поставленный пакет снимается.
_UV_PIP_WRITE = frozenset({"install"})

#: Имя пакета и ничего больше: «pkg», «pkg==1.2.3».
#: Дополнения в квадратных скобках («httpx[http2]») отвергаются РАНЬШЕ —
#: общим запретом на метасимволы оболочки в argv. Здесь они описаны в
#: выражении, но недостижимы; переписывать общий запрет ради них не стоит:
#: пакет ставится и без дополнений.
#: Флаги запрещены ЦЕЛИКОМ, и это одно правило закрывает сразу всё опасное:
#: `--index-url` и `--find-links` (пакет с чужого сервера), `--upgrade`
#: (сломать закреплённое окружение, на котором агент сам и работает),
#: `--target` (положить мимо окружения). Адреса и пути тоже запрещены: ни
#: `git+https://…`, ни `.`, ни `/path` — только имя из общего хранилища.
_PKG_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # имя
    r"(\[[A-Za-z0-9,._-]+\])?"              # необязательные дополнения
    r"((==|>=|<=|~=|!=|>|<)[0-9A-Za-z.*+-]+)?$"  # необязательная версия
)


#: Флаги чтения `uv pip`: только вид вывода. Всё, что принимает путь или адрес
#: (`--python`, `--target`, `--prefix`, `--index-url`, `--config-file`,
#: `--directory`), и всё, что ходит в сеть (`--outdated`), сюда не входит.
_UV_PIP_READ_FLAGS: dict[str, frozenset[str]] = {
    "list": frozenset({"--editable", "-e", "--exclude-editable", "--format", "--strict"}),
    "show": frozenset({"--files", "-f", "--strict"}),
    "freeze": frozenset({"--exclude-editable", "--strict"}),
    "tree": frozenset({"--depth", "-d", "--prune", "--package", "--no-dedupe",
                       "--invert", "--strict", "--show-version-specifiers"}),
}
#: Значение флага чтения: слово или число, без пути и адреса.
_UV_FLAG_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_uv_read_argv(sub: str, rest: list[str]) -> None:
    """Чтение `uv pip`: флаги из списка, остальное — имена без путей."""
    allowed = _UV_PIP_READ_FLAGS[sub]
    for token in (a.strip() for a in rest):
        if token.startswith("-"):
            flag, eq, value = token.partition("=")
            if flag not in allowed or (eq and not _UV_FLAG_VALUE_RE.match(value)):
                raise PermissionError(
                    f"shell_exec 'uv pip {sub}' does not take '{token}' "
                    f"(allowed flags: {', '.join(sorted(allowed))}): a flag "
                    "could point the read at another environment, file or server"
                )
        elif not _PKG_NAME_RE.match(token):
            raise PermissionError(
                f"shell_exec 'uv pip {sub}' takes names only, got '{token}': "
                "an address or a path is not a name"
            )


def _validate_uv_argv(argv: list[str]) -> None:
    """Пропускает только `uv pip install <имена>` и чтение `uv pip list/show`.

    Ставить себе инструмент — сильная сторона агента, а не нарушение (слово
    оператора 2026-09-23 о его же попытке поставить пакет: «он наоборот
    сделал правильно, а не писал мне сообщение каждые пять минут»). Поэтому
    установка идёт БЕЗ одобрения — но только из общего хранилища, только по
    имени, и только в его собственное окружение.
    """
    if len(argv) < 3 or argv[1].strip().lower() != "pip":
        raise PermissionError(
            "shell_exec 'uv' allows only 'uv pip …' "
            f"(install / {', '.join(sorted(_UV_PIP_READ))})"
        )
    sub = argv[2].strip().lower()
    if sub not in (_UV_PIP_READ | _UV_PIP_WRITE):
        raise PermissionError(
            f"shell_exec 'uv pip {sub}' is not allowed "
            f"(allowed: install, {', '.join(sorted(_UV_PIP_READ))})"
        )
    if sub not in _UV_PIP_WRITE:
        _validate_uv_read_argv(sub, argv[3:])
        return
    names = [a.strip() for a in argv[3:] if a.strip()]
    if not names:
        raise PermissionError("shell_exec 'uv pip install' requires a package name")
    for name in names:
        if name.startswith("-"):
            raise PermissionError(
                f"shell_exec 'uv pip install' takes no flags, got '{name}': "
                "a flag could point the install at another server, break the "
                "pinned environment, or land outside it"
            )
        if not _PKG_NAME_RE.match(name):
            raise PermissionError(
                f"shell_exec 'uv pip install' takes a package NAME, got "
                f"'{name}': an address, a path or a repository is not a name"
            )


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

# `branch` and `tag` are read-only per argv[1], but `git branch -f main HEAD`
# moves a protected ref and `-D` / `tag -d` delete one — all three would
# classify as `read_only` and never reach the approval gate. So for these two
# the WHOLE argv is checked: listing flags only, and no positional argument
# at all, since the positional IS the mutation.
LISTING_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "branch": frozenset({"--list", "-l", "-a", "--all", "-v", "-vv", "--verbose",
                         "--show-current", "-r", "--remotes", "--color", "--no-color"}),
    "tag": frozenset({"--list", "-l", "-n", "--color", "--no-color", "--sort"}),
}


# `reflog expire/delete/drop` destroy history under a read-only verb, so the
# subcommand is allow-listed; `git reflog <ref>` must be spelled `show <ref>`.
_REFLOG_READ_SUBCOMMANDS: frozenset[str] = frozenset({"show", "list", "exists"})

# Options that make a read-only git verb write a file (`--output`, also by
# unique prefix) or compare files outside the repository (`--no-index`).
_GIT_FORBIDDEN_OPTIONS: frozenset[str] = frozenset({"--no-index"})

# Long options whose value is text to match or a format, never a path.
_TEXT_VALUED_OPTIONS: frozenset[str] = frozenset({
    "--grep", "--author", "--committer", "--format", "--pretty", "--date",
    "--since", "--until", "--after", "--before", "--regexp",
})

# Commands whose arguments name files to READ. The first bare word of a search
# is its pattern, not a path.
_SEARCH_COMMANDS: frozenset[str] = frozenset({"grep", "findstr"})
_SLASH_SWITCH_COMMANDS: frozenset[str] = frozenset({"findstr", "where"})
_SLASH_SWITCH_RE = re.compile(r"^/(?:[A-Za-z]|OFF|OFFLINE)(?::(.*))?$", re.IGNORECASE)

# findstr also takes its switches glued together: `/spin`.
_SLASH_CLUSTER_RE = re.compile(r"^/[A-Za-z]+$")
# grep short flags whose NEXT word is a count or a mode, not a pattern or a path.
_GREP_VALUE_FLAGS = "ABCmdD"
# Commands that take no argument at all: `hostname <name>` renames the machine.
_BARE_ONLY_COMMANDS: frozenset[str] = frozenset({"hostname"})

# git short options whose ATTACHED value names a file to read (`-O<orderfile>`).
_GIT_SHORT_PATH_OPTIONS: dict[str, tuple[str, ...]] = {
    "blame": ("-O", "-S"),
    "ls-files": ("-O", "-X"),
}
# Repository config must not start a program under a read-only verb.
_GIT_SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=false", "-c", "safe.bareRepository=explicit",
)
_GIT_NO_DRIVER_OPTIONS: dict[str, tuple[str, ...]] = {
    "diff": ("--no-ext-diff", "--no-textconv"),
    "log": ("--no-ext-diff", "--no-textconv"),
    "show": ("--no-ext-diff", "--no-textconv"),
    "blame": ("--no-textconv",),
}

_WIN_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _split_grep_cluster(token: str) -> tuple[str, str]:
    """Flag letters of a short cluster and the value glued to its -e / -f."""
    for i, ch in enumerate(token[1:], 1):
        if ch in "ef":
            return token[1:i + 1], token[i + 1:]
    return token[1:], ""


def _mask_matches(mask: str, path: Path) -> bool:
    """True when a Windows file mask may select `path` (errs towards yes)."""
    if "~" in mask:
        return True
    body = "".join(
        ".*" if ch == "*" else ".?" if ch == "?" else re.escape(ch)
        for ch in mask.rstrip(".")
    )
    names = [path.name]
    if sys.platform == "win32":
        # A mask is matched against the 8.3 alias too.
        import ctypes

        buf = ctypes.create_unicode_buffer(520)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 520):
            names.append(Path(buf.value).name)
    return any(re.fullmatch(body, name, re.IGNORECASE) for name in names)


def _is_link(entry: os.DirEntry[str]) -> bool:
    """A symlink, or a Windows junction, which `is_symlink` does not report."""
    if entry.is_symlink():
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _resolves_inside(path: str, root: Path) -> bool:
    """True when `path` resolves into `root`, or cannot be resolved."""
    try:
        Path(path).resolve().relative_to(root)
    except ValueError:
        return False
    except (OSError, RuntimeError):
        return True
    return True


def trusted_which(name: str, workspace_root: Path) -> str | None:
    """Full path of `name` on PATH; the working folder and the workspace are not searched."""
    hit = shutil.which(name)
    if hit is None:
        return None
    if Path(hit).anchor and not _resolves_inside(hit, workspace_root):
        return hit
    # shutil.which on Windows looks in the current folder first, and PATH can
    # name the workspace: a file the agent wrote must not become the program.
    names = [name]
    if sys.platform == "win32":
        exts = [
            e for e in (os.environ.get("PATHEXT") or _WIN_DEFAULT_PATHEXT).split(os.pathsep) if e
        ]
        if not any(name.lower().endswith(e.lower()) for e in exts):
            names = [name + e for e in exts]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry) or _resolves_inside(entry, workspace_root):
            continue
        for candidate in names:
            full = os.path.join(entry, candidate)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return None


# Subcommands that record work the agent has already done. Without them the
# agent can write a file and run the tests but never commit the result, so a
# programming task cannot reach its end.
#
# The line is the repository boundary: these three touch the local index,
# working tree and local refs only. `push`/`pull`/`fetch`/`clone` (network)
# and `reset`/`rebase`/`merge`/`cherry-pick` (rewriting existing history)
# stay out — recording new work is not permission to alter recorded work.
WRITE_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({"add", "commit", "checkout"}),
}

# A branch the agent creates for itself. It may not commit onto a branch it did
# not make: the operator's own branch is not a scratch pad.
AGENT_BRANCH_PREFIX = "agent/"

# Never commit here even if the operator left the checkout on one of them.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master"})

# Mutating commands handled with `delete_path_if_created` compensation.
# Both produce ONE new path and accept exactly one positional argument.
MUTATING_COMMANDS: frozenset[str] = frozenset({"mkdir", "touch"})

#: Команды, чьё действие ОБРАТИМО: поставленный пакет снимается.
#: Ворота пропускают обратимое с записью причины, без одобрения
#: (core/policy.py). Открыто словом оператора 2026-09-23.
_REVERSIBLE_COMMANDS: frozenset[str] = _FAMILY_UV

ALL_WHITELIST: frozenset[str] = READ_ONLY_COMMANDS | MUTATING_COMMANDS | _REVERSIBLE_COMMANDS

# Characters that would let argv elements compose into a shell — even
# though we always run with shell=False, blocking these at the input
# layer means a malicious planner can't smuggle them through a quote
# trick on some platform. Includes whitespace controls so newline
# injection is impossible too.
#
# Braces are NOT here. `{` and `}` compose nothing in any shell (bash brace
# expansion yields words, never a second command; cmd.exe ignores them), and
# banning them made the agent's own reference syntax `{{step:N.output}}` —
# and half of its Python — unsearchable: exam 2026-09-05, turns 35–36, the
# same `findstr` was dropped twice for the `{` in the pattern.
_FORBIDDEN_CHARS = frozenset(";|&<>`$()[]\n\r\t\0")

# Hard limits — short by design.
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_CAP = 64 * 1024  # 64 KiB per stream


def _oem_encoding() -> str | None:
    """Кодовая страница консоли Windows (cp866 здесь), None вне Windows.

    Отдельной функцией — чтобы полигоны могли судить декодер на любой ОС.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:  # noqa: BLE001 — нет OEM — нет запасного декодера
        return None


# The bounded runner lives in core.bounded_subprocess since 2026-09-05: the same
# grandchild-holds-the-pipe hang was measured a second time in
# core.repo_provenance, and one fix must serve both. The old names stay
# importable for tests/test_shell_exec.py.
_TREE_KILL_GRACE_SECONDS = TREE_KILL_GRACE_SECONDS
_kill_process_tree = kill_process_tree
_run_with_tree_kill = run_with_tree_kill


class ShellExecTool(Tool):
    name = "shell_exec"
    # What this says is what the planner believes it may do: a stale list is
    # read as the contract, and the planner reports failures it never
    # attempted. Keep in step with READ_ONLY_SUBCOMMANDS / WRITE_SUBCOMMANDS.
    description = (
        "Execute ONE whitelisted shell command inside the workspace. "
        "Read-only commands (whoami, hostname, where/which, git "
        "log/diff/status/show/branch/tag/blame, findstr/grep) run "
        "without approval. Mutating commands (mkdir, touch) escalate "
        "to the approval gate and ship with a compensation plan that "
        "can undo the change via :rollback. "
        "You CAN record your own work with git, each in one exact shape, "
        "all of them approval-gated: "
        "['git','checkout','-b','agent/<name>'] to create your own branch "
        "(existing branches cannot be switched to), "
        "['git','add','<path>',…] with explicit paths (no -A), and "
        "['git','commit','-m','<message>'] with nothing else — no --amend, "
        "no --no-verify. Committing is refused on main/master, so create the "
        "agent/ branch first. Push, pull, fetch, reset, rebase and merge stay "
        "out. A search never reads credential files: a mask or a recursive "
        "search that would reach one is refused — narrow the folder or use "
        "find_in_files; grep takes -r, not -R. "
        "Shell metacharacters, absolute paths, and any command outside "
        "the tiny built-in whitelist are rejected before dispatch."
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

        empty/missing argv -> external (rejected anyway); argv[0] in
        READ_ONLY -> read_only (no approval gate); in MUTATING ->
        irreversible (gate fires); anything else -> external (gate fires).
        """
        argv = arguments.get("argv") if isinstance(arguments, dict) else None
        if not isinstance(argv, list) or not argv:
            return "external"
        cmd = argv[0]
        if not isinstance(cmd, str):
            return "external"
        cmd_norm = cmd.strip().lower()
        write_subs = WRITE_SUBCOMMANDS.get(cmd_norm)
        # A recording subcommand rides in on a command whose other
        # subcommands are read-only, so the verdict has to look at
        # argv[1]. Classified `irreversible` so the policy gate asks:
        # a commit is not undone by deleting a path.
        if (
            write_subs is not None
            and len(argv) > 1
            and isinstance(argv[1], str)
            and argv[1].strip().lower() in write_subs
        ):
            return "irreversible"
        if cmd_norm in _REVERSIBLE_COMMANDS:
            # `uv pip list/show` ничего не меняет; `uv pip install` обратим.
            sub = argv[2].strip().lower() if len(argv) > 2 and isinstance(argv[2], str) else ""
            return self._read_only_if_valid(argv) if sub in _UV_PIP_READ else "reversible"
        if cmd_norm in READ_ONLY_COMMANDS:
            return self._read_only_if_valid(argv)
        if cmd_norm in MUTATING_COMMANDS:
            return "irreversible"
        return "external"

    def _read_only_if_valid(self, argv: list[Any]) -> Risk:
        """`read_only` only for an argv that `run` would accept."""
        try:
            self._validate_argv(argv)
        except (PermissionError, ValueError, OSError):
            # The gate must not record "allow" for what execution refuses.
            return "external"
        return "read_only"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_argv(self, argv: list[str]) -> tuple[str, list[str]]:
        """Return (command_norm, validated_argv) or raise PermissionError."""
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

        if cmd in _REVERSIBLE_COMMANDS:
            _validate_uv_argv(argv)
            return cmd, list(argv)
        if cmd in _BARE_ONLY_COMMANDS and len(argv) != 1:
            raise PermissionError(
                f"shell_exec '{cmd}' takes no arguments: only the bare form reads"
            )

        # Subcommand whitelist (e.g. git log/diff/status only).
        sub_allowed = READ_ONLY_SUBCOMMANDS.get(cmd)
        write_allowed = WRITE_SUBCOMMANDS.get(cmd, frozenset())
        if sub_allowed is not None:
            if len(argv) < 2:
                raise PermissionError(
                    f"shell_exec '{cmd}' requires a subcommand "
                    f"(allowed: {sorted(sub_allowed | write_allowed)})"
                )
            sub = argv[1].strip().lower()
            listing_flags = LISTING_ONLY_SUBCOMMANDS.get(sub)
            if listing_flags is not None:
                for extra in argv[2:]:
                    token = extra.strip()
                    if not token.startswith("-"):
                        raise PermissionError(
                            f"shell_exec '{cmd} {sub}' takes no name: "
                            f"'{token}' would create or move a ref. Use "
                            f"['git','checkout','-b','{AGENT_BRANCH_PREFIX}…'] "
                            "to make a branch."
                        )
                    if token.split("=", 1)[0].lower() not in listing_flags:
                        raise PermissionError(
                            f"shell_exec '{cmd} {sub}' allows listing flags "
                            f"only, got '{token}' — deleting or moving a ref "
                            "is not a read"
                        )
            if sub in write_allowed:
                self._validate_write_subcommand(cmd, sub, argv)
            elif sub not in sub_allowed:
                raise PermissionError(
                    f"shell_exec '{cmd} {argv[1]}' — subcommand not in "
                    f"whitelist {sorted(sub_allowed | write_allowed)}"
                )
            else:
                self._validate_read_subcommand(cmd, sub, argv)
                self._refuse_outside_reads(cmd, argv[2:])
                for token in argv[2:]:
                    if len(token) > 2 and token[:2] in _GIT_SHORT_PATH_OPTIONS.get(sub, ("-O",)):
                        self._confine(token[2:])
        elif cmd in READ_ONLY_COMMANDS:
            self._refuse_outside_reads(cmd, argv[1:])

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

    @staticmethod
    def _validate_read_subcommand(cmd: str, sub: str, argv: list[str]) -> None:
        """A read-only verb stays read-only whatever its options say."""
        rest = [a.strip() for a in argv[2:]]
        for token in rest:
            name = token.split("=", 1)[0]
            if (
                name in _GIT_FORBIDDEN_OPTIONS
                or name.startswith("--out")
                or (len(name) > 2 and "--output".startswith(name))
            ):
                raise PermissionError(
                    f"shell_exec '{cmd} {sub}' refuses '{token}': the option "
                    "writes a file or reads outside the repository, and this "
                    "verb is allowed for reading the repository only"
                )
        if sub == "reflog" and not (rest and rest[0].lower() in _REFLOG_READ_SUBCOMMANDS):
            for token in rest:
                if not token.startswith("-") and not token.isdigit():
                    raise PermissionError(
                        f"shell_exec '{cmd} reflog' reads only: '{token}' is not "
                        f"one of {sorted(_REFLOG_READ_SUBCOMMANDS)} — expiring or "
                        "deleting reflog entries destroys history; to read a "
                        "ref's log use ['git','reflog','show','<ref>']"
                    )

    def _refuse_outside_reads(self, cmd: str, args: list[str]) -> None:
        """Every path a read-only command names must lie inside the workspace."""
        search = cmd in _SEARCH_COMMANDS
        pattern_pending = search
        skip_next = recursive = False
        base = ""
        paths: list[str] = []
        for token in args:
            if skip_next:
                skip_next = False
                continue
            if search and _SLASH_CLUSTER_RE.match(token) and "s" in token.lower():
                recursive = True
            switch = _SLASH_SWITCH_RE.match(token) if cmd in _SLASH_SWITCH_COMMANDS else None
            if switch is not None:
                letter, value = token[1].lower(), switch.group(1)
                if cmd != "findstr" or value is None:
                    continue
                if letter == "f":
                    raise PermissionError(
                        f"shell_exec 'findstr' refuses '{token}': a file LIST "
                        "can name files outside the workspace"
                    )
                if letter in "gd":
                    self._confine_read(value)
                if letter == "d":
                    base = value
                if letter in "cg":
                    pattern_pending = False
                continue
            if token.startswith("-") and token not in ("-", "--"):
                flag, eq, value = token.partition("=")
                if not search:
                    if eq and flag.startswith("--") and flag not in _TEXT_VALUED_OPTIONS:
                        self._confine(value)
                    continue
                takes_pattern, skip_next, deep = self._search_option(token, flag, eq, value)
                pattern_pending = pattern_pending and not takes_pattern
                recursive = recursive or deep
                continue
            if pattern_pending:
                pattern_pending = False
                continue
            if search and "*" not in token and "?" not in token:
                self._confine_read(token)
            else:
                self._confine(token)
            paths.append(token)
        if search:
            self._refuse_secret_search(paths, recursive, base)

    def _search_option(
        self, token: str, flag: str, eq: str, value: str
    ) -> tuple[bool, bool, bool]:
        """One grep option: `(supplies the pattern, takes the next word, recurses)`."""
        if flag.startswith("--"):
            if len(flag) > 3 and "--dereference-recursive".startswith(flag):
                raise PermissionError(
                    f"shell_exec refuses '{token}': it follows every link met while "
                    "descending, and a link can lead outside the workspace; use -r"
                )
            deep = len(flag) > 2 and (
                "--recursive".startswith(flag) or "--directories".startswith(flag)
            )
            if flag == "--regexp":
                return True, not eq, deep
            if len(flag) > 4 and "--file".startswith(flag):
                if eq:
                    self._confine_read(value)
                return True, False, deep
            if eq and flag not in _TEXT_VALUED_OPTIONS:
                self._confine(value)
            return False, False, deep
        letters, attached = _split_grep_cluster(token)
        if "R" in letters:
            raise PermissionError(
                f"shell_exec refuses '{token}': -R follows every link met while "
                "descending, and a link can lead outside the workspace; use -r"
            )
        deep = bool(set(letters) & set("rd"))
        if letters.endswith("e"):
            return True, not attached, deep
        if letters.endswith("f"):
            if attached:
                self._confine_read(attached)
            return True, False, deep
        return False, token[-1] in _GREP_VALUE_FLAGS, deep

    def _confine_read(self, path_str: str) -> Path:
        """A file a search reads: inside the workspace and not a credential store."""
        target = self._confine(path_str)
        if _is_credential_path(target.relative_to(self.workspace_root).as_posix()):
            raise PermissionError(
                f"shell_exec refuses to search the credential file '{path_str}': "
                "the agent's own tools do not fetch keys"
            )
        return target

    def _refuse_secret_search(self, paths: list[str], recursive: bool, base: str) -> None:
        """A mask or a recursion must not sweep up what a named path may not read."""
        starts = {self.workspace_root, self._confine(base) if base else self.workspace_root}
        for token in paths or (["."] if recursive else []):
            wild = "*" in token or "?" in token
            head, _, name = token.replace("\\", "/").rpartition("/")
            for start in starts:
                if not wild and (start / token).is_dir():
                    if recursive:
                        self._scan_search_tree(start / token, None, deep=True)
                elif wild or recursive:
                    # findstr /S looks for the NAME in every folder below.
                    self._scan_search_tree(start / head, name, deep=recursive)

    def _scan_search_tree(self, root: Path, mask: str | None, *, deep: bool) -> None:
        """Refuse when the files a search would open hold a credential or lie outside."""
        root = root.resolve()
        if not _resolves_inside(str(root), self.workspace_root):
            raise PermissionError(f"shell_exec search folder '{root.name}' escapes workspace")
        seen = {root}
        stack = [root]
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                selected = mask is None or _mask_matches(mask, path)
                if _is_link(entry):
                    path = path.resolve()
                    if not _resolves_inside(str(path), self.workspace_root):
                        # findstr walks through links; grep -r does not.
                        if sys.platform == "win32" and (selected or (deep and path.is_dir())):
                            raise PermissionError(
                                f"shell_exec refuses this search: '{entry.name}' is a "
                                "link leading outside the workspace; search a narrower "
                                "folder or use find_in_files"
                            )
                        continue
                if path.is_dir():
                    if deep and path not in seen:
                        seen.add(path)
                        stack.append(path)
                    continue
                rel = path.relative_to(self.workspace_root).as_posix()
                if selected and _is_credential_path(rel):
                    raise PermissionError(
                        "shell_exec refuses this search: it would read the credential "
                        f"file '{rel}'; search a narrower folder or mask, or use "
                        "find_in_files, which skips credential files"
                    )

    def _which(self, name: str) -> str | None:
        """Full path of `name` on PATH; see `trusted_which`."""
        return trusted_which(name, self.workspace_root)

    def _current_branch(self) -> str:
        """The checked-out branch name, or "" when it cannot be read.

        Read through git rather than by parsing `.git/HEAD`, so a worktree or a
        detached HEAD answers the same way git itself would. An unreadable
        answer is treated as "unknown" by the caller, which then refuses: a
        guard that cannot see the branch must not assume a safe one.
        """
        git = self._which("git")
        if git is None:
            return ""
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv; the branch read is the guard's own
                [git, *_GIT_SAFE_CONFIG, "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
                # Same sandbox contract as the commands this tool dispatches;
                # reading the branch must not be the one path that inherits the
                # parent environment.
                env=self._safe_env(),
                # Explicit: a non-zero exit is an ANSWER here (no repository,
                # detached HEAD), read from `returncode` below. Raising would
                # turn "cannot tell" into a crash on the routing path.
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _validate_write_subcommand(self, cmd: str, sub: str, argv: list[str]) -> None:
        """Shape and branch checks for the subcommands that record work."""
        if sub == "add":
            paths = argv[2:]
            if not paths:
                raise PermissionError("shell_exec 'git add' requires explicit paths")
            for path_str in paths:
                if path_str.startswith("-"):
                    raise PermissionError(
                        f"shell_exec 'git add' takes paths only, got option "
                        f"'{path_str}' — a sweep is not a recorded intention"
                    )
                target = self._validate_path_in_workspace(path_str)
                # `git add <dir>` stages the directory recursively, and `.` is
                # a directory. Refusing `-A` while accepting those forbids the
                # spelling and permits the act: the same unreviewed sweep, one
                # character shorter. Each file the agent means to record has to
                # be named.
                if target == self.workspace_root or target.is_dir():
                    raise PermissionError(
                        f"shell_exec 'git add' refuses the directory "
                        f"'{path_str}': name each file to record it — adding a "
                        "directory stages whatever it happens to contain"
                    )
        elif sub == "commit":
            if len(argv) != 4 or argv[2] != "-m" or not argv[3].strip():
                raise PermissionError(
                    "shell_exec 'git commit' accepts exactly "
                    "['git', 'commit', '-m', <message>]"
                )
        elif sub == "checkout":
            if len(argv) != 4 or argv[2] != "-b":
                raise PermissionError(
                    "shell_exec 'git checkout' may only create a branch: "
                    "['git', 'checkout', '-b', <name>]"
                )
            branch = argv[3]
            if not branch.startswith(AGENT_BRANCH_PREFIX) or branch == AGENT_BRANCH_PREFIX:
                raise PermissionError(
                    f"shell_exec 'git checkout -b' requires a name under "
                    f"'{AGENT_BRANCH_PREFIX}', got '{branch}'"
                )
            return  # creating its own branch is exactly how it leaves ours

        current = self._current_branch()
        if not current:
            raise PermissionError(
                f"shell_exec '{cmd} {sub}' refused: the current branch could "
                "not be read, and an unknown branch is not a safe one"
            )
        if current == "HEAD":
            # `git rev-parse --abbrev-ref HEAD` answers the literal "HEAD" on a
            # detached HEAD: readable, so the guard above passes, and not a
            # branch, so a commit here is reachable from no ref at all and
            # survives only until the next gc.
            raise PermissionError(
                f"shell_exec '{cmd} {sub}' refused: HEAD is detached, so the "
                f"commit would belong to no branch; create a "
                f"'{AGENT_BRANCH_PREFIX}…' branch first"
            )
        if current in PROTECTED_BRANCHES:
            raise PermissionError(
                f"shell_exec '{cmd} {sub}' refused on protected branch "
                f"'{current}'; create a '{AGENT_BRANCH_PREFIX}…' branch first"
            )
        if not current.startswith(AGENT_BRANCH_PREFIX):
            # The rule this file states is "a branch the agent created", and
            # enumerating forbidden names does not say that — it permits any
            # operator branch not called main. Requiring the prefix does.
            raise PermissionError(
                f"shell_exec '{cmd} {sub}' refused on '{current}': the agent "
                f"records only on a branch it created under "
                f"'{AGENT_BRANCH_PREFIX}'"
            )

    def _validate_path_in_workspace(self, path_str: str) -> Path:
        """A path to WRITE: confined to the workspace and not a control file."""
        candidate = self._confine(path_str)
        if ".git" in (p.lower() for p in candidate.relative_to(self.workspace_root).parts):
            raise PermissionError(
                f"shell_exec refuses to write under .git: '{path_str}' — git runs "
                "the programs its own folder names"
            )
        # `touch` пустого выключателя или лимитов — та же запись тормоза
        # (core/control_files.py): запрет до одобрения.
        guarded = control_file_hit(self.workspace_root, candidate)
        if guarded:
            raise PermissionError(refuse_message(guarded))
        return candidate

    def _confine(self, path_str: str) -> Path:
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

        try:
            candidate = (self.workspace_root / path_str).resolve()
            candidate.relative_to(self.workspace_root)
        except (ValueError, OSError, RuntimeError) as exc:
            raise PermissionError(
                f"shell_exec path '{path_str}' escapes workspace"
            ) from exc
        return candidate

    # ------------------------------------------------------------------
    # Compensation plan — built BEFORE execution
    # ------------------------------------------------------------------
    def _build_compensation_plan(
        self, cmd: str, argv: list[str], target_existed_before: bool,
        target_path: Path | None = None,
    ) -> CompensationPlan:
        write_subs = WRITE_SUBCOMMANDS.get(cmd, frozenset())
        if write_subs and len(argv) > 1 and argv[1].strip().lower() in write_subs:
            # Checked BEFORE the read-only branch: `git` lives in
            # READ_ONLY_COMMANDS, so a commit would otherwise be filed as
            # "nothing to undo" — true of `git log`, false here.
            #
            # The plan is a noop because the undo is not this tool's to
            # perform: `reset` and `rebase` are outside the whitelist, and a
            # compensation that rewrites history would hand back the very
            # permission the whitelist withholds. What bounds this instead is
            # stated: the work lands on a branch the agent made, never on a
            # protected one, and the policy gate asked before it ran.
            return CompensationPlan.noop(
                tool_name=self.name,
                description=(
                    f"'{cmd} {argv[1]}' records work on an agent branch; "
                    "not auto-undone — history rewriting is out of scope"
                ),
            )

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
            recorded = path_str
            if target_path is not None:  # the path the command really acts on, not its spelling or a link in front of it
                try:
                    recorded = str(target_path.relative_to(self.workspace_root))
                except ValueError:
                    recorded = path_str
            return CompensationPlan(
                tool_name=self.name,
                description=f"undo '{cmd} {path_str}' by deleting the created path",
                actions=[
                    CompensationAction(
                        kind="delete_path_if_created",
                        description=f"delete path created by {cmd}",
                        path=recorded,
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
        """Execute one whitelisted command in the workspace sandbox."""
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

        plan = self._build_compensation_plan(cmd, argv, target_existed_before, target_path)

        if cmd in _REVERSIBLE_COMMANDS:
            # Ставить — только В СВОЁ окружение. Интерпретатор берётся не из
            # PATH и не из слов агента, а тот, на котором он сам работает:
            # системный pip/uv поставил бы в ДРУГОЙ Python (замер 2026-09-23 —
            # pdfplumber есть у агента и нет в системном), и агент получил бы
            # «установлено» на пакет, которого не увидит. Подставляется здесь,
            # а не просится у планировщика: путь к своему окружению не та
            # вещь, которую стоит угадывать.
            # Чтение — тоже: иначе `list` показывает окружение, которое нашёл uv.
            if len(argv) > 2:
                argv =[*argv[:3], "--python", sys.executable, *argv[3:]]
            return self._run_subprocess(cmd, argv, plan)
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
        exe = self._which(real_cmd)
        if exe is None:
            raise FileNotFoundError(
                f"shell_exec cannot find executable '{real_cmd}' on PATH"
            )

        # Arguments are adapted when the PROGRAM changed underneath the caller
        # — or when the program is findstr, whose slash dialect the CALLER
        # never speaks (see _normalise_argv_for).
        run_argv = [
            exe,
            *self._normalise_argv_for(
                self._git_hardened(list(argv)) if real_cmd == "git" else list(argv),
                substituted=substituted, real_cmd=real_cmd,
            )[1:],
        ]
        env = self._safe_env()
        if real_cmd in _FAMILY_GREP and sys.platform == "win32":
            # The MSYS runtime expands * and {a,b} in EVERY argument, the
            # pattern included, into file names validation never saw.
            env.update(MSYS="noglob", CYGWIN="noglob")
        started = time.monotonic()
        stdout_bytes, stderr_bytes, exit_code, timed_out = _run_with_tree_kill(
            run_argv, cwd=self.workspace_root, env=env, timeout=self.timeout_seconds,
        )

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
                classify_shell_result(real_cmd, exit_code=exit_code, stderr=stderr_safe), strict=False,
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
        """Mutating whitelist is interpreted by the tool, not spawned."""
        assert target is not None  # noqa: S101 — narrowing; mypy hint; validator ensures this
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
                classify_shell_result(cmd, exit_code=exit_code, stderr=stderr_safe), strict=False,
            )),
            "compensation_plan": plan.to_dict(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_binary(self, cmd: str) -> tuple[str, bool]:
        """Pick the binary to run: `(name, substituted)`."""
        if self._which(cmd) is not None:
            return cmd, False
        alias = self._platform_alias(cmd)
        if alias != cmd and self._which(alias) is not None:
            return alias, True
        return cmd, False

    @staticmethod
    def _git_hardened(argv: list[str]) -> list[str]:
        """Add the options that stop git starting programs named in repository config."""
        extra = _GIT_NO_DRIVER_OPTIONS.get(argv[1].strip().lower(), ())
        return [argv[0], *_GIT_SAFE_CONFIG, argv[1], *extra, *argv[2:]]

    @staticmethod
    def _normalise_argv_for(
        argv: list[str], *, substituted: bool, real_cmd: str = ""
    ) -> list[str]:
        """Make the arguments readable by the program that will actually run.

        On Windows `findstr` reads `/` as a switch prefix, so `core/loop.py`
        parses as `core` plus `/l /o /o /p`. Until 2026-08-29 this ran only
        when a SUBSTITUTION happened (grep→findstr), on the premise that "the
        requested binary's own dialect is already correct". The premise died
        in the live interrogation of 2026-08-28: the CALLER is a model that
        speaks POSIX paths even when it asks for findstr by name — three
        direct probes fell on their own slashes. findstr is therefore
        normalised whenever it is the binary that runs.
        """
        if sys.platform != "win32" or not (substituted or real_cmd == "findstr"):
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

    # Git reads the committer identity from the user's global config, found
    # through HOME (POSIX) or USERPROFILE/HOMEDRIVE+HOMEPATH (Windows).
    # Without them `git commit` dies on "Author identity unknown" even when
    # every other check passed. These name a directory, carry no credential
    # unlike the rest of the environment this withholds, and git only reads
    # its own config files there.
    _GIT_IDENTITY_ENV: tuple[str, ...] = (
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    )

    def _safe_env(self) -> dict[str, str]:
        """Minimal env: PATH, (Windows) SystemRoot, and the home lookup."""
        # A folder the agent can write to must not supply programs to a child.
        env = {"PATH": os.pathsep.join(
            entry for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and os.path.isabs(entry)
            and not _resolves_inside(entry, self.workspace_root)
        )}
        if sys.platform == "win32":
            # SystemRoot is REQUIRED for many Windows .exe to even start.
            sr = os.environ.get("SystemRoot")  # noqa: SIM112 — Windows-only variable; this is the spelling the OS documents
            if sr:
                env["SystemRoot"] = sr
            # PATHEXT is how Windows turns the NAME `python` into `python.exe`.
            # Without it every by-name lookup fails and the agent asking its own
            # environment a question gets a FALSE NEGATIVE — it concludes its
            # tools may not be connected. It carries no credential; it is a
            # list of suffixes.
            pathext = os.environ.get("PATHEXT")
            if pathext:
                env["PATHEXT"] = pathext
        for name in self._GIT_IDENTITY_ENV:
            value = os.environ.get(name)
            if value:
                env[name] = value
        return env

    def _cap_and_decode(self, raw: bytes) -> tuple[str, bool]:
        """Truncate to output_cap_bytes; decode UTF-8, then the console's OEM.

        Windows console tools answer in the OEM code page (cp866 on this
        machine), and until 2026-08-29 the refusal «Не удалось открыть»
        reached the agent as «�� �������» — it could not read WHY it was
        refused (live interrogation, round 3). UTF-8 stays first: it is what
        every modern tool emits; OEM is the fallback for the console natives;
        `replace` remains the last resort so no bytes ever raise.
        """
        truncated = False
        if len(raw) > self.output_cap_bytes:
            raw = raw[: self.output_cap_bytes]
            truncated = True
        try:
            return raw.decode("utf-8"), truncated
        except UnicodeDecodeError:
            pass
        oem = _oem_encoding()
        if oem:
            try:
                return raw.decode(oem), truncated
            except (UnicodeDecodeError, LookupError):
                pass
        return raw.decode("utf-8", errors="replace"), truncated

    # ------------------------------------------------------------------
    # Output validation (Tool contract)
    # ------------------------------------------------------------------
    def execution_status(self, output: Any) -> str:
        """The command's own verdict, not the subprocess's."""
        if isinstance(output, dict):
            return str(output.get("execution_status") or "success")
        return "success"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        """Answers ONE question: does this object match the tool's schema?"""
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
