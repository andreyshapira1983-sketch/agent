"""WHY THIS EXISTS.

Ревизия PR #333, дефект 5 — единственный, который ломал сам замысел опыта:

    `run_self_apply_lane` создаёт временную ветку, меняет код, гоняет
    целевые тесты, затем полную батарею, делает локальный commit — и сразу
    после этого `vcs.checkout(original_branch)`. То есть после успешной
    починки работающая песочница возвращается на СТАРЫЙ код.

Замер по живому дереву подтверждает: `core/self_apply_lane.py:740` —
`vcs.checkout(original_branch)` стоит следующей строкой после `vcs.commit`
(:739), а исход зовётся `committed_local` (:754). `lesson_from_apply_result`
при этом называет его `accepted`, хотя принятия не произошло: проверенный
кандидат лежит в боковой ветке, и следующий цикл стартует из того же кода,
который только что сломался.

Отсюда последовательность опыта обрывается на третьем звене:

    ошибка → ремонт → тесты → ??? → следующая ошибка

Замыкать её внутри полосы НЕЛЬЗЯ: тогда код, который сам себя меняет, сам же
себя и принимает, и достаточно одной успешной правки полосы, чтобы принять
что угодно дальше. Поэтому принимающий вынесен наружу и подчинён трём
правилам, каждое из которых здесь и проверяется:

  1. принять можно ТОЛЬКО тот SHA, который полоса предъявила в реестре;
  2. принять можно только шаг ВПЕРЁД от нынешней головы опыта — ровно один
     commit, чей единственный родитель есть нынешняя голова;
  3. перед принятием забор и батарея перепроверяются заново, в свежем
     рабочем дереве на этом самом SHA, а не по словам полосы.

Ни один тест здесь не трогает `main`, не делает push и не делает merge.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ── маленький настоящий репозиторий ──────────────────────────────────────────
#
# Подделка git здесь была бы бесполезна: половина утверждений — про то, как
# устроено родство коммитов, и подделанное родство доказывало бы только
# подделку. Репозиторий настоящий, но крошечный и целиком локальный.

def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr or done.stdout}")
    return (done.stdout or "").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "burn_in_repo"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch", "main")
    _git(root, "config", "user.email", "burn-in@localhost")
    _git(root, "config", "user.name", "Burn In")
    (root / "core").mkdir()
    (root / "core" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "core" / "policy.py").write_text("GATE = True\n", encoding="utf-8")
    # Ровно те строки, что стоят в `.gitignore` настоящего репозитория. Без
    # них снасть расходится с жизнью в существенном месте: полоса делает
    # `git add -A`, и указатель опыта уезжал бы в коммит кандидата.
    (root / ".gitignore").write_text(
        "state/burn_in_head.json\n"
        "state/burn_in_offers.jsonl\n"
        "state/burn_in_adoptions.jsonl\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "seed")
    return root


def _candidate(
    repo: Path,
    *,
    path: str = "core/widget.py",
    text: str = "VALUE = 2\n",
    force: bool = False,
) -> str:
    """Проверенный кандидат в боковой ветке — ровно то, что оставляет полоса."""
    base = _git(repo, "rev-parse", "HEAD")
    branch = f"self-apply-candidate-{_git(repo, 'rev-list', '--count', '--all')}-{len(text)}"
    _git(repo, "checkout", "--quiet", "-b", branch)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    if force:
        _git(repo, "add", "-f", path)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "self-apply: candidate")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "main")
    assert _git(repo, "rev-parse", "HEAD") == base, (
        "подготовка обязана оставить дерево на прежней голове — "
        "иначе тест доказывал бы принятие, которого не было"
    )
    return sha


class _Battery:
    """Батарея с заданным исходом. Считает, сколько раз её звали и откуда."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[Path] = []

    def __call__(self, worktree: Path) -> tuple[bool, str]:
        self.calls.append(worktree)
        return (self.ok, "" if self.ok else "3 failed")


# ── 2. отказы: чужой SHA, непредъявленный SHA, не-шаг ────────────────────────

def test_an_unoffered_commit_is_refused(repo: Path) -> None:
    """«Произвольный код не выбирает себе любой commit» — буквальная проверка.

    Кандидат существует, проходит батарею и является честным шагом вперёд.
    Не хватает ровно одного: полоса его не предъявляла. Этого достаточно для
    отказа, иначе принимающий берёт что угодно из истории.
    """
    from core.burn_in_supervisor import adopt_offer, experiment_head

    start = experiment_head(repo)
    sha = _candidate(repo)

    verdict = adopt_offer(repo, sha=sha, battery=_Battery(ok=True))

    assert not verdict.accepted
    assert "не предъявлен" in verdict.reason, verdict.reason
    assert experiment_head(repo) == start


