"""The agent picks the next campaign goal itself, starting from the charter (goal judge: verbatim anchor, novelty, authority invariant, success_check)."""
from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.mentor_channel import mentor_block, open_questions
from core.state_integrity import read_state_jsonl_unlocked
from core.word_overlap import word_set as _tokens
from core.workspace_inventory import inventory_block as _inventory_block
from core.workspace_inventory import unattended_tools as _unattended_tools
from core.workspace_inventory import workspace_inventory as _workspace_inventory

CHARTER_RELPATH = Path("knowledge") / "doctrine" / "future" / "CORPORATE_MODEL.md"

# Charter decisions persist in memory: every goal-choice outcome survives its tick so the next
# choice sees what was declined.
DECISIONS_RELPATH = Path("data") / "charter_decisions.jsonl"

# How many latest DECLINED goals the selector shows the model.
_RECENT_DECLINED = 6

# The authority invariant judges INTENT, not vocabulary: a verb changing one's OWN rights next to an
# object of power, or a hard form ("without a human", bypass).
# Analysing authority is not seizing it. Regex pairs below; input is the whole goal in lower case.
_AUTHORITY_OBJECTS = (
    r"authority|authorit|governance|kill.?switch|approval|permission|merge"
    r"|push|полномочи|прав[оа]|одобрени|слияни"
)
_AUTHORITY_CHANGE_VERBS = (
    r"widen|expand|extend|change|modify|remove|grant|disable|bypass|skip"
    r"|получить|расшир|измен|снять|отключ|обойти|обход|минуя|выдать"
)
_AUTHORITY_HARD_RE = _re.compile(
    r"без человека|without (?:a )?human|bypass|в обход"
)
_AUTHORITY_INTENT_RE = _re.compile(
    rf"(?:{_AUTHORITY_CHANGE_VERBS})[^.;]{{0,60}}?(?:{_AUTHORITY_OBJECTS})"
    rf"|(?:{_AUTHORITY_OBJECTS})[^.;]{{0,40}}?(?:{_AUTHORITY_CHANGE_VERBS})"
)


def _widens_own_authority(goal: str) -> bool:
    """True only for an intent to change/widen/bypass one's own rights."""
    low = (goal or "").lower()
    return bool(_AUTHORITY_HARD_RE.search(low) or _AUTHORITY_INTENT_RE.search(low))

# How many latest journal goals are shown to the model and guard novelty.
_RECENT_GOALS = 8

# Outcomes where the goal was voiced but gave no work: it may be revisited (otherwise a cost cap
# would make a goal "recent" and lock it from repeating).
_NO_WORK_RESULTS = frozenset({
    "cost_cap", "repeat", "idle", "approval_wait", "dirty_tree_wait",
    "stalled", "error", "declined",
    # Outcomes of a run without work; rows that have the honest `work_done` field do not need this
    # list.
    "blocked", "failed", "empty", "inconclusive",
    "waiting",  # waiting inside a shift is not work on the topic
})

_JACCARD_REPEAT = 0.6

# Self/world balance: if 7 or more of the last 10 chosen goals were about self, the next must look
# outward.
# The 7-of-10 number is an operator decision, not literature.
_OUTWARD_WINDOW, _OUTWARD_SELF_MAX = 10, 7
# A goal looks outward only if it NAMES an external source; everything else is work inside the own
# folder.
# A vocabulary of "self" words missed on live goals.
_WORLD_RE = _re.compile(
    r"https?://|интернет|первоисточник|\bweb\b|web_search|web_fetch|\bonline\b|knowledge_library/|"
    r"math_study/library|книг|учебник|стать[юяией]|\bpaper\b|\bbook\b|arxiv|площадк|"
    r"agent market|\bmarket\b|заказ",
    _re.IGNORECASE)


def goal_faces(goal: str) -> str:
    """'world' if the goal names an external source; otherwise 'self'."""
    return "world" if _WORLD_RE.search(goal or "") else "self"


def _balance_or_abandoned(root: Path, goal: str, outward_due: str, change_ts: str) -> str:
    """Two refusals from the history of earlier choices: self-balance and an abandoned goal."""
    if outward_due and goal_faces(goal) == "self":
        return (f"{outward_due}; the next goal must face the outside world "
                "(a web source, a library book, a market order)")
    abandoned = _repeats_recent(goal, _abandoned_goals(root, change_ts))
    if abandoned:
        return (f"goal abandoned after {_ABANDON_AFTER}+ paid attempts without a result "
                f"since the world last changed: {abandoned[:80]!r}")
    return ""


