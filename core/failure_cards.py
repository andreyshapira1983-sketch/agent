"""Карточки прошлых ошибок: «эта ошибка уже была — вот что тогда помогло».

Слово оператора 24.09: увидев у себя знакомую ошибку, агент должен пойти и
посмотреть, как она решалась раньше. Замер того же дня по 446 ходам на
сервере: 510 провалов шагов, 355 из них (70%) — подписи, встречавшиеся в
других ходах. «No module named 'core'» — 32 раза в 14 ходах за четыре дня;
внутри хода он выкручивался в 25 случаях из 32, а в следующем ходу ошибался
заново. Выученное в ходе не переносилось: память доставалась один раз, в
начале хода, по тексту вопроса (loop_context.py), рефлексия в кампании
выключена, а в реестрах дефектов ни одна из 13 частых ошибок не упомянута.

Устройство — по литературе: AutoGuide (arXiv 2403.08978) — урок вида «когда
X — делай Y», достаётся по текущему состоянию, а не по задаче; ExpeL — уроки
из пар провал/удача; ProactAgent (arXiv 2604.20572) — к памяти обращаются,
когда она нужна, а не на каждом ходу.

  * Подпись ошибки — последняя строка исключения или отказа, без чисел и
    путей рабочей папки.
  * Карточка рождается в конце хода из пары «провал → позже удача того же
    инструмента»: одну строку урока пишет дешёвая модель; нет удачи — урока нет.
  * При новом провале с той же подписью урок встаёт в причину сбоя
    (ReplanTrigger.reason) или рядом с выводом в круге наблюдения — туда, где
    его читает планировщик, и НЕ в улики.
  * Показанную карточку судит среда: следующий вызов того же инструмента
    удался — помогла, та же ошибка снова — нет. Урок, который не помогает,
    снимается: устаревший урок сильнее всего вредит трудным задачам.
"""
from __future__ import annotations

import ast
import json
import os
import re
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from core.code_citations import annotate

CARDS_RELPATH = Path("data") / "failure_cards.jsonl"
MAX_CARDS = 300
MAX_NEW_PER_TURN = 2
_MAX_ASKS = 3
_SIMILAR = 0.6
_DIGESTED_KEEP = 500

_EXC_RE = re.compile(r"\b[A-Za-z_]\w*(?:Error|Exception|Refused)\b:?.*")
_REFUSAL_RE = re.compile(r"\b(?:refus\w*|отказ\w*)\b.*", re.IGNORECASE)
_FAILED_TEST_RE = re.compile(r"\b(tests/[\w/.-]+\.py)::(\w+)")
_WORKSPACE_RE = re.compile(r"(?:/root/agent-main|[A-Za-z]:\\[^\s'\"]*?agent-main)[\\/]")
_TMP_RE = re.compile(r"/tmp/[^\s'\"]+")  # noqa: S108 — узнаёт путь в тексте ошибки, файлов не создаёт
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_NUM_RE = re.compile(r"\d+")
_LONG_QUOTE_RE = re.compile(r"'[^'\n]{31,}'|\"[^\"\n]{31,}\"")


@dataclass
class Card:
    sig: str
    tool: str
    error: str
    lesson: str | None = None
    status: str = "unlearned"      # unlearned | active | retired | guard | rejected
    created: str = ""
    last_seen: str = ""
    seen: int = 1
    hits: int = 0
    misses: int = 0
    asked: int = 0
    source_trace: str = ""


# ── подпись ошибки ──────────────────────────────────────────────────────────

#: Карточки, на которых модель может учиться. «guard» — отказ сторожа: после
#: него удачный следующий вызов не лекарство, а часто обход (24.09 выучено
#: «лимит голоса исчерпан — пиши в журнал дефектов» и «гейт блокирует
#: __import__ — используй importlib.import_module»). «rejected» — урок
#: отвергнут проверяющим и не учится заново.
_LEARNABLE = frozenset({"unlearned", "retired"})

