"""Self-repair without a human in the loop: defect, edit, patch_check, own code."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.control_files import CONTROL_RELPATHS
from core.protected_paths import is_protected, path_key

DAILY_CAP = 5
# Marker of a defect that a code edit does not close (environment, access, money): a human decides
# it and the route skips it.
OPERATOR_DECISION = "operator_decision"
RETRY_AFTER = timedelta(hours=6)
STATE_RELPATH = "data/patch_route_state.json"
LOG_RELPATH = "data/self_repair_log.jsonl"
_PATCH_RE = re.compile(r"proposals/selffix/[\w.\-]+/edits\.txt")
# Where self-repair does not enter: money and budget, keys and secret editing, outside access,
# policy and approvals, the apply route and this module, agent assembly.
# Prefixes ON TOP of core/protected_paths.py: the route without a human is narrower than the lane
# with approval.
_FORBIDDEN = (
    "core/approval", "core/policy", "core/secret", "core/redaction", "core/dlp",
    "core/budget", "core/standing_grant", "core/self_apply", "core/patch_route",
    "core/actuation_gateway", "core/kill", "core/safe_vcs", "tools/web_", "tools/shell_exec",
    "tools/patch_check", "tools/run_tests", "app/", "cli/", ".env", ".git",
    # The brakes as a whole (core/control_files.py): an edit without a human touches neither them
    # nor the module that lists them.
    "core/control_files", "config/", *CONTROL_RELPATHS,
    # The "red before fix" witness rule (red_before_fix): the judge of self-repair itself.
    "core/self_repair_utils",
)
_FORBIDDEN_KEYS = tuple(p.casefold() for p in _FORBIDDEN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / STATE_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"attempted": {}, "applied": {}}


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _log(root: Path, event: dict[str, Any]) -> None:
    path = root / LOG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now().isoformat(), **event}, ensure_ascii=False) + "\n")


# After how long an unchanged defect skip is written to the summary again.
SKIP_LOG_REPEAT = timedelta(hours=24)


def _log_skip_once(root: Path, state: dict[str, Any], now: datetime, fingerprint: str, title: str) -> None:
    """"Defect without a task skipped": once, not on every cycle.

    A new line only if the skip is new, the title changed or a day passed (like Alertmanager's repeat_interval: repeat the unchanged rarely, a change at once).
    """
    seen = state.setdefault("skip_logged", {}).get(fingerprint) or {}
    try:
        fresh = now - datetime.fromisoformat(seen.get("at", "")) < SKIP_LOG_REPEAT
    except ValueError:
        fresh = False
    if fresh and seen.get("title") == title:
        return
    _log(root, {"event": "defect_without_a_task_skipped", "issue": fingerprint, "title": title})
    state["skip_logged"][fingerprint] = {"at": now.isoformat(), "title": title}
    _save_state(root, state)


def _slug(fingerprint: str) -> str:
    return re.sub(r"[^\w.\-]+", "-", fingerprint)[:60].strip("-") or "defect"


def defect_goal(root: Path) -> Any:
    """Goal: fix one open registry defect by an edit with a green patch_check."""
    from core.defect_intake import intake_observations
    from core.drive_goal import DriveGoal
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    state, now = _state(root), _now()

    registry = SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    # Intake of own signals lives here, not in the approval command:
    # `sync_self_improvement_issue_registry` was called only by cli/commands_approval.py and never
    # in the campaign automaton.
    try:
        intake_observations(root, registry)
    except Exception as exc:  # noqa: BLE001 — intake must not break goal choice
        _log(root, {"event": "defect_intake_failed", "error": f"{type(exc).__name__}: {exc}"})
    for issue in registry.unresolved():
        # An environment defect (install a package, grant access) is not closed by a code edit.
        if issue.action == OPERATOR_DECISION:
            continue
        # A defect without a NAMED task is not closed by an edit: the goal would read "Fix defect X.
        # What to do: " and the agent, not knowing what to change, writes an excuse or junk.
        # Such a defect first needs a formulation: human work or a separate turn, not a blind edit.
        if not str(issue.suggested_next_action or "").strip():
            _log_skip_once(root, state, now, issue.fingerprint, (issue.title or "")[:120])
            continue
        tried = state["attempted"].get(issue.fingerprint)
        if tried and now - datetime.fromisoformat(tried) < RETRY_AFTER:
            continue
        patch = f"proposals/selffix/{_slug(issue.fingerprint)}/edits.txt"
        state["attempted"][issue.fingerprint] = now.isoformat()
        state.setdefault("patch_to_defect", {})[patch] = issue.fingerprint
        _save_state(root, state)
        evidence = "; ".join(issue.evidence[:3])[:900]
        # Its own decision on this defect (core/own_decisions.py).
        from core.own_decisions import decision_line

        decided = decision_line(root, issue.fingerprint)
        # "Exactly 1 file changes": otherwise the clarification gate (core/loop_gates.py) answers
        # with a question and nobody answers it in the automaton.
        # Two failure modes are cured by a word in the task: an EXCUSE in the edit file (read more
        # instead of explaining the wall) and delimiters glued into one line; the form is shown
        # verbatim so it need not be guessed.
        goal = (f"Почини свой дефект «{issue.title}» ({issue.fingerprint}). Улики: {evidence}. "
                f"Что сделать: {issue.suggested_next_action[:400]}. {decided}"
                f"Меняется ровно 1 файл: {patch}. Все остальные файлы только читаются. "
                f"В {patch} пиши ТОЛЬКО блоки правки — каждая метка на своей строке:\n"
                "FILE:core/имя.py\n<<<<<<< SEARCH\n<старый текст дословно>\n=======\n"
                "<новый текст>\n>>>>>>> REPLACE\n"
                "Вместо SEARCH можно «<<<<<<< LINES a-b» с номерами строк из file_read. "
                "Новый файл (тест) — блок с ПУСТЫМ SEARCH. Свой тест обязателен: правка без "
                "теста не зелёная. "
                "Если чтение не дало точного текста для SEARCH — ПРОЧИТАЙ ЕЩЁ РАЗ нужные строки "
                "(file_read с номерами) и собери блок по прочитанному. Объяснение, почему не "
                f"вышло, в {patch} не кладётся: это не правка, и проверка её отвергнет. "
                f"Затем вызывай patch_check с путём {patch}, пока verdict не станет green. "
                "Не трогай деньги, ключи, политику, одобрения и сеть — такие правки не лягут.")
        return DriveGoal(status="proposed", goal=goal, success_check=f"Файл {patch} создан",
                         drive="self_improvement_need", action="pursue_goal")
    return None


def patch_goal_verdict(root: Path | str, success_check: str) -> dict[str, Any] | None:
    """Verdict of a self-repair goal: a real edit or a file for the file's sake.

    patch_check itself sets the verdict, without the full suite or applying; the full suite still meets a real edit in `settle_patch`. `None`: the goal is not about an edit, the ordinary judge decides.
    """
    match = _PATCH_RE.search(success_check or "")
    if not match:
        return None
    from tools.patch_check import PatchCheckTool

    rel, root = match.group(0), Path(root)
    if not (root / rel).is_file():
        return {"verdict": "missing", "reason": f"файла правки {rel} нет",
                "artifacts_named": [rel], "artifacts_observed": []}
    try:
        check = PatchCheckTool(workspace_root=root).run(path=rel, full=False)
    except Exception as exc:  # noqa: BLE001 — a broken tool does not judge
        return {"verdict": "unverifiable", "reason": f"patch_check не отработал: {exc}"[:200],
                "artifacts_named": [rel], "artifacts_observed": [rel]}
    green = check.get("verdict") == "green"
    why = str(check.get("why") or "")
    errors = "; ".join(str(e) for e in (check.get("errors") or ()))[:200]
    return {
        "verdict": "verified" if green else "missing",
        "reason": (f"patch_check: {check.get('verdict')}"
                   + (f" — {why} {errors}".rstrip() if not green else "")),
        "artifacts_named": [rel],
        "artifacts_observed": [rel] if green else [],
    }


# Marks by which a test is switched off without deleting it.
_SILENCERS = ("pytest.mark.skip", "pytest.skip(", "pytest.mark.xfail", "@unittest.skip")


def weakened_judges(root: Path, contents: dict[str, str]) -> list[str]:
    """Existing tests that the edit makes SMALLER.

    The route without a human may add tests but not weaken judges: fewer `test_*`, fewer `assert` or new skip/xfail (Darwin Godel Machine, Sakana 2025: the agent removed the marks by which the detector caught forgery). Weakening is a human decision.
    """
    from core.self_apply_lane import _test_surface

    out: list[str] = []
    for rel, after_src in contents.items():
        path = root / rel
        if not rel.replace("\\", "/").startswith("tests/") or not path.is_file():
            continue
        before_src = path.read_text(encoding="utf-8", errors="replace")
        before, after = _test_surface(before_src), _test_surface(after_src)
        silenced = sum(after_src.count(s) for s in _SILENCERS) > sum(before_src.count(s) for s in _SILENCERS)
        if before is None or after is None or after[0] < before[0] or after[1] < before[1] or silenced:
            out.append(rel)
    return out


def _forbidden(paths: list[str]) -> list[str]:
    """Edit paths forbidden to self-repair; the normalised path is compared, not the spelling."""
    return [p for p in paths
            if is_protected(p) or (path_key(p) or "").startswith(_FORBIDDEN_KEYS)]


def _merge(root: Path, branch: str) -> str:
    """Fast-forward the main branch to the commit of the apply route (it does not merge itself)."""
    done = subprocess.run(["git", "-C", str(root), "merge", "--ff-only", branch],  # noqa: S603, S607
                          capture_output=True, text=True, check=False, timeout=120)
    return "" if done.returncode == 0 else (done.stderr or done.stdout).strip()[:300]


def settle_patch(agent: Any, workspace: Path | str, success_check: str) -> dict[str, Any] | None:
    """Run the goal's edit and apply it if everything is green; None: not an edit goal."""
    from core.approval_inbox import ApprovalInbox
    from core.budget_kill_switch import BudgetKillSwitch, default_path
    from core.safe_vcs import SafeVCS
    from core.self_apply_bridge import build_self_apply_payload, run_approved_self_apply
    from core.self_repair_utils import red_before_fix
    from tools.patch_check import PatchCheckTool, patched_contents
    from tools.run_tests import RunTestsTool

    match = _PATCH_RE.search(success_check or "")
    if not match:
        return None
    root, rel = Path(workspace), match.group(0)
    if not (root / rel).is_file():
        return {"verdict": "missing", "reason": f"файла правки {rel} нет"}
    check = PatchCheckTool(workspace_root=root).run(path=rel, full=True)
    if check.get("verdict") != "green" or check.get("full_exit_code") != 0:
        _log(root, {"patch": rel, "result": "red", "why": check.get("why"), "errors": check.get("errors")})
        return {"verdict": "missing", "reason": f"patch_check: {check.get('why')} {check.get('errors') or ''}"[:300]}
    blocked = _forbidden(check.get("files") or [])
    if blocked:
        _log(root, {"patch": rel, "result": "forbidden", "files": blocked})
        return {"verdict": "missing", "reason": "правка трогает запретное: " + ", ".join(blocked)}
    # Reproduction before the edit (Agentless; "plausible is not correct" in APR): a defect is
    # closed by a CODE edit whose new test failed on the old code.
    # Tests-only edits or edits without a witness do not fix code; they are taken to a human.
    code_files = [p for p in check.get("files") or [] if not str(p).replace("\\", "/").startswith("tests/")]
    # One rule with self-repair (core/self_repair_utils.red_before_fix): code 5 "no tests" is not a
    # witness, though not zero.
    if not code_files or not red_before_fix({"timed_out": False, "exit_code": check.get("witness_exit_code")}):
        _log(root, {"patch": rel, "result": "no_witness", "files": check.get("files")})
        return {"verdict": "missing", "reason": "нет свидетеля: правка должна менять код и нести "
                "новый тест, который падает до правки и проходит после"}
    contents = patched_contents(root, rel)
    weakened = weakened_judges(root, contents)
    if weakened:
        _log(root, {"patch": rel, "result": "weakens_judges", "files": weakened})
        return {"verdict": "missing", "reason": "правка ослабляет существующие тесты — это решает "
                "человек: " + ", ".join(weakened)}
    state, today = _state(root), _now().date().isoformat()
    if state["applied"].get(today, 0) >= DAILY_CAP:
        _log(root, {"patch": rel, "result": "daily_cap"})
        return {"verdict": "missing", "reason": f"дневной потолок {DAILY_CAP} правок исчерпан"}
    files = [{"path": p, "content": c} for p, c in contents.items()]
    inbox = ApprovalInbox(path=root / "data" / "approval_inbox.jsonl")
    item = inbox.add(operation="self_apply_lane.run", summary=f"self-repair {rel}", risk="reversible",
                     reasons=("patch_check green with full suite",),
                     payload=build_self_apply_payload(files=files, reason=f"self-repair {rel}",
                                                      evidence=(rel,), origin="self_repair_route",
                                                      workspace=root),
                     dedup_key=f"self_repair:{rel}")
    inbox.approve(item.id, reason="стоячее правило самопочинки (оператор 2026-09-22)",
                  actor="standing_rule:self_repair")
    report = run_approved_self_apply(inbox=inbox, item_id=item.id, workspace=root, vcs=SafeVCS(workspace=root),
                                     test_runner=RunTestsTool(workspace_root=root),
                                     kill_switch=BudgetKillSwitch(path=default_path(root)))
    report = report if isinstance(report, dict) else report.to_dict()
    if report.get("status") != "committed_local" or not report.get("branch"):
        _log(root, {"patch": rel, "result": report.get("status"), "why": report.get("reason")})
        return {"verdict": "missing", "reason": f"путь применения: {report.get('status')} {report.get('reason')}"[:300]}
    merge_error = _merge(root, report["branch"])
    if merge_error:
        _log(root, {"patch": rel, "result": "merge_failed", "why": merge_error, "branch": report["branch"]})
        return {"verdict": "missing", "reason": "не перемоталось: " + merge_error}
    state["applied"][today] = state["applied"].get(today, 0) + 1
    _save_state(root, state)
    fingerprint = state.get("patch_to_defect", {}).get(rel)
    if fingerprint:
        from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

        SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH).transition(
            status="resolved", observed_at=_now().isoformat(), fingerprint=fingerprint,
            evidence=f"починено самим агентом: {rel}, patch_check green + полный набор, коммит {report.get('commit_hash')}")
    _log(root, {"patch": rel, "result": "applied", "commit": report.get("commit_hash"),
                "files": report.get("files_changed")})
    return {"verdict": "verified", "reason": f"правка поставлена, коммит {report.get('commit_hash')}"}
