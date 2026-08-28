"""Trusted low-risk self-apply lane (TD-023).

This closes the first *safe* self-build loop:

    propose -> classify low-risk -> apply on a temp branch -> targeted tests
    -> full pytest -> rollback (red) OR local commit (green)

It is intentionally NOT general autonomous write access. The lane only touches a
narrow allowlist of source/test/docs files, works exclusively on a dedicated
temporary branch, never pushes, never merges to the base branch, and rolls the
working tree back automatically the moment any test stage fails.

Hard safety gates (checked before any file is touched, first trip wins):
  0. contradicted instructions   -> status="conflict_block"
  1. budget kill-switch active   -> status="budget_kill_switch"
  2. hour budget near-exhaustion -> status="budget_wait"
  3. pending approvals           -> status="approval_wait"
  4. patch not classified low-risk / denylisted -> status="rejected"
  5. target changed since proposal -> status="stale_proposal"
  6. workspace not clean         -> status="rejected"

The heavy dependencies (git, test runner) are injected so the whole lane is
unit-testable with fakes — no real provider/network/LLM call ever happens here.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.conflict_episode import ConflictEpisodeStore, episodes_from_outcome
from core.conflict_episode import default_path as episode_store_path
from core.instruction_conflict_gate import Directive, InstructionConflictOutcome
from core.instruction_conflict_gate import evaluate as evaluate_instruction_conflict
from core.safe_vcs import SafeVCS, VcsError
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)
from core.self_repair_utils import _tests_passed as _tests_ok

# Directories whose ``*.py`` files may be auto-applied in this lane.
_ALLOWED_CODE_DIRS = ("core", "cli", "tools", "tests")

# Explicit denylist — checked *before* the allowlist so a sensitive path can
# never slip through even if it also happens to look allowlisted (e.g. a
# ``.md`` file under ``.github``). Matching is by exact relative path or by a
# path prefix / filename rule.
_DENY_EXACT = frozenset(
    {
        "config/budget_limits.json",
        "config/model_registry.json",
        "config/model_catalog.json",
        "config/budget_limits.example.json",
        "config/model_registry.example.json",
        # MIR-139/H-39 (слово оператора 2026-08-28): улики ПРОШЛОГО. Правка
        # судьи меняет будущие вердикты и потому идёт с флагом и глазом
        # человека; правка реестра/леджера меняет прошлые улики задним
        # числом — подсудимый не правит материалы своего дела.
        "docs/audit/MASTER_ISSUE_REGISTRY.md",
        "docs/audit/HISTORICAL_FAILURE_LEDGER.md",
    }
)


def judge_touching_note(paths) -> str:
    """«ПРАВИТ СУДЬЮ» — флаг для глаза оператора (MIR-139).

    tests/ полосе не запрещены — новые тесты и есть главный продукт агента, —
    но заявка, трогающая судей, обязана кричать об этом с первой строки:
    самооценка через собственные тесты остаётся идеальной, пока реальность
    падает (поле, июль 2026).
    """
    touched = sorted({
        str(p).replace("\\", "/")
        for p in paths
        if str(p).replace("\\", "/").startswith("tests/")
    })
    if not touched:
        return ""
    return f"⚠ ПРАВИТ СУДЬЮ (tests/: {', '.join(touched[:3])}) — "
_DENY_PREFIXES = (".github/", ".git/", "config/", "secrets/", ".venv/")
_DENY_SUFFIXES = (".lock", ".pem", ".key")
_DENY_NAMES = frozenset(
    {
        ".env",
        "requirements.txt",
        "requirements-dev.txt",
        "poetry.lock",
        "package-lock.json",
        "pipfile.lock",
        "id_rsa",
        "credentials",
        "credentials.json",
    }
)
# Filenames that clearly denote secrets/keys/env regardless of directory.
_DENY_NAME_SUBSTR = ("secret", "credential", ".env")


@dataclass(frozen=True)
class FileChange:
    """One full-content file replacement (create or overwrite)."""

    path: str
    content: str
    #: Состояние файла в момент ПОДАЧИ предложения. Три различимых случая, а не
    #: два: `base_checked=False` — не смотрели; `True` с пустым хешем — файла не
    #: было; `True` с хешем — файл был таким. Зачем: MIR-168.
    base_sha256: str = ""
    base_checked: bool = False


@dataclass(frozen=True)
class SelfApplyProposal:
    """A validated low-risk patch to route through the trusted apply lane."""

    files: tuple[FileChange, ...]
    reason: str = ""
    evidence: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ("tests",)
    test_pattern: str | None = None
    #: The requirements this patch is answering, each tagged with the authority
    #: of its source (see ``docs/INSTRUCTION_AUTHORITY.md``). When two of them
    #: contradict each other the lane refuses to apply anything — an empty
    #: tuple means "no contradiction was recorded" and the lane proceeds.
    directives: tuple[Directive, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "test_paths", tuple(self.test_paths))
        object.__setattr__(self, "directives", tuple(self.directives))


class TestRunner(Protocol):
    """Subset of ``tools.run_tests.RunTestsTool`` the lane depends on."""

    def run(
        self,
        paths: list[str] | None = ...,
        pattern: str | None = ...,
    ) -> dict[str, Any]: ...


@dataclass
class SelfApplyReport:
    status: str
    reason: str = ""
    branch: str | None = None
    files_changed: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    rollback_status: str = "none"  # none | restored | failed
    commit_hash: str | None = None
    rejected_files: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_human_action: str = ""
    #: Procedural-memory ids written when the lane refused on a conflict, so an
    #: operator ruling can be attached to the exact stop it settles.
    episode_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "branch": self.branch,
            "files_changed": list(self.files_changed),
            "tests_run": list(self.tests_run),
            "rollback_status": self.rollback_status,
            "commit_hash": self.commit_hash,
            "rejected_files": list(self.rejected_files),
            "risks": list(self.risks),
            "next_human_action": self.next_human_action,
            "episode_ids": list(self.episode_ids),
        }


# ── risk classification ─────────────────────────────────────────────────────


def _normalize_rel(path: str) -> str | None:
    """Return a clean forward-slash relative path, or None if unsafe/absolute."""
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.replace("\\", "/").strip()
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):  # absolute / drive
        return None
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in parts):
        return None
    return "/".join(parts)


def _is_denied(rel: str) -> bool:
    lower = rel.lower()
    name = lower.rsplit("/", 1)[-1]
    if lower in {d.lower() for d in _DENY_EXACT}:
        return True
    if any(lower.startswith(pfx) for pfx in _DENY_PREFIXES):
        return True
    if any(lower.endswith(sfx) for sfx in _DENY_SUFFIXES):
        return True
    if name in _DENY_NAMES:
        return True
    return bool(any(sub in name for sub in _DENY_NAME_SUBSTR))


def _is_allowed(rel: str) -> bool:
    lower = rel.lower()
    top = lower.split("/", 1)[0]
    if top in _ALLOWED_CODE_DIRS and lower.endswith(".py"):
        return True
    if top == "docs":
        return True
    return bool(lower.endswith(".md"))


#: Каталоги, где живёт ПОЛИТИКА — то, что патч меняет по существу.
_POLICY_DIRS: tuple[str, ...] = ("core/", "cli/", "tools/", "app/", "api/")


def _test_surface(source: str) -> tuple[int, int] | None:
    """(тестов, проверок) в исходнике, или None, если его не разобрать.

    Считаются функции с именем `test_*` (включая методы) и операторы `assert`.
    Это грубая мерка судьи НАРОЧНО: её задача — заметить, что судья стал
    меньше, а не оценить качество набора.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    tests = 0
    asserts = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                tests += 1
        elif isinstance(node, ast.Assert):
            asserts += 1
    return tests, asserts