#: Отказ защиты, а не поломка: запрет, лимит, «не замер, а действие».
_GUARD_RE = re.compile(
    r"PermissionError|refus|отказ|not allowed|bypass|budget spent|запрещ|"
    r"blocked by (?:the )?(?:injection|policy|guard|gate)|injection guard|"
    r"is an action, not a measurement|turns a measurement into an action",
    re.IGNORECASE)


def is_guard_refusal(text: str) -> bool:
    """Ошибка — отказ сторожа: урок из неё не рождается."""
    return bool(_GUARD_RE.search(text or ""))


def _untrusted(*parts: Any) -> bool:
    """Does any of these texts carry what the injection guard flags?

    The tool call is journaled with its RAW output before the guard runs, so a
    call whose output the guard blocked still looked like the successful
    «fix», its output went into the lesson prompt, and the lesson reached the
    planner (проверка 16.11, серьёзная №1; дыра 07.10 открылась снова).
    Fail-closed: a scanner error counts as untrusted."""
    try:
        from core.injection_guard import scan_for_injection

        for part in parts:
            text = part if isinstance(part, str) else json.dumps(part, ensure_ascii=False, default=str)
            # Only what the guard BLOCKS — the same bar the guard itself uses
            # to withhold content. "suspicious" (an HTML comment, an emoji
            # joiner, «override the default») would starve ordinary lessons.
            # Bounded: the scanner is slow on huge text, and the lesson prompt
            # sees at most the first few hundred characters anyway.
            if scan_for_injection((text or "")[:_SCAN_CHARS]).verdict == "blocked":
                return True
    except Exception:  # noqa: BLE001 — fail-closed, reason above
        return True
    return False


_SCAN_CHARS = 20_000


def failure_text(tool: str, output: Any, error: str | None = None) -> str | None:
    """Текст провала шага; None — шаг удался.

    Жёсткий провал несёт `error`. Мягкий — удачный вызов, в выводе которого
    провал: красный patch_check, красные тесты, упавший опыт python_probe,
    ненулевой код shell_exec.
    """
    if error:
        return str(error)
    if not isinstance(output, dict):
        return None
    # Самая говорящая строка — последней: когда в тексте нет ни исключения, ни
    # отказа, подпись берёт последнюю строку (замер 24.09: «exit_code=N» первой
    # строкой слил все упавшие команды shell_exec в одну карточку).
    if tool == "patch_check" and output.get("verdict") == "red":
        parts = [str(output.get("why") or ""), str(output.get("tests_output") or ""),
                 *reversed([str(e) for e in output.get("errors") or ()])]
        return "\n".join(p for p in parts if p) or "patch_check: red"
    if tool == "run_tests" and (output.get("failed") or output.get("errors")):
        names = "\n".join(f"FAILED {n}" for n in output.get("failed_tests") or ())
        return f"{output.get('stdout_tail') or ''}\n{names}".strip()
    # shell_exec сам судит свою команду (execution_status): его провал приходит
    # жёстким путём. Удачный вызов с кодом 1 и пустым stderr — это grep без
    # совпадений, ответ, а не ошибка (замер 24.09: 24 таких «провала» в одной
    # карточке «exit_code=N»).
    code = output.get("exit_code")
    if tool == "python_probe" and code not in (0, None):
        stderr = str(output.get("stderr") or "").strip()
        return f"exit_code={code}\n{stderr[-2000:]}" if stderr else f"exit_code={code}"
    return None


def failed_output_reason(output: Any) -> str:
    """Причина сбоя, когда инструмент вернул провал без исключения.

    До 24.09 такая причина была буквально «tool execution failed»: код выхода
    и stderr упавшей команды shell_exec терялись, и планировщик переделывал
    шаг вслепую (34 таких провала в следах сервера).
    """
    if isinstance(output, dict) and output.get("exit_code") not in (0, None):
        stderr = str(output.get("stderr") or "").strip()
        return f"tool execution failed: exit_code={output['exit_code']}" + (
            f"; stderr: {stderr[-600:]}" if stderr else "")
    return "tool execution failed"