def test_a_sibling_commit_is_refused(repo: Path) -> None:
    """Принимается только ШАГ от нынешней головы, а не любой её потомок.

    Две ветки от одной головы — два кандидата. Принять можно один; второй
    после этого больше не является шагом вперёд и обязан получить отказ, а не
    молча откатить опыт вбок.
    """
    from core.burn_in_supervisor import (
        adopt_offer,
        experiment_head,
        offer_verified_commit,
    )

    first = _candidate(repo, path="core/widget.py", text="VALUE = 2\n")
    second = _candidate(repo, path="core/other.py", text="OTHER = 1\n")
    for sha in (first, second):
        offer_verified_commit(repo, sha=sha, proposal_id="p", tests_run=["full"])

    assert adopt_offer(repo, sha=first, battery=_Battery(ok=True)).accepted
    verdict = adopt_offer(repo, sha=second, battery=_Battery(ok=True))

    assert not verdict.accepted
    assert "не шаг" in verdict.reason, verdict.reason
    assert experiment_head(repo) == first, "опыт уехал вбок"


def test_a_ref_name_is_not_a_sha(repo: Path) -> None:
    """Имя ссылки — не SHA. `HEAD`, `main~1`, короткий префикс — все отказ.

    Ссылка разрешается в разное в разное время: принять `HEAD` значит принять
    то, что окажется головой к моменту проверки. Опыт обязан двигаться по
    неподвижным именам.
    """
    from core.burn_in_supervisor import adopt_offer

    sha = _candidate(repo)
    for name in ("HEAD", "main", sha[:8], sha.upper() + "x"):
        verdict = adopt_offer(repo, sha=name, battery=_Battery(ok=True))
        assert not verdict.accepted, f"ссылка {name!r} принята как SHA"


# ── 4. след ──────────────────────────────────────────────────────────────────

