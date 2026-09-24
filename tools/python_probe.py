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
  * `inputs` (2026-09-19): файлы рабочей папки КОПИРУЮТСЯ в папку эксперимента.
    Замер на внешнем экзамене: суммы 40–60 чисел агент считал в уме и писал
    неверные, а однажды записал круглое 10000 вместо суммы; лаборатория файлов
    задачи не видела. Копия, не ссылка: записать назад в рабочую папку
    эксперимент по-прежнему не может. Путь только относительный, внутри рабочей
    папки; размер и число файлов ограничены.
"""
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
#: Потолок одного входа. 2 МБ не хватало на собственные журналы агента:
#: живой разговор 2026-09-20 — `data/approval_inbox.jsonl` (2 435 380 байт)
#: не принят как вход, и на вопрос «почему ты не правил свой код» агент
#: ответил догадками, не сумев посчитать по ящику заявок.
_INPUT_MAX_BYTES = 16 * 1024 * 1024
_INPUT_MAX_TOTAL = 32 * 1024 * 1024

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

#: Имена динамического импорта: атрибутом, строкой или импортом — всё обход.
_DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module"})

_OUTPUT_CAP = 16 * 1024  # 16 KiB на поток: эксперимент — не дамп

#: Выключатель импорта модулей рабочей папки. Ставит ОПЕРАТОР в окружении
#: процесса агента; сам опыт его не видит (окружение ребёнка чистое, `-I`).
#: По умолчанию выключен.
#:
#: Зачем: открытая запись агента probe-cwd-isolation-2026-09-20 и её
#: severity=high соседка — «на этом измерительном приборе стоят все прочие
#: решения». Лаборатория не могла импортировать ни одного модуля агента, и
#: 2026-09-21 он не смог исполнить ни строки собственного кода, чтобы
#: проверить свои же утверждения о нём: числа выводились из чтения формулы.
#:
#: Цена: с выключателем опыт может вызвать ЛЮБОЙ модуль агента, в том числе
#: пишущий в data/, — АСТ-гейт выше судит только текст опыта. Поэтому это
#: решение оператора, а не агента; на безнадзорном пути инструмент закрыт.
WORKSPACE_IMPORT_ENV = "PYTHON_PROBE_WORKSPACE_IMPORT"
_WORKSPACE_PACKAGES = ("core", "tools", "app", "cli")


def _is_glob(value: str) -> bool:
    """Строка — шаблон пути (glob), а не путь: в ней есть * или ?."""
    return "*" in value or "?" in value


def _with_workspace_on_path(code: str, root: Path) -> str:
    """Опыт, которому виден корень рабочей папки.

    Путь вставляется в сам код, а не в PYTHONPATH: `-I` игнорирует окружение.
    Опыт исполняется через compile под именем `<string>`, поэтому номера
    строк в его ошибке — строки самого опыта, без сдвига на вставку.
    """
    return (
        f"import sys\nsys.path.insert(0, {str(root)!r})\n"
        f"exec(compile({code!r}, '<string>', 'exec'))\n"
    )


#: События аудита Python (docs.python.org, «Audit events table»), которые —
#: действие, а не замер: процесс и сеть. Удаление, переименование и запись
#: файлов судятся по пути: внутри папки опыта и временной папки можно.
#: ctypes здесь НЕТ: numpy/matplotlib и сам Python грузят через него свои
#: библиотеки при импорте (проверка 24.09 уронила обычный замер). Из текста опыта
#: ctypes запрещён гейтом; через ctypes растяжку можно обойти — поэтому она и
#: не стена (документация Python: audit hooks — не песочница).
_RUNTIME_ACTIONS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn", "os.kill", "os.startfile",
    "socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo",
})
_RUNTIME_PATH_EVENTS = frozenset({"os.remove", "os.rmdir", "shutil.rmtree", "os.rename"})


def _with_runtime_tripwire(code: str) -> str:
    """Опыт с растяжкой внутри: действие отказывается и при обходе проверки текста.

    24.09: проверка текста ловила `import subprocess` и `__import__`, но
    `importlib.import_module("subprocess")`, `builtins.__import__` и
    `Path(...).write_text` проходили, а карточки прошлых ошибок успели выучить
    урок «используй importlib.import_module». Документация Python прямо
    говорит, что хук аудита — не песочница: умышленный код его обойдёт. Это
    растяжка для агента, честно пошедшего по выученному обходу, а не стена;
    стена — отдельный пользователь, как у convert_file (решение оператора).
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
    """Одна названная причина отказа или None. Судит структуру, не строки."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        # Обходы гейта, проверенные 24.09 опытом: importlib.import_module,
        # from importlib import import_module, builtins.__import__,
        # getattr(…, "__import__"), exec/eval строки (код в строке гейт не видит).
        # Тот же класс ломал «песочницу» smolagents (CVE-2025-5120, CVE-2025-9959).
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


class PythonProbeTool(Tool):
    name = "python_probe"
    description = (
        "Run a SMALL Python experiment in THIS runtime to MEASURE behaviour: "
        "versions, real signatures, whether a feature exists here — or to "
        "COMPUTE over workspace files listed in `inputs` (copied read-only into "
        "the experiment's cwd, open them by the same relative path). The code "
        "runs in an isolated interpreter with no API keys, a temp cwd, a hard "
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
        """Скопировать файлы рабочей папки в папку эксперимента; причина отказа — названа."""
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
            # Тот же запрет, что у file_read: копия ключей в лабораторию — тот
            # же вынос ключей, только через stdout эксперимента.
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
        """Скопировать файлы, которые код называет, но в `inputs` не передал.

        Файл, не прошедший пределы (размер, число), остаётся в missing_inputs —
        с тем же объяснением, что и раньше; файл ключей не копируется никогда.
        """
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
        """Переданные файлы-журналы, чьи строки завёрнуты в конверт состояния."""
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
        """Файлы рабочей папки, которые код называет, но в `inputs` не передал.

        Замер 2026-09-19: эксперимент без входного файла честно напечатал
        «exists: False», и эта строка ушла в sum.txt как результат. Код,
        считающий по файлу, которого лаборатория не видит, результата не даёт.
        """
        if self.workspace_root is None:
            return []
        given = {Path(p).as_posix() for p in inputs or ()}
        named = {
            n.value for n in ast.walk(ast.parse(code))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and 0 < len(n.value) < 260 and "\n" not in n.value
        }
        missing = []
        for value in sorted(named):
            rel = Path(value)
            if rel.is_absolute() or ".." in rel.parts or rel.as_posix() in given:
                continue
            if _is_glob(value):
                # ШАБЛОН считается наравне с каталогом. Замер 2026-09-24: девять
                # проб подряд делали glob("logs/trace_*.jsonl") без inputs, в
                # пустой папке лаборатории получали ноль и докладывали «поле
                # пустое» — строка со звёздочкой не файл и не каталог, проверка
                # молчала. Шаблон, под который в рабочей папке есть файлы, не
                # переданные в inputs, — тот же невидимый отказ.
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
                # КАТАЛОГ считается наравне с файлом. Замер 2026-09-23: агент
                # мерил свой код пробой `Path("core").glob("*.py")` и получил
                # «py_files 0»; каталога в лаборатории нет, но проверка смотрела
                # только `is_file()` и промолчала, так что ноль ушёл в ответ как
                # факт о коде. Невидимый отказ, выглядящий как настоящий ноль, —
                # его собственная открытая запись в реестре дефектов.
                covered = any(g == rel.as_posix() or g.startswith(rel.as_posix() + "/")
                              for g in given)
                if covered:
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
            # Замер 2026-09-19 (опыт с библиотекой книг): планировщик верно
            # выбрал лабораторию для «сколько маркеров страниц в файле», но не
            # передал файл в `inputs`; эксперимент упал FileNotFoundError, круг
            # наблюдения прочёл это как «неверный путь», и бюджет попыток
            # кончился. Файл рабочей папки, который код называет, копируется
            # сам — только чтение, те же пределы; ключи — никогда.
            auto = self._auto_inputs(code, inputs, cwd, copied)
            try:
                proc = subprocess.run(  # noqa: S603 — argv фиксирован, shell=False
                    # `-X utf8`: вывод эксперимента — UTF-8 на любой ОС. Замер
                    # 2026-09-19 (рабочий экзамен, отчёт по продажам): на Windows
                    # ребёнок печатал в кодировке консоли, родитель читал UTF-8,
                    # и кириллица приходила «���»; агент «восстановил» названия
                    # товаров выдумкой. PYTHONIOENCODING не годится: `-I` его
                    # игнорирует.
                    [sys.executable, "-I", "-X", "utf8", "-c",
                     _with_runtime_tripwire(
                         _with_workspace_on_path(code, self.workspace_root)
                         if workspace_import else code)],
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
                # Каталог через inputs не передашь — только перечислением файлов.
                # Без этой строки совет «add them to inputs» невыполним, а счёт
                # по пустому каталогу читается как измеренный ноль.
                notes.append(f"{dirs} are DIRECTORIES: the lab starts in an empty "
                             "temp folder, so a count over them is not a measurement "
                             "of the code — pass the individual files in inputs, or "
                             "read the directory with file_read/find_in_files instead")
        if enveloped := self._enveloped_inputs(copied + auto):
            # Замер 2026-09-24: проба читала поля строки снаружи конверта и
            # доложила «у всех 20 эпизодов trace_id, tools_used и текст пусты»
            # — у всех 118 они заполнены. Урок в памяти не всплыл; говорит
            # инструмент — в момент действия.
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
            # Не пусто — вывод не результат: код называл файлы, которых не видел.
            "missing_inputs": missing,
            # Видел ли опыт модули агента. Без этого отказ импорта выглядел
            # как факт о коде (открытая запись агента severity=high).
            "workspace_import": workspace_import,
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
        """Таймаут — единственный провал: упавший эксперимент измерил падение."""
        if isinstance(output, dict) and output.get("timed_out"):
            return "failed"
        return "success"