def error_line(text: str) -> str:
    """Строка, по которой ошибку узнают: исключение с сообщением, отказ, упавший
    тест, иначе последняя строка вывода (не строка кода выхода)."""
    lines = [ln.strip().removeprefix("E ").strip() for ln in text.splitlines() if ln.strip()]
    for pattern in (_EXC_RE, _REFUSAL_RE):
        found = [m.group(0) for ln in lines if (m := pattern.search(ln))]
        # «TypeError» без сообщения — обрезанная сводка pytest; она сливала
        # разные ошибки в одну карточку (замер 24.09: 31 провал под одним именем).
        told = [f for f in found if ": " in f and len(f.split(": ", 1)[1]) > 3]
        if told or found:
            return (told or found)[-1]
    if m := _FAILED_TEST_RE.search(text):
        return f"FAILED {m.group(1)}::{m.group(2)}"
    rest = [ln for ln in lines if not ln.startswith("exit_code=")]
    line = rest[-1] if rest else (lines[0] if lines else "")
    return line.split("stderr: ", 1)[-1]


def signature(tool: str, text: str) -> str:
    """Подпись: инструмент + строка ошибки без чисел, хешей, путей папки и
    длинных цитат (длинная цитата — чужой текст, короткая — имя, её оставляем)."""
    line = _WORKSPACE_RE.sub("", error_line(text))
    line = _LONG_QUOTE_RE.sub("'<…>'", line)
    line = _NUM_RE.sub("N", _HEX_RE.sub("H", _TMP_RE.sub("<tmp>", line)))
    return f"{tool}|{' '.join(line.split())[:160]}"


def _tokens(sig: str) -> set[str]:
    return set(re.findall(r"\w+", sig.lower()))


# ── хранилище ───────────────────────────────────────────────────────────────

class CardStore:
    """Карточки в data/failure_cards.jsonl; первая строка — что уже разобрано."""

    def __init__(self, workspace: Path | str) -> None:
        self.path = Path(workspace) / CARDS_RELPATH
        self.cards, self.digested = _read_cards(self.path)
        # Что было на диске при чтении: сохранение не воскрешает удалённое
        # здесь и не затирает добавленное другим писателем (проверка 16.11, средняя).
        self._loaded = set(self.cards)
        self._loaded_state = {k: _card_state(c) for k, c in self.cards.items()}
        # Сбои вопроса модели за этот разбор (проверка 16.11, мелкая).
        self.lesson_errors: list[str] = []

    def find(self, tool: str, sig: str) -> Card | None:
        """Та же подпись, иначе самая близкая по словам (≥ 0.6) у того же инструмента."""
        if sig in self.cards:
            return self.cards[sig]
        want, best, best_score = _tokens(sig), None, _SIMILAR
        for card in self.cards.values():
            if card.tool != tool:
                continue
            have = _tokens(card.sig)
            score = len(want & have) / max(1, len(want | have))
            if score >= best_score:
                best, best_score = card, score
        return best

    def save(self) -> None:
        with _cards_lock(self.path):
            # Карточки, которые другой писатель добавил после нашего чтения,
            # сохраняются: раньше последний save затирал их (проверка 16.11, средняя).
            disk_cards, disk_digested = _read_cards(self.path)
            for sig, card in disk_cards.items():
                if sig not in self.cards and sig not in self._loaded:
                    self.cards[sig] = card
            for trace, n in disk_digested.items():
                self.digested[trace] = max(n, self.digested.get(trace, 0))
            if len(self.cards) > MAX_CARDS:
                # Отказ проверяющего и отказ сторожа не вытесняются первыми:
                # иначе плохой урок выучивается снова (проверка 16.11, средняя).
                rank = {"rejected": 3, "active": 2, "guard": 2, "unlearned": 1, "retired": 0}
                # A card new or changed in this save is not evicted by it (18.11),
                # but a reviewer's rejection still outranks it.
                keep = sorted(self.cards.values(),
                              key=lambda c: (c.status == "rejected", self._fresh(c),
                                             rank.get(c.status, 0), c.seen, str(c.last_seen)),
                              reverse=True)[:MAX_CARDS]
                self.cards = {c.sig: c for c in keep}
            digested = dict(list(self.digested.items())[-_DIGESTED_KEEP:])
            rows = [json.dumps({"_digested": digested})]
            rows += [json.dumps(asdict(c), ensure_ascii=False) for c in self.cards.values()]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _tmp_name(self.path)
            tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self._loaded = set(self.cards)
            self._loaded_state = {k: _card_state(c) for k, c in self.cards.items()}

    def _fresh(self, card: Card) -> bool:
        return self._loaded_state.get(card.sig) != _card_state(card)


