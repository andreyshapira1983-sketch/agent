# -*- coding: utf-8 -*-
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

import contextlib
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


# ── 1. принятие ──────────────────────────────────────────────────────────────

def test_a_verified_commit_becomes_the_next_head(repo: Path) -> None:
    """Успешный путь целиком: предъявлено → перепроверено → принято.

    Красный до правки: принимающего не существовало, и голова опыта никуда не
    двигалась — следующий цикл стартовал из того же кода.
    """
    from core.burn_in_supervisor import (
        adopt_offer, experiment_head, offer_verified_commit,
    )

    start = _git(repo, "rev-parse", "HEAD")
    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["targeted", "full"])

    battery = _Battery(ok=True)
    verdict = adopt_offer(repo, sha=sha, battery=battery)

    assert verdict.accepted, f"проверенный кандидат не принят: {verdict.reason}"
    assert experiment_head(repo) == sha, "голова опыта осталась на старом коде"
    assert experiment_head(repo) != start
    assert battery.calls, "батарея не перепроверена — принято по словам полосы"


def test_the_battery_runs_on_the_candidate_not_on_the_old_code(repo: Path) -> None:
    """Перепроверка обязана идти НА КАНДИДАТЕ, иначе она проверяет не то.

    Батарея, запущенная в старом дереве, зелена по причине, не имеющей
    отношения к предлагаемому изменению.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    sha = _candidate(repo, text="VALUE = 42\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-2", tests_run=["full"])

    seen: list[str] = []

    def battery(worktree: Path) -> tuple[bool, str]:
        seen.append((worktree / "core" / "widget.py").read_text(encoding="utf-8"))
        return True, ""

    adopt_offer(repo, sha=sha, battery=battery)

    assert seen == ["VALUE = 42\n"], (
        f"батарея видела не код кандидата, а {seen!r}"
    )


def test_the_working_tree_is_left_where_it_was(repo: Path) -> None:
    """Принятие не вправе переставлять ветку рабочего дерева.

    Голова опыта — отдельный указатель, а не `main` и не текущая ветка. Опыт
    двигается, репозиторий человека остаётся на месте.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    before_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_head = _git(repo, "rev-parse", "HEAD")
    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-3", tests_run=["full"])

    adopt_offer(repo, sha=sha, battery=_Battery(ok=True))

    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert _git(repo, "rev-parse", "HEAD") == before_head, "принятие сдвинуло main"


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
        adopt_offer, experiment_head, offer_verified_commit,
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


# ── 3. отказы: забор и батарея ───────────────────────────────────────────────

def test_a_commit_touching_the_fence_is_refused(repo: Path) -> None:
    """Кандидат, трогающий забор, не принимается — даже зелёный.

    Иначе забор перестаёт быть забором за одно принятие: достаточно одной
    правки `core/policy.py`, прошедшей батарею, и дальше полномочие решает
    уже изменённый код.
    """
    from core.burn_in_supervisor import (
        adopt_offer, experiment_head, offer_verified_commit,
    )

    start = experiment_head(repo)
    sha = _candidate(repo, path="core/policy.py", text="GATE = False\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-fence", tests_run=["full"])

    verdict = adopt_offer(repo, sha=sha, battery=_Battery(ok=True))

    assert not verdict.accepted
    assert "забор" in verdict.reason, verdict.reason
    assert experiment_head(repo) == start


def test_the_supervisor_protects_itself(repo: Path) -> None:
    """Принимающий стоит за собственным забором.

    Без этого правила достаточно одного принятого изменения самого
    принимающего, чтобы все остальные правила стали необязательными.
    """
    from core.burn_in_supervisor import SUPERVISOR_FENCE

    assert "core/burn_in_supervisor.py" in SUPERVISOR_FENCE
    assert "core/self_apply_lane.py" in SUPERVISOR_FENCE
    assert "core/burn_in_sandbox.py" in SUPERVISOR_FENCE


