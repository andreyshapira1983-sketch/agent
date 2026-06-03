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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Tool → recognisable keywords (English + Russian) that count as a
# reasoning-side mention. Keep the lists short and high-precision: false
# negatives (missed mention) lead to noisy warnings, false positives
# (matched on common stem) silently hide real mismatches.
_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "file_read": ("file_read", "read_file", "read the file", "read file",
                  "reads the file", "reading the file", "read ", "reads ",
                  "прочита", "прочту", "читаю", "содерж", "файл"),
    "list_dir": ("list_dir", "ls ", "list the dir", "list files",
                 "содержим", "директор", "каталог", "папк"),
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
    "self_repair": ("self_repair", "repair", "почини", "исправ"),
    "current_time": ("current_time", "current time", "today's date",
                     "current date", "now()", "сегодн", "текущая дат",
                     "текущее врем", "число", "дату"),
}


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


def _reasoning_mentions(reasoning: str, tool: str) -> bool:
    """Does ``reasoning`` contain any keyword tied to ``tool``?"""
    text = reasoning.lower()
    if tool.lower() in text:
        return True
    for kw in _TOOL_KEYWORDS.get(tool, ()):
        if kw.lower() in text:
            return True
    return False


def _tool_alias_in_text(text: str, tool: str) -> bool:
    """Same as ``_reasoning_mentions`` but external-callable for symmetry."""
    return _reasoning_mentions(text, tool)


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
    if not text or text.startswith("(no reasoning") or text.startswith("(planner output"):
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
        # High-signal keywords: those that are themselves tool-like
        # tokens (contain underscore or are >= 6 chars and unambiguous).
        for kw in keywords:
            if (("_" in kw or len(kw.strip()) >= 6) and kw.lower() in text_lower):
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
