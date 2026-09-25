"""Принимающий: кто переводит проверенный кандидат в рабочее состояние опыта.

ПОЧЕМУ ЭТОТ МОДУЛЬ СУЩЕСТВУЕТ ОТДЕЛЬНО ОТ ПОЛОСЫ.

`core/self_apply_lane.py` умеет полный ремонт: ветка, правка, целевые тесты,
полная батарея, локальный commit. И сразу после этого — `checkout` обратно на
исходную ветку (:740). Ревизия PR #333 назвала это разомкнутой петлёй, и она
права: проверенный кандидат остаётся сбоку, следующий цикл стартует из того же
кода, который только что сломался.

Соблазн — убрать `checkout` из полосы. Это было бы худшей из возможных правок:
тогда код, который сам себя изменяет, сам же себя и принимает. Достаточно
одного удачного изменения, прошедшего батарею, чтобы дальше принимал уже
изменённый принимающий. Полномочие обязано быть у того, кого изменяемый код
изменить не может.

Отсюда устройство:

  полоса  →  предъявляет SHA в реестре  →  ПРИНИМАЮЩИЙ  →  голова опыта

Принимающий не верит полосе ни в чём. Он перепроверяет заново и принимает
только при выполнении ВСЕХ условий:

  1. имя — неподвижное: ровно сорок шестнадцатеричных знаков. Ссылка (`HEAD`,
     `main`, короткий префикс) разрешается в разное в разное время, и принять
     ссылку значит принять то, что окажется под ней к моменту проверки;
  2. SHA предъявлен полосой в реестре предложений. Иначе «принять» означает
     «взять любой commit из истории», а это ровно то, что запрещено;
  3. это ШАГ: единственный родитель кандидата — нынешняя голова опыта. Не
     потомок вообще, а именно следующий. Два кандидата от одной головы —
     принять можно один, второй обязан получить отказ, а не увезти опыт вбок;
  4. кандидат не трогает забор — ни ворота, ни счетовода полномочия, ни самого
     принимающего, ни полосу;
  5. батарея зелена В СВЕЖЕМ РАБОЧЕМ ДЕРЕВЕ НА ЭТОМ SHA. Не по словам полосы и
     не в старом дереве: зелень старого дерева не имеет отношения к
     предлагаемому изменению.

Только после всех пяти голова опыта двигается — атомарной заменой файла-
указателя. Это НЕ ветка: ни `main`, ни текущая ветка человека не двигаются,
push и merge не делаются и сделаны быть не могут (см. `_VERBS`).

Про «неизменяемость» честно. Принимающий лежит в том же дереве, что и агент, и
в абсолютном смысле неизменяемым быть не может. Закрыто это двумя разными
запретами, и оба нужны: правило 4 отказывает любому кандидату, трогающему этот
файл, а процесс принимающего запускается человеком из исходного checkout и
поэтому не перечитывает себя по ходу опыта. Изменить принимающего может только
человек, остановив опыт.
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

#: Забор принимающего. Шире забора песочницы ровно на одну запись — на самого
#: принимающего: без неё достаточно одного принятого изменения этого файла,
#: чтобы все прочие правила стали необязательными.
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
    # Память опыта. Не код, но полномочие: коммит, несущий свой указатель
    # головы, назначил бы опыту стартовую точку сам — ровно то, ради изъятия
    # чего принимающего и вынесли наружу. Полоса сгребает нетрекаемое через
    # `git add -A` (`core/safe_vcs.py:133`), так что попасть сюда можно и не
    # целясь; поэтому эти три имени ещё и в `.gitignore`.
    "state/burn_in_head.json",
    "state/burn_in_offers.jsonl",
    "state/burn_in_adoptions.jsonl",
    "config/burn_in_sandbox.json",
    "scripts/install_daemon.ps1",
})

#: Заповедные деревья принимающего — второй вид записи забора, и он есть
#: только здесь. У песочницы такого списка НЕТ и он ей не нужен: `.github/**`
#: она уже относит к запрещённому классу (`core.self_apply_lane._is_denied`,
#: который она спрашивает в `sandbox_execution_verdict`), и забор дерева там
#: оказался бы лишним. Забор принимающего перечислял
#: полномочие и память опыта и не перечислял СУДЬЮ: батарея описана в
#: `.github/workflows`, указания ревизору — в `.github/skills`. Кандидат,
#: переписывающий того, кто выносит о нём приговор, проходил принятие, потому
#: что проверка забора — точное сравнение имён, а судья — это дерево.
SUPERVISOR_FENCED_TREES: frozenset[str] = frozenset({
    ".github/",
})

#: Полное имя объекта git и ничего короче. Сорок знаков, нижний регистр.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Глаголы git, которыми принимающему разрешено пользоваться. Как и в
#: `core/safe_vcs.py`, это список разрешённого, а не запрещённого: свойство не
#: в том, что `push` забыт, а в том, что ни один вызов не достаёт до сети.
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
    """SHA, из которого стартует следующий цикл.

    Пока принято ничего не было — это нынешняя голова репозитория. Дальше это
    файл, и именно поэтому цепочка переживает перезапуск: без записанного
    указателя опыт не длиннее одного шага.

    ОТСУТСТВУЮЩИЙ файл и ИСПОРЧЕННЫЙ файл — разные события, и сводить их к
    одному ответу нельзя (ревизия PR #334). Первое значит «опыт ещё не
    начинался». Второе значит «опыт шёл, и его память повреждена»: ответить на
    это головой репозитория — значит тихо отмотать цепочку к тому коду, с
    которого всё начиналось, и записать это в журнал как честный первый шаг.
    Повреждённая память останавливает опыт.

    Испорченные БАЙТЫ — тоже повреждённая память (ревизия PR #337).
    `read_text` падает раньше разбора, а `UnicodeDecodeError` — подкласс
    `ValueError`, но не `OSError`, поэтому он пролетал мимо обоих
    обработчиков: договор держался только для файла, который удалось
    прочитать как текст.
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
    """Поставить рабочее дерево следующего цикла на голову опыта.

    Недостающее звено (ревизия PR #334). До этого принятие двигало указатель,
    но `run_self_apply_lane` ответвлялся от головы ТЕКУЩЕГО checkout
    (`core/self_apply_lane.py:644`), а указателя не читал никто, — значит
    следующий цикл снова работал на том коде, который только что сломался, и
    цепочка была метаданными.

    Дерево заводит ПРИНИМАЮЩИЙ, и в этом всё свойство: у кода, который сам
    себя меняет, нет способа выбрать себе commit, потому что он не выбирает
    дерево, в котором его запустят. `sha` здесь — не выбор, а сверка: назвать
    можно только то, что уже принято, и назвать неправильно значит получить
    отказ.

    Дерево каждый раз заводится заново, а не переставляется: остатки прошлого
    цикла не вправе доехать до следующего — иначе «стартовали из принятого»
    перестало бы быть правдой.

    Голова читается под тем же замком, под которым заводится дерево (ревизия
    PR #335): между чтением и `worktree add` чужое принятие вправе сдвинуть
    указатель, и тогда цикл стартовал бы из устаревшего коммита, а функция
    назвала бы его следующим. Путь дерева приводится к полному до обеих
    проверок — `_git` ходит с `cwd=repo`, поэтому относительное имя иначе
    читалось в двух разных системах координат.
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
    """Замена указателя целиком: сначала рядом, потом на место.

    Опыт десятичасовой; обрыв питания посреди записи не вправе оставить голову
    в наполовину написанном виде — такой файл `experiment_head` прочитает как
    отсутствующий и молча откатит опыт к голове репозитория.
    """
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
    """Сторона полосы: «вот SHA, который я проверила».

    Предъявление НЕ является принятием и никаких прав не даёт. Оно лишь
    сужает множество того, что принимающий вообще станет рассматривать.
    """
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
        # `UnicodeDecodeError` — не `OSError` (ревизия PR #337), и без него
        # реестр, испорченный на уровне байтов, ронял принимающего целиком.
        # Соседняя оговорка ниже гласит, что одна испорченная строка не вправе
        # остановить десятичасовой опыт; испорченные байты не вправе тем более,
        # а умолчание здесь и так «не предъявлен», то есть отказ.
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # Испорченная строка не разрешает и не запрещает — читаем дальше.
            # Умолчание всего чтения и так «не предъявлен», то есть отказ.
            continue
        # Не словарь — тоже законный JSON: `[1, 2]`, `"строка"`, `17`, `null`
        # разбираются без ошибки, а `.get` на них даёт `AttributeError`
        # (ревизия PR #334). Тогда кто угодно, кто умеет дописать строку в
        # журнал, останавливал бы принятие навсегда.
        if isinstance(row, dict) and row.get("sha") == sha:
            return True
    return False


# ── решение ──────────────────────────────────────────────────────────────────

def _fence_key(path: str) -> str:
    """Один вид записи для сличения с забором.

    Забор песочницы (`core/burn_in_sandbox._fence_prints`) к этому времени уже
    сверяет приведённый путь, а принимающий сверял сырую строку
    `git diff --name-only` (ревизия PR #334). На файловой системе,
    безразличной к регистру, `Core/Policy.py` и `core/policy.py` — один файл и
    две разные строки, то есть забор обходился написанием имени.

    Регистр складывается ВСЕГДА, а не через `os.path.normcase` (ревизия
    PR #335). На POSIX `normcase` — тождество, поэтому починка держалась
    только на Windows, а батарея репозитория ходит по Linux: там забора
    по-прежнему не было, и мой свидетель был бы красным. Складывать всюду —
    отказ строже нужного на файловой системе, различающей регистр (там
    `Core/Policy.py` — честно другой файл), но цена отказа здесь — один
    лишний круг, а цена пропуска — код за забором.
    """
    return str(path).replace("\\", "/").strip().strip('"').casefold()


def _in_fenced_tree(key: str) -> bool:
    """Лежит ли приведённый путь внутри заповедного дерева.

    Сверяется приведённой записью, как и имя: иначе второй вид записи оказался
    бы слабее первого и обход, закрытый ревизиями PR #334 и #335 для имён,
    снова открылся бы для деревьев.
    """
    return any(key.startswith(t.casefold()) for t in SUPERVISOR_FENCED_TREES)


def adopt_offer(
    repo: Path,
    *,
    sha: str,
    battery: Callable[[Path], tuple[bool, str]],
    fence: Any = None,
) -> AdoptionVerdict:
    """Единственный способ сдвинуть голову опыта.

    `battery` получает путь СВЕЖЕГО рабочего дерева на кандидате и отвечает
    `(зелено, подробность)`. Он вынесен наружу нарочно: принимающий решает,
    ЧТО считается доказательством, а чем его добывать — дело вызывающего
    (в опыте это `pytest`, в тестах — детерминированная подделка).

    Решение целиком стоит под замком: между чтением головы и её записью лежит
    полная батарея, то есть минуты (ревизия PR #334). Без сериализации два
    кандидата-брата от одной головы оба читали бы одну `previous`, оба
    признавались бы шагом вперёд и оба писали бы голову — цепочка теряла бы
    ветвь молча, и журнал показывал бы два честных принятия.
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
    """Отдельное дерево на кандидате, батарея в нём, затем дерева нет.

    Свежее — потому что проверять надо код кандидата, а не текущее дерево с
    чужими недописанными правками. Убирается всегда, в том числе после
    отказа: десять часов отказов не вправе оставлять десять деревьев на диске.
    """
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