def judge_change_risks(
    files: tuple[FileChange, ...] | list[FileChange], *, workspace: Path,
) -> list[str]:
    """Назвать риск, если патч меняет политику и ОСЛАБЛЯЕТ её судью одним актом.

    MIR-139. Поле разделяет не по «трогать тесты», а по «менять политику и её
    судью одновременно»: честная работа тесты трогает — Stage A обязан сперва
    написать ПАДАЮЩИЙ приёмочный тест, а починки правят существующие. Опасна
    именно та половина, где судья становится МЕНЬШЕ, потому что добавленной
    проверкой самооценку не поднять.

    Это не второй судья и не ужесточение собственного набора — анти-требование
    записи запрещает и то, и другое: более строгий самописный аршин остаётся
    самописным. Решение остаётся у человека в ящике одобрений (§9); здесь
    закрывается ровно то, чего ему не хватало — что судья изменился и куда.

    Считать надо ДО применения патча: после него «до» уже негде взять.
    """
    changes = list(files)
    policy_touched = [
        rel for rel in (_normalize_rel(c.path) for c in changes)
        if rel and rel.startswith(_POLICY_DIRS)
    ]
    if not policy_touched:
        return []

    risks: list[str] = []
    for change in changes:
        rel = _normalize_rel(change.path)
        if rel is None or not rel.startswith("tests/"):
            continue
        existing = Path(workspace) / rel
        if not existing.exists():
            # Новый файл тестов — прибавка к судье, а не убавка.
            continue
        try:
            before_src = existing.read_text(encoding="utf-8")
        except OSError:
            continue
        before = _test_surface(before_src)
        after = _test_surface(change.content)
        if before is None or after is None:
            risks.append(
                f"judge {rel} changed in the same patch as policy, and the "
                f"change could NOT be measured (unparseable source) — read the "
                f"diff before approving"
            )
            continue
        if after[0] < before[0] or after[1] < before[1]:
            risks.append(
                f"judge {rel} was WEAKENED in the same patch as policy "
                f"({', '.join(policy_touched[:3])}): "
                f"tests {before[0]} -> {after[0]}, asserts {before[1]} -> "
                f"{after[1]}. The green suite below was produced BY this "
                f"weakened judge and is not independent evidence (MIR-139)"
            )
    return risks


