"""Read-only parsers for grounded self-build backlog signals (TD-036, Phase 1).

Every function here is pure and deterministic: it takes already-loaded text (or
already-loaded value reviews) and returns structured :class:`SignalRecord`s, each
carrying a ``problem_quote`` that is an *exact substring* of its source and an
``evidence_ref`` that points at the source line. Nothing here reads the network,
calls an LLM, touches git, or writes any file.

Phase 1 sources (closed set):

* **TECH_DEBT.md** — entries whose status is not Done (``Partial`` / deferred /
  empty status). The entry *title* is human-authored, so it is used verbatim as
  the grounded ``problem_quote``.
* **knowledge/generated/AGENT_ANATOMY.md** — the "Candidate follow-ups (TD-030+)" advisory list.
  The bold heading of each numbered item is used verbatim as the quote. These
  numbers are advisory text only, never treated as executable TD ids.
* **docs/proposals/self-build-grounded-target-coverage-proposal.md** — the
  TD-038 slice 2 Approach D pilot signal for ``docs/self_build.md`` only.
* **data/value_reviews.jsonl** — used ONLY as an anti-repeat / penalty signal:
  a target whose latest human verdict rejected it should not be re-proposed.

Deliberately conservative: only the stable, unambiguous shape of each source is
parsed. Anything ambiguous is skipped rather than guessed at.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# Verdicts that mark a target as weak. ``rejected_wrong_target`` fully suppresses
# a repeat; the others reduce its rank.
_SUPPRESS_VERDICTS = frozenset({"rejected_wrong_target"})
_PENALTY_VERDICTS = frozenset(
    {"rejected_low_value", "rejected_misleading_summary", "rejected_risky"}
)

# A TECH_DEBT entry title, e.g. "TD-012 — Provider Catalog Refresh" or
# "TD-011 / TD-012 — Live Model Discovery ..." or "P1 — Something".
_TD_TITLE_RE = re.compile(r"^(TD-\d+(?:\s*/\s*TD-\d+)*|P\d+[A-Za-z]?)\s+—\s+\S")
# The id token at the start of such a title.
_TD_ID_RE = re.compile(r"^(TD-\d+(?:\s*/\s*TD-\d+)*|P\d+[A-Za-z]?)")
# A numbered anatomy candidate item: "1. **TD-030 (candidate): Unify ...**  rest"
_ANATOMY_ITEM_RE = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*")
_SELF_BUILD_DOC_TARGET = "docs/self_build.md"
_SELF_BUILD_DOC_SOURCE = (
    "docs/proposals/self-build-grounded-target-coverage-proposal.md"
)
_SELF_BUILD_DOC_QUOTE_RE = re.compile(
    r"^\s*-\s+\*\*What:\*\*\s+first grounded target is `docs/self_build\.md`"
)


@dataclass(frozen=True)
class SignalRecord:
    """One grounded backlog signal. ``problem_quote`` is an exact substring of
    the source text; ``evidence_ref`` is ``<file>:<line>``.
    """

    signal_source: str
    target_path: str
    evidence_ref: str
    problem_quote: str
    rank_hint: float = 0.0


@dataclass(frozen=True)
class ValuePenalties:
    """Targets to suppress or penalize, derived from human value reviews."""

    suppressed: frozenset[str]
    penalized: frozenset[str]

    @classmethod
    def empty(cls) -> ValuePenalties:
        return cls(frozenset(), frozenset())


def _status_is_open(status_text: str) -> bool:
    """True when a TECH_DEBT status marks unfinished work. Conservative: only the
    concrete open markers present in the file count as open."""
    low = status_text.strip().lower()
    if not low:
        return True  # an empty ``Статус:`` marks an unfilled (open) entry
    return ("partial" in low) or ("отлож" in low)


def _slug(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-")


def open_tech_debt(tech_debt_text: str) -> list[SignalRecord]:
    """Entries whose status is not Done. The title line is the grounded quote."""
    lines = tech_debt_text.splitlines()
    records: list[SignalRecord] = []
    seen_ids: set[str] = set()
    for i, line in enumerate(lines):
        title = line.strip()
        if not _TD_TITLE_RE.match(title):
            continue
        # The status is the first ``Статус:`` line within a few lines below the
        # title; collect its value plus any immediately-following bullet lines.
        status_parts: list[str] = []
        j = i + 1
        found_status = False
        while j < len(lines) and j < i + 6:
            s = lines[j].strip()
            if s.startswith("Статус:"):
                found_status = True
                status_parts.append(s[len("Статус:"):].strip())
                k = j + 1
                while k < len(lines) and lines[k].strip():
                    status_parts.append(lines[k].strip())
                    k += 1
                break
            j += 1
        if not found_status:
            continue
        if not _status_is_open(" ".join(status_parts)):
            continue
        id_match = _TD_ID_RE.match(title)
        target = (
            re.sub(r"\s+", " ", id_match.group(1)).strip()
            if id_match
            else _slug(title)
        )
        if target in seen_ids:
            continue
        seen_ids.add(target)
        records.append(
            SignalRecord(
                signal_source="tech_debt",
                target_path=target,
                evidence_ref=f"TECH_DEBT.md:{i + 1}",
                problem_quote=title,
            )
        )
    return records


def anatomy_candidates(anatomy_text: str) -> list[SignalRecord]:
    """The bold heading of each item under 'Candidate follow-ups (TD-030+)'."""
    lines = anatomy_text.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if "Candidate follow-ups" in line and line.lstrip().startswith("#"):
            start = i + 1
            break
    if start is None:
        return []
    records: list[SignalRecord] = []
    seen: set[str] = set()
    for i in range(start, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("#"):
            break  # next section ends the advisory list
        m = _ANATOMY_ITEM_RE.match(stripped)
        if not m:
            continue
        heading = m.group(1).strip()
        target = "anatomy:" + _slug(heading)
        if target in seen:
            continue
        seen.add(target)
        records.append(
            SignalRecord(
                signal_source="anatomy",
                target_path=target,
                evidence_ref=f"knowledge/generated/AGENT_ANATOMY.md:{i + 1}",
                # The heading is a substring of the raw line (which includes the
                # surrounding ``**``), so it is trivially traceable to source.
                problem_quote=heading,
            )
        )
    return records


def self_build_docs_candidate(proposal_text: str) -> list[SignalRecord]:
    """TD-038 slice 2 Approach D: one grounded docs-only pilot target."""
    lines = proposal_text.splitlines()
    has_td_038 = any("TD-038 slice 2" in line for line in lines)
    has_approach_d = any(
        line.startswith("### D. Mapper coverage")
        and "docs-only" in line
        and "operator-guide" in line
        for line in lines
    )
    if not (has_td_038 and has_approach_d):
        return []
    for i, line in enumerate(lines):
        if _SELF_BUILD_DOC_QUOTE_RE.match(line):
            quote_lines = [line.rstrip()]
            j = i + 1
            while j < len(lines) and lines[j].startswith("  "):
                quote_lines.append(lines[j].rstrip())
                j += 1
            quote = "\n".join(quote_lines)
            return [
                SignalRecord(
                    signal_source="self_build_docs",
                    target_path=_SELF_BUILD_DOC_TARGET,
                    evidence_ref=f"{_SELF_BUILD_DOC_SOURCE}:{i + 1}",
                    problem_quote=quote,
                )
            ]
    return []


# ── Architecture-audit signal (TD-036 follow-up) ──────────────────────────────
# Source constant for backlog signals derived from the agent's own architecture
# audit (core.architecture_audit.audit_architecture). This is what lets the agent
# find its own work from self-analysis instead of only human-written docs.
ARCHITECTURE_AUDIT_SOURCE = "architecture_audit"


def _select_audit_target(
    evidence_files: list[str],
    exists: Callable[[str], bool] | None,
) -> str:
    """Pick the concrete target for an audit gap.

    A gap's check is green only when *all* its evidence files exist, so the most
    actionable target is the first one that is still MISSING. When an ``exists``
    predicate is supplied we return that first-missing file; otherwise (or if all
    files already exist) we fall back to the first evidence file. This is what
    lets the agent go after ``README.md`` instead of re-targeting an
    already-present ``AGENT_DOCTRINE.md``.
    """
    if not evidence_files:
        return ""
    if exists is not None:
        for path in evidence_files:
            try:
                present = exists(path)
            except Exception:  # noqa: BLE001 — a bad predicate must not break audit
                present = True
            if not present:
                return path
    return evidence_files[0]


def architecture_audit_candidates(
    priority_gaps: Iterable[Mapping[str, Any]],
    *,
    exists: Callable[[str], bool] | None = None,
) -> tuple[list[SignalRecord], str]:
    """Turn an architecture audit's priority gaps into grounded backlog
    signals.

    ``exists`` is an optional ``(rel_path) -> bool`` predicate (kept
    injectable so the function stays pure/testable). Gaps without a usable
    title are skipped. Best-effort per-gap: a malformed gap entry is skipped
    rather than raising, so a bad audit never breaks the backlog.
    """
    records: list[SignalRecord] = []
    quotes: list[str] = []
    seen: set[str] = set()
    for gap in priority_gaps or []:
        try:
            title = str(gap.get("title") or "").strip()
            if not title:
                continue
            gap_id = str(gap.get("id") or _slug(title)).strip() or _slug(title)
            if gap_id in seen:
                continue
            evidence_files = [
                str(f).strip()
                for f in (gap.get("evidence_files") or [])
                if str(f).strip()
            ]
            target = _select_audit_target(evidence_files, exists) or (
                "architecture:" + _slug(title)
            )
        except AttributeError:
            continue  # not a mapping-shaped gap
        seen.add(gap_id)
        quotes.append(title)
        records.append(
            SignalRecord(
                signal_source=ARCHITECTURE_AUDIT_SOURCE,
                target_path=target,
                evidence_ref=f"architecture_audit:{gap_id}",
                problem_quote=title,
            )
        )
    return records, "\n".join(quotes)


# The code_todo organ (scanning the tree for TODO/FIXME/XXX comment markers)
# lived here until 2026-08-28 and was erased by operator ruling: a marker a
# person typed is the retired "human assigns — agent executes" model, not a
# self-measured signal. History: MIR-183 in docs/audit/MASTER_ISSUE_REGISTRY.md.

OVERSIZED_MODULE_SOURCE = "oversized_module"

# A module past this many lines is a "split me" advisory. The threshold is a soft
# structural budget, not a hard rule: it only surfaces the file for a human to
# consider splitting into focused modules. Bounded like the code-TODO organ so a
# repo full of large files cannot flood the backlog.
_OVERSIZED_MODULE_MIN_LINES = 800
_MAX_OVERSIZED_RECORDS = 25

#: Функция длиннее этого — та, которую НЕЛЬЗЯ прочесть и найти в ней проблему.
#: Слово оператора 2026-09-23, договор о дроблении: «файл делится, когда в нём
#: лишние функции, которые к нему не относятся, либо когда он так велик, что
#: его не прочесть и не увидеть, где беда; маленький файл легче наблюдать».
#:
#: Замер, которым договор проверен. Механизм предложил разделить
#: core/loop_step_execution.py (1158 строк) — вынести восемь методов по 25-32
#: строки. Все двенадцать методов файла относятся к исполнению шага, чужого
#: в нём нет; линейка файлов его не сторожит (потолок 2000). А `_execute_step`
#: на 566 строк — ровно то, что прочесть нельзя, — оставался нетронутым.
#: Правило мерило файл, а не содержимое: двадцать файлов core/ длиннее 800
#: строк стали кандидатами разом, и агент дробил бы их по очереди, вынося
#: отовсюду мелочь. Мерить надо то, из-за чего дробят.
_UNREADABLE_FUNCTION_LINES = 200


def _code_line_count(content: str) -> tuple[int, bool]:
    """Lines that carry code, and whether the module parsed.

    MIR-099: this sensor used to count TOTAL lines, and 5 of its 10 live
    verdicts flipped when prose was excluded — its first self-chosen proposal
    targeted a module of 870 lines with only 602 of code. The census rule,
    applied verbatim: a docstring line or a pure-comment line is prose; a line
    carrying code plus a trailing comment is CODE. The 275-module census also
    showed the errors were one-directional (zero modules under the limit in
    total but over it in code), so counting code removes noise and cannot
    newly miss anything. On a syntax error the caller falls back to the total
    — the sensor must not go blind on an unparseable module.
    """
    import ast
    import io
    import tokenize

    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return 0, False
    prose: set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef))
                and ast.get_docstring(node, clean=False) is not None
                and node.body and isinstance(node.body[0], ast.Expr)):
            first = node.body[0]
            for line in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                prose.add(line)
    code_lines: set[int] = set()
    try:
        lines = content.splitlines()
        for tok in tokenize.generate_tokens(io.StringIO(content).readline):
            if tok.type == tokenize.COMMENT:
                at = tok.start[0]
                if at - 1 < len(lines) and lines[at - 1].strip().startswith("#"):
                    prose.add(at)
            elif tok.type == tokenize.STRING:
                # A multi-line literal that is NOT a docstring is payload, not
                # explanation: an embedded prompt, SQL or template is bulk the
                # module carries. Counting only its FIRST line made
                # `core/planner_prompt.py` read as 8 code lines of 553 —
                # measured while auditing this sensor's own closure, and it is
                # a false-NEGATIVE channel: the old total-lines counter would
                # have flagged such a module and this one would not.
                for line in range(tok.start[0], tok.end[0] + 1):
                    code_lines.add(line)
            elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                  tokenize.DEDENT, tokenize.ENDMARKER):
                code_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        return 0, False
    return len(code_lines - prose), True

# The emitted target is deliberately abstract (``split:<file>``) so that this
# module stays a pure detector: it names a structural property, not a file to
# edit. Resolving it to a concrete module and deciding whether that module may
# be touched belongs to core/backlog_target_mapper.py and the Manager's
# critical gate, not here.
_OVERSIZED_TARGET_PREFIX = "split:"


def _longest_function(content: str) -> tuple[str, int, int]:
    """Самая длинная функция модуля: имя, число строк и где она начинается.

    Метод класса называется вместе с классом (`Класс.метод`), иначе читающий
    не найдёт `_execute_step` среди одноимённых. Немой разбор — не повод
    объявить файл здоровым и не повод его дробить: нечитаемый для машины
    модуль возвращает пустое имя и ноль строк, и сигнала не будет.
    """
    import ast

    try:
        tree = ast.parse(content or "")
    except (SyntaxError, ValueError):
        return "", 0, 0
    best_name, best_size, best_at = "", 0, 0
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner[id(child)] = node.name
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        size = (node.end_lineno or node.lineno) - node.lineno + 1
        if size > best_size:
            prefix = owner.get(id(node))
            best_name = f"{prefix}.{node.name}" if prefix else node.name
            best_size, best_at = size, node.lineno
    return best_name, best_size, best_at


def oversized_module_candidates(
    files: Iterable[tuple[str, str]],
) -> tuple[list[SignalRecord], str]:
    """Turn oversized source modules into grounded, report-only backlog
    signals.

    ``files`` is an iterable of ``(rel_path, content)`` pairs already read
    by the caller (this function performs NO IO, so it stays
    pure/deterministic and unit-testable). Every file whose line count is at
    or above :data:`_OVERSIZED_MODULE_MIN_LINES` becomes one
    :class:`SignalRecord` whose ``target_path`` is the abstract
    ``split:<rel_path>`` (report-only — see
    :data:`_OVERSIZED_TARGET_PREFIX`), ``evidence_ref`` is ``<rel_path>:1``,
    and ``problem_quote`` states the concrete line count.
    """
    measured: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for rel_path, content in files:
        rel = str(rel_path or "").replace("\\", "/").strip()
        if not rel or content is None or rel in seen:
            continue
        seen.add(rel)
        total = content.count("\n") + 1 if content else 0
        # Повод дробить — не длина файла, а функция, которую нельзя прочесть.
        # Файл без такой функции читается, и беду в нём видно: дробить его
        # значит двигать мелочь и множить файлы без смысловой границы.
        name, size, at = _longest_function(content)
        if size >= _UNREADABLE_FUNCTION_LINES:
            measured.append((size, total, rel, name, at))

    measured.sort(key=lambda item: (-item[0], item[2]))

    records: list[SignalRecord] = []
    quotes: list[str] = []
    kept = measured[:_MAX_OVERSIZED_RECORDS]
    count = len(kept)
    for index, (decisive, total, rel, name, at) in enumerate(kept):
        quote = (
            f"{rel}: функция {name} занимает {decisive} строк при пороге "
            f"читаемости {_UNREADABLE_FUNCTION_LINES} — её нельзя прочесть "
            f"целиком и увидеть, где беда (весь файл {total} строк). Разделить "
            "надо её: вынести смысловые части тела в отдельные функции или "
            "модуль. Переносить мелких соседей незачем — они и так читаются."
        )
        quotes.append(quote)
        records.append(
            SignalRecord(
                signal_source=OVERSIZED_MODULE_SOURCE,
                target_path=f"{_OVERSIZED_TARGET_PREFIX}{rel}",
                # Улика ведёт к САМОЙ функции, а не к первой строке файла:
                # читающий должен открыть то, из-за чего дробят.
                evidence_ref=f"{rel}:{at}",
                problem_quote=quote,
                # Worst-first: the biggest module gets the largest bump so it
                # ranks ahead of its peers while staying below the next tier.
                rank_hint=(count - index) * 0.001,
            )
        )
    return records, "\n".join(quotes)



SPLIT_PROOF_SOURCE = "split_proof"


def split_proof_candidates(
    proofs: Iterable[tuple[str, Any]],
) -> tuple[list[SignalRecord], str]:
    """Модули с ДОКАЗАТЕЛЬСТВОМ из core/split_proof.py — работа для рук.

    Эпизод 2026-09-21: драйв нашёл настоящий дубль в core/task_queue.py и
    поставил цель, а руки (`_default_grounded_selector`) ответили «no grounded
    backlog candidate»: весь бэклог был семью записями «файл большой», и ни
    одной доказанной. Доказательство доходило до головы и не доходило до рук.

    ``proofs`` — пары ``(rel, proof)`` уже в порядке силы; без IO. Цель —
    тот же абстрактный ``split:<rel>``, что у размера: отображение в файл и
    решение, КАК править (свести дубль или вынести предмет), остаются за
    картографом и производителем.
    """
    items = [(str(rel).replace("\\", "/").strip(), proof) for rel, proof in proofs]
    items = [(rel, proof) for rel, proof in items if rel and proof is not None]
    records: list[SignalRecord] = []
    quotes: list[str] = []
    for index, (rel, proof) in enumerate(items):
        quote = proof.describe(rel)
        quotes.append(quote)
        records.append(SignalRecord(
            signal_source=SPLIT_PROOF_SOURCE,
            target_path=f"{_OVERSIZED_TARGET_PREFIX}{rel}",
            evidence_ref=f"{rel}:1",
            problem_quote=quote,
            rank_hint=(len(items) - index) * 0.001,
        ))
    return records, "\n".join(quotes)


def value_review_penalties(
    reviews: Iterable,
    item_target_map: Mapping[str, str] | None = None,
) -> ValuePenalties:
    """Derive suppress/penalize target sets from human value reviews.

    ``reviews`` is an iterable of objects with ``item_id`` and ``verdict``
    (e.g. :class:`core.value_review.ValueReview`). A review contributes only
    when its ``item_id`` resolves to a target via ``item_target_map`` (best-
    effort: with no map, no penalties are produced, and value_reviews is
    never mutated).
    """
    if not item_target_map:
        return ValuePenalties.empty()
    effective: dict[str, str] = {}
    for review in reviews:
        item_id = str(getattr(review, "item_id", "") or "")
        verdict = str(getattr(review, "verdict", "") or "")
        if item_id:
            effective[item_id] = verdict  # write order -> last wins
    suppressed: set[str] = set()
    penalized: set[str] = set()
    for item_id, verdict in effective.items():
        target = item_target_map.get(item_id)
        if not target:
            continue
        if verdict in _SUPPRESS_VERDICTS:
            suppressed.add(target)
        elif verdict in _PENALTY_VERDICTS:
            penalized.add(target)
    # A suppressed target is not also merely penalized.
    penalized -= suppressed
    return ValuePenalties(frozenset(suppressed), frozenset(penalized))