def _outward_due(root: Path) -> str:
    """Reason for "time to look outward" ("" if not): counted over the latest chosen goals."""
    from core.state_integrity import read_state_jsonl

    try:
        rows = read_state_jsonl(root / DECISIONS_RELPATH)
    except Exception:  # noqa: BLE001 — doubt means the rule stays silent, the choice does not fail
        return ""
    chosen = [str(r.get("goal") or "") for r in rows if r.get("status") == "proposed"][-_OUTWARD_WINDOW:]
    own = sum(1 for g in chosen if goal_faces(g) == "self")
    if len(chosen) < _OUTWARD_WINDOW or own < _OUTWARD_SELF_MAX:
        return ""
    return f"{own} of your last {len(chosen)} chosen goals were about yourself"


@dataclass(frozen=True)
class CharterGoalReport:
    """Outcome of the choice: a proposed goal or a refusal with a named reason."""

    status: str  # "proposed" | "declined"
    goal: str = ""
    charter_quote: str = ""
    why_now: str = ""
    success_check: str = ""
    reason: str = ""
    # Accounting of past stops is MACHINE-checked, not a phrase in reasoning: stop_considered says
    # whether the choice had the stop journal before its eyes; previous_stop_ref is the signature of
    # the wall it actually relied on.
    stop_considered: bool = False
    previous_stop_ref: str = ""


def _decline(reason: str) -> CharterGoalReport:
    return CharterGoalReport(status="declined", reason=reason)


# How many latest stops goal choice sees: a window, not the whole history, so the stop journal does
# not become a second store of truth.
_RECENT_STOPS = 20


# A stop of goal CHOICE against the repeat wall is not shown to the chooser: its content (the
# declined variant with reason) is already in the DECLINED block, and showing it made a self-feeding
# loop.
_PICKER_OWN_STOP = "goal_selection_failure"
# Only this wall duplicates DECLINED; `goal_parse` and others are different news.
_REPEAT_WALL = "goal_repeat"


def _recent_stops(workspace: Path) -> tuple[dict[str, str], ...]:
    """Latest own stops: a LOW-TRUST hint (a matching signature means "check the past wall", not "this reason is true").

    Read-only; an unreadable journal is not a refusal of choice. The window counts DISTINCT WALLS, not rows; repeats become a number (`hits`).
    """
    path = workspace / "data" / "self_stops.jsonl"
    if not path.is_file():
        return ()
    try:
        rows = read_state_jsonl_unlocked(path)
    except (OSError, ValueError):
        return ()
    # The folding key is the signature; rows without one must NOT be folded (calling them one wall
    # would invent a journal).
    #
    # A refusal to oneself on goal repeat is not shown, and this decision survived a review: the
    # skip was set against SELF-FEEDING (the shown `goal_repeat` wall called for "dealing with the
    # repeat wall", got declined as a repeat and left a new wall), not against overflow, and a
    # counter does not break that loop.
    # The cost is real: in `data/self_stops.jsonl` all 25 records are of this kind. Whoever fixes it
    # fixes the LOOP, not the display.
    collapsed: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if (str(payload.get("kind") or "") == _PICKER_OWN_STOP
                and str(payload.get("reason") or "") == _REPEAT_WALL):
            continue
        stop = {
            "kind": str(payload.get("kind") or ""),
            "reason": str(payload.get("reason") or ""),
            "signature": str(payload.get("signature") or ""),
            "ts": str(payload.get("ts") or ""),
        }
        key = stop["signature"] or f"#unsigned:{index}"
        seen = collapsed.pop(key, None)
        # The LAST hit is shown, so a wall being hit right now does not look like yesterday's; re-
        # insertion keeps the order by last hit.
        stop["hits"] = (seen["hits"] + 1) if seen else 1
        collapsed[key] = stop
    return tuple(list(collapsed.values())[-_RECENT_STOPS:])


# This many PAID passes without result and the goal is abandoned until the world changes (Voyager,
# arXiv 2305.16291: a task is dropped after four failed rounds).
_ABANDON_AFTER = 4


def _abandoned_goals(workspace: Path, world_ts: str) -> tuple[str, ...]:
    """Goals with `_ABANDON_AFTER`+ paid passes without work since the last world change.

    Paid means with model calls: approval waits, gate walls and idling do not count. A world change is the capability journal or a foreign main-branch edit (core/wake_events.py); after it the goal is open again.
    """
    from core.wake_events import outside_commit_ts

    since = max(world_ts or "", outside_commit_ts(workspace))
    misses: dict[str, int] = {}
    path = workspace / "data" / "campaign_ledger.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        p = row.get("payload") if isinstance(row.get("payload"), dict) else row
        goal = str(p.get("goal") or "").strip()
        if (goal and p.get("work_done") is False and int(p.get("llm_calls_spent") or 0) > 0
                and str(p.get("ts") or "") > since):
            misses[goal] = misses.get(goal, 0) + 1
    return tuple(g for g, n in misses.items() if n >= _ABANDON_AFTER)