def classify_patch_risk(
    files: tuple[FileChange, ...] | list[FileChange],
) -> tuple[bool, str, list[str]]:
    """Pure low-risk gate: every file must be allowlisted and not denylisted.

    Returns ``(ok, reason, rejected_files)``. ``ok`` is True only when the patch
    touches at least one file and every file passes both checks.
    """
    files = list(files)
    if not files:
        return False, "empty patch: no files to apply", []
    rejected: list[str] = []
    for change in files:
        rel = _normalize_rel(change.path)
        if rel is None:
            rejected.append(change.path)
            continue
        if _is_denied(rel) or not _is_allowed(rel):
            rejected.append(change.path)
    if rejected:
        return (
            False,
            ("patch is not low-risk: files outside the allowlist or on the "
            "denylist"),
            rejected,
        )
    return True, "all files are low-risk (allowlisted, not denylisted)", []


# ── helpers ─────────────────────────────────────────────────────────────────


# Matches pytest's error-prefixed traceback tail lines, e.g.
#   "E   ImportError: cannot import name '_ToolRun' from 'core.self_repair_types'"
_PYTEST_ERROR_LINE_RE = re.compile(
    r"^E\s+(\w*(?:Error|Exception|Warning)\b.*)$", re.MULTILINE
)
# Fallback: a bare "SomethingError: ..." line without pytest's "E " prefix.
_BARE_ERROR_LINE_RE = re.compile(
    r"^\s*(\w*(?:Error|Exception)\b:.*)$", re.MULTILINE
)


def _first_error_line(text: str) -> str:
    """Return the first Python error/exception line found in captured output."""
    if not text:
        return ""
    match = _PYTEST_ERROR_LINE_RE.search(text) or _BARE_ERROR_LINE_RE.search(text)
    if match:
        return match.group(1).strip()[:200]
    return ""


def _failure_detail(result: Any) -> str:
    """Build a short, human-readable reason for a failed test stage."""
    if not isinstance(result, dict):
        return ""
    parts: list[str] = []
    failed_tests = result.get("failed_tests") or []
    if isinstance(failed_tests, (list, tuple)) and failed_tests:
        shown = [str(t) for t in list(failed_tests)[:3]]
        extra = len(failed_tests) - len(shown)
        label = "tests=" + ", ".join(shown)
        if extra > 0:
            label += f" (+{extra} more)"
        parts.append(label)
    err = _first_error_line(
        f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}"
    )
    if err:
        parts.append(err)
    return "; ".join(parts)[:400]


