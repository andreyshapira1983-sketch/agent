"""Adopter: moves the experiment head to a verified candidate only after re-verification.

Separated from the lane so self-modifying code cannot adopt itself: full SHA, offered by the lane, exactly one step, fence intact, battery green in a fresh tree. Branches, push and merge are never touched (see `_VERBS`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.file_lock import exclusive_file_lock
from core.protected_paths import PROTECTED_FILES, PROTECTED_TREES, path_key

__all__ = [
    "SUPERVISOR_FENCE",
    "AdoptionVerdict",
    "adopt_offer",
    "adoption_log",
    "experiment_head",
    "materialise_next_cycle",
    "next_start_point",
    "offer_ledger",
    "offer_verified_commit",
]

#: The adopter's fence is the shared list in core/protected_paths.py: the sandbox fence, the adopter itself (or one adopted change to this file would cancel all other rules) and the experience memory. The lane runs `git add -A`, so the experience memory names are also in `.gitignore`.
SUPERVISOR_FENCE: frozenset[str] = PROTECTED_FILES

#: Fenced trees, matched by prefix. `.github/` is the judge (battery and reviewer instructions); a candidate may not rewrite its judge. Trees of the shared list (`PROTECTED_TREES`) are checked together with these.
SUPERVISOR_FENCED_TREES: frozenset[str] = frozenset({
    ".github/",
})

#: Full git object name: forty chars, lower case.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Allowed git verbs (allowlist): no call reaches the network.
_VERBS: frozenset[str] = frozenset({
    "cat-file", "diff", "rev-list", "rev-parse", "worktree",
})

_HEAD_FILE = "state/burn_in_head.json"
_OFFERS_FILE = "state/burn_in_offers.jsonl"
_ADOPTIONS_FILE = "state/burn_in_adoptions.jsonl"


class SupervisorError(RuntimeError):
    """The adopter could not even query git; a refusal, not an exception escaping."""


@dataclass(frozen=True)
class AdoptionVerdict:
    """Outcome of one decision; `accepted=False` always carries a named reason."""

    accepted: bool
    sha: str
    reason: str = ""
    previous_head: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "accepted": self.accepted,
            "sha": self.sha,
            "reason": self.reason,
            "previous_head": self.previous_head,
            "checks": dict(self.checks),
        }



def _head_file(repo: Path) -> Path:
    return Path(repo) / _HEAD_FILE


def offer_ledger(repo: Path) -> Path:
    return Path(repo) / _OFFERS_FILE


def adoption_log(repo: Path) -> Path:
    return Path(repo) / _ADOPTIONS_FILE



def _git(repo: Path, *args: str) -> str:
    verb = args[0] if args else ""
    if verb not in _VERBS:
        raise SupervisorError(f"отказано: 'git {verb}' не в списке разрешённых")
    done = subprocess.run(  # noqa: S603 — argv, no shell; verbs from _VERBS
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        raise SupervisorError(
            f"git {' '.join(args)} ({done.returncode}): "
            f"{(done.stderr or done.stdout or '').strip()}"
        )
    return (done.stdout or "").strip()


def _commit_exists(repo: Path, sha: str) -> bool:
    try:
        return _git(repo, "cat-file", "-t", sha) == "commit"
    except SupervisorError:
        return False


def _parents(repo: Path, sha: str) -> list[str]:
    line = _git(repo, "rev-list", "--parents", "-n", "1", sha)
    return line.split()[1:]


def _changed_paths(repo: Path, base: str, sha: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", f"{base}..{sha}")
    return [p.strip().replace("\\", "/") for p in out.splitlines() if p.strip()]



def experiment_head(repo: Path) -> str:
    """SHA the next cycle starts from: the experiment pointer or the repo head.

    A missing file means the experiment has not started; a corrupt one (incl. bad bytes) raises `SupervisorError` so the chain never silently resets.
    """
    repo = Path(repo)
    path = _head_file(repo)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _git(repo, "rev-parse", "HEAD")
    except UnicodeDecodeError as exc:
        raise SupervisorError(
            f"указатель опыта {path} повреждён на уровне байтов: {exc}. Опыт "
            f"остановлен нарочно: молчаливый откат к голове репозитория стёр "
            f"бы всю цепочку"
        ) from exc
    except OSError as exc:
        raise SupervisorError(f"указатель опыта {path} не читается: {exc}") from exc

    try:
        sha = str(json.loads(raw).get("sha", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SupervisorError(
            f"указатель опыта {path} повреждён: {exc}. Опыт остановлен нарочно: "
            f"молчаливый откат к голове репозитория стёр бы всю цепочку"
        ) from exc

    if not _SHA_RE.match(sha):
        raise SupervisorError(
            f"указатель опыта {path} содержит не полное имя объекта: {sha!r}"
        )
    return sha


def next_start_point(repo: Path) -> str:
    """Name for the next cycle (separate name, different meaning)."""
    return experiment_head(repo)


def materialise_next_cycle(
    repo: Path, workspace: Path, *, sha: str | None = None
) -> str:
    """Put a fresh worktree of the next cycle on the experiment head.

    The adopter creates the tree, so code does not choose its commit; `sha` is only a cross-check. The head is read under the same lock as `worktree add`.
    """
    repo = Path(repo)
    workspace = Path(workspace).resolve()

    with exclusive_file_lock(_head_file(repo).with_suffix(".lock")):
        head = next_start_point(repo)

        if sha is not None and str(sha) != head:
            raise SupervisorError(
                f"дерево следующего цикла ставится только на голову опыта "
                f"{head}; запрошен {sha!r}. Выбор коммита принадлежит "
                f"принимающему"
            )

        if workspace.exists():
            try:
                _git(repo, "worktree", "remove", "--force", str(workspace))
            except SupervisorError as exc:
                raise SupervisorError(
                    f"{workspace} существует и не является рабочим деревом "
                    f"этого репозитория; принимающий не станет его удалять: "
                    f"{exc}"
                ) from exc
        _git(repo, "worktree", "prune")
        _git(repo, "worktree", "add", "--detach", str(workspace), head)
    return head


def _write_head(repo: Path, sha: str, *, previous: str) -> None:
    """Atomically replace the head pointer: write beside, then `os.replace`."""
    path = _head_file(Path(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "sha": sha,
            "previous": previous,
            "at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")



def offer_verified_commit(
    repo: Path,
    *,
    sha: str,
    proposal_id: str,
    tests_run: Any = (),
    reason: str = "",
) -> bool:
    """Lane side: offer a verified SHA; this is not adoption and grants no rights."""
    if not _SHA_RE.match(str(sha or "")):
        return False
    _append(offer_ledger(Path(repo)), {
        "at": datetime.now(timezone.utc).isoformat(),
        "sha": sha,
        "proposal_id": str(proposal_id or ""),
        "tests_run": [str(t) for t in (tests_run or ())],
        "reason": str(reason or ""),
    })
    return True


def _was_offered(repo: Path, sha: str) -> bool:
    path = offer_ledger(Path(repo))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is not `OSError`; the default is already "not offered".
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # A corrupt row neither allows nor forbids; keep reading.
            continue
        # A non-dict is also valid JSON; without this check one such row would block adoption forever.
        if isinstance(row, dict) and row.get("sha") == sha:
            return True
    return False



def _fence_key(path: str) -> str:
    """One normal form of a path for comparing with the fence.

    Case is always folded (not via `normcase`, identity on POSIX): an extra refusal is cheaper than code behind the fence.
    """
    return path_key(path) or str(path).replace("\\", "/").strip().strip('"').casefold()


def _in_fenced_tree(key: str) -> bool:
    """Whether the normalised path lies inside a fenced tree."""
    trees = (*SUPERVISOR_FENCED_TREES, *PROTECTED_TREES)
    return any((key + "/").startswith(t.casefold()) for t in trees)


def _default_guard() -> frozenset[str]:
    return frozenset(_fence_key(p) for p in SUPERVISOR_FENCE)


def _crosses_fence(path: str, guard: frozenset[str]) -> bool:
    """Whether the candidate path touches the fence: a listed file or a fenced tree."""
    key = _fence_key(path)
    return key in guard or _in_fenced_tree(key)


def adopt_offer(
    repo: Path,
    *,
    sha: str,
    battery: Callable[[Path], tuple[bool, str]],
    fence: Any = None,
) -> AdoptionVerdict:
    """The only way to move the experiment head.

    `battery(tree) -> (green, detail)` runs in a fresh tree on the candidate. The whole decision is under the lock so two candidates from one head cannot both be adopted.
    """
    repo = Path(repo)
    with exclusive_file_lock(_head_file(repo).with_suffix(".lock")):
        return _adopt_offer_locked(repo, sha=sha, battery=battery, fence=fence)


def _adopt_offer_locked(
    repo: Path,
    *,
    sha: str,
    battery: Callable[[Path], tuple[bool, str]],
    fence: Any = None,
) -> AdoptionVerdict:
    guard = frozenset(_fence_key(p) for p in fence) if fence is not None else _default_guard()
    previous = experiment_head(repo)

    def refuse(reason: str, **checks: Any) -> AdoptionVerdict:
        verdict = AdoptionVerdict(
            accepted=False, sha=str(sha), reason=reason,
            previous_head=previous, checks=checks,
        )
        _append(adoption_log(repo), verdict.to_dict())
        return verdict

    # 1. name is fixed
    if not _SHA_RE.match(str(sha or "")):
        return refuse("имя не является полным SHA: ссылка разрешается в разное")
    if not _commit_exists(repo, sha):
        return refuse("такого коммита в репозитории нет")

    # 2. offered by the lane
    if not _was_offered(repo, sha):
        return refuse("SHA не предъявлен полосой в реестре предложений")

    # 3. one step from the current head
    parents = _parents(repo, sha)
    if parents != [previous]:
        return refuse(
            "кандидат не шаг от головы опыта: "
            f"родители {parents or ['нет']}, голова {previous}",
            parents=parents,
        )

    # 4. fence intact
    touched = _changed_paths(repo, previous, sha)
    if not touched:
        return refuse("кандидат ничего не меняет", changed=touched)
    crossed = sorted(p for p in touched if _crosses_fence(p, guard))
    if crossed:
        return refuse(f"кандидат трогает забор: {', '.join(crossed)}",
                      changed=touched, crossed=crossed)

    # 5. battery on the candidate itself
    ok, detail = _verify_in_fresh_worktree(repo, sha, battery)
    if not ok:
        return refuse("батарея на кандидате не зелена" + (f": {detail}" if detail else ""),
                      changed=touched)

    _write_head(repo, sha, previous=previous)
    verdict = AdoptionVerdict(
        accepted=True, sha=str(sha),
        reason="предъявлен, шаг от головы, забор цел, батарея зелена",
        previous_head=previous, checks={"changed": touched},
    )
    _append(adoption_log(repo), verdict.to_dict())
    return verdict


def _verify_in_fresh_worktree(
    repo: Path, sha: str, battery: Callable[[Path], tuple[bool, str]]
) -> tuple[bool, str]:
    """Run the battery in a fresh tree on the candidate; the tree is always removed."""
    holder = Path(tempfile.mkdtemp(prefix="burn-in-verify-"))
    tree = holder / "tree"
    try:
        _git(repo, "worktree", "add", "--detach", "--quiet", str(tree), sha)
    except SupervisorError as exc:
        shutil.rmtree(holder, ignore_errors=True)
        return False, f"рабочее дерево не создано: {exc}"
    try:
        ok, detail = battery(tree)
        return bool(ok), str(detail or "")
    except Exception as exc:  # noqa: BLE001 — a failed check does not adopt
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            _git(repo, "worktree", "remove", "--force", str(tree))
        except SupervisorError:
            shutil.rmtree(tree, ignore_errors=True)
        try:
            _git(repo, "worktree", "prune")
        except SupervisorError:
            pass
        shutil.rmtree(holder, ignore_errors=True)
