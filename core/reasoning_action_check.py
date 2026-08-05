"""Reasoning ↔ action consistency — MAST FM-2.6 (13.2%).

Two detectors of the same requirement, and only one of them may decide.

:func:`check_step_justification` reads the reason the planner STATED for each
step. That is a structural fact, so it can block: a step whose author gave no
reason for it does not run.

:func:`check_reasoning_actions` guesses the same thing by matching keywords
against the free-text ``reasoning`` blob. Measured over every ``planner`` event
in ``logs/`` on 2026-08-05 — 108 real turns — it fires on 44 of them, and the
accusations do not survive reading: ``file_write`` has no entry in the keyword
table at all, ``list_dir`` demands the literal phrase "list files" while the
prose says "listing actual directory contents", ``file_read`` keys on ``"read "``
with a trailing space so "reading core/loop.py" cannot match. It stays an
observer for that reason (``docs/audit/SENSOR_SIGNAL_MEASUREMENT.md``, S4).

The one-line version: the planner is already required to state a rationale per
step, the code threw it away, and a keyword table was left guessing at what had
been discarded.

The lexical half, kept for the record — two failure shapes:

* ``unjustified_action``: a step uses a tool that has no recognisable
  mention (or alias) in the reasoning text — the agent acts without
  having argued for it.
* ``mentioned_but_not_planned``: the reasoning explicitly names a tool /
  action class that the plan does not contain — the agent argues for one
  thing and does another. This direction can also name a tool that is in no
  registry (``self_repair``, demanded 5 times in the logs), so it accuses the
  planner of omitting a step it had no way to produce.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class JustificationReport:
    """Which planner steps arrived without the reason the contract demands."""

    unjustified_labels: tuple[str, ...] = ()  # step labels with no stated reason
    unjustified_tools: tuple[str, ...] = ()  # their tools, deduplicated
    justified_tools: tuple[str, ...] = ()  # tools whose step stated a reason
    exempt_tools: tuple[str, ...] = ()  # steps the planner did not author

    @property
    def has_unjustified(self) -> bool:
        return bool(self.unjustified_labels)

    def to_log_payload(self) -> dict:
        return {
            "unjustified_labels": list(self.unjustified_labels),
            "unjustified_tools": list(self.unjustified_tools),
            "justified_tools": list(self.justified_tools),
            "exempt_tools": list(self.exempt_tools),
        }


def check_step_justification(
    sources: Iterable[dict],
) -> JustificationReport:
    """Which steps the planner chose without saying why.

    Reads ``rationale``, which `LLMPlanner._validate_steps` attaches to every
    step it produces, and which the output contract has always demanded
    ("<one sentence explaining WHY this step is needed>").

    A MISSING key and an EMPTY one are not the same and must not be merged.
    Steps injected by the system — a forced plan, the explicit-file-hint
    read — never pass through the planner and so carry no key at all; holding
    them to a contract they were never shown would block the very paths that
    exist to rescue a turn. Only ``""`` is an accusation, and it accuses the
    one author who was asked.

    Deliberately not a judgement of the reason's QUALITY. A model required to
    fill a field will fill it, so this cannot detect a hollow rationale — it
    detects the absence of one, which is the part that can be checked without
    guessing. The lexical detector above is what a quality judgement would
    look like, and its measured cost is in this module's docstring.
    """
    unjustified: list[str] = []
    unjustified_tools: list[str] = []
    justified: list[str] = []
    exempt: list[str] = []

    for step in sources:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "")
        if "rationale" not in step:
            exempt.append(tool)
            continue
        if str(step.get("rationale") or "").strip():
            justified.append(tool)
            continue
        # The label identifies WHICH step, because a plan may hold four
        # `file_read`s of which one is unreasoned; naming only the tool would
        # send the whole group back.
        unjustified.append(str(step.get("label") or tool))
        unjustified_tools.append(tool)

    return JustificationReport(
        unjustified_labels=tuple(unjustified),
        unjustified_tools=_dedupe(unjustified_tools),
        justified_tools=_dedupe(justified),
        exempt_tools=_dedupe(exempt),
    )


def _dedupe(seq: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x for x in seq if x))


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
