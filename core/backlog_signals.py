"""Read-only, pure parsers for grounded self-build backlog signals.

Each :class:`SignalRecord` carries a ``problem_quote`` that is an exact substring of its source and an ``evidence_ref`` pointing at the source line.
Sources: TECH_DEBT.md (open entries), the AGENT_ANATOMY.md candidate follow-ups, the self-build docs proposal pilot, and
data/value_reviews.jsonl (anti-repeat penalty only). Conservative: ambiguous shapes are skipped, never guessed.
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
        # status = first ``Статус:`` line a few lines below the title, plus immediately-following bullet lines
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
                # the heading is a substring of the raw line, so it is traceable to source
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


# Source constant for backlog signals derived from the agent's own architecture audit (core.architecture_audit.audit_architecture).
ARCHITECTURE_AUDIT_SOURCE = "architecture_audit"


def _select_audit_target(
    evidence_files: list[str],
    exists: Callable[[str], bool] | None,
) -> str:
    """Pick the concrete target for an audit gap: the first MISSING evidence file when an `exists` predicate is given, else the first evidence file."""
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
    """Turn an architecture audit's priority gaps into grounded backlog signals.

    `exists` is an optional ``(rel_path) -> bool`` predicate (injectable to stay pure). Gaps without a usable title, or malformed, are skipped.
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



OVERSIZED_MODULE_SOURCE = "oversized_module"

# A module past this many lines is a "split me" advisory: a soft structural budget that only surfaces the file for a human to consider.
_OVERSIZED_MODULE_MIN_LINES = 800
_MAX_OVERSIZED_RECORDS = 25

#: A function longer than this is one that cannot be read to find a problem in it; the split rule measures what makes people split, not file length.
_UNREADABLE_FUNCTION_LINES = 200


def _code_line_count(content: str) -> tuple[int, bool]:
    """Lines that carry code, and whether the module parsed.

    A docstring line or pure-comment line is prose; a code line with a trailing comment is code. On a syntax error the caller falls back to the total.
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
                # a multi-line literal that is not a docstring is payload (prompt, SQL, template) and counts line by line
                for line in range(tok.start[0], tok.end[0] + 1):
                    code_lines.add(line)
            elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                  tokenize.DEDENT, tokenize.ENDMARKER):
                code_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        return 0, False
    return len(code_lines - prose), True

# The emitted target is abstract (``split:<file>``): resolving it to a module belongs to core/backlog_target_mapper.py and the Manager's critical gate.
_OVERSIZED_TARGET_PREFIX = "split:"


def _longest_function(content: str) -> tuple[str, int, int]:
    """The longest function of the module: name, line count and start line.

    A method is named with its class (`Class.method`); an unparseable module returns an empty name and zero lines (no signal).
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
    """Turn oversized source modules into grounded, report-only backlog signals; performs NO IO.

    `files` is an iterable of ``(rel_path, content)``; each file whose longest function spans
    at least :data:`_UNREADABLE_FUNCTION_LINES` lines yields a :class:`SignalRecord` (longest
    first, at most :data:`_MAX_OVERSIZED_RECORDS`) with target ``split:<rel_path>``,
    ``evidence_ref`` ``<rel_path>:<function line>`` and a quote naming the function and its size.
    """
    measured: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for rel_path, content in files:
        rel = str(rel_path or "").replace("\\", "/").strip()
        if not rel or content is None or rel in seen:
            continue
        seen.add(rel)
        total = content.count("\n") + 1 if content else 0
        # the reason to split is an unreadable function, not file length
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
                # the evidence points at the function itself, not the first line of the file
                evidence_ref=f"{rel}:{at}",
                problem_quote=quote,
                # worst-first: the biggest module gets the largest bump but stays below the next tier
                rank_hint=(count - index) * 0.001,
            )
        )
    return records, "\n".join(quotes)



SPLIT_PROOF_SOURCE = "split_proof"


def split_proof_candidates(
    proofs: Iterable[tuple[str, Any]],
) -> tuple[list[SignalRecord], str]:
    """Modules with PROOF from core/split_proof.py: work for the hands.

    `proofs` are ``(rel, proof)`` pairs already in order of strength; no IO. The target is the abstract ``split:<rel>``.
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

    `reviews` have ``item_id`` and ``verdict``; a review counts only when ``item_id`` resolves via ``item_target_map`` (no map, no penalties). Never mutates value_reviews.
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