def _recent_goals(workspace: Path) -> tuple[tuple[str, str], ...]:
    """Goals that actually OCCUPIED a run, not merely sounded.

    A goal enters "recent" only if at least one cycle with work was spent on it. Outcomes without work (`cost_cap`, `repeat`, `idle`, `dirty_tree_wait`, `approval_wait`) do not occupy the topic: they should be revisited.
    """
    path = workspace / "data" / "campaign_ledger.jsonl"
    if not path.is_file():
        return ()
    worked_ts: dict[str, str] = {}
    unworked: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            continue
        result = str(payload.get("result") or "").strip()
        recorded = payload.get("work_done")
        if isinstance(recorded, bool):
            # the ledger writes the outcome word, and it decides
            did_work = recorded
        else:
            # Rows before the outcome field: an explicit blacklist of workless outcomes, but a
            # product (request, artifact) is work under any outcome.
            # Everything else (including old-format rows without `result`) counts as occupying the
            # topic; do not invert it, or an incomplete row would bring back the repeat loop.
            did_work = bool(payload.get("proposal") or payload.get("artifact")) \
                or result not in _NO_WORK_RESULTS
        if did_work:
            ts = str(payload.get("ts") or "")
            if goal in worked_ts:
                worked_ts[goal] = max(worked_ts[goal], ts)
            else:
                worked_ts[goal] = ts
            unworked.discard(goal)
        elif goal not in worked_ts:
            unworked.add(goal)
    # The window is by the LAST work, not the first appearance, otherwise a topic worked on
    # yesterday but first voiced a month ago falls out.
    pairs = sorted(worked_ts.items(), key=lambda kv: kv[1])
    return tuple(pairs[-_RECENT_GOALS:])


# The artifact the goal works ON is its identity: engineering goals come from a template and two
# splits of different modules matched on 0.78; judge the subject of the work, not the wording, and
# leave the threshold alone.
_GOAL_IDENTITY_RE = _re.compile(
    r"[\w/\\.-]+\.(?:py|md|jsonl|json|toml|yaml|yml|txt)\b"
)


def _goal_identity(goal: str) -> str:
    """Basename of the file the goal works on, or "" if none named.

    A basename, not a path: "model_router.py" and "core/model_router.py" are one work.
    """
    match = _GOAL_IDENTITY_RE.search(goal or "")
    if not match:
        return ""
    name = match.group(0).replace("\\", "/").strip("'\"")
    return name.rsplit("/", 1)[-1].casefold()


# Stage of work on the subject. The file-identity rule equated "implement X.md" with "draft X.md"
# and "trace X.py" with "split X.py".
# The FIRST verb of the goal decides. For a document paper and code are different work; for code, a
# paper about it and its edit are one.
_GOAL_STAGE_RES: tuple[tuple[str, _re.Pattern[str]], ...] = (
    ("read", _re.compile(
        r"\b(?:trace|investigate|read|study|analy[sz]e|compare|measure|verify|"
        r"run|check|audit|inspect|review|explain|diagnose|examine|test)\b")),
    ("paper", _re.compile(
        r"\b(?:draft|write|propose|design|plan|document|outline|extend|revise|"
        r"specify|define)\b")),
    ("code", _re.compile(
        r"\b(?:implement|build|wire|land|code|ship|split|refactor|extract|"
        r"decompose|fix|repair|add|create|integrate|migrate)\b")),
)


def _goal_stage(goal: str, identity: str) -> str:
    """'read' | 'paper' | 'code' | 'work' | '' by the first verb."""
    low = (goal or "").lower()
    first: tuple[int, str] | None = None
    for stage, pattern in _GOAL_STAGE_RES:
        match = pattern.search(low)
        if match and (first is None or match.start() < first[0]):
            first = (match.start(), stage)
    if first is None:
        return ""
    stage = first[1]
    if stage in ("paper", "code") and not identity.endswith(".md"):
        return "work"  # about code: a proposal to edit and the edit are one work
    return stage


