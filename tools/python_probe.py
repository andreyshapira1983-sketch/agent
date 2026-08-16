"""Python Probe — лаборатория: маленький эксперимент над СВОЕЙ средой.

Зачем существует: живая проба 2026-08-16 (trace_3bb22486) — агент дал верный
вердикт «itertools.batched здесь не работает», но ВЫВЕЛ его из версии, а не
измерил: планировщик хотел запустить python, белый список shell_exec выбросил
оба шага, и самый весомый источник об окружении — собственный эксперимент —
оказался структурно недоступен. Подробности: docs/CODE_NOTES.md,
"The verdict was inferred, the lab was locked".

Контракт (узкий сознательно):
  * меряет, не действует: структурный AST-гейт отклоняет процессы
    (subprocess/multiprocessing), сеть (socket/urllib/http/requests/httpx),
    массовые операции ФС (shutil), ctypes, __import__-обход, open() с
    записью и эффектные os.* — ДО запуска, с названной причиной;
  * контейнер: отдельный интерпретатор `-I` (изолированный режим), cwd —
    свежая временная папка, окружение — PATH/SystemRoot и ничего больше
    (ключей API у эксперимента нет), жёсткий таймаут, потолок вывода,
    редакция секретов на выходе;
  * упавший эксперимент — УДАВШИЙСЯ замер: ImportError и есть данные;
    `execution_status` == failed только у таймаута;
  * гейт — защита от случайности, не от противника: это признано, и потому
    безнадзорному пути инструмент закрыт (см. _AUTONOMOUS_GOAL_BLOCKED_TOOLS).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

from core.redaction import redact_text
from tools.base import Tool

#: Модули, чей импорт превращает замер в действие. Корни, не подстроки.
_FORBIDDEN_MODULES = frozenset({
    "subprocess", "multiprocessing", "socket", "shutil", "ctypes",
    "urllib", "http", "requests", "httpx", "ftplib", "smtplib", "paramiko",
})

#: Эффектные вызовы os.* — точечные, сам os разрешён (os.name, os.environ —
#: законные замеры; окружение всё равно чистое).
_FORBIDDEN_OS_CALLS = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
    "system", "popen", "spawnl", "spawnv", "kill", "chmod", "chown",
})

_OUTPUT_CAP = 16 * 1024  # 16 KiB на поток: эксперимент — не дамп


def _forbidden_reason(code: str) -> str | None:
    """Одна названная причина отказа или None. Судит структуру, не строки."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
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


class PythonProbeTool(Tool):
    name = "python_probe"
    description = (
        "Run a SMALL Python experiment in THIS runtime to MEASURE behaviour: "
        "versions, real signatures, whether a feature exists here. The code "
        "runs in an isolated interpreter with no API keys, a temp cwd, a hard "
        "timeout and an output cap. A failing snippet is a valid measurement "
        "(the ImportError IS the data). Process/network/write operations are "
        "refused. Args: code (str), timeout_seconds (int, default 10)."
    )
    risk = "read_only"

    def run(self, *, code: str, timeout_seconds: int = 10) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("python_probe needs non-empty code")
        try:
            reason = _forbidden_reason(code)
        except SyntaxError as exc:
            raise ValueError(f"experiment does not parse: {exc.msg}") from exc
        if reason:
            raise ValueError(f"refused before execution: {reason}")

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            for key in ("SystemRoot", "PATHEXT", "SYSTEMDRIVE"):
                val = os.environ.get(key)
                if val:
                    env[key] = val
        started = time.monotonic()
        timed_out = False
        with tempfile.TemporaryDirectory(prefix="python_probe_") as cwd:
            try:
                proc = subprocess.run(  # noqa: S603 — argv фиксирован, shell=False
                    [sys.executable, "-I", "-c", code],
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
        return {
            "code": code,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "timed_out": timed_out,
        }

    def execution_status(self, output: Any) -> str:
        """Таймаут — единственный провал: упавший эксперимент измерил падение."""
        if isinstance(output, dict) and output.get("timed_out"):
            return "failed"
        return "success"
