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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.burn_in_supervisor import (  # noqa: E402
    adopt_offer,
    experiment_head,
    offer_ledger,
)

#: Потолок батареи. Десятичасовой опыт не вправе застрять на одном кандидате:
#: зависшая проверка неотличима от отказа и обязана им стать.
_BATTERY_TIMEOUT_SECONDS = 3600


def _pending_offers(repo: Path, head: str) -> list[str]:
    """SHA из реестра, ещё не принятые. Порядок предъявления сохраняется."""
    try:
        lines = offer_ledger(repo).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            sha = str(json.loads(line).get("sha") or "")
        except ValueError:
            continue
        if sha and sha != head and sha not in seen:
            seen.append(sha)
    return seen


def _battery(worktree: Path) -> tuple[bool, str]:
    """Полная батарея в дереве кандидата. Зелено — значит зелено целиком."""
    try:
        done = subprocess.run(  # noqa: S603 — фиксированный argv
            [sys.executable, "-m", "pytest", "-q", "--no-header"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_BATTERY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"батарея не уложилась в {_BATTERY_TIMEOUT_SECONDS} с"
    tail = (done.stdout or "").strip().splitlines()[-1:] or [""]
    return done.returncode == 0, tail[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="путь к песочнице")
    parser.add_argument("--sha", default="", help="принять именно этот SHA")
    parser.add_argument("--show", action="store_true",
                        help="только показать голову опыта и ожидающие предложения")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    head = experiment_head(repo)
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
