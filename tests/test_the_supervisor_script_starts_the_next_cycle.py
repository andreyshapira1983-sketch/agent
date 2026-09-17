# -*- coding: utf-8 -*-
"""WHY THIS EXISTS.

Ревизия PR #334, три замечания по внешнему принимающему
(`scripts/burn_in_supervisor.py`). Все три проверены по живому коду и
подтверждены.

1. ГЛАВНОЕ. «`next_start_point()` только читает указатель; ни один
   производственный путь его не потребляет». Замер: единственным его
   потребителем был тест. Принимающий научился ЗАПИСЫВАТЬ принятие и не умел
   из него СТАРТОВАТЬ, поэтому цепочка принятий оставалась метаданными, а
   следующий цикл ответвлялся от головы текущего checkout
   (`core/self_apply_lane.py:644`). Здесь у указателя появляется настоящий
   потребитель: глагол `--next`, ставящий рабочее дерево следующего цикла на
   принятую голову.

2. `_pending_offers` звал `json.loads(line).get("sha")`, ловя только
   `ValueError`. Строка реестра `[1, 2]` — законный JSON и не словарь, `.get`
   на списке даёт `AttributeError`, и внешний принимающий падает целиком.
   Одна испорченная строка не вправе остановить опыт на десять часов.

3. `_battery` пользовался `subprocess.run(..., timeout=)`. В этом
   репозитории уже есть `core/bounded_subprocess.py`, заведённый ровно
   потому, что такой потолок лжёт: убивается прямой потомок, а `communicate`
   без потолка продолжает ждать трубу, которую держит внук. Докстринг того
   модуля несёт два замера от 2026-09-05: 30 с превратились в 16 минут, 20 с
   — в 600 с. Батарея pytest порождает внуков по устройству.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.burn_in_supervisor as script


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
    root = tmp_path / "sandbox"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch", "main")
    _git(root, "config", "user.email", "burn-in@localhost")
    _git(root, "config", "user.name", "Burn In")
    (root / "core").mkdir()
    (root / "core" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "state/burn_in_head.json\n"
        "state/burn_in_offers.jsonl\n"
        "state/burn_in_adoptions.jsonl\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "seed")
    return root


def _adopted(repo: Path) -> str:
    """Один честный шаг опыта: предъявлено, перепроверено, принято."""
    from core.burn_in_supervisor import adopt_offer, offer_verified_commit

    _git(repo, "checkout", "--quiet", "-b", "cand")
    (repo / "core" / "widget.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "self-apply: candidate")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "main")
    offer_verified_commit(repo, sha=sha, proposal_id="p-1", tests_run=["full"])
    verdict = adopt_offer(repo, sha=sha, battery=lambda _w: (True, ""))
    assert verdict.accepted, verdict.reason
    return sha


# ── 1. у указателя появляется потребитель ────────────────────────────────────

def test_the_script_can_start_the_next_cycle_from_the_adopted_commit(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--next` ставит дерево следующего цикла на принятую голову.

    Красный до правки: глагола не существовало, и принятие некому было
    превратить в работающий код.
    """
    sha = _adopted(repo)
    cycle = tmp_path / "cycle"

    code = script.main(["--repo", str(repo), "--next", str(cycle)])

    assert code == 0
    assert (cycle / "core" / "widget.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert sha in capsys.readouterr().out


def test_the_next_cycle_is_refused_when_nothing_was_adopted_yet(
    repo: Path, tmp_path: Path
) -> None:
    """Сосед: без принятия дерево ставится на нынешнюю голову, а не падает.

    Первый цикл опыта — законное состояние, и начинать его надо с того, что
    есть.
    """
    cycle = tmp_path / "cycle_first"

    assert script.main(["--repo", str(repo), "--next", str(cycle)]) == 0
    assert (cycle / "core" / "widget.py").read_text(encoding="utf-8") == "VALUE = 1\n"


# ── 2. испорченная строка реестра ────────────────────────────────────────────

def test_one_malformed_ledger_row_does_not_stop_the_experiment(repo: Path) -> None:
    """Строка-не-словарь пропускается, соседние предложения читаются."""
    from core.burn_in_supervisor import offer_ledger

    head = _git(repo, "rev-parse", "HEAD")
    good = "b" * 40
    ledger = offer_ledger(repo)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "[1, 2]\n"
        '"строка"\n'
        "17\n"
        "не json совсем\n"
        + json.dumps({"sha": good}) + "\n",
        encoding="utf-8",
    )

    assert script._pending_offers(repo, head) == [good]


# ── 3. потолок батареи обязан быть настоящим ─────────────────────────────────

