"""Явное полномочие песочницы: самопочинка изолированной копии без человека.

Включается только двумя жестами вместе: ``AGENT_BURN_IN_SANDBOX=on`` и метка
``config/burn_in_sandbox.json`` с абсолютным путём этой копии; при любом сомнении — None.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Жест запускающего процесс.
SANDBOX_ENV_FLAG = "AGENT_BURN_IN_SANDBOX"

#: Жест готовившего копию. Путь repo-relative, как всё в полосе.
SANDBOX_MARKER = "config/burn_in_sandbox.json"

#: Значения, считающиеся «включено»; всё прочее — выключено (fail-closed).
_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes"})

#: Забор, который песочница не вправе двигать изнутри: каждый, кто решает о
#: полномочии (ворота, одобрения, счётчик расхода, git-глаголы, сама полоса).
_FENCE: frozenset[str] = frozenset({
    SANDBOX_MARKER,
    "core/burn_in_sandbox.py",
    "core/burn_in_supervisor.py",
    "scripts/burn_in_supervisor.py",
    "scripts/autonomous_repair.py",
    "core/policy.py",
    "core/rule_approved_apply.py",
    "core/autonomous_runtime.py",
    "core/approval_inbox.py",
    "core/actuation_gateway.py",
    "core/safe_vcs.py",
    "core/self_apply_lane.py",
    "scripts/install_daemon.ps1",
})

_DEFAULT_MAX_APPLIES_PER_DAY = 20

#: Стоки памяти, которые открывает полномочие, и только они: без обобщения опыта
#: долгий прогон повторяет те же ошибки. Властные стоки не открываются никогда.
SANDBOX_DURABLE_SINKS: frozenset[str] = frozenset({"procedure", "knowledge"})


@dataclass(frozen=True)
class SandboxAuthority:
    """Включённое полномочие; префикс `id` показывает в журнале расхода, чем разрешено изменение."""

    id: str
    workspace: str
    max_applies_per_day: int
    expires_at: str
    reason: str

    def is_live(self, now: datetime | None = None) -> bool:
        """Не истёк ли срок прямо сейчас; нечитаемая отметка — «истёк»."""
        raw = str(self.expires_at or "").strip()
        if not raw:
            return False
        try:
            deadline = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return deadline > (now or datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace": self.workspace,
            "max_applies_per_day": self.max_applies_per_day,
            "expires_at": self.expires_at,
            "reason": self.reason,
        }


def _flag_is_on(env: Any) -> bool:
    source = os.environ if env is None else env
    return str(source.get(SANDBOX_ENV_FLAG, "")).strip().lower() in _ON_VALUES


def _same_place(declared: str, workspace: Path) -> bool:
    """Называет ли метка именно эту копию абсолютным путём.

    Относительный путь (`"."`) переносим: метка включила бы полномочие в любом
    дереве, куда её скопировали.
    """
    try:
        if not Path(declared).is_absolute():
            return False
        return Path(declared).resolve() == Path(workspace).resolve()
    except (OSError, ValueError):
        return False


def _others_can_write(path: Path) -> bool:
    """Открыта ли метка на запись всем (как StrictModes в OpenSSH).

    Запись группой не отклоняется: при маске 0002 это отклонило бы каждую
    свежую метку. На Windows проверки нет.
    """
    if os.name != "posix":
        return False
    try:
        return bool(path.stat().st_mode & 0o002)
    except OSError:
        return False


def load_sandbox_authority(
    workspace: Any, *, env: Any = None, now: datetime | None = None
) -> SandboxAuthority | None:
    """Полномочие песочницы, или None при любом сомнении (fail-closed, ничего не чинит)."""
    if not _flag_is_on(env):
        return None
    root = Path(workspace or ".")
    marker = root / SANDBOX_MARKER
    if _others_can_write(marker):
        return None
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("sandbox") is not True:
        return None
    declared = str(raw.get("workspace") or "").strip()
    if not declared or not _same_place(declared, root):
        return None
    expires_at = str(raw.get("expires_at") or "").strip()
    if not expires_at:
        return None
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline <= (now or datetime.now(timezone.utc)):
        return None
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        # Полномочие без названного повода нельзя ни проверить, ни отозвать.
        return None
    try:
        cap = int(raw.get("max_applies_per_day", _DEFAULT_MAX_APPLIES_PER_DAY))
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return SandboxAuthority(
        id=f"sandbox:{expires_at}",
        workspace=str(Path(root).resolve()),
        max_applies_per_day=cap,
        expires_at=expires_at,
        reason=reason,
    )


def _fence_prints(root: Path) -> frozenset[str]:
    """Забор как разрешённые пути: `resolve()` снимает обход ссылкой, `normcase` — регистром."""
    return frozenset(
        os.path.normcase(str((root / rel).resolve())) for rel in _FENCE
    )


def sandbox_execution_verdict(proposal: Any, *, workspace: Any) -> tuple[bool, str]:
    """Можно ли применить предложение в песочнице, и если нет — почему.

    Шире производственного вердикта только классом файла: код и тесты внутри копии разрешены.
    """
    from core.self_apply_lane import _is_allowed, _is_denied, _normalize_rel

    files = tuple(getattr(proposal, "files", ()) or ())
    if not files:
        return False, "no files in the proposal"
    root = Path(workspace).resolve()
    fence = _fence_prints(root)
    for change in files:
        raw = str(getattr(change, "path", "") or "")
        rel = _normalize_rel(raw)
        if rel is None:
            return False, f"{raw!r} points outside the sandbox workspace"
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            return False, f"{rel!r} resolves outside the sandbox workspace"
        if os.path.normcase(str(target)) in fence:
            return False, f"{rel!r} is the sandbox fence; it is not moved from inside"
        # Класс проверяется и по резолвнутому пути: ссылка с невинным именем
        # может вести в `.github/workflows/`, а `_write_file` пишет по резолву.
        names = [rel]
        try:
            resolved_rel = target.relative_to(root).as_posix()
        except ValueError:
            resolved_rel = rel
        if resolved_rel != rel:
            names.append(resolved_rel)
        for name in names:
            if _is_denied(name):
                return False, f"{name!r} is a denied class (secrets, CI, infrastructure)"
        # Разрешённое полосой проверяется и здесь, иначе заявка занимает
        # единицу потолка и затем отвергается полосой.
        for name in names:
            if not _is_allowed(name):
                return False, (
                    f"{name!r} is outside the lane allowlist; the lane would refuse it"
                )
    return True, "sandbox authority: workspace-local change, verified by the lane"


def sandbox_applies_today(workspace: Any, authority: SandboxAuthority) -> int:
    """Сколько применений песочница уже израсходовала сегодня (UTC)."""
    from core.autonomous_runtime import standing_runs_today

    return standing_runs_today(workspace, authority.id)


def reserve_sandbox_apply(workspace: Any, authority: SandboxAuthority) -> bool:
    """Занять одну единицу дневного потолка песочницы, или отказать.

    Журнал и атомарный примитив общие с постоянным грантом; срок сверяется и
    здесь, потому что слив заявок длится минуты.
    """
    from core.autonomous_runtime import reserve_standing_grant_use

    if not authority.is_live():
        return False
    return reserve_standing_grant_use(
        workspace, authority.id, max_per_day=authority.max_applies_per_day
    )


class TermedSinks(frozenset):
    """Список стоков, в котором расширение носит срок полномочия с собой.

    `durable_writes` привязан к агенту на всю жизнь, поэтому срок живёт в самом
    списке; базовые стоки сроку не подчинены.
    """

    __slots__ = ("_deadline", "_granted")

    def __new__(cls, base, granted, deadline):
        self = super().__new__(cls, frozenset(base) | frozenset(granted))
        self._granted = frozenset(granted) - frozenset(base)
        self._deadline = deadline
        return self

    def contains_at(self, sink: object, now: datetime) -> bool:
        if not frozenset.__contains__(self, sink):
            return False
        if sink in self._granted and now >= self._deadline:
            return False
        return True

    def __contains__(self, sink: object) -> bool:
        return self.contains_at(sink, datetime.now(timezone.utc))


def memory_profile_for(base: dict, workspace: Any, *, env: Any = None) -> dict:
    """Безнадзорный профиль памяти: копия базового или с `SANDBOX_DURABLE_SINKS` на срок."""
    profile = dict(base)
    authority = load_sandbox_authority(workspace, env=env)
    if authority is None:
        return profile
    try:
        deadline = datetime.fromisoformat(authority.expires_at)
    except ValueError:  # pragma: no cover — полномочие уже разобрало эту строку
        return profile
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    profile["durable_writes"] = TermedSinks(
        frozenset(profile.get("durable_writes") or ()),
        SANDBOX_DURABLE_SINKS,
        deadline,
    )
    return profile
