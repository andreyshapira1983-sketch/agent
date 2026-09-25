"""Тормоза агента — файлы, которые меняет только оператор.

Выключатель бюджета, лимиты расходов, дневной счётчик самоправок и список
запретов оператора останавливают или ограничивают агента. До 2026-09-25 их
берегли только ворота одобрения: перезапись существующего файла шла к
человеку, а новый файл (снятый выключатель ещё не лежит на диске) — вовсе
без вопроса. Тормоз, который агент может отпустить сам, не тормоз.

Источники: AgentSpec (arXiv 2503.18666, ICSE'26) — правило проверяется в
момент действия, до исполнения, детерминированно, а не судом модели;
«OPSEC for Agents» (dev.to/rxkov) — всё вне границы запрещено по
умолчанию, а отказ пишется в след как сигнал намерения.

Поэтому запрет жёсткий: инструменты записи (`file_write`, `shell_exec
touch/mkdir`) отказывают ДО одобрения, и никакое одобрение его не снимает.
Оператор меняет эти файлы сам — руками или своими командами (`:budget`,
`:kill-switch`), которые пишут мимо инструментов агента.
"""
from __future__ import annotations

from pathlib import Path

#: Пути от корня рабочей папки. Заодно — их рядом лежащие .tmp (атомарная
#: запись выключателя идёт через `<путь>.tmp` и replace).
CONTROL_RELPATHS: tuple[str, ...] = (
    "data/budget_kill_switch.json",
    "config/budget_limits.json",
    "data/patch_route_state.json",
    "config/vetoed_goals.txt",
    ".env",
    # Решения Клода по очереди на суд (core/judge_queue.py): подсудимый их не пишет.
    "data/judge_rulings.jsonl",
)


def control_file_hit(workspace_root: Path, target: Path) -> str | None:
    """Какой тормоз задевает запись в `target` (или None).

    Сравнение по разрешённому пути, так что `./config//budget_limits.json`
    и обход через ссылку ловятся так же, как прямое имя.
    """
    root = Path(workspace_root).resolve()
    resolved = Path(target).resolve()
    for rel in CONTROL_RELPATHS:
        guarded = (root / rel).resolve()
        if resolved == guarded or resolved == guarded.with_name(guarded.name + ".tmp"):
            return rel
    return None


def refuse_message(rel: str) -> str:
    return (f"refusing to write {rel}: it is an operator-only control file "
            "(kill switch, spend limits, self-repair cap, veto list, secrets); "
            "no approval unlocks it — ask the operator to change it")
