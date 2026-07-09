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
* **docs/AGENT_ANATOMY.md** — the "Candidate follow-ups (TD-030+)" advisory list.
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
from dataclasses import dataclass
from typing import Iterable, Mapping

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
    the source text; ``evidence_ref`` is ``<file>:<line>``."""

    signal_source: str
    target_path: str
    evidence_ref: str
    problem_quote: str


@dataclass(frozen=True)
class ValuePenalties:
    """Targets to suppress or penalize, derived from human value reviews."""

    suppressed: frozenset[str]
    penalized: frozenset[str]

    @classmethod
    def empty(cls) -> "ValuePenalties":
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
                evidence_ref=f"docs/AGENT_ANATOMY.md:{i + 1}",
                # The heading is a substring of the raw line (which includes the
                # surrounding ``**``), so it is trivially traceable to source.
                problem_quote=heading,
            )
        )
    return records


def self_build_docs_candidate(proposal_text: str) -> list[SignalRecord]:
    """TD-038 slice 2 Approach D: one grounded docs-only pilot target.

    This deliberately does not parse arbitrary proposal prose. It only emits the
    already allowlisted ``docs/self_build.md`` target when the TD-038 slice 2
    proposal contains the Approach D section and the exact "first grounded
    target" quote.
    """
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


def value_review_penalties(
    reviews: Iterable,
    item_target_map: Mapping[str, str] | None = None,
) -> ValuePenalties:
    """Derive suppress/penalize target sets from human value reviews.

    ``reviews`` is an iterable of objects with ``item_id`` and ``verdict`` (e.g.
    :class:`core.value_review.ValueReview`). A review contributes only when its
    ``item_id`` resolves to a target via ``item_target_map`` (best-effort: with
    no map, no penalties are produced, and value_reviews is never mutated).

    Latest verdict per ``item_id`` wins (input assumed in write order).
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
