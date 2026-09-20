"""Reasoning ↔ action consistency check — MAST FM-2.6 (13.2%).

Pure-Python heuristic comparing the planner's free-text ``reasoning`` field
against the chosen ``steps``. Two failure shapes are surfaced:

* ``unjustified_action``: a step uses a tool that has no recognisable
  mention (or alias) in the reasoning text — the agent acts without
  having argued for it.
* ``mentioned_but_not_planned``: the reasoning explicitly names a tool /
  action class that the plan does not contain — the agent argues for one
  thing and does another.

This is observational only: it produces a report; it does not block the
plan. Down the line a stricter mode could replan or demote confidence.

STRUCTURAL REPLACEMENT, 2026-09-20. The branch this note ends with proposed
reading the per-step ``rationale`` instead of guessing from the free text —
and the planner has been asked for that field all along
(``core/planner_prompt.py``). The pipeline DROPPED it: the sanitiser rebuilds
each step as tool + arguments + expected_outcome. Re-measured over the 1208
planner turns of that day's run: the keyword check fires on 654 of them (54%),
with 208 accusations against ``list_dir``, 187 against ``python_probe`` — a
tool the table did not contain at all — and 155 against ``find_in_files``.
Filling the table changed the rate by nothing (54% before, 54% after): the
accusations simply moved into the reverse direction. So the word table is kept
only as the fallback for plans that carry no rationale, and ``check_by_rationale``
judges what can be judged: a step either states why it is there or it does not.

MEASURED, and the number is the reason it must stay observational
(2026-08-19, over every ``planner`` event in ``logs/`` — 268 real turns
carrying both a reasoning text and a plan): **the detector fires on 190 of
them, 71 %**.

Read that as a FIRING RATE, not an error rate — the distinction is the
whole epistemic content of this note. What was and was not established:

* FACT — it fires on 190 of 268 turns.
* FACT — 241 of its 255 ``unjustified`` accusations land on tools whose
  table entry is a narrow literal (see below), i.e. they are explained by
  the table's shape rather than by anything about the plan.
* FACT — exactly TWO accusations were adjudicated by reading them, and
  both were false. Both flagged ``list_dir``: «inspect the relevant source
  code and recent audit evidence read-only» and «…план должен начинаться с
  web_search; при этом у вас в системе недоступны web_* инструменты…» —
  each argues plainly for the step it took, in words the table lacks.
* UNKNOWN — the true false-positive rate over all 190. No ground truth was
  labelled, and 2 read cases do not license a rate.
* SUPPORTED INFERENCE — putting an enforcement threshold on top of this is
  unsafe while the structural defects below stand.

Where the accusations concentrate, in the code below:

* 241 of 255 accusations are keyword misses on tools the table DOES know —
  ``list_dir`` (114) demands the literal "list files"/"ls "/"каталог",
  ``file_read`` (44) keys on ``"read "`` WITH a trailing space, so
  "reading core/loop.py" cannot match;
* 14 accuse tools the table has no entry for at all: the table knows 13
  tools while the registry ships 15 — ``file_write``, ``python_probe`` and
  ``lesson_provenance`` are invisible here, so planning them is flagged by
  construction;
* the reverse direction is worse than noisy: ``self_repair`` (4 firings)
  sits in the table but exists in no registry, so the check accuses the
  planner of omitting a step it cannot produce.

The measurement is reproducible: iterate ``planner`` events in ``logs/``,
call :func:`check_reasoning_actions` on ``reasoning`` + ``tools_chosen``,
and count. tests/test_the_mismatch_sensor_was_measured.py pins the
structural half so the table cannot drift further from the registry in
silence. Provenance: first measured on 2026-08-05 in
``wip/mir-015-structural-justification`` (108 turns, 44 firings), a branch
that never merged; re-measured today against main before being written
here. The branch also proposed a structural replacement — read the
per-step ``rationale`` the planner is asked for instead of guessing from
prose — which is NOT imported here: that is a behaviour change and a
separate operator decision (MIR-015).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Tool → recognisable keywords (English + Russian) that count as a
# reasoning-side mention. Keep the lists short and high-precision: false
# negatives (missed mention) lead to noisy warnings, false positives
# (matched on common stem) silently hide real mismatches.
_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    # MIR-015 (замер 2026-08-19): 158 из 241 ложных обвинений — глаголы
    # ОСМОТРА, не значившиеся упоминанием инструментов осмотра («inspect the
    # source», «изучу», «посмотрю структуру»). Осмотровый глагол оправдывает
    # осмотровый инструмент; обратное направление этим не вооружается — там
    # сильными считаются только токены с подчёркиванием.
    "file_read": ("file_read", "read_file", "read the file", "read file",
                  "reads the file", "reading the file", "read ", "reads ",
                  "прочита", "прочту", "читаю", "содерж", "файл",
                  "inspect", "examine", "look at", "изуч", "посмотр",
                  "просмотр", "загля", "открою"),
    "list_dir": ("list_dir", "ls ", "list the dir", "list files",
                 "содержим", "директор", "каталог", "папк",
                 "inspect", "examine", "structure", "структур",
                 "изуч", "посмотр", "просмотр", "состав", "обзор"),
    "find_in_files": ("find_in_files", "grep", "search the workspace", "search files",
                      "найти", "найд", "поиск", "ищу", "искать", "locate", "find "),
    "web_search": ("web_search", "search the web", "google", "search engine",
                   "поиск", "найти в", "поищ"),
    "web_fetch": ("web_fetch", "fetch the page", "fetch url", "загруз",
                  "скача", "открыть страниц", "url", "сайт"),
    "shell_exec": ("shell_exec", "shell", "powershell", "bash",
                   "команд", "выполн", "запуст"),
    "run_tests": ("run_tests", "pytest", "test suite", "run the test",
                  "run tests", "тест", "прогон"),
    "diff_file": ("diff_file", "diff", "разниц", "сравн"),
    "read_logs": ("read_logs", "лог", "logs", "журнал"),
    "semantic_scholar_search": ("semantic_scholar", "scholar", "статьи",
                                "paper", "publication"),
    "rss_fetch": ("rss", "feed", "лента"),
    "spawn_subagent": ("subagent", "субагент", "delegate", "делегир"),
    "current_time": ("current_time", "current time", "today's date",
                     "current date", "now()", "сегодн", "текущая дат",
                     "текущее врем", "число", "дату"),
    # 2026-09-20: пять инструментов таблица не знала вовсе, и планирование
    # каждого объявлялось «действием без довода» ПО ПОСТРОЕНИЮ. За сутки это
    # дало 187 обвинений одному `python_probe`. Запись `self_repair` убрана:
    # такого инструмента в реестре нет, и обратное направление обвиняло
    # планировщика в пропуске шага, который он не может сделать.
    "python_probe": ("python_probe", "probe", "посчита", "подсчита", "расч",
                     "вычисл", "compute", "calculate", "numeric", "скрипт",
                     "эксперимент", "python", "проверю"),
    "file_write": ("file_write", "запиш", "запис", "сохран", "write the file",
                   "write to", "создам файл", "save"),
    "lesson_provenance": ("lesson_provenance", "урок", "lesson", "происхожден"),
    "journal_append": ("journal_append", "в журнал", "journal", "заметк"),
    "memory_recall": ("memory_recall", "вспомн", "из памяти", "recall"),
    "memory_bank": ("memory_bank", "запомн", "remember", "в память"),
    "model_roster": ("model_roster", "roster", "реестр моделей", "какие модели"),
    "model_route": ("model_route", "маршрут", "routing", "какая модель"),
}


# Single-token keywords that are precise enough to signal, on their own,
# that the reasoning is advocating for a specific tool in the reverse
# (mentioned-but-not-planned) direction. These are product/library names
# or otherwise unambiguous — unlike generic verb/noun stems, they do not
# appear in ordinary planner prose by accident. Everything else in the
# reverse direction must be a tool token ("_") or a multi-word phrase.
_STRONG_SINGLE_TOKENS: frozenset[str] = frozenset({
    "pytest", "google", "powershell", "scholar", "publication",
    "subagent", "субагент", "delegate",
})


@dataclass(frozen=True)
class MismatchReport:
    """Outcome of one consistency check."""

    unjustified_actions: tuple[str, ...] = ()  # tools used without mention
    mentioned_but_not_planned: tuple[str, ...] = ()  # tools mentioned but absent
    matched_tools: tuple[str, ...] = ()  # tools correctly mentioned & used

    @property
    def has_mismatch(self) -> bool:
        return bool(self.unjustified_actions or self.mentioned_but_not_planned)

    def to_log_payload(self) -> dict:
        return {
            "unjustified_actions": list(self.unjustified_actions),
            "mentioned_but_not_planned": list(self.mentioned_but_not_planned),
            "matched_tools": list(self.matched_tools),
        }


def _keyword_in_text(text: str, kw: str) -> bool:
    """Does the lowercased ``text`` contain keyword ``kw``?

    Matching mode depends on the keyword shape so that short, ambiguous
    stems do not silently match inside unrelated words:

    * Multi-word phrases ("read the file") keep substring semantics — a
      whole phrase is already high-signal.
    * Non-ASCII entries ("прочита", "содерж") are deliberate Cyrillic
      stems meant to match inflected forms ("прочитаю"), so they also use
      substring matching.
    * A single ASCII token ("read", "ls", "url") requires a word boundary,
      so it no longer matches inside "thread", "calls" or "curl".
    """
    kw = kw.strip().lower()
    if not kw:
        return False
    if " " in kw or not kw.isascii():
        return kw in text
    pattern = r"(?<![0-9a-z])" + re.escape(kw) + r"(?![0-9a-z])"
    return re.search(pattern, text) is not None


def _reasoning_mentions(reasoning: str, tool: str) -> bool:
    """Does ``reasoning`` contain any keyword tied to ``tool``?"""
    text = reasoning.lower()
    if tool.lower() in text:
        return True
    return any(_keyword_in_text(text, kw) for kw in _TOOL_KEYWORDS.get(tool, ()))


#: Довод короче этого — не довод, а отписка («needed», «нужно»).
_MIN_RATIONALE_WORDS = 3


def check_by_rationale(steps: Sequence[dict]) -> MismatchReport:
    """Структурная проверка: у шага есть СВОЙ довод или его нет.

    Замер 2026-09-20 (1208 ходов суток): словарная проверка срабатывала на
    54%, и обвинения объяснялись формой таблицы, а не планом — 187 из них
    достались `python_probe`, которого в таблице не было вовсе. Дополнение
    таблицы ничего не изменило (54% до и после): обвинения просто переехали
    в обратное направление. Угадывать довод по прозе незачем — планировщик
    пишет его к каждому шагу, конвейер его терял (core/planner.py).
    """
    unjustified: list[str] = []
    matched: list[str] = []
    for step in steps or ():
        tool = str((step or {}).get("tool") or "")
        if not tool:
            continue
        rationale = str((step or {}).get("rationale") or "").strip()
        if len(rationale.split()) >= _MIN_RATIONALE_WORDS:
            matched.append(tool)
        else:
            unjustified.append(tool)
    return MismatchReport(
        unjustified_actions=tuple(dict.fromkeys(unjustified)),
        mentioned_but_not_planned=(),
        matched_tools=tuple(dict.fromkeys(matched)),
    )


def check_reasoning_actions(
    reasoning: str,
    tools_used: Iterable[str],
) -> MismatchReport:
    """Compare reasoning text to the set of tools chosen by the planner.

    ``reasoning`` may be empty / placeholder ("(no reasoning provided)") —
    in that case nothing is reported (cannot infer mismatch from absence
    of any reasoning at all; that is a separate failure mode handled by
    the planner-parse warnings).
    """
    text = (reasoning or "").strip()
    tools_list = [t for t in tools_used if t]
    if not text or text.startswith(("(no reasoning", "(planner output")):
        return MismatchReport()

    used_set = set(tools_list)
    unjustified: list[str] = []
    matched: list[str] = []
    for tool in tools_list:
        if _reasoning_mentions(text, tool):
            matched.append(tool)
        else:
            unjustified.append(tool)

    # Reverse direction: scan known tool keywords inside reasoning and
    # flag any tool that the reasoning advocates for but the plan omits.
    mentioned_extra: list[str] = []
    for tool, keywords in _TOOL_KEYWORDS.items():
        if tool in used_set:
            continue
        # Require a *strong* mention: the literal tool name OR a unique
        # high-signal keyword. We deliberately do NOT trigger on weak
        # stems like "файл" alone, or every mention of "тест" would
        # imply a missing run_tests step.
        text_lower = text.lower()
        if tool.lower() in text_lower:
            mentioned_extra.append(tool)
            continue
        # High-signal keywords only. A bare ">= 6 chars" rule used to
        # accept generic verb/noun stems ("выполн", "запуст", "команд",
        # "загруз", "содерж", "содержим", "сегодн", ...) that appear in
        # ordinary planner prose even when the tool was never intended —
        # so the reverse check fired on almost every turn. We now accept a
        # keyword as strong advocacy only when it is genuinely tool-like:
        #   * contains an underscore (a tool token, e.g. "run_tests"), or
        #   * is a multi-word phrase (inherently specific, e.g.
        #     "search the web"), or
        #   * is one of a curated set of unambiguous single tokens.
        for kw in keywords:
            k = kw.strip().lower()
            if not k:
                continue
            is_strong = ("_" in k or " " in k or k in _STRONG_SINGLE_TOKENS)
            if is_strong and _keyword_in_text(text_lower, k):
                mentioned_extra.append(tool)
                break

    # Deduplicate while preserving order.
    def _uniq(seq: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return tuple(out)

    return MismatchReport(
        unjustified_actions=_uniq(unjustified),
        mentioned_but_not_planned=_uniq(mentioned_extra),
        matched_tools=_uniq(matched),
    )
