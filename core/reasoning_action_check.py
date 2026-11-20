"""Reasoning vs action consistency check (MAST FM-2.6); observational only: it reports, never blocks.

Pure-Python heuristic comparing the planner's free-text ``reasoning`` with the
chosen ``steps``. ``unjustified_action``: a step's tool has no recognisable
mention in the reasoning. ``mentioned_but_not_planned``: the reasoning names a
tool the plan lacks. The keyword table is only a fallback for plans without
per-step rationale; ``check_by_rationale`` judges whether a step states why it
is there.

Measured firing rate (not an error rate): 190 of 268 planner turns (2026-08-19)
and 54% of 1208 turns (2026-09-20). That is why it must not gate anything;
tests/test_the_mismatch_sensor_was_measured.py pins the table against the registry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Tool -> recognisable keywords (English + Russian) that count as a reasoning-side mention. Keep lists short and high-precision.
_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Inspection verbs justify inspection tools; the reverse direction counts only underscore tokens as strong.
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
    "patch_check": ("patch_check", "patch", "правк", "проверю правку", "edits.txt"),
    "diff_file": ("diff_file", "diff", "разниц", "сравн"),
    "read_logs": ("read_logs", "лог", "logs", "журнал"),
    "semantic_scholar_search": ("semantic_scholar", "scholar", "статьи",
                                "paper", "publication"),
    "rss_fetch": ("rss", "feed", "лента"),
    "spawn_subagent": ("subagent", "субагент", "delegate", "делегир"),
    "convert_file": ("convert_file", "ocr", "распозна", "скан", "конверт",
                     "в pdf", "to pdf", "libreoffice", "ffmpeg", "blender",
                     "inkscape", "рендер", "render"),
    "current_time": ("current_time", "current time", "today's date",
                     "current date", "now()", "сегодн", "текущая дат",
                     "текущее врем", "число", "дату"),
    # Tools missing from the table were flagged "action without argument" by construction; `self_repair` is not in the registry, so it is not listed.
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


# Single-token keywords precise enough to signal advocacy for a tool in the reverse direction (product/library names). Everything else there must be a tool token ("_") or a multi-word phrase.
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

        Multi-word phrases and non-ASCII (Cyrillic stem) entries use substring matching; a single ASCII token requires a word boundary ("read" must not match "thread")."""
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


#: A reasoning shorter than this is a brush-off ("needed"), not an argument.
_MIN_RATIONALE_WORDS = 3


def check_by_rationale(steps: Sequence[dict]) -> MismatchReport:
    """Structural check: a step has its OWN rationale or it does not.

        The word-table check accused by the table's shape, not by the plan; the planner writes a rationale per step."""
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

        An empty or placeholder ``reasoning`` reports nothing."""
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

    # Reverse direction: flag any tool the reasoning advocates but the plan omits.
    mentioned_extra: list[str] = []
    for tool, keywords in _TOOL_KEYWORDS.items():
        if tool in used_set:
            continue
        # Require a strong mention (literal tool name or unique keyword); weak stems like "файл" alone must not imply a missing step.
        text_lower = text.lower()
        if tool.lower() in text_lower:
            mentioned_extra.append(tool)
            continue
        # High-signal keywords only: contains an underscore (tool token), is a multi-word phrase, or is a curated unambiguous single token. A bare length rule accepted generic stems.
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