def test_a_red_battery_refuses_and_leaves_the_head_alone(repo: Path) -> None:
    """Красная перепроверка — отказ, и голова опыта не двигается."""
    from core.burn_in_supervisor import (
        adopt_offer, experiment_head, offer_verified_commit,
    )

    start = experiment_head(repo)
    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-red", tests_run=["full"])

    verdict = adopt_offer(repo, sha=sha, battery=_Battery(ok=False))

    assert not verdict.accepted
    assert "батарея" in verdict.reason, verdict.reason
    assert experiment_head(repo) == start


def test_a_refusal_leaves_no_worktree_behind(repo: Path) -> None:
    """Отказ убирает за собой: свежее дерево не переживает проверку.

    Десять часов отказов не вправе превращаться в десять часов накопленного
    мусора на диске — это тот самый молчаливый рост ошибок, ради которого
    затевался аудит.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-clean", tests_run=["full"])

    adopt_offer(repo, sha=sha, battery=_Battery(ok=False))

    listing = _git(repo, "worktree", "list")
    assert listing.count("\n") == 0, f"после отказа остались деревья:\n{listing}"


# ── 4. след ──────────────────────────────────────────────────────────────────

def test_every_decision_is_written_down(repo: Path) -> None:
    """И принятие, и отказ попадают в журнал. Молчание — не исход."""
    from core.burn_in_supervisor import (
        adoption_log, adopt_offer, offer_verified_commit,
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


def test_the_head_survives_a_restart(repo: Path) -> None:
    """Голова опыта переживает перезапуск — иначе цепочка не длиннее одного шага."""
    from core.burn_in_supervisor import (
        adopt_offer, experiment_head, next_start_point, offer_verified_commit,
    )

    sha = _candidate(repo)
    offer_verified_commit(repo, sha=sha, proposal_id="p-live", tests_run=["full"])
    adopt_offer(repo, sha=sha, battery=_Battery(ok=True))

    assert next_start_point(repo) == sha
    assert experiment_head(repo) == sha


# ── 5. слово исхода ──────────────────────────────────────────────────────────

def test_a_local_commit_is_not_called_accepted(repo: Path) -> None:
    """`committed_local` — «кандидат проверен», а не «агент принял».

    Ревизия PR #333: `lesson_from_apply_result` называла локальный commit
    исходом `accepted`, хотя работающий агент оставался на прежнем коде. Урок
    с неверным словом учит неверному: по журналу выходило, что изменение
    принято, тогда как принято оно не было.
    """
    from core.self_build_rules import lesson_from_apply_result

    lesson = lesson_from_apply_result(
        {
            "proposal_id": "p-word",
            "status": "committed_local",
            "reason": "tests passed",
            "files_changed": ["core/widget.py"],
            "tests_run": ["targeted", "full"],
            "commit_hash": "a" * 40,
        },
        origin="rule_approved_apply",
    )

    assert lesson is not None
    assert lesson.outcome == "verified_candidate", (
        f"локальный commit назван {lesson.outcome!r}; принятие делает "
        "принимающий, а не полоса"
    )


def test_the_lane_still_leaves_the_repository_where_it_found_it() -> None:
    """Полоса по-прежнему возвращается на исходную ветку, и это ВЕРНО.

    Соблазн «починить» дефект 5 внутри полосы — убрать `checkout`. Тогда
    принятие делал бы тот же код, который себя и менял. Свидетель стоит
    здесь, чтобы будущая правка не приняла это за улучшение.
    """
    import inspect

    import core.self_apply_lane as lane

    text = inspect.getsource(lane)
    assert "vcs.checkout(original_branch)" in text, (
        "полоса перестала возвращать дерево на исходную ветку — "
        "принятие переехало внутрь того, кто себя меняет"
    )


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


def test_the_next_cycle_starts_from_the_adopted_commit(repo: Path, tmp_path: Path) -> None:
    """После принятия следующий цикл работает на коде кандидата.

    Это и есть недостающее звено последовательности: ошибка → ремонт → тесты
    → ПРИНЯТИЕ → новый работающий агент. До правки голова опыта двигалась, а
    работать продолжал старый код.
    """
    from core.burn_in_supervisor import (
        adopt_offer, materialise_next_cycle, offer_verified_commit,
    )

    sha = _candidate(repo, text="VALUE = 42\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])
    assert adopt_offer(repo, sha=sha, battery=_Battery()).accepted

    cycle = tmp_path / "cycle_next"
    got = materialise_next_cycle(repo, cycle)

    assert got == sha, f"следующий цикл поставлен не на принятое: {got!r}"
    assert (cycle / "core" / "widget.py").read_text(encoding="utf-8") == "VALUE = 42\n", (
        "дерево следующего цикла несёт СТАРЫЙ код — принятие осталось "
        "записью в файле, а работает по-прежнему то, что сломалось"
    )


def test_the_next_cycle_cannot_be_asked_for_another_commit(
    repo: Path, tmp_path: Path
) -> None:
    """Дерево ставится на ГОЛОВУ ОПЫТА, а не на то, что попросили.

    Иначе замыкание петли само стало бы дырой: код, которому дали назвать
    SHA, выбрал бы себе любой — включая свой непринятый.
    """
    from core.burn_in_supervisor import (
        SupervisorError, adopt_offer, materialise_next_cycle,
        offer_verified_commit,
    )

    good = _candidate(repo, text="VALUE = 7\n")
    offer_verified_commit(repo, sha=good, proposal_id="p-1", tests_run=["full"])
    assert adopt_offer(repo, sha=good, battery=_Battery()).accepted
    unadopted = _candidate(repo, path="core/widget.py", text="VALUE = 999\n")

    with pytest.raises(SupervisorError):
        materialise_next_cycle(repo, tmp_path / "cycle_bad", sha=unadopted)


def test_materialising_the_next_cycle_leaves_the_checkout_alone(
    repo: Path, tmp_path: Path
) -> None:
    """Репозиторий человека остаётся там, где стоял.

    Принятие двигает ОТДЕЛЬНЫЙ указатель. Ни ветка рабочего дерева, ни main
    не трогаются — это условие всего замысла, а не деталь.
    """
    from core.burn_in_supervisor import (
        adopt_offer, materialise_next_cycle, offer_verified_commit,
    )

    before_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_head = _git(repo, "rev-parse", "HEAD")
    sha = _candidate(repo, text="VALUE = 5\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])
    adopt_offer(repo, sha=sha, battery=_Battery())

    materialise_next_cycle(repo, tmp_path / "cycle_x")

    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert _git(repo, "rev-parse", "HEAD") == before_head, (
        "принятие переставило голову рабочей копии человека"
    )


def test_the_second_cycle_reuses_its_tree(repo: Path, tmp_path: Path) -> None:
    """Десять часов — это много циклов, и каждый не заводит новое дерево.

    Второй вызов по тому же пути обязан переставить существующее дерево на
    новую голову, а не упасть на «worktree уже существует».
    """
    from core.burn_in_supervisor import (
        adopt_offer, materialise_next_cycle, offer_verified_commit,
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


# ── 7. испорченный указатель не отматывает опыт назад ────────────────────────
#
# Ревизия PR #334, замечание к `experiment_head`: при ЛЮБОМ сбое чтения он
# возвращает `git rev-parse HEAD`. Отсутствующий файл и испорченный файл —
# разные события: первое значит «опыт ещё не начинался», второе значит «опыт
# шёл, и его память повреждена». Свести их к одному ответу — значит тихо
# начать цепочку заново на старом коде, и в журнале это будет выглядеть как
# добросовестный первый шаг.


def test_a_damaged_head_pointer_stops_the_experiment(repo: Path) -> None:
    """Повреждённая память опыта — отказ, а не молчаливый откат к HEAD."""
    from core.burn_in_supervisor import SupervisorError, experiment_head

    head_file = repo / "state" / "burn_in_head.json"
    head_file.parent.mkdir(parents=True, exist_ok=True)
    head_file.write_text("{\"sha\": \"not-a-sha\"", encoding="utf-8")

    with pytest.raises(SupervisorError):
        experiment_head(repo)


def test_a_missing_head_pointer_still_means_the_first_cycle(repo: Path) -> None:
    """Сосед: отсутствие файла — по-прежнему законное начало опыта."""
    from core.burn_in_supervisor import experiment_head

    assert experiment_head(repo) == _git(repo, "rev-parse", "HEAD")


# ── 8. память опыта лежит внутри дерева, которое опыт переписывает ───────────
#
# Замерено при постройке теста выше, сверх ревизии. Полоса на
# `core/self_apply_lane.py:738` зовёт `vcs.stage_all()`, то есть буквально
# `git add -A` (`core/safe_vcs.py:133`). Указатель опыта лежит в
# `state/burn_in_head.json`, git его не отслеживает и не игнорирует. Отсюда
# две беды подряд:
#
#   1. кандидат ЗАБИРАЕТ указатель в свой коммит, а `vcs.checkout(original)`
#      следом его УДАЛЯЕТ — и `experiment_head` читает это как «опыт ещё не
#      начинался», молча отматывая цепочку к исходному коду;
#   2. принятый коммит, несущий свой `state/burn_in_head.json`, назначил бы
#      опыту стартовую точку — ровно то полномочие, ради изъятия которого
#      принимающего и вынесли наружу.
#
# Замер (временный репозиторий, та же последовательность, что у полосы):
# голова после принятия — кандидат; после `add -A` во втором кандидате и
# `checkout main` файла НЕТ, `experiment_head` возвращает seed.


def test_a_candidate_may_not_carry_the_experiments_memory(repo: Path) -> None:
    """Кандидат, тронувший указатель опыта, отвергается забором."""
    from core.burn_in_supervisor import (
        SUPERVISOR_FENCE, adopt_offer, offer_verified_commit,
    )

    assert "state/burn_in_head.json" in SUPERVISOR_FENCE, (
        "указатель опыта не под забором: принятый коммит смог бы сам "
        "назначить, откуда стартует следующий цикл"
    )
    sha = _candidate(
        repo, path="state/burn_in_head.json", text='{"sha": "x"}\n', force=True
    )
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    verdict = adopt_offer(repo, sha=sha, battery=_Battery())

    assert not verdict.accepted


def test_the_experiments_memory_cannot_be_staged_by_the_lane() -> None:
    """`git add -A` в настоящем репозитории не вправе подобрать указатель.

    Забор ловит кандидата, который тронул память ОСОЗНАННО. Эта проверка про
    случайность: полоса сгребает всё нетрекаемое, и без игнора указатель
    уезжает в коммит без единого намерения.
    """
    root = Path(__file__).resolve().parents[1]
    for name in ("state/burn_in_head.json", "state/burn_in_offers.jsonl",
                 "state/burn_in_adoptions.jsonl"):
        done = subprocess.run(
            ["git", "check-ignore", "-q", name], cwd=str(root),
            capture_output=True, text=True,
        )
        assert done.returncode == 0, f"{name} не игнорируется; `git add -A` его заберёт"


# ── 9. три оставшихся замечания ревизии по принимающему ──────────────────────


def test_a_non_dict_ledger_row_does_not_break_adoption(repo: Path) -> None:
    """I4. `[1, 2]` — законный JSON и не словарь.

    `json.loads(line).get("sha")` ловилось только на `ValueError`, а `.get` на
    списке даёт `AttributeError`. Одна испорченная строка реестра роняла
    принятие целиком — то есть кто угодно, кто умеет дописать строку в
    журнал, останавливал опыт.
    """
    from core.burn_in_supervisor import adopt_offer, offer_ledger, offer_verified_commit

    sha = _candidate(repo)
    ledger = offer_ledger(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # Именно ПЕРЕД годной строкой: чтение возвращается на первом совпадении,
    # и мусор позади него ничего бы не доказал.
    ledger.write_text("[1, 2]\n\"строка\"\n17\nnull\n", encoding="utf-8")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    assert adopt_offer(repo, sha=sha, battery=_Battery()).accepted


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
        adopt_offer, experiment_head, offer_verified_commit,
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


def test_the_fence_folds_case_on_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """`os.path.normcase` на POSIX — тождество, то есть забора там не было.

    Самое неприятное из ревизии PR #335, и потому что это МОЯ ошибка метода:
    свидетель `test_the_fence_is_not_fooled_by_the_spelling_of_a_path` зелен
    на Windows и красен на Linux, а я показал только зелёный. Соседний тест
    песочницы про регистр несёт `skipif` ровно по этой причине — я его правило
    не перенёс.

    Здесь POSIX подделывается честно: `normcase` заменяется тождеством,
    то есть ровно тем, чем он там и является.
    """
    import os.path as ospath

    from core import burn_in_supervisor as sup

    monkeypatch.setattr(ospath, "normcase", lambda s: s)

    assert sup._fence_key("Core/Policy.py") == sup._fence_key("core/policy.py"), (
        "на файловой системе, различающей регистр, забор обходится "
        "написанием имени"
    )


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


def test_the_next_cycle_tree_is_placed_where_the_caller_meant(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Относительный путь дерева читался в двух разных системах координат.

    `workspace.exists()` считал от текущего каталога, а `git worktree add`
    через `cwd=repo` — от корня репозитория. Запуск
    `--repo /tmp/repo --next cycle` из другого места заводил
    `/tmp/repo/cycle`, а проверка существования и печать говорили о
    `<cwd>/cycle`. Следующий вызов падал или сообщал не о том дереве.
    """
    from core.burn_in_supervisor import materialise_next_cycle

    here = tmp_path / "откуда-запустили"
    here.mkdir()
    monkeypatch.chdir(here)

    materialise_next_cycle(repo, Path("cycle"))

    assert (here / "cycle" / "core" / "widget.py").exists(), (
        "дерево заведено не там, где просил вызывающий"
    )
    assert not (repo / "cycle").exists(), "дерево уехало внутрь репозитория"