def test_every_decision_is_written_down(repo: Path) -> None:
    """И принятие, и отказ попадают в журнал. Молчание — не исход."""
    from core.burn_in_supervisor import (
        adopt_offer,
        adoption_log,
        offer_verified_commit,
    )

    good = _candidate(repo)
    offer_verified_commit(repo, sha=good, proposal_id="p-ok", tests_run=["full"])
    adopt_offer(repo, sha=good, battery=_Battery(ok=False))
    adopt_offer(repo, sha=good, battery=_Battery(ok=True))

    written = [
        json.loads(line)
        for line in adoption_log(repo).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert [w["accepted"] for w in written] == [False, True]
    assert all(w["sha"] == good for w in written)
    assert all(w.get("reason") is not None for w in written)


# ── 6. петля замыкается: следующий цикл СТАРТУЕТ из принятого ────────────────
#
# Ревизия Copilot по PR #334, замечание к `next_start_point`:
#
#     «`next_start_point()` только читает указатель; ни один производственный
#     путь его не потребляет. `run_self_apply_lane` по-прежнему создаёт свою
#     временную ветку от головы ТЕКУЩЕГО checkout, а принятие нарочно не
#     трогает main, поэтому после успешного принятия следующая полоса снова
#     работает на старом коде.»
#
# Замер по живому дереву подтверждает буквально: `core/self_apply_lane.py:644`
# берёт `original_branch = vcs.current_branch()`, а :673 —
# `vcs.create_temp_branch(branch)` — ответвляется оттуда же. Указателя опыта
# не читает никто. Значит PR #334 научился ЗАПИСЫВАТЬ принятие и не научился
# из него СТАРТОВАТЬ: цепочка была метаданными.
#
# Замыкает её не агент, а принимающий: он и ставит рабочее дерево следующего
# цикла. Так у кода, который сам себя меняет, нет способа выбрать себе
# commit — он не выбирает дерево, в котором его запустят.


def test_the_next_cycle_cannot_be_asked_for_another_commit(
    repo: Path, tmp_path: Path
) -> None:
    """Дерево ставится на ГОЛОВУ ОПЫТА, а не на то, что попросили.

    Иначе замыкание петли само стало бы дырой: код, которому дали назвать
    SHA, выбрал бы себе любой — включая свой непринятый.
    """
    from core.burn_in_supervisor import (
        SupervisorError,
        adopt_offer,
        materialise_next_cycle,
        offer_verified_commit,
    )

    good = _candidate(repo, text="VALUE = 7\n")
    offer_verified_commit(repo, sha=good, proposal_id="p-1", tests_run=["full"])
    assert adopt_offer(repo, sha=good, battery=_Battery()).accepted
    unadopted = _candidate(repo, path="core/widget.py", text="VALUE = 999\n")

    with pytest.raises(SupervisorError):
        materialise_next_cycle(repo, tmp_path / "cycle_bad", sha=unadopted)


def test_the_second_cycle_reuses_its_tree(repo: Path, tmp_path: Path) -> None:
    """Десять часов — это много циклов, и каждый не заводит новое дерево.

    Второй вызов по тому же пути обязан переставить существующее дерево на
    новую голову, а не упасть на «worktree уже существует».
    """
    from core.burn_in_supervisor import (
        adopt_offer,
        materialise_next_cycle,
        offer_verified_commit,
    )

    first = _candidate(repo, text="VALUE = 2\n")
    offer_verified_commit(repo, sha=first, proposal_id="p-1", tests_run=["full"])
    adopt_offer(repo, sha=first, battery=_Battery())
    cycle = tmp_path / "cycle_same"
    materialise_next_cycle(repo, cycle)

    _git(repo, "checkout", "--quiet", "-b", "candidate-second", first)
    (repo / "core" / "widget.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "self-apply: candidate")
    second = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "main")
    offer_verified_commit(repo, sha=second, proposal_id="p-2", tests_run=["full"])
    assert adopt_offer(repo, sha=second, battery=_Battery()).accepted

    got = materialise_next_cycle(repo, cycle)

    assert got == second
    assert (cycle / "core" / "widget.py").read_text(encoding="utf-8") == "VALUE = 3\n", (
        "второй цикл остался на дереве первого"
    )


# ── 9. три оставшихся замечания ревизии по принимающему ──────────────────────


def test_the_fence_is_not_fooled_by_the_spelling_of_a_path(repo: Path) -> None:
    """I3. Забор принимающего сверял сырые строки `git diff --name-only`.

    Забор песочницы к этому времени уже сверяет приведённый путь
    (`core/burn_in_sandbox.py:_fence_prints`), а принимающий — нет. На
    файловой системе, безразличной к регистру, `Core/Policy.py` и
    `core/policy.py` — один файл и две разные строки.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    sha = _candidate(repo, path="core/policy.py", text="GATE = False\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    verdict = adopt_offer(
        repo, sha=sha, battery=_Battery(), fence={"Core/Policy.py"},
    )

    assert not verdict.accepted
    assert "забор" in verdict.reason


def test_two_adoptions_cannot_race_past_the_same_head(repo: Path) -> None:
    """S4. Между чтением головы и её записью не было замка.

    Два кандидата-брата от одной головы: принять можно ровно одного. Без
    сериализации оба читали одну `previous`, оба видели себя шагом вперёд и
    оба писали голову — цепочка теряла ветвь молча.
    """
    import threading

    from core.burn_in_supervisor import (
        adopt_offer,
        experiment_head,
        offer_verified_commit,
    )

    base = _git(repo, "rev-parse", "HEAD")
    left = _candidate(repo, text="VALUE = 10\n")
    right = _candidate(repo, text="VALUE = 20\n")
    assert _git(repo, "rev-parse", "HEAD") == base
    for sha in (left, right):
        offer_verified_commit(repo, sha=sha, proposal_id="p", tests_run=["full"])

    verdicts: list[Any] = []
    gate = threading.Barrier(2)

    def adopt(sha: str) -> None:
        gate.wait()
        verdicts.append(adopt_offer(repo, sha=sha, battery=_Battery()))

    threads = [threading.Thread(target=adopt, args=(s,)) for s in (left, right)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    accepted = [v for v in verdicts if v.accepted]
    assert len(accepted) == 1, (
        f"принято {len(accepted)} кандидатов от одной головы; "
        f"исходы: {[(v.accepted, v.reason) for v in verdicts]}"
    )
    assert experiment_head(repo) == accepted[0].sha


# ── 10. ревизия PR #335: четыре замечания по замыканию петли ─────────────────


def test_the_fence_still_refuses_a_case_variant_on_a_posix_runner(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То же утверждение целиком, через настоящее решение."""
    import os.path as ospath

    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    monkeypatch.setattr(ospath, "normcase", lambda s: s)
    sha = _candidate(repo, path="core/policy.py", text="GATE = False\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    verdict = adopt_offer(
        repo, sha=sha, battery=_Battery(), fence={"Core/Policy.py"},
    )

    assert not verdict.accepted


def test_an_offer_ledger_broken_at_the_byte_level_is_refused_not_raised(
    repo: Path,
) -> None:
    """Ревизия PR #338: правку `_was_offered` не спрашивал никто.

    В PR #338 я починил трёх читателей состояния, а свидетель реестра ходил
    через `--show`. Этот глагол печатает ожидающих и возвращается ДО принятия
    (`scripts/burn_in_supervisor.py`), то есть `_was_offered` в ядре при
    испорченных байтах не исполнялся ни разу, и регрессия в нём прошла бы все
    добавленные тесты, а упала бы на `adopt_offer`.

    Замечание верное и по существу: незакрытая правка — это не починка, а
    заявление о починке. Ожидаемый исход — отказ «не предъявлен»: реестр,
    который нельзя прочитать, ничего не разрешает.
    """
    from core.burn_in_supervisor import (
        adopt_offer,
        offer_ledger,
        offer_verified_commit,
    )

    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])
    offer_ledger(repo).write_bytes(b'{"sha": "\xff\xfe\x00"}')

    verdict = adopt_offer(repo, sha=sha, battery=_Battery())

    assert not verdict.accepted
    assert "не предъявлен" in verdict.reason, verdict.reason
