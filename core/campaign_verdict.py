"""Campaign verdict: whether the campaign's own criterion converged, and the recorded fact. Words: verified (all traces found, at least one newer than the run start), preexisting (all older than the run), missing, unverifiable (no observable trace named), unmet (fresh traces but content judge says no). Nothing relies on the verdict yet."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state_integrity import append_state_jsonl_unlocked, state_file_lock
from core.success_check import observe_success_check

#: The only journal path; a second writer with its own path would break readers that merge the lines.
VERDICT_RELPATH = "data/campaign_verdicts.jsonl"

#: Verdict words; the list is closed so that ignorance is named separately from verification.
VERDICTS = ("verified", "preexisting", "missing", "unverifiable", "unmet")

_UNSTATED = "критерий не назван: цель поставлена без проверки"


def _fresh_traces(
    named: tuple[str, ...] | list[str], workspace: Any, since: float | None,
) -> list[str]:
    """Traces modified no earlier than the run start; without `since` nothing counts as fresh."""
    if since is None:
        return []
    root = Path(workspace or ".")
    fresh: list[str] = []
    for relpath in named:
        try:
            mtime = (root / relpath).stat().st_mtime
        except OSError:
            continue
        if mtime >= since:
            fresh.append(relpath)
    return fresh


def _unfilled(paths: list[str], workspace: Any) -> list[str]:
    """Unfilled template slots in fresh traces (core.observation_round)."""
    from core.observation_round import unfilled_placeholders

    holes: list[str] = []
    for rel in paths:
        try:
            text = (Path(workspace or ".") / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        holes += [f"{rel}: {h[:80]}" for h in unfilled_placeholders(text)]
    return holes


def against_start(observation: dict[str, Any], workspace: Any, since: float | None) -> dict[str, Any]:
    """Goal verdict relative to the start: all traces older than the start -> preexisting."""
    if observation.get("verdict") != "verified" or since is None:
        return observation
    fresh = _fresh_traces([str(a) for a in observation.get("artifacts") or ()], workspace, since)
    holes = _unfilled(fresh, workspace)
    if holes:
        # A stub note with placeholder text must not count just because the file exists.
        return {**observation, "verdict": "missing",
                "reason": "след — незаполненный шаблон: " + "; ".join(holes[:3])}
    if fresh:
        return observation
    named = ", ".join(str(a) for a in observation.get("artifacts") or ())
    return {**observation, "verdict": "preexisting",
            "reason": "следы найдены, но все старше начала цели: " + named}


def judge_campaign(
    *, goal: str, success_check: str, workspace: Any, since: float | None = None, llm: Any = None,
) -> dict[str, Any]:
    """Say whether the campaign criterion converged, and by what it is observed."""
    check = str(success_check or "").strip()
    # A self-repair goal is judged by patch_check, not by file presence (core/patch_route.patch_goal_verdict).
    from core.patch_route import patch_goal_verdict

    special = patch_goal_verdict(workspace, check)
    if special is not None:
        observed = list(special.get("artifacts_observed") or ())
        named_patch = list(special.get("artifacts_named") or ())
        return {
            "goal": str(goal or ""),
            "success_check": check,
            "verdict": str(special.get("verdict") or "unverifiable"),
            "reason": str(special.get("reason") or ""),
            "named_traces": named_patch,
            "missing_traces": [a for a in named_patch if a not in observed],
            "fresh_traces": observed,
        }
    observation = observe_success_check(check, workspace)
    named = [str(a) for a in observation.get("artifacts") or ()]
    missing = [str(m) for m in observation.get("missing") or ()]
    reason = str(observation.get("reason") or "")
    verdict = str(observation.get("verdict") or "unverifiable")
    fresh: list[str] = []
    if verdict == "verified":
        fresh = _fresh_traces(named, workspace, since)
        if not fresh:
            verdict = "preexisting"
            reason = (
                "следы найдены, но все старше начала прогона: "
                + ", ".join(named)
            )
        else:
            verdict, reason = _content(check, fresh, workspace, llm, verdict, reason)
    if not check:
        # An unset and an unverifiable criterion both give `unverifiable`, but the human needs the reasons told apart.
        reason = _UNSTATED
    return {
        "goal": str(goal or ""),
        "success_check": check,
        "verdict": verdict,
        "reason": reason,
        "named_traces": named,
        "missing_traces": missing,
        "fresh_traces": fresh,
    }


def _content(check: str, fresh: list[str], workspace: Any, llm: Any,
             verdict: str, reason: str) -> tuple[str, str]:
    """Fresh trace read by the content judge; without a model the verdict stays as it was."""
    from core.goal_content_judge import judge_content

    content = judge_content(check, fresh, workspace, llm)
    if content is None or content["met"]:
        return verdict, reason
    return "unmet", "по содержанию не выполнено: " + "; ".join(content["unmet"])[:400]


def record_campaign_verdict(workspace: Any, verdict: dict[str, Any]) -> dict[str, Any]:
    """Append the verdict to its own journal; the cycle registry is untouched because its readers count one line per cycle."""
    if verdict.get("verdict") == "verified":
        verdict = {**verdict, "judge_item": _to_judge(workspace, verdict)}
    path = Path(workspace or ".") / VERDICT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        append_state_jsonl_unlocked(path, [dict(verdict)])
    return verdict


def _to_judge(workspace: Any, verdict: dict[str, Any]) -> str:
    """Send an "achieved" verdict to Claude for judgment (core/judge_queue.py); it does not count until decided."""
    from core.judge_queue import submit

    try:
        return submit(workspace or ".", kind="goal_verified", subject=str(verdict.get("goal") or ""),
                      provisional="verified", evidence=list(verdict.get("fresh_traces") or ()),
                      detail=f"success_check: {verdict.get('success_check') or ''}; "
                             f"reason: {verdict.get('reason') or ''}")
    except OSError:
        return ""  # queue unavailable: the verdict stays unjudged, i.e. does not count


def judge_and_record(
    *, goal: str, success_check: str, workspace: Any, started_at: Any, ts: Any,
    stop_reason: str = "", cycles_run: int = 0, proposals: int = 0, artifacts: int = 0,
    llm: Any = None,
) -> tuple[dict[str, Any], str]:
    """Judge the goal, name the run and journal the verdict; returns the verdict and the write error (empty on success). A failed write does not fail the run, but the error is returned."""
    try:
        since = started_at.timestamp()
    except (AttributeError, OverflowError, OSError, ValueError):
        since = None
    verdict = judge_campaign(
        goal=goal, success_check=success_check, workspace=workspace, since=since, llm=llm,
    )
    # Name the run: without it the verdict cannot be rechecked or tied to registry rows.
    verdict.update({
        "stop_reason": str(stop_reason or ""),
        "cycles_run": int(cycles_run),
        "started_at": _isoformat(started_at),
        "ts": _isoformat(ts),
        "proposals": int(proposals),
        "artifacts": int(artifacts),
    })
    try:
        record_campaign_verdict(workspace, verdict)
    except OSError as exc:
        return verdict, str(exc)
    return verdict, ""


def _isoformat(moment: Any) -> str:
    try:
        return str(moment.isoformat())
    except AttributeError:
        return str(moment or "")


def verdict_summary_line(verdict: dict[str, Any]) -> str:
    """Human line: the verdict word and what it is observed by."""
    word = str(verdict.get("verdict") or "unverifiable")
    missing = verdict.get("missing_traces") or []
    fresh = verdict.get("fresh_traces") or []
    tail = ""
    if word == "missing" and missing:
        tail = "  not found: " + ", ".join(str(m) for m in missing)
    elif word == "verified" and fresh:
        tail = "  made during the run: " + ", ".join(str(f) for f in fresh)
    elif word == "preexisting":
        tail = "  older than this run: " + ", ".join(
            str(n) for n in verdict.get("named_traces") or []
        )
    elif word in ("unverifiable", "unmet"):
        tail = "  " + str(verdict.get("reason") or "")
    return f"goal_achieved={word}{tail}"