def _card_state(card: Card) -> tuple:
    return (card.status, card.seen, str(card.last_seen), card.lesson)


_INT_FIELDS = ("seen", "hits", "misses", "asked")


def _read_cards(path: Path) -> tuple[dict[str, Card], dict[str, int]]:
    """Карточки и «разобрано» с диска; битая строка пропускается, а не роняет
    показ урока (проверка 16.11, средняя)."""
    cards: dict[str, Card] = {}
    digested: dict[str, int] = {}
    try:
        raw_lines = path.read_bytes().splitlines()
    except OSError:
        return cards, digested
    for raw in raw_lines:
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if "_digested" in row:
            got = row["_digested"]
            if isinstance(got, dict):
                for k, v in got.items():
                    try:
                        digested[str(k)] = int(v)
                    except (TypeError, ValueError):
                        continue
        elif isinstance(row.get("sig"), str):
            card = _card_from_row(row)
            if card is not None:
                cards[card.sig] = card
    return cards, digested


_REQUIRED_STR = ("sig", "tool", "error")


def _card_from_row(row: dict[str, Any]) -> Card | None:
    """Карточка из строки с проверкой типа КАЖДОГО поля: чужой тип ронял показ,
    учёбу и вытеснение (проверка 17.11, средняя). Нет строковых sig/tool/error —
    строка пропускается; прочее чужое — к умолчанию."""
    if not all(isinstance(row.get(k), str) for k in _REQUIRED_STR):
        return None
    known: dict[str, Any] = {k: row[k] for k in _REQUIRED_STR}
    for name in Card.__dataclass_fields__:
        if name in known or name not in row:
            continue
        value = row[name]
        if name in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                continue
            try:
                known[name] = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
        elif isinstance(value, str):
            known[name] = value
        # нестроковое значение строкового поля (и lesson) — умолчание
    return Card(**known)


_held = threading.local()


def _lock_held(path: Path) -> bool:
    return str(Path(path)) in getattr(_held, "paths", set())


@contextmanager
def _cards_lock(path: Path) -> Iterator[None]:
    """Замок файла карточек; повторный вход в том же потоке не ждёт сам себя."""
    from core.state_integrity import state_file_lock

    key = str(Path(path))
    paths = getattr(_held, "paths", None)
    if paths is None:
        paths = _held.paths = set()
    if key in paths:
        yield
        return
    with state_file_lock(path):
        paths.add(key)
        try:
            yield
        finally:
            paths.discard(key)


def _tmp_name(path: Path) -> Path:
    """Свой временный файл у каждого писателя: общий .tmp делили два хода."""
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp")


# ── показ в момент ошибки ───────────────────────────────────────────────────

def note_for(workspace: Path | str, tool: str, text: str) -> str | None:
    """Строка «прошлый опыт» для этой ошибки или None."""
    card = CardStore(workspace).find(tool, signature(tool, text))
    if card is None or card.status != "active" or not card.lesson:
        return None
    if _untrusted(card.lesson):
        # A lesson stored before this check may carry injected text: it is
        # never shown to the planner (проверка 16.11, серьёзная №1).
        return None
    tally = f"встречалась {card.seen} раз"
    if card.hits + card.misses:
        tally += f"; урок помог {card.hits} из {card.hits + card.misses}"
    # Урок мог называть код, которого больше нет (core/code_citations.py).
    return f"ПРОШЛЫЙ ОПЫТ ({tally}): {annotate(workspace, card.lesson)}"