def test_the_head_is_read_under_the_same_lock_that_creates_the_tree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Между чтением головы и заведением дерева не было замка.

    Замечание ревизии, помеченное как suppressed, и оно верное: другой
    принимающий вправе сдвинуть голову ровно в этот промежуток, и тогда цикл
    стартует из устаревшего коммита, а функция назовёт его следующим.
    """
    from core import burn_in_supervisor as sup

    events: list[str] = []
    real_lock = sup.exclusive_file_lock
    real_git = sup._git

    @contextlib.contextmanager
    def watched_lock(path):  # noqa: ANN001, ANN202
        events.append("замок взят")
        with real_lock(path):
            yield
        events.append("замок отпущен")

    def watched_git(where, *args):  # noqa: ANN001, ANN202
        if args[:2] == ("worktree", "add"):
            events.append("дерево заведено")
        return real_git(where, *args)

    monkeypatch.setattr(sup, "exclusive_file_lock", watched_lock)
    monkeypatch.setattr(sup, "_git", watched_git)

    sup.materialise_next_cycle(repo, tmp_path / "cycle_locked")

    assert "дерево заведено" in events, "дерево не заводилось вовсе"
    assert events.index("замок взят") < events.index("дерево заведено"), (
        "голова прочитана и дерево заведено вне замка"
    )
    assert events.index("дерево заведено") < events.index("замок отпущен")


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


@pytest.mark.parametrize(
    "rel",
    [
        ".github/workflows/ci.yml",
        ".github/skills/code-review/SKILL.md",
    ],
)
def test_the_accepting_side_refuses_a_candidate_that_rewrites_its_judge(
    repo: Path, rel: str
) -> None:
    """У принимающего тот же пробел, что и у песочницы.

    `SUPERVISOR_FENCE` перечисляет полномочие и память опыта, но не того, кто
    выносит приговор. Кандидат, переписывающий батарею или указания ревизору,
    проходил принятие: проверка забора — точное сравнение имён, а судья — это
    дерево.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    sha = _candidate(repo, path=rel, text="что угодно\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    verdict = adopt_offer(repo, sha=sha, battery=_Battery())

    assert not verdict.accepted
    assert "забор" in verdict.reason, verdict.reason


def test_a_fenced_tree_is_not_fooled_by_the_spelling_of_a_path(repo: Path) -> None:
    """Дерево сверяется так же, как и имя: приведённой записью.

    Иначе второй вид записи оказался бы слабее первого, и обход, закрытый
    ревизиями PR #334 и #335 для имён, снова открылся бы для деревьев.
    """
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    sha = _candidate(repo, path=".GitHub/workflows/ci.yml", text="x\n")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])

    verdict = adopt_offer(repo, sha=sha, battery=_Battery())

    assert not verdict.accepted
    assert "забор" in verdict.reason, verdict.reason