def file_base_sha256(text: str) -> str:
    """Отметка состояния файла — одна формула на снятие и на сверку (MIR-168)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _base_state_gate(
    workspace: Path, changes
) -> tuple[SelfApplyReport | None, list[str]]:
    """Отказ, если файл уже не тот, плюс оговорка, если отметки не было.

    Второе возвращаемое — не украшение: «отметку не снимали» не равно «отметка
    сошлась», и непроверенное обязано называться непроверенным (MIR-168).
    """
    unverified = [] if all(c.base_checked for c in changes) else ["base_unverified"]
    stale = _stale_changes(workspace, changes)
    if not stale:
        return None, unverified
    return SelfApplyReport(
        status="stale_proposal",
        reason=(
            "the proposal was built on a different version of "
            f"{', '.join(stale)}; refusing to overwrite newer work"
        ),
        rejected_files=stale,
        risks=["stale_proposal"],
        next_human_action=(
            "Rebuild the proposal against the current file, then approve the "
            "fresh one."
        ),
    ), unverified


def _stale_changes(workspace: Path, changes) -> list[str]:
    """Пути, чей файл на диске уже не тот, на котором предложение построено.

    Не сверяется молча то, чего не мерили: запись без отметки пропускается
    здесь и объявляется отдельно. Замер и границы: MIR-168.
    """
    stale: list[str] = []
    for change in changes:
        if not change.base_checked:
            continue
        target = Path(workspace) / change.path
        try:
            current = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Файла нет. Это совпадает с предложением только если его не было
            # и в момент подачи.
            if change.base_sha256:
                stale.append(change.path)
            continue
        if not change.base_sha256 or file_base_sha256(current) != change.base_sha256:
            stale.append(change.path)
    return stale


#: Документы, которые КОД читает как власть или как маршрут. Документ здесь
#: бывает не текстом, а решением: хартия выбирает агенту цели и лежит в той же
#: папке, куда он пишет черновики. Правило «только создание» их и так не
#: пропустит — запрет назван отдельно, чтобы не опираться на побочный эффект.
_AUTHORITY_DOCUMENTS: frozenset[str] = frozenset({
    "knowledge/doctrine/future/CORPORATE_MODEL.md",
    "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
    "knowledge/doctrine/ROADMAP.md",
    "knowledge/generated/AGENT_ANATOMY.md",
    "knowledge/maps/COMMANDS_MAP.md",
})


def autonomous_execution_verdict(proposal: SelfApplyProposal) -> tuple[bool, str]:
    """Можно ли применить это предложение БЕЗ человека, и если нет — почему.

    Правило не даёт новых полномочий. Создавать файлы агент уже вправе через
    `file_write` — без тестов и без отката; полоса делает то же самое с
    прицельными тестами, полной батареей и откатом, то есть добавляет проверку
    к уже разрешённому действию. Всё, что шире, остаётся за человеком.

    Три условия, и каждое отсекает свой класс вреда: файла не должно было
    существовать (перезапись необратима, I-1); это должен быть документ (новый
    тест — это новый судья, а приёмка идёт той же батареей, MIR-139); и он не
    должен быть документом, который код читает как власть.

    Замер и отвергнутые варианты: MIR-173.
    """
    if not proposal.files:
        return False, "no files in the proposal"
    for change in proposal.files:
        path = (change.path or "").replace("\\", "/").strip()
        if not change.base_checked:
            return False, f"unstamped origin for {path!r}: not checked is not permission"
        if change.base_sha256:
            return False, f"{path!r} existed when the proposal was made; an overwrite is the operator's"
        if not path.lower().endswith(".md"):
            return False, f"{path!r} is not a document; code and tests stay with the operator"
        if path in _AUTHORITY_DOCUMENTS:
            return False, f"{path!r} is read by code as authority"
    return True, "creates documents only, none of them read as authority"


def _write_file(workspace: Path, rel: str, content: str) -> None:
    target = (workspace / rel).resolve()
    root = workspace.resolve()
    if root not in target.parents and target != root:
        raise VcsError(f"refusing to write outside workspace: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── orchestration ───────────────────────────────────────────────────────────


def _record_conflict_episodes(
    conflict: InstructionConflictOutcome,
    *,
    workspace: Path,
    context: str,
    now_iso: str | None,
) -> tuple[list[str], str]:
    """Bank the stop in procedural memory. Never raises.

    The refusal is the safety-critical part; losing the episode is a memory
    loss, not a safety failure, so a broken store must not turn a clean refusal
    into an unhandled error.

    Returns ``(episode_ids, risk_note)``. The note is non-empty only when the
    write failed: an empty id list would otherwise be indistinguishable from
    "no episode was needed", and the operator would have no way to know the
    stop went unrecorded and needs re-banking.
    """
    try:
        episodes = episodes_from_outcome(
            conflict, context=context, now_iso=now_iso
        )
        if not episodes:
            return [], ""
        ConflictEpisodeStore(episode_store_path(workspace)).save_many(episodes)
        return [episode.id for episode in episodes], ""
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return [], (
            "конфликт НЕ записан в процедурную память "
            f"({type(exc).__name__}); остановка в силе, но эпизод потерян"
        )


def run_self_apply_lane(  # noqa: PLR0911 — flat: depth 2, all 15 returns are guard clauses
    proposal: SelfApplyProposal,
    *,
    workspace: Path,
    vcs: SafeVCS,
    test_runner: TestRunner,
    budget_snapshot: dict | None = None,
    approvals_pending: int = 0,
    kill_switch: BudgetKillSwitch | None = None,
    branch_prefix: str = "self-apply",
    now_iso: str | None = None,
) -> SelfApplyReport:
    """Run the trusted low-risk self-apply loop end to end.

    Returns a structured :class:`SelfApplyReport`. Never pushes, never merges,
    never edits the base branch directly, and rolls back automatically on any
    test failure or unexpected error.
    """
    # 0. contradicted instructions -------------------------------------------
    # Checked before the budget gates on purpose: a patch answering two
    # incompatible requirements must not be applied even when there is budget,
    # no pending approval and the diff looks low-risk. The lane changes nothing
    # and hands the contradiction back to the operator.
    conflict = evaluate_instruction_conflict(proposal.directives)
    if conflict.is_blocked:
        episode_ids, memory_risk = _record_conflict_episodes(
            conflict, workspace=workspace, context=proposal.reason,
            now_iso=now_iso,
        )
        risks = [finding.priority_verdict() for finding in conflict.findings]
        if memory_risk:
            risks.append(memory_risk)
        return SelfApplyReport(
            status="conflict_block",
            reason=conflict.reason,
            risks=risks,
            next_human_action=conflict.report(),
            episode_ids=episode_ids,
        )

    # 1. budget kill-switch ---------------------------------------------------
    switch = kill_switch or BudgetKillSwitch(path=default_path(workspace))
    ks_state = switch.status(budget_snapshot, now_iso=now_iso)  # read-only
    if ks_state.active:
        return SelfApplyReport(
            status="budget_kill_switch",
            reason=(
                f"budget kill-switch active: {ks_state.window} "
                f"{ks_state.counter} {ks_state.used}/{ks_state.limit}"
            ),
            next_human_action=(
                "Clear the kill-switch (:budget-kill-switch --clear) once the "
                "day budget window has recovered."
            ),
        )

    # 2. budget near-exhaustion ----------------------------------------------
    near, budget_reasons = is_budget_near_exhaustion(
        hour_budget_headroom(budget_snapshot or {})
    )
    if near:
        return SelfApplyReport(
            status="budget_wait",
            reason="; ".join(budget_reasons),
            next_human_action=(
                "Wait for the hour budget window to refill before retrying the "
                "self-apply lane."
            ),
        )

    # 3. pending approvals ----------------------------------------------------
    pending = int(approvals_pending or 0)
    if pending > 0:
        return SelfApplyReport(
            status="approval_wait",
            reason=f"{pending} approval item(s) pending",
            next_human_action=(
                "Clear the approval queue (:approval-list / :approval-triage) "
                "before running the self-apply lane."
            ),
        )

    # 4. low-risk classification ---------------------------------------------
    ok, risk_reason, rejected = classify_patch_risk(proposal.files)
    if not ok:
        return SelfApplyReport(
            status="rejected",
            reason=risk_reason,
            rejected_files=rejected,
            risks=[risk_reason],
            next_human_action=(
                "Narrow the patch to allowlisted source/test/docs files, or "
                "route sensitive changes through explicit human approval."
            ),
        )

    # 5. the file the patch was built on must still be the file on disk ------
    stale_report, base_risks = _base_state_gate(workspace, proposal.files)
    if stale_report is not None:
        return stale_report

    files_changed = [_normalize_rel(c.path) or c.path for c in proposal.files]

    # 6. require a clean working tree ----------------------------------------
    try:
        if not vcs.is_clean():
            return SelfApplyReport(
                status="rejected",
                reason="workspace has uncommitted changes; refusing to apply",
                next_human_action=(
                    "Commit or stash existing changes so the lane starts from a "
                    "clean tree."
                ),
            )
        original_branch = vcs.current_branch()
    except VcsError as exc:
        return SelfApplyReport(
            status="error",
            reason=f"vcs precheck failed: {exc}",
            next_human_action="Inspect the repository state manually.",
        )

    branch = f"{branch_prefix}/{_timestamp()}"
    next_human = (
        f"Review branch {branch}, then merge/push manually if the change is "
        "acceptable."
    )

    # MIR-139: мерка судьи снимается ЗДЕСЬ — после проверки чистого дерева и
    # ДО применения патча. Позже «до» уже негде взять: на диске новое.
    judge_risks = judge_change_risks(proposal.files, workspace=Path(workspace))

    def _rollback() -> str:
        try:
            vcs.reset_hard()
            vcs.clean_untracked()
            vcs.checkout(original_branch)
            vcs.delete_branch(branch)
            return "restored"
        except VcsError:
            return "failed"

    # 6. create temp branch + apply ------------------------------------------
    try:
        vcs.create_temp_branch(branch)
    except VcsError as exc:
        return SelfApplyReport(
            status="error",
            reason=f"could not create temp branch: {exc}",
            next_human_action="Inspect the repository state manually.",
        )

    try:
        for change in proposal.files:
            rel = _normalize_rel(change.path) or change.path
            _write_file(workspace, rel, change.content)
    except (VcsError, OSError) as exc:
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason=f"apply failed: {exc}",
            branch=branch,
            files_changed=files_changed,
            rollback_status=rollback_status,
            next_human_action="Inspect the proposal; the tree was restored.",
        )

    # 7. targeted tests -------------------------------------------------------
    tests_run: list[str] = []
    targeted = test_runner.run(
        paths=list(proposal.test_paths),
        pattern=proposal.test_pattern,
    )
    tests_run.append("targeted")
    if not _tests_ok(targeted):
        rollback_status = _rollback()
        detail = _failure_detail(targeted)
        return SelfApplyReport(
            status="rolled_back",
            reason="targeted tests failed" + (f": {detail}" if detail else ""),
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            risks=["targeted tests red; change discarded"],
            next_human_action="Review the failing targeted tests; tree restored.",
        )

    # 8. full pytest ----------------------------------------------------------
    full = test_runner.run(paths=None, pattern=None)
    tests_run.append("full")
    if not _tests_ok(full):
        rollback_status = _rollback()
        detail = _failure_detail(full)
        return SelfApplyReport(
            status="rolled_back",
            reason="full pytest failed" + (f": {detail}" if detail else ""),
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            risks=["full suite red; change discarded"],
            next_human_action="Review the failing full suite; tree restored.",
        )

    # 9. local commit (no push, no merge) ------------------------------------
    message = _commit_message(proposal, branch)
    try:
        vcs.stage_all()
        commit_hash = vcs.commit(message)
        vcs.checkout(original_branch)  # leave the repo on its original branch
    except VcsError as exc:
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason=f"local commit failed: {exc}",
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            next_human_action="Inspect the repository state manually.",
        )

    return SelfApplyReport(
        status="committed_local",
        reason="targeted + full tests passed; committed locally on temp branch",
        branch=branch,
        files_changed=files_changed,
        tests_run=tests_run,
        rollback_status="none",
        commit_hash=commit_hash,
        risks=[
            ("change is committed locally only — not pushed, not merged; a human "
            "must review before it reaches the base branch"),
            *judge_risks,
            *base_risks,
        ],
        next_human_action=next_human,
    )


def _commit_message(proposal: SelfApplyProposal, branch: str) -> str:
    head = proposal.reason.strip() or "self-apply: low-risk automated patch"
    body_files = ", ".join(
        _normalize_rel(c.path) or c.path for c in proposal.files
    )
    return f"{head}\n\nself-apply lane ({branch})\nfiles: {body_files}"