def test_the_battery_is_bounded_by_the_tree_killing_runner() -> None:
    """Батарея ждёт через `core/bounded_subprocess`, а не через `run(timeout=)`.

    Утверждение о происхождении, а не о тексте: `_battery` обязана звать
    `run_with_tree_kill`. Голый `subprocess.run(timeout=)` в дереве процессов
    pytest не ограничивает ожидание вовсе — модуль-ограничитель заведён с
    двумя замерами именно этого.
    """
    import ast
    import inspect
    import textwrap

    source = inspect.getsource(script._battery)
    assert "run_with_tree_kill" in source, (
        "батарея ждёт голым subprocess.run(timeout=): зависший внук "
        "остановит десятичасовой опыт навсегда"
    )
    # Докстринг называет голый вызов, чтобы объяснить, почему его тут нет;
    # утверждение — про тело, поэтому пояснение из него вырезано разбором.
    fn = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(fn, ast.FunctionDef)
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    assert "subprocess.run" not in ast.unparse(fn)


def test_a_battery_that_outlives_its_ceiling_is_a_refusal(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Превышенный потолок — отказ с названной причиной, а не исключение."""
    def _timed_out(argv, *, cwd, env, timeout):  # noqa: ANN001, ANN202
        return b"", b"", None, True

    monkeypatch.setattr(script, "run_with_tree_kill", _timed_out)

    ok, reason = script._battery(repo)

    assert not ok
    assert str(script._BATTERY_TIMEOUT_SECONDS) in reason


def test_a_green_battery_is_still_green(repo: Path) -> None:
    """Сосед: обычный путь через ограничитель по-прежнему читает исход."""
    def _ok(argv, *, cwd, env, timeout):  # noqa: ANN001, ANN202
        return b"5 passed\n", b"", 0, False

    original = script.run_with_tree_kill
    script.run_with_tree_kill = _ok
    try:
        ok, tail = script._battery(repo)
    finally:
        script.run_with_tree_kill = original

    assert ok and tail == "5 passed"


def test_a_wrong_sha_is_refused_even_when_a_tree_is_asked_for(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--sha` при `--next` молча пропадал (ревизия PR #335).

    `sha` объявлен сверкой: назвать можно только то, что уже принято. Но
    глагол `--next` звал `materialise_next_cycle` без него, и
    `--next --sha <чужой>` заводил дерево на текущей голове да ещё и выходил
    с успехом. Сверка, которую можно не заметить, — не сверка.

    Печать спрашивается вслух (ревизия PR #336): `capsys` был объявлен и не
    использован, а ведь именно словесный отказ и есть половина утверждения —
    принимающего читает человек, и «не 0» ему ни о чём не говорит.
    """
    code = script.main([
        "--repo", str(repo), "--next", str(tmp_path / "cycle"),
        "--sha", "0" * 40,
    ])

    assert code != 0, "дерево заведено вопреки названному не тому коммиту"
    assert not (tmp_path / "cycle").exists()
    printed = capsys.readouterr().out
    assert "отказ:" in printed, "отказ молчит"
    assert "0" * 40 in printed, "отказ не называет коммит, из-за которого он случился"


# ── ревизия PR #336: отказ словами на всех глаголах ──────────────────────────


def _break_the_head(repo: Path) -> None:
    """Испортить указатель опыта ровно так, как это делает обрыв питания."""
    from core.burn_in_supervisor import _head_file

    head = _head_file(repo)
    head.parent.mkdir(parents=True, exist_ok=True)
    head.write_text("{\"sha\": \"обор", encoding="utf-8")


def test_a_broken_head_is_refused_in_words_not_a_traceback(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`experiment_head` звался ДО разбора глагола (ревизия PR #336).

    В PR #335 чтение головы стало fail-closed: испорченный указатель — это
    `SupervisorError`, а не «опыт не начинался». В PR #336 я обернул `--next`
    и написал в комментарии «отказ печатается словами, а не трассировкой:
    принимающего читает человек». Тремя строками выше стояло
    `head = experiment_head(repo)` вне всякого обработчика, то есть комментарий
    опровергался собственным файлом.
    """
    _break_the_head(repo)

    code = script.main(["--repo", str(repo), "--next", str(tmp_path / "cycle")])

    assert code == 2, "трассировка вместо отказа"
    assert "отказ:" in capsys.readouterr().out
    assert not (tmp_path / "cycle").exists()


def test_a_broken_head_is_refused_in_words_on_every_verb(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """То же и для `--show`: трассировку завёл я, значит убирать мне.

    Ревизия назвала только `--next`, но до fail-closed чтения (PR #335)
    испорченный указатель вообще не был отказом. Значит трассировку на
    остальных глаголах завёл тот же мой коммит, и чинится она здесь же, а не
    когда-нибудь.
    """
    _break_the_head(repo)

    code = script.main(["--repo", str(repo), "--show"])

    assert code == 2, "трассировка вместо отказа"
    assert "отказ:" in capsys.readouterr().out
