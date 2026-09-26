"""Принимающий: кто переводит проверенный кандидат в рабочее состояние опыта.

Вынесен из полосы, чтобы самоизменяющийся код не принимал сам себя: голова опыта
сдвигается только после перепроверки (полный SHA, предъявлен, ровно шаг, забор цел,
батарея зелена в свежем дереве). Ветки, push и merge не трогаются (см. `_VERBS`).
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

#: Забор принимающего: забор песочницы плюс сам принимающий, иначе одно принятое
#: изменение этого файла отменило бы все прочие правила.
SUPERVISOR_FENCE: frozenset[str] = frozenset({
    "core/burn_in_supervisor.py",
    "scripts/burn_in_supervisor.py",
    "scripts/autonomous_repair.py",
    "core/burn_in_sandbox.py",
    "core/self_apply_lane.py",
    "core/self_apply_bridge.py",
    "core/policy.py",
    "core/rule_approved_apply.py",
    "core/autonomous_runtime.py",
    "core/approval_inbox.py",
    "core/actuation_gateway.py",
    "core/safe_vcs.py",
    # Память опыта: коммит со своим указателем головы назначил бы себе старт сам.
    # Полоса делает `git add -A`, поэтому эти имена ещё и в `.gitignore`.
    "state/burn_in_head.json",
    "state/burn_in_offers.jsonl",
    "state/burn_in_adoptions.jsonl",
    "config/burn_in_sandbox.json",
    "scripts/install_daemon.ps1",
})

#: Заповедные деревья: сверка по префиксу. `.github/` — это судья (батарея и
#: указания ревизору), а кандидат не вправе переписывать своего судью.
SUPERVISOR_FENCED_TREES: frozenset[str] = frozenset({
    ".github/",
})

#: Полное имя объекта git: сорок знаков, нижний регистр.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Разрешённые глаголы git (список разрешённого): ни один вызов не достаёт до сети.
_VERBS: frozenset[str] = frozenset({
    "cat-file", "diff", "rev-list", "rev-parse", "worktree",
})

_HEAD_FILE = "state/burn_in_head.json"
_OFFERS_FILE = "state/burn_in_offers.jsonl"
_ADOPTIONS_FILE = "state/burn_in_adoptions.jsonl"


class SupervisorError(RuntimeError):
    """Принимающий не смог даже спросить git. Отказ, а не исключение наружу."""


@dataclass(frozen=True)
class AdoptionVerdict:
    """Исход одного решения. `accepted=False` всегда несёт названную причину."""

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


# ── пути ─────────────────────────────────────────────────────────────────────

def _head_file(repo: Path) -> Path:
    return Path(repo) / _HEAD_FILE


def offer_ledger(repo: Path) -> Path:
    return Path(repo) / _OFFERS_FILE


def adoption_log(repo: Path) -> Path:
    return Path(repo) / _ADOPTIONS_FILE


# ── git ──────────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    verb = args[0] if args else ""
    if verb not in _VERBS:
        raise SupervisorError(f"отказано: 'git {verb}' не в списке разрешённых")
    done = subprocess.run(  # noqa: S603 — argv, без оболочки; глаголы из _VERBS
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


# ── голова опыта ─────────────────────────────────────────────────────────────

def experiment_head(repo: Path) -> str:
    """SHA, из которого стартует следующий цикл: указатель опыта или голова репо.

    Отсутствующий файл — «опыт не начинался»; испорченный (в т.ч. байты) —
    `SupervisorError`, иначе цепочка тихо откатилась бы к началу.
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
    """Имя для следующего цикла. Отдельное имя, потому что смысл другой."""
    return experiment_head(repo)


def materialise_next_cycle(
    repo: Path, workspace: Path, *, sha: str | None = None
) -> str:
    """Поставить свежее рабочее дерево следующего цикла на голову опыта.

    Дерево заводит принимающий, поэтому код не выбирает себе коммит; `sha` —
    лишь сверка. Голова читается под тем же замком, что и `worktree add`.
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
    """Атомарно заменить указатель головы: запись рядом, затем `os.replace`."""
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


# ── реестр предложений ───────────────────────────────────────────────────────

def offer_verified_commit(
    repo: Path,
    *,
    sha: str,
    proposal_id: str,
    tests_run: Any = (),
    reason: str = "",
) -> bool:
    """Сторона полосы: предъявить проверенный SHA; это не принятие и прав не даёт."""
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
        # `UnicodeDecodeError` — не `OSError`; умолчание и так «не предъявлен».
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # Испорченная строка не разрешает и не запрещает — читаем дальше.
            continue
        # Не словарь — тоже законный JSON; без проверки одна такая строка
        # останавливала бы принятие навсегда.
        if isinstance(row, dict) and row.get("sha") == sha:
            return True
    return False


# ── решение ──────────────────────────────────────────────────────────────────

def _fence_key(path: str) -> str:
    """Один вид записи для сличения с забором.

    Регистр складывается всегда, а не через `normcase` (на POSIX тождество):
    лишний отказ дешевле кода за забором.
    """
    return str(path).replace("\\", "/").strip().strip('"').casefold()


def _in_fenced_tree(key: str) -> bool:
    """Лежит ли приведённый путь внутри заповедного дерева."""
    return any(key.startswith(t.casefold()) for t in SUPERVISOR_FENCED_TREES)


def adopt_offer(
    repo: Path,
    *,
    sha: str,
    battery: Callable[[Path], tuple[bool, str]],
    fence: Any = None,
) -> AdoptionVerdict:
    """Единственный способ сдвинуть голову опыта.

    `battery(tree) -> (зелено, подробность)` запускается в свежем дереве на
    кандидате. Решение целиком под замком, чтобы два кандидата от одной головы
    не приняли оба.
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
    guard = frozenset(_fence_key(p) for p in (fence if fence is not None else SUPERVISOR_FENCE))
    previous = experiment_head(repo)

    def refuse(reason: str, **checks: Any) -> AdoptionVerdict:
        verdict = AdoptionVerdict(
            accepted=False, sha=str(sha), reason=reason,
            previous_head=previous, checks=checks,
        )
        _append(adoption_log(repo), verdict.to_dict())
        return verdict

    # 1. имя неподвижно
    if not _SHA_RE.match(str(sha or "")):
        return refuse("имя не является полным SHA: ссылка разрешается в разное")
    if not _commit_exists(repo, sha):
        return refuse("такого коммита в репозитории нет")

    # 2. предъявлен полосой
    if not _was_offered(repo, sha):
        return refuse("SHA не предъявлен полосой в реестре предложений")

    # 3. это шаг от нынешней головы
    parents = _parents(repo, sha)
    if parents != [previous]:
        return refuse(
            "кандидат не шаг от головы опыта: "
            f"родители {parents or ['нет']}, голова {previous}",
            parents=parents,
        )

    # 4. забор цел
    touched = _changed_paths(repo, previous, sha)
    if not touched:
        return refuse("кандидат ничего не меняет", changed=touched)
    crossed = sorted(
        p for p in touched
        if _fence_key(p) in guard or _in_fenced_tree(_fence_key(p))
    )
    if crossed:
        return refuse(f"кандидат трогает забор: {', '.join(crossed)}",
                      changed=touched, crossed=crossed)

    # 5. батарея на самом кандидате
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
    """Прогнать батарею в свежем дереве на кандидате; дерево убирается всегда."""
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
    except Exception as exc:  # noqa: BLE001 — сбой проверки не принимает
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
