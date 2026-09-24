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
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

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
    status: str = "unlearned"      # unlearned | active | retired
    created: str = ""
    last_seen: str = ""
    seen: int = 1
    hits: int = 0
    misses: int = 0
    asked: int = 0
    source_trace: str = ""


# ── подпись ошибки ──────────────────────────────────────────────────────────

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


def _error_line(text: str) -> str:
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
    line = _WORKSPACE_RE.sub("", _error_line(text))
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
        self.cards: dict[str, Card] = {}
        self.digested: dict[str, int] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "_digested" in row:
                self.digested = {str(k): int(v) for k, v in row["_digested"].items()}
            elif "sig" in row:
                known = {k: row[k] for k in Card.__dataclass_fields__ if k in row}
                self.cards[row["sig"]] = Card(**known)

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
        if len(self.cards) > MAX_CARDS:
            rank = {"active": 2, "unlearned": 1, "retired": 0}
            keep = sorted(self.cards.values(), key=lambda c: (rank.get(c.status, 0), c.seen, c.last_seen),
                          reverse=True)[:MAX_CARDS]
            self.cards = {c.sig: c for c in keep}
        digested = dict(list(self.digested.items())[-_DIGESTED_KEEP:])
        rows = [json.dumps({"_digested": digested})]
        rows += [json.dumps(asdict(c), ensure_ascii=False) for c in self.cards.values()]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


# ── показ в момент ошибки ───────────────────────────────────────────────────

def note_for(workspace: Path | str, tool: str, text: str) -> str | None:
    """Строка «прошлый опыт» для этой ошибки или None."""
    card = CardStore(workspace).find(tool, signature(tool, text))
    if card is None or card.status != "active" or not card.lesson:
        return None
    tally = f"встречалась {card.seen} раз"
    if card.hits + card.misses:
        tally += f"; урок помог {card.hits} из {card.hits + card.misses}"
    return f"ПРОШЛЫЙ ОПЫТ ({tally}): {card.lesson}"


def witness_note(workspace: Path | str, text: str) -> str | None:
    """Что охраняет упавший тест — первые строки его описания из кода.

    Описания тестов-свидетелей называют запись реестра (MIR-…, H-…) и причину:
    так реестры доходят до агента в момент, когда он их задел.
    """
    m = _FAILED_TEST_RE.search(text or "")
    if m is None:
        return None
    path = Path(workspace) / m.group(1)
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


def _workspace(loop: Any) -> Path:
    root = getattr(loop, "_file_read_workspace_root", None)
    found = root() if callable(root) else None
    return Path(found) if found else Path.cwd()


def with_past_experience(loop: Any, trigger: Any) -> Any:
    """Сбой шага с приложенным прошлым опытом (в причину — не в улики)."""
    if trigger is None:
        return trigger
    ws = _workspace(loop)
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
    store = CardStore(workspace)
    results = turn_results(events)
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
                        last_seen=r["ts"], seen=0, source_trace=r["trace"])
            store.cards[sig] = card
        card.seen += 1
        card.last_seen = r["ts"]
        fixed = next((x for x in later if not x["fail"]), None)
        if (card.status != "active" and fixed is not None and card.asked < _MAX_ASKS
                and asked < max_new and llm is not None):
            asked += 1
            card.asked += 1
            try:
                lesson = _ask_lesson(llm, r["tool"], r["fail"], r["args"], fixed["args"], fixed["output"])
            except Exception:  # noqa: BLE001 — урок необязателен; ход не должен падать из-за него
                lesson = None
            if lesson:
                card.lesson, card.status, card.hits, card.misses = lesson, "active", 0, 0
    if trace_id:
        store.digested[trace_id] = len(results)
    store.save()
    return store


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
    if path is None:
        return
    try:
        from core.model_router import ModelRole
        router = getattr(loop, "model_router", None)
        llm = router.for_role(ModelRole.MEMORY_SUMMARY) if router is not None else None
        learn_from_events(_workspace(loop), read_events(path), llm,
                          trace_id=str(getattr(log, "trace_id", "") or ""))
    except Exception as exc:  # noqa: BLE001 — учёба не должна ронять ход; причина — в журнал
        _log(loop, "failure_cards_error", {"error": f"{type(exc).__name__}: {exc}"[:300]})