def witness_note(workspace: Path | str, text: str) -> str | None:
    """Что охраняет упавший тест — первые строки его описания из кода.

    Описания тестов-свидетелей называют запись реестра (MIR-…, H-…) и причину:
    так реестры доходят до агента в момент, когда он их задел.
    """
    m = _FAILED_TEST_RE.search(text or "")
    if m is None:
        return None
    # Только файлы внутри <workspace>/tests/: «tests/../..» читал чужой файл
    # (проверка 16.11, мелкая).
    try:
        tests_dir = (Path(workspace) / "tests").resolve()
        path = (Path(workspace) / m.group(1)).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_relative_to(tests_dir):
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    doc = next((ast.get_docstring(n) for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == m.group(2)), None)
    doc = doc or ast.get_docstring(tree)
    if not doc:
        return None
    head = " ".join(ln.strip() for ln in doc.strip().splitlines()[:3] if ln.strip())
    return f"ТЕСТ {m.group(1)}::{m.group(2)} ОХРАНЯЕТ: {head[:300]}"


def _workspace(loop: Any) -> Path | None:
    """Рабочая папка цикла. Нет её — карточек нет: запасной `Path.cwd()` писал
    data/failure_cards.jsonl туда, где запущен процесс (тест в копии /root/verify,
    2026-09-24)."""
    root = getattr(loop, "_file_read_workspace_root", None)
    found = root() if callable(root) else None
    return Path(found) if found else None


def with_past_experience(loop: Any, trigger: Any) -> Any:
    """Сбой шага с приложенным прошлым опытом (в причину — не в улики)."""
    if trigger is None:
        return trigger
    ws = _workspace(loop)
    if ws is None:
        return trigger
    reason = str(getattr(trigger, "reason", "") or "")
    notes = [n for n in (note_for(ws, trigger.tool_name or "", reason), witness_note(ws, reason)) if n]
    if not notes:
        return trigger
    _log(loop, "failure_card_shown", {"tool": trigger.tool_name,
                                      "sig": signature(trigger.tool_name or "", reason)})
    return replace(trigger, reason=reason + "".join(f"\n    {n}" for n in notes))


