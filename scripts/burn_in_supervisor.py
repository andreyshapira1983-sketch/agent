# -*- coding: utf-8 -*-
"""Внешний принимающий: один шаг опыта, запускаемый ЧЕЛОВЕКОМ.

Запускать из исходного checkout, а не из того дерева, которое агент меняет:

    python scripts/burn_in_supervisor.py --repo C:\\path\\to\\sandbox

Что он делает. Берёт последний SHA, предъявленный полосой в реестре
(`state/burn_in_offers.jsonl`), и отдаёт его `core.burn_in_supervisor.adopt_offer`.
Тот перепроверяет всё заново — неподвижность имени, предъявление, шаг от головы
опыта, целость забора — и гоняет ПОЛНУЮ батарею в свежем рабочем дереве на этом
самом SHA. Голова опыта двигается только после зелёной батареи.

Чего он НЕ делает и делать не может: не трогает `main`, не переставляет ветку
рабочего дерева, не делает push и не делает merge (список разрешённых глаголов
git в `core/burn_in_supervisor._VERBS` их не содержит вовсе).

Почему отдельный процесс, а не часть тика. Принимающий обязан быть тем, кого
изменяемый код изменить не может. Человек стартует его из исходного checkout,
и по ходу десяти часов он себя не перечитывает; кандидат, трогающий его файл,
получает отказ по правилу забора. Внутри тика оба свойства были бы утрачены.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bounded_subprocess import run_with_tree_kill  # noqa: E402
from core.burn_in_supervisor import (  # noqa: E402
    adopt_offer,
    experiment_head,
    materialise_next_cycle,
    offer_ledger,
    SupervisorError,
)

#: Потолок батареи. Десятичасовой опыт не вправе застрять на одном кандидате:
#: зависшая проверка неотличима от отказа и обязана им стать.
_BATTERY_TIMEOUT_SECONDS = 3600


def _pending_offers(repo: Path, head: str) -> list[str]:
    """SHA из реестра, ещё не принятые. Порядок предъявления сохраняется."""
    try:
        lines = offer_ledger(repo).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` — не `OSError` (ревизия PR #337). Без него
        # реестр, испорченный на уровне байтов, давал трассировку вместо
        # пустого списка ожидающих.
        return []
    seen: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # Не словарь — тоже законный JSON. `[1, 2]` и `"строка"` разбираются
        # без ошибки, а `.get` на них даёт `AttributeError`, и внешний
        # принимающий падает целиком (ревизия PR #334). Одна испорченная
        # строка не вправе остановить десятичасовой опыт.
        if not isinstance(row, dict):
            continue
        sha = str(row.get("sha") or "")
        if sha and sha != head and sha not in seen:
            seen.append(sha)
    return seen


def _battery(worktree: Path) -> tuple[bool, str]:
    """Полная батарея в дереве кандидата. Зелено — значит зелено целиком.

    Ждём через `core/bounded_subprocess`, а не через `subprocess.run(timeout=)`:
    последний убивает только прямого потомка, после чего `communicate` без
    потолка продолжает ждать трубу, которую держит внук. Батарея pytest
    порождает внуков по устройству, а докстринг ограничителя несёт два замера
    от 2026-09-05 — 30 с превратились в 16 минут, 20 с в 600 с. Зависшая
    проверка неотличима от отказа и обязана им стать.
    """
    stdout, _stderr, code, timed_out = run_with_tree_kill(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=str(worktree),
        env=None,
        timeout=_BATTERY_TIMEOUT_SECONDS,
    )
    if timed_out:
        return False, f"батарея не уложилась в {_BATTERY_TIMEOUT_SECONDS} с"
    text = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else str(stdout)
    tail = text.strip().splitlines()[-1:] or [""]
    return code == 0, tail[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="путь к песочнице")
    parser.add_argument("--sha", default="", help="принять именно этот SHA")
    parser.add_argument("--show", action="store_true",
                        help="только показать голову опыта и ожидающие предложения")
    parser.add_argument("--next", default="", metavar="PATH",
                        help="поставить рабочее дерево следующего цикла на голову опыта")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()

    if args.next:
        # Единственный потребитель указателя опыта, и потому единственное
        # место, где цепочка принятий превращается в работающий код. Дерево
        # заводит принимающий: у кода, который сам себя меняет, нет способа
        # выбрать себе commit, потому что он не выбирает дерево.
        #
        # `--sha` здесь протянут насквозь (ревизия PR #335): раньше он молча
        # пропадал, и `--next --sha <чужой>` заводил дерево на текущей голове
        # да ещё и выходил с успехом. Сверка, которую можно не заметить, — не
        # сверка. Отказ печатается словами, а не трассировкой: принимающего
        # читает человек.
        #
        # Голова здесь нарочно НЕ читается заранее (ревизия PR #336): раньше
        # `experiment_head` звался до разбора глагола, и испорченный указатель
        # давал трассировку прежде, чем управление доходило до этого
        # обработчика, — то есть комментарий выше опровергался файлом, в
        # котором он написан. `materialise_next_cycle` читает голову сам и
        # делает это под замком, так что читать её здесь было и незачем.
        try:
            started = materialise_next_cycle(
                repo, Path(args.next), sha=args.sha or None,
            )
        except SupervisorError as exc:
            print(f"отказ: {exc}")
            return 2
        print(f"следующий цикл стартует из {started}")
        print(f"дерево: {Path(args.next).resolve()}")
        return 0

    # Тот же отказ словами и на остальных глаголах. Fail-closed чтение головы
    # завёл PR #335: до него испорченный указатель вообще не был отказом.
    # Значит трассировку на `--show` и на принятии завёл тот же мой коммит.
    try:
        head = experiment_head(repo)
    except SupervisorError as exc:
        print(f"отказ: {exc}")
        return 2
    pending = _pending_offers(repo, head)

    if args.show:
        print(f"голова опыта: {head}")
        print("ожидают:", ", ".join(pending) if pending else "нет")
        return 0

    wanted = [args.sha] if args.sha else pending[-1:]
    if not wanted:
        print("предъявленных кандидатов нет")
        return 0

    verdict = adopt_offer(repo, sha=wanted[0], battery=_battery)
    print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2))
    return 0 if verdict.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