def _repeats_recent(goal: str, recent: tuple[str, ...]) -> str:
    mine = _tokens(goal)
    if not mine:
        return ""
    mine_id = _goal_identity(goal)
    for old in recent:
        old_id = _goal_identity(old)
        if mine_id and old_id:
            # Both goals named a subject: it decides, with the stage of work (the next step on the
            # same paper is not a repeat).
            if mine_id == old_id:
                mine_stage = _goal_stage(goal, mine_id)
                old_stage = _goal_stage(old, old_id)
                if mine_stage and old_stage and mine_stage != old_stage:
                    continue
                return old
            continue
        theirs = _tokens(old)
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= _JACCARD_REPEAT:
            return old
    return ""


# How many charter lines reach goal choice.
#
# Raised from 80 to 180 on the operator's word: 89 of 169 eligible lines were never seen. The window
# costs tokens at every choice; in return the charter either fits entirely or `anchor_overflow` says
# by how many lines it outgrew the window.
_ANCHOR_WINDOW = 180


def _anchor_lines(charter: str) -> tuple[str, ...]:
    """Numbered anchors: the content lines of the charter itself.

    The window is FINITE: a section appended at the end was silently never an anchor.
    """
    anchors: list[str] = []
    for raw in charter.splitlines():
        line = raw.strip().lstrip("-*> ").strip()
        if len(line) >= 40 and not line.startswith("#"):
            anchors.append(line)
    return tuple(anchors[:_ANCHOR_WINDOW])


def anchor_overflow(charter: str) -> int:
    """How many charter lines did NOT reach goal choice.

    A function, not a print inside selection: the loss must be a NUMBER that can be shown and tested. Zero means the charter fits the window entirely.
    """
    anchors: list[str] = []
    for raw in charter.splitlines():
        line = raw.strip().lstrip("-*> ").strip()
        if len(line) >= 40 and not line.startswith("#"):
            anchors.append(line)
    return max(0, len(anchors) - _ANCHOR_WINDOW)


def _record_decision(
    root: Path, *, status: str, goal: str, reason: str = "",
    mentor_questions_shown: int = 0,
) -> None:
    """Append one decision row; a failure to record must not fail the pick."""
    from datetime import datetime, timezone

    from core.state_integrity import append_state_jsonl

    try:
        append_state_jsonl(root / DECISIONS_RELPATH, [{
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "goal": goal,
            "reason": reason,
            # A missing key means zero, as for all causal keys; without this entry "saw the question
            # and declined" is indistinguishable from "did not see".
            **({"mentor_questions_shown": mentor_questions_shown}
               if mentor_questions_shown else {}),
        }])
    except Exception:  # noqa: BLE001, S110 — the decision journal must not fail the choice;
        pass  # silence here is cheaper than a failed charter tick


def _recent_declined(root: Path) -> tuple[tuple[str, str], ...]:
    """Last declined (goal, reason) pairs, newest last; unreadable = empty."""
    from core.state_integrity import read_state_jsonl

    try:
        rows = read_state_jsonl(root / DECISIONS_RELPATH)
    except Exception:  # noqa: BLE001 — doubt means empty, not a failure
        return ()
    declined = [
        (str(r.get("goal") or ""), str(r.get("reason") or ""))
        for r in rows if r.get("status") == "declined" and r.get("goal")
    ]
    return tuple(declined[-_RECENT_DECLINED:])