def experience_notes(loop: Any, artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Для круга наблюдения: метка шага → прошлый опыт по его мягкому провалу."""
    ws, out = _workspace(loop), {}
    if ws is None:
        return out
    for label, meta in artifacts.items():
        tool = str(meta.get("tool") or "")
        text = failure_text(tool, meta.get("output"))
        if not text:
            continue
        notes = [n for n in (note_for(ws, tool, text), witness_note(ws, text)) if n]
        if notes:
            out[label] = "\n".join(notes)
            _log(loop, "failure_card_shown", {"tool": tool, "sig": signature(tool, text)})
    return out


def _log(loop: Any, event: str, payload: dict[str, Any]) -> None:
    log = getattr(loop, "log", None)
    if log is not None:
        log.log(event, payload)


# ── учёба в конце хода ──────────────────────────────────────────────────────

_LESSON_SYSTEM = (
    "Ты пишешь урок для агента из его собственного опыта. Дан провалившийся "
    "вызов инструмента, его ошибка и следующий УДАВШИЙСЯ вызов того же "
    "инструмента в том же ходе. Если удачный вызов действительно обходит причину "
    "ошибки, напиши ОДНУ строку по-русски не длиннее 200 знаков: «Когда <инструмент> "
    "даёт <суть ошибки> — <что делать иначе>». Общее правило, без номеров шагов и "
    "частных чисел. Урок говорит ИМЕННО об этой ошибке — тот же код, то же имя, "
    "не о похожей. Если удачный вызов с ошибкой не связан — ответь ровно НЕТ."
)
_CODE_RE = re.compile(r"\b\d{3}\b")


def _lesson_fits(lesson: str, error: str) -> bool:
    """Урок не называет чужой код ошибки. Проверка 24.09 первых 57 уроков:
    к ошибке 404 модель написала урок про 403 — такой урок учит не тому."""
    return all(code in error for code in _CODE_RE.findall(lesson))


def _ask_lesson(llm: Any, tool: str, error: str, failed: Any, fixed: Any, fixed_out: Any) -> str | None:
    user = (f"инструмент: {tool}\nошибка: {error[:400]}\n"
            f"провалившийся вызов: {json.dumps(failed, ensure_ascii=False, default=str)[:800]}\n"
            f"удавшийся вызов: {json.dumps(fixed, ensure_ascii=False, default=str)[:800]}\n"
            f"его вывод (начало): {json.dumps(fixed_out, ensure_ascii=False, default=str)[:300]}")
    raw = llm.complete(system=_LESSON_SYSTEM, user=user, max_tokens=200, temperature=0.2)
    line = next((ln.strip() for ln in str(raw or "").splitlines() if ln.strip()), "")
    if not line or line.strip("«».").upper() == "НЕТ" or len(line) > 300:
        return None
    return line if _lesson_fits(line, error) else None


def turn_results(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Вызовы инструментов хода по порядку: инструмент, аргументы, вывод, провал."""
    calls: dict[str, tuple[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for ev in events:
        p = ev.get("payload") or {}
        if ev.get("event") == "tool_call":
            calls[str(p.get("id"))] = (str(p.get("tool_name") or ""), p.get("arguments"))
        elif ev.get("event") == "write_compose_failed":
            # Текст записи не собрался — file_write даже не зван, но для агента
            # это провал записи, и планировщик видит его как сбой file_write
            # (loop_step_execution._compose_write_content). Живой замер 24.09:
            # «в файл правки пошёл текст без блоков» трижды за один ход, и ни
            # одной карточки — учёба смотрела только на вызовы инструментов.
            out.append({"tool": "file_write", "args": {"path": p.get("path")}, "output": None,
                        "fail": f"текст записи не собрался: {p.get('error') or ''}",
                        "ts": str(ev.get("ts") or ""), "trace": str(ev.get("trace_id") or "")})
        elif ev.get("event") == "tool_result":
            tool, args = calls.get(str(p.get("tool_call_id")), ("", None))
            err = p.get("error") if p.get("status") != "success" else None
            if p.get("status") != "success" and not err:
                err = failed_output_reason(p.get("output"))
            out.append({"tool": tool, "args": args, "output": p.get("output"),
                        "fail": failure_text(tool, p.get("output"), err),
                        "ts": str(ev.get("ts") or ""), "trace": str(ev.get("trace_id") or "")})
    return out


def learn_from_events(workspace: Path | str, events: list[dict[str, Any]], llm: Any,
                      *, trace_id: str = "", max_new: int = MAX_NEW_PER_TURN) -> CardStore:
    """Разобрать ход: посчитать повторы, рассудить показанные карточки, выучить новые."""
    results = turn_results(events)
    # Вопросы модели — без замка: замок на Windows ждёт ~10 с, а три вопроса
    # дольше, и второй процесс падал. Ответы собираются на снимке, потом под
    # замком файл читается заново, правится и пишется (проверка 16.11, мелкая).
    lessons: dict[int, str | None] = {}
    errors: list[str] = []

    def ask(i: int, r: dict[str, Any], fixed: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            lessons[i] = _ask_lesson(llm, r["tool"], r["fail"], r["args"], fixed["args"], fixed["output"])
        except Exception as exc:  # noqa: BLE001 — урок необязателен; причина уходит в журнал хода
            lessons[i] = None
            errors.append(f"{type(exc).__name__}: {exc}"[:300])
        return True, lessons[i]

    if llm is not None:
        _learn_pass(CardStore(workspace), results, ask, trace_id=trace_id, max_new=max_new)

    def answered(i: int, r: dict[str, Any], fixed: dict[str, Any]) -> tuple[bool, str | None]:
        return (i in lessons), lessons.get(i)

    # Загрузка, правка и запись — под одним замком: тик и ход REPL теряли
    # карточки друг друга (проверка 16.11, средняя).
    with _cards_lock(Path(workspace) / CARDS_RELPATH):
        store = CardStore(workspace)
        _learn_pass(store, results, answered, trace_id=trace_id, max_new=max_new)
        store.save()
    store.lesson_errors = errors
    return store


def _learn_pass(store: CardStore, results: list[dict[str, Any]], lesson_for: Any,
                *, trace_id: str, max_new: int) -> None:
    """Один проход разбора по карточкам `store`; урок даёт `lesson_for(i, r, fixed)`
    → (спрошено ли, урок)."""
    start = store.digested.get(trace_id, 0) if trace_id else 0
    asked = 0
    for i in range(start, len(results)):
        r = results[i]
        if not r["fail"]:
            continue
        sig = signature(r["tool"], r["fail"])
        card = store.find(r["tool"], sig)
        later = [x for x in results[i + 1:] if x["tool"] == r["tool"]]
        if card is not None and card.status == "active" and card.lesson and card.created < r["ts"]:
            if later and not later[0]["fail"]:
                card.hits += 1
            elif later and store.find(r["tool"], signature(r["tool"], later[0]["fail"])) is card:
                card.misses += 1
            if card.misses >= 2 and card.misses > 2 * card.hits:
                card.status = "retired"
        if card is None:
            card = Card(sig=sig, tool=r["tool"], error=r["fail"][:300], created=r["ts"],
                        last_seen=r["ts"], seen=0, source_trace=r["trace"],
                        status="guard" if is_guard_refusal(r["fail"]) else "unlearned")
            store.cards[sig] = card
        card.seen += 1
        card.last_seen = r["ts"]
        fixed = next((x for x in later if not x["fail"]), None)
        # Аргументы провалившегося вызова тоже идут в вопрос модели (проверка 17.11, средняя).
        if fixed is not None and _untrusted(r["fail"], r["args"], fixed["args"], fixed["output"]):
            # Flagged material never becomes a lesson, not even as context
            # for the model that writes one.
            fixed = None
        if (card.status in _LEARNABLE and not is_guard_refusal(r["fail"]) and fixed is not None
                and card.asked < _MAX_ASKS and asked < max_new):
            was_asked, lesson = lesson_for(i, r, fixed)
            if not was_asked:
                continue
            asked += 1
            card.asked += 1
            if lesson and not _untrusted(lesson):
                card.lesson, card.status, card.hits, card.misses = lesson, "active", 0, 0
    if trace_id:
        store.digested[trace_id] = len(results)


def read_events(path: Path | str) -> list[dict[str, Any]]:
    events = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def learn_after_turn(loop: Any) -> None:
    """Конец хода: выучить его провалы. Тормоз оператора на авто-память уважается."""
    policy = getattr(loop, "write_policy", None)
    if "agent-auto" in (getattr(policy, "frozen_sources", None) or ()):
        return
    log = getattr(loop, "log", None)
    path = getattr(log, "path", None)
    ws = _workspace(loop)
    if path is None or ws is None:
        return
    try:
        from core.model_router import ModelRole
        router = getattr(loop, "model_router", None)
        llm = router.for_role(ModelRole.MEMORY_SUMMARY) if router is not None else None
        store = learn_from_events(ws, read_events(path), llm,
                                  trace_id=str(getattr(log, "trace_id", "") or ""))
        # Урок не спросился — ход живёт, но причина в журнале (проверка 16.11, мелкая).
        for err in store.lesson_errors:
            _log(loop, "failure_card_lesson_error", {"error": err})
    except Exception as exc:  # noqa: BLE001 — учёба не должна ронять ход; причина — в журнал
        _log(loop, "failure_cards_error", {"error": f"{type(exc).__name__}: {exc}"[:300]})
