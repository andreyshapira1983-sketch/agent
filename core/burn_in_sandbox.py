"""Явное полномочие песочницы: самопочинка изолированной копии без человека.

Зачем этот модуль существует. Аудит автономности 2026-09-17 показал, что на
безнадзорном пути агент не может починить даже собственную изолированную копию:
`autonomous_execution_verdict` пропускает ровно один класс изменений («новый
документ .md»), а `core/self_repair.py` без `approval_provider` обрывается на
`unavailable`. Десятичасовой прогон в таких условиях измерял бы не самопочинку,
а скорость, с которой агент упирается в ожидание человека.

Что это НЕ есть. Не обход `PolicyGate`, не тихий флаг и не ослабление
производственных ворот. Производственный путь остаётся байт-в-байт прежним:
пока полномочие не включено ДВУМЯ независимыми жестами, ни одна строка ниже
ничего не решает, и `load_sandbox_authority` возвращает None при любом
сомнении.

Два жеста нарочно разной природы:

1. переменная окружения ``AGENT_BURN_IN_SANDBOX=on`` — жест того, кто ЗАПУСКАЕТ
   процесс;
2. файл ``config/burn_in_sandbox.json``, который называет АБСОЛЮТНЫЙ путь
   именно этой рабочей копии, срок и суточный потолок — жест того, кто готовил
   копию.

Ни один из них поодиночке не полномочие. Требование «метка называет свой путь»
закрывает единственный по-настоящему опасный случай: песочницу склонировали в
производственное дерево вместе с меткой.

Границы, которые полномочие НЕ раздвигает:

* писать можно только внутри этой копии — проверяется здесь и ещё раз в
  `core/self_apply_lane._write_file`;
* забор не двигается изнутри: ни метку, ни этот модуль, ни установщик демона
  песочница править не вправе. Самоизменяющаяся граница — отсутствие границы;
* запрещённые классы полосы (секреты, CI, инфраструктура) остаются
  запрещёнными;
* применение идёт ТОЙ ЖЕ полосой `run_self_apply_lane`: прицельные тесты,
  полная батарея, отдельная ветка, локальный коммит и откат при красной
  проверке. Ни push, ни merge в полосе нет и не появляется;
* расход считается тем же журналом, что у стоячего гранта
  (`data/standing_grant_usage.jsonl`), потому что два счётчика одного
  полномочия — это ноль счётчиков.
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

#: Значения переменной, считающиеся «включено». Всё прочее — выключено, потому
#: что умолчание обязано быть закрытым.
_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes"})

#: Забор, который песочница не вправе двигать изнутри.
#:
#: Ревизия PR #333 нашла здесь фантом: стоял `core/policy_gate.py`, файла с
#: таким именем в дереве нет вовсе, а настоящий `PolicyGate` живёт в
#: `core/policy.py`. Запись выглядела охраной ворот и не была ею. Отсюда
#: правило: за забором стоит КАЖДЫЙ, кто решает о полномочии, — ворота, место
#: замыкания петли одобрений и счетовод расхода постоянного гранта; иначе
#: песочница вправе переписать своего счетовода, не тронув ни одной записи
#: забора. Существование названного проверяет
#: `test_every_fence_entry_names_a_real_file`.
_FENCE: frozenset[str] = frozenset({
    SANDBOX_MARKER,
    "core/burn_in_sandbox.py",
    "core/burn_in_supervisor.py",
    "scripts/burn_in_supervisor.py",
    "core/policy.py",
    "core/rule_approved_apply.py",
    "core/autonomous_runtime.py",
    "core/actuation_gateway.py",
    "core/self_apply_lane.py",
    "scripts/install_daemon.ps1",
})

_DEFAULT_MAX_APPLIES_PER_DAY = 20

#: Стоки долговременной памяти, которые ОТКРЫВАЕТ полномочие песочницы, и
#: только они. Производственный безнадзорный профиль разрешает `episode` и
#: `hygiene`; обобщение опыта (`procedure`, `knowledge`) закрыто умолчанием-
#: запретом, и это верно: обобщение без присмотра меняет поведение будущих
#: прогонов. В песочнице оно нужно — иначе десятичасовой прогон копит эпизоды
#: и повторяет те же ошибки, то есть измеряет что угодно, кроме обучения.
#: Список перечислим нарочно: разрушающие и властные стоки (`hygiene` сверх
#: производственного, `profile`, `source_registry`, `assumptions`) не
#: открываются ни при каком полномочии.
SANDBOX_DURABLE_SINKS: frozenset[str] = frozenset({"procedure", "knowledge"})


@dataclass(frozen=True)
class SandboxAuthority:
    """Включённое полномочие, которое себя называет.

    `id` уходит в общий журнал расхода, поэтому у него собственный префикс:
    по журналу видно, чем именно разрешено каждое изменение.
    """

    id: str
    workspace: str
    max_applies_per_day: int
    expires_at: str
    reason: str

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
    """Называет ли метка ИМЕННО эту копию — и называет ли абсолютно.

    Относительный путь резолвится от текущего каталога, а тик запускают из
    корня рабочей копии. Значит метка с `"."` переносима: она включит
    полномочие в любом дереве, куда её скопировали, — ровно в том случае,
    ради которого проверка и заведена (ревизия Copilot по PR #333).
    """
    try:
        if not Path(declared).is_absolute():
            return False
        return Path(declared).resolve() == Path(workspace).resolve()
    except (OSError, ValueError):
        return False


def load_sandbox_authority(
    workspace: Any, *, env: Any = None, now: datetime | None = None
) -> SandboxAuthority | None:
    """Полномочие песочницы, или None. Любое сомнение — None.

    Fail-closed по построению: функция ничего не создаёт, не чинит и не
    достраивает. Испорченная метка, чужой путь, истёкший срок, нечитаемый файл
    — все эти случаи неразличимы для вызывающего и означают «производственный
    путь», то есть прежнее поведение.
    """
    if not _flag_is_on(env):
        return None
    root = Path(workspace or ".")
    marker = root / SANDBOX_MARKER
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
    """Забор в том виде, в каком его можно сравнивать: разрешённые пути.

    Сверять имя — значит сверять написание. Ссылка с невинным именем и вариант
    регистра на файловой системе, регистр не различающей, дают ДРУГУЮ строку и
    ТОТ ЖЕ файл; обе формы обхода нашла ревизия PR #333. `resolve()` снимает
    первую, `normcase` — вторую.
    """
    return frozenset(
        os.path.normcase(str((root / rel).resolve())) for rel in _FENCE
    )


def sandbox_execution_verdict(proposal: Any, *, workspace: Any) -> tuple[bool, str]:
    """Можно ли применить это предложение в песочнице, и если нет — почему.

    Шире производственного вердикта ровно на один пункт: класс файла. Код и
    тесты внутри копии разрешены — в этом весь эксперимент. Всё остальное
    строже или столь же строго: за пределы копии не писать, забор не двигать,
    запрещённые классы полосы не трогать.
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
        if _is_denied(rel):
            return False, f"{rel!r} is a denied class (secrets, CI, infrastructure)"
        # Разрешённые классы полосы спрашиваются ЗДЕСЬ, а не только там.
        # Ревизия Copilot по PR #333: ворота проверяли лишь запрещённое, а
        # полоса проверяет и разрешённое (`classify_patch_risk` -> `_is_allowed`).
        # Разрыв стоил суточного потолка: заявка проходила ворота, ЗАНИМАЛА
        # единицу и отвергалась полосой. Ворота, одобряющие неприменимое, —
        # не ворота, а задержка. Шире производства песочница остаётся: код и
        # тесты (`core`, `cli`, `tools`, `tests`) здесь разрешены, тогда как
        # автономное производство пропускает один класс — новый документ.
        if not _is_allowed(rel):
            return False, f"{rel!r} is outside the lane allowlist; the lane would refuse it"
    return True, "sandbox authority: workspace-local change, verified by the lane"


def sandbox_applies_today(workspace: Any, authority: SandboxAuthority) -> int:
    """Сколько применений песочница уже израсходовала сегодня (UTC)."""
    from core.autonomous_runtime import standing_runs_today

    return standing_runs_today(workspace, authority.id)


def record_sandbox_apply(workspace: Any, authority: SandboxAuthority) -> None:
    """Записать расход ДО применения — общим журналом, не своим."""
    from core.autonomous_runtime import record_standing_grant_use

    record_standing_grant_use(workspace, authority.id)


def reserve_sandbox_apply(workspace: Any, authority: SandboxAuthority) -> bool:
    """Занять одну единицу дневного потолка песочницы, или отказать.

    Тот же общий журнал и тот же атомарный примитив, что у стоячего гранта:
    два счётчика одного полномочия — это ноль счётчиков, а два ОДНОВРЕМЕННЫХ
    слива на одном счётчике без замка — потолок, который можно превысить.
    """
    from core.autonomous_runtime import reserve_standing_grant_use

    return reserve_standing_grant_use(
        workspace, authority.id, max_per_day=authority.max_applies_per_day
    )


class TermedSinks(frozenset):
    """Список стоков, в котором РАСШИРЕНИЕ носит свой срок с собой.

    Ревизия PR #333: кампания строит агента один раз и работает часами, а срок
    полномочия проверялся только при построении профиля. Полномочие, истёкшее
    на втором часу, продолжало открывать обучение до конца прогона.

    Перестроить агента нельзя по устройству: `_durable_learning_suppressed`
    (`core/loop_memory_write.py`) прямо оговаривает, что `durable_writes`
    привязан к экземпляру на всю его жизнь и API на прогон нет нарочно.
    Поэтому срок едет внутрь самого списка: проверка `sink not in allowlist`
    остаётся той же строкой кода, но после срока расширение отвечает «нет».

    Производственная часть списка сроку не подчинена — закрывается ровно то,
    что полномочие открыло.
    """

    __slots__ = ("_granted", "_deadline")

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
    """Безнадзорный профиль памяти: производственный, либо профиль песочницы.

    Без включённого полномочия возвращается КОПИЯ базового профиля — ни одна
    его строка не меняется, производственная политика остаётся той же, что
    была до 2026-09-17, и тип списка остаётся обычным `frozenset`.

    С полномочием список стоков расширяется ровно на `SANDBOX_DURABLE_SINKS`,
    и расширение носит СРОК полномочия с собой (`TermedSinks`). Это не
    «разрешение писать что угодно»: каждая запись по-прежнему проходит
    `MemoryWritePolicy` и сторожа `_durable_learning_suppressed`, у которого
    сухость прогона и режим аудита стоят ВЫШЕ любого списка.
    """
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