def _recent_verdicts(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Last (verdict, summary, reason) on the agent's own proposals — the
    verdict bridge's reader half; unreadable = empty."""
    from core.state_integrity import read_state_jsonl

    try:
        rows = read_state_jsonl(root / "data" / "approval_outcomes.jsonl")
    except Exception:  # noqa: BLE001 — doubt means empty, not a failure
        return ()
    out = [
        (str(r.get("verdict") or ""), str(r.get("summary") or ""),
         str(r.get("reason") or ""))
        for r in rows if r.get("verdict") in ("approved", "denied")
    ]
    return tuple(out[-6:])


def _backlog_lines(root: Path) -> tuple[str, ...]:
    """Top real engineering candidates the agent MAY act on; failures = empty.

    The list goes into a prompt as "real, measured candidates", so it must not name forbidden files (the self-build mechanism in `CRITICAL_DENY` was once chosen). A doubtful candidate is not offered either.
    """
    try:
        from core.backlog_selector import load_backlog
        from core.self_build_producer import (
            _candidate_concrete_targets,
            _is_self_build_target_allowed,
        )

        def actionable(candidate: object) -> bool:
            try:
                targets = _candidate_concrete_targets(candidate, root)
            except Exception:  # noqa: BLE001 — an unclear candidate is not offered
                return False
            return any(_is_self_build_target_allowed(t) for t in targets)

        out = []
        for c in (x for x in load_backlog(root) if actionable(x)):
            out.append(
                f"{getattr(c, 'signal_source', '?')}: "
                f"{str(getattr(c, 'problem_quote', ''))[:110]}"
            )
            if len(out) == 6:
                break
        return tuple(out)
    except Exception:  # noqa: BLE001 — doubt means empty, not a failure
        return ()


def _open_defect_lines(root: Path) -> tuple[str, ...]:
    """Unclosed defects of its own registry; doubt = empty."""
    try:
        from core.self_improvement_issues import SelfImprovementIssueRegistry

        registry = SelfImprovementIssueRegistry(
            root / "data" / "self_improvement_issues.jsonl"
        )
        out: list[str] = []
        for issue in registry.unresolved()[:5]:
            suggestion = str(issue.suggested_next_action or "").strip()
            out.append(
                f"[open defect] {issue.title[:110]}"
                + (f" — next: {suggestion[:90]}" if suggestion else "")
            )
        return tuple(out)
    except Exception:  # noqa: BLE001 — an unreadable registry costs the hint,
        # not the run: goal choice goes on without these lines.
        return ()


def _unexplained_lines(root: Path) -> tuple[str, ...]:
    """Observations nobody has explained; doubt = empty."""
    try:
        from core.causal_climb_action import unexplained_observations

        return tuple(
            f"[unexplained] {str(observation.observed_mismatch)[:120]} "
            f"(seen {observation.occurrences}x)"
            for observation in [
                o for o in unexplained_observations(root)
                if not (_PICKER_OWN_STOP in str(getattr(o, "observed_mismatch", ""))
                        and _REPEAT_WALL in str(getattr(o, "observed_mismatch", "")))
            ][:5]
        )
    except Exception:  # noqa: BLE001 — same rule: the hint is optional.
        return ()


def _open_work_lines(root: Path) -> tuple[str, ...]:
    """The agent's own unclosed matters: what waits for it specifically.

    Sources differ in meaning: the registry says "broken here", the ladder says "unclear here".
    """
    return _open_defect_lines(root) + _unexplained_lines(root)


def _ask(
    llm: Any, charter: str, anchors: tuple[str, ...], recent: tuple[str, ...],
    declined: tuple[tuple[str, str], ...] = (),
    backlog: tuple[str, ...] = (),
    verdicts: tuple[tuple[str, str, str], ...] = (),
    mentor_questions: str = "",
    stops: tuple[dict[str, str], ...] = (),
    open_work: tuple[str, ...] = (),
    inventory: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    system = (
        "You are choosing YOUR OWN next piece of work. You are the agent this "
        "charter describes; the charter is the target shape you are moving "
        "toward. Propose ONE small, bounded, verifiable goal for a single "
        "campaign run that moves you toward the charter FROM where you are "
        "now.\n"
        "\n"
        "THREE KINDS OF WORK ARE OPEN TO YOU, and the third is the one you "
        "keep forgetting you have:\n"
        "  (a) REPAIR — name ONE real engineering candidate from your backlog "
        "or from your own open defects and turn it into a reviewed proposal "
        "(a module split, a failing-test task for a proven gap).\n"
        "  (b) UNDERSTAND YOURSELF — read your own journals, code, anatomy "
        "map and past decisions to answer a question about yourself that you "
        "cannot answer today: why a run stopped, which of your capabilities is "
        "dead, what you actually did over a period, whether a claim you made "
        "about yourself holds against the record.\n"
        "  (c) STUDY THE OUTSIDE WORLD — when the answer is not inside this "
        "repository, say so and go look. web_search and web_fetch are open in "
        "every run of yours; no special wording in the goal is needed to use "
        "them. Prior art before "
        "invention: how others solved this is evidence, and what you bring "
        "back is a HYPOTHESIS with its source, never a truth.\n"
        "\n"
        "Every product still goes through human approval. Never widen your "
        "own authority. Anchor the goal by CHOOSING "
        "one numbered charter line it serves.\n"
        "\n"
        "HOW TO CHOOSE WHAT MATTERS (the operator's priority ladder, "
        "2026-09-02):\n"
        "  P0 never violate constraints: no unauthorised spend, no "
        "irreversible external acts, no fabricated success.\n"
        "  P1 finish what is open — if a blocker of an open item has cleared, "
        "RESUME it before starting anything new.\n"
        "  P2 remove a PROVEN blocker of useful work (your own measured "
        "defect first).\n"
        "  P3 externally useful work: code, analysis, research, commercial "
        "tasks.\n"
        "  P4 grow a capability ONLY with the proven chain defect -> blocks "
        "capability -> needed for mission.\n"
        "  P5 open research (self-model, literature, experiments).\n"
        "Self-expansion is not the goal; mission capability is. Ask first: "
        "which MEASURED limitation currently blocks useful external work the "
        "most? Your measured capability map lives at "
        "knowledge/doctrine/future/WHAT_I_HAVE_AND_WHAT_I_LACK.md — it names "
        "what you have, what you lack, and the exam you have not passed.\n"
        "ONE PAPER RULE (operator's word, 2026-09-03): do NOT propose writing "
        "a new TARGET/DRAFT contract or design document while an existing one "
        "under knowledge/doctrine/future/ remains unimplemented — seven such "
        "contracts already wait. Implementing a piece of one of them, or "
        "revising one against measured reality, is welcome; adding an eighth "
        "is not. A document is the cheapest thing you can produce and the "
        "least likely to change anything.\n"
        "\n"
        "Reply with ONE JSON object "
        'only: {"goal": "<one concrete goal, 20-300 chars>", '
        '"anchor_id": <number of the charter line this goal serves>, '
        '"why_now": "<1-2 sentences>", '
        '"success_check": "<how a reviewer will see the goal is done>", '
        '"previous_stop_ref": "<signature of the past stop you took into account, or empty string if none applies>"}.'
    )
    user = (
        "The charter (your target shape):\n" + charter
        + "\n\nNumbered anchors (pick anchor_id from these):\n"
        + "\n".join(f"[{i}] {a}" for i, a in enumerate(anchors))
        + _inventory_block(inventory, tools)
        + "\n\nRecent campaign goals (do NOT repeat them):\n"
        + ("\n".join(f"- {g}" for g in recent) or "- (none)")
    )
    if open_work:
        # Its own unclosed matters: the registry says "broken here", the ladder says "unclear here";
        # before, only a list of big files was visible.
        user += (
            "\n\nYOUR OWN OPEN WORK — defects you recorded and observations "
            "nobody has explained. These are yours, they are waiting, and they "
            "are usually a better goal than another module split:\n"
            + "\n".join(f"- {line}" for line in open_work)
        )
    if stops:
        # Low-trust hint: a matching signature means "check the past wall", not "this reason is
        # true".
        stop_lines = "\n".join(
            "- [{kind}] {reason} (hit {hits}x, signature {sig}, latest {ts})".format(
                kind=st["kind"], reason=st["reason"],
                hits=st.get("hits", 1),
                sig=st["signature"][:12], ts=st["ts"][:19],
            )
            for st in stops
        )
        user += (
            "\n\nYour own recent STOPS — runs that produced no work. "
            "These are hints, not proven causes: a matching wall means CHECK "
            "it, not that the cause is true. If your goal risks the same wall, "
            "either choose differently or say why this time is different, and "
            "put the matching signature into previous_stop_ref:\n"
            + stop_lines
        )
    if declined:
        user += (
            "\n\nRecently DECLINED proposals — your own gates rejected these; "
            "do not re-propose them or their rephrasings, choose a DIFFERENT "
            "charter anchor instead:\n"
            + "\n".join(f"- {g!r} (declined: {r})" for g, r in declined)
        )
    if backlog:
        user += (
            "\n\nYour current engineering backlog (real, measured candidates "
            "you may turn into a reviewed proposal):\n"
            + "\n".join(f"- {b}" for b in backlog)
        )
    if verdicts:
        user += (
            "\n\nRecent VERDICTS on your own past proposals (learn from the "
            "fate of your work — what was valued, what was rejected and why):\n"
            + "\n".join(_verdict_line(v, s, r) for v, s, r in verdicts)
        )
    if mentor_questions:
        # Mentor channel: questions, not orders; its authority is named in the block (advisory, may
        # be declined).
        user += "\n\n" + mentor_questions
    try:
        raw = llm.complete(system=system, user=user, max_tokens=1200, temperature=0.4)
    except Exception as exc:  # noqa: BLE001 — a model refusal is a choice refusal, not a crash
        return None, f"the model call failed: {type(exc).__name__}"
    # Silence (finish_reason=length, content='') is not a parse failure: the client tells them apart
    # (last_answer_was_truncated) and the seam passes the difference on.
    if not str(raw or "").strip():
        if bool(getattr(llm, "last_answer_was_truncated", False)):
            return None, (
                "the model produced no final output: the reply was truncated "
                "before any answer (reasoning consumed the token budget)"
            )
        return None, "the model returned an empty reply"
    parsed = _last_json_object(raw)
    if parsed is None:
        return None, "the model's reply contained no parseable JSON object"
    return parsed, ""


def _last_json_object(raw: str) -> dict | None:
    """The last COMPLETE JSON object in the answer, or None.

    Every "{" is tried as an object start and the last one that parses into a dict wins (braces in prose before the final JSON must not spoil a good answer).
    """
    decoder = json.JSONDecoder()
    found: dict | None = None
    for pos, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[pos:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            found = obj
    return found



# Veto list written by a HUMAN: one goal or work subject per line, `#` is a note. Lives in `config/`
# because neither memory hygiene nor the self-fix feed reaches it.
VETO_RELPATH = "config/vetoed_goals.txt"


def _operator_vetoes(root: Path) -> tuple[str, ...] | None:
    """Veto lines. `None` means the list exists but could not be read."""
    path = root / VETO_RELPATH
    if not path.exists():
        return ()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return tuple(
        line.strip() for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _verdict_line(v: str, s: str, r: str) -> str:
    """One verdict as the charter reads it."""
    line = f"- [{v}] {s}" + (f" — reviewer: {r}" if r else "")
    if v == "approved":
        line += (" — APPROVED items are applied by the operator's lane, not by "
                 "you: do not set a goal to implement or re-propose this; "
                 "choose other work")
    return line


def _ask_shorter(llm: Any, goal: str) -> str:
    """One attempt to semantically shorten the same goal to the limit.

    Policy and recovery are separate: the 300 limit is NOT changed or bypassed, but a length refusal must lead to rewording, not to stopping the system.
    """
    prompt = (
        f"Your goal is {len(goal)} characters; the hard limit is 300. "
        "Shorten it to at most 300 characters, keeping the SAME semantic "
        'goal — do not change what is to be done. Reply with ONE JSON '
        'object only: {"goal": "<the same goal, shortened>"}.'
    )
    try:
        raw = llm.complete(system=prompt, user=goal, max_tokens=400,
                           temperature=0.2)
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1]) if start >= 0 else None
    except Exception:  # noqa: BLE001 — shortening is recovery, not a load-bearing
        # construction: its failure returns the earlier honest length refusal.
        return ""
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("goal") or "").strip()


def propose_charter_goal(llm: Any, workspace: str | Path) -> CharterGoalReport:
    """One goal from the charter, or a refusal naming which gates were not passed."""
    root = Path(workspace)
    charter_path = root / CHARTER_RELPATH
    if not charter_path.is_file():
        return _decline(f"charter missing: {CHARTER_RELPATH.as_posix()}")
    charter = charter_path.read_text(encoding="utf-8")
    anchors = _anchor_lines(charter)
    if not anchors:
        return _decline("the charter has no anchorable lines")
    # Charter loss is recorded EVERY time and goes into the decision journal: a counter without a
    # reader is silence with a number.
    dropped = anchor_overflow(charter)
    if dropped:
        _record_decision(
            root, status="charter_truncated", goal="",
            reason=(f"{dropped} строк хартии не дошли до выбора цели: окно "
                    f"якорей {_ANCHOR_WINDOW}, подходящих строк "
                    f"{_ANCHOR_WINDOW + dropped}"),
        )
    recent_pairs = _recent_goals(root)
    # The world may have changed: record the set of blocked tools of the unattended path (a row only
    # on change) and cut off from the repeat guard topics whose last WORK is older than the last
    # change.
    # Recentness is not completion: a "nothing more to do" verdict from another world does not lock
    # the topic.
    try:
        from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS
        from core.capability_events import (
            last_capability_change_ts,
            record_capability_snapshot,
        )
        record_capability_snapshot(root, _AUTONOMOUS_GOAL_BLOCKED_TOOLS)
        change_ts = last_capability_change_ts(root)
    except Exception:  # noqa: BLE001 — without the event journal the earlier
        # rule applies (everything recent blocks): losing the new softening must not break goal
        # choice.
        change_ts = ""
    recent = tuple(
        goal_text for goal_text, work_ts in recent_pairs
        if not (change_ts and work_ts and work_ts < change_ts)
    )
    stops = _recent_stops(root)

    # Every outcome below persists in memory: the decision survives its tick and the next choice
    # sees what was declined.
    def _declined(reason: str, goal_text: str = "") -> CharterGoalReport:
        _record_decision(root, status="declined", goal=goal_text, reason=reason)
        return CharterGoalReport(
            status="declined", reason=reason, stop_considered=bool(stops),
        )

    # Read BEFORE the model call: a veto lost to a read failure would bring back revoked work, so an
    # unreadable list refuses by name and for free.
    vetoes = _operator_vetoes(root)
    if vetoes is None:
        return _declined(f"operator veto list unreadable: {VETO_RELPATH}")

    mentor_qs = open_questions(root)
    outward_due = _outward_due(root)
    # The balance rule is seen by the model BEFORE a call is spent, where the gate decisions are.
    declined_now = _recent_declined(root)
    if outward_due:
        declined_now += ((
            "THIS TIME: any goal about your own code, journals, detectors or defects",
            (f"{outward_due}; the next goal MUST FACE THE OUTSIDE WORLD - name the outside "
             "source in the goal itself: a web page or paper, a library book, a market order"),
        ),)
    parsed, why = _ask(
        llm, charter, anchors, recent, declined_now,
        _backlog_lines(root), _recent_verdicts(root),
        mentor_questions=mentor_block(mentor_qs),
        stops=stops,
        open_work=_open_work_lines(root),
        inventory=_workspace_inventory(root),
        tools=_unattended_tools(root),
    )
    if not parsed:
        return _declined(why or "the model returned no parseable goal")

    goal = str(parsed.get("goal") or "").strip()
    why_now = str(parsed.get("why_now") or "").strip()
    check = str(parsed.get("success_check") or "").strip()
    try:
        anchor_id = int(parsed.get("anchor_id"))
    except (TypeError, ValueError):
        anchor_id = -1

    if len(goal) > 300:
        shortened = _ask_shorter(llm, goal)
        if shortened:
            goal = shortened
    if not 20 <= len(goal) <= 300:
        return _declined(f"goal length {len(goal)} outside 20..300", goal)
    if _widens_own_authority(goal):
        return _declined(
            "goal would widen the agent's own authority — the charter's hard "
            "invariant forbids it", goal,
        )
    if not 0 <= anchor_id < len(anchors):
        return _declined(
            f"anchor_id {anchor_id} does not point at a charter line "
            "(fabricated anchor)", goal,
        )
    quote = anchors[anchor_id]
    # A human's word binds like own history and by the same rule: the work SUBJECT is judged, since
    # the model words it anew each time.
    vetoed = _repeats_recent(goal, vetoes)
    if vetoed:
        return _declined(
            f"goal is vetoed by the operator ({VETO_RELPATH}): {vetoed[:80]!r}",
            goal,
        )
    repeated = _repeats_recent(goal, recent)
    if repeated:
        return _declined(
            f"goal repeats a recent campaign goal: {repeated[:80]!r}", goal)
    blocked = _balance_or_abandoned(root, goal, outward_due, change_ts)
    if blocked:
        return _declined(blocked, goal)
    if not check:
        return _declined(
            "success_check is empty — a goal without a check is a wish", goal)

    _record_decision(root, status="proposed", goal=goal,
                     mentor_questions_shown=len(mentor_qs))
    # Machine trace of past-wall accounting: the signature comes ONLY from the stop journal; a
    # string invented by the model does not pass.
    claimed_ref = str(parsed.get("previous_stop_ref") or "").strip()
    known = {st["signature"] for st in stops if st["signature"]}
    return CharterGoalReport(
        status="proposed", goal=goal, charter_quote=quote,
        why_now=why_now, success_check=check,
        stop_considered=bool(stops),
        previous_stop_ref=claimed_ref if claimed_ref in known else "",
    )


def charter_status_lines(workspace: Path) -> list[str]:
    """A line on why goal choice refused, or empty if it works.

    Read-only and never raises.
    """
    from datetime import datetime, timedelta, timezone

    from core.state_integrity import read_state_jsonl

    try:
        path = Path(workspace) / DECISIONS_RELPATH
        if not path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = []
        for row in read_state_jsonl(path):
            payload = row.get("payload", row)
            try:
                when = datetime.fromisoformat(str(payload.get("ts")))
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when >= cutoff:
                recent.append(payload)
        declined = [r for r in recent if str(r.get("status")) == "declined"]
        if not declined or len(declined) < len(recent):
            return []
        reason = str(declined[-1].get("reason") or "")
        line = (
            f"[CHARTER] goal choice declined {len(declined)}x in 24h, "
            f"nothing proposed: {reason[:90]}"
        )
    except Exception:  # noqa: BLE001 — a status line is not worth a tick
        return []
    return [line]
