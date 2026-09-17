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
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "seed")
    return root


def _candidate(repo: Path, *, path: str = "core/widget.py", text: str = "VALUE = 2\n") -> str:
    """Проверенный кандидат в боковой ветке — ровно то, что оставляет полоса."""
    base = _git(repo, "rev-parse", "HEAD")
    branch = f"self-apply-candidate-{_git(repo, 'rev-list', '--count', '--all')}-{len(text)}"
    _git(repo, "checkout", "--quiet", "-b", branch)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
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
