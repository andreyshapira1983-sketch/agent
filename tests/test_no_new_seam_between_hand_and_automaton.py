"""Шов: механизм записи, который умеет человек руками, а автомат — нет.

Слово оператора 2026-09-23: «думай шире, не лови баги по одному». Поводом
были три случая за сутки, каждый найденный отдельно и поздно:

* приём своих сигналов в дефекты висел на команде одобрения — в кампании не
  вызывался НИ РАЗУ, и корень, починенный накануне, в автомате был мёртв;
* регистрация веб-источника доступна только команде `:ingest-web`, поэтому за
  ночь агент прочитал десятки первоисточников и не записал ни одного;
* оттуда же его собственный ответ оператору: связи «прочитал X → стал работать
  лучше» у него нет, потому что накопления не происходит.

Исследование производственных агентов называет это прямо: отказы копятся
«в швах между компонентами, где не бежит ни один тест» (arXiv:2606.14589,
годовое наблюдение; там же измерено, что ~70% тихих отказов поймал человек
глазами, а тесты и аудиты всё это время были зелёными). Шов невидим поодиночке:
код есть, функция работает, тесты зелёные — не работает только связь.

Этот сторож ищет швы РАЗОМ и краснеет на НОВОМ. Он не требует, чтобы каждая
команда была доступна автомату: презентация, учения и отладка законно остаются
ручными. Он требует, чтобы новый разрыв был назван и объяснён здесь — как и
всякая другая линейка проекта.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Имена механизмов записи: то, что МЕНЯЕТ состояние или копит знание.
_WRITING_NAMES = (
    "ingest", "register", "record", "save", "store", "upsert", "bank",
    "apply", "settle", "commit", "learn", "consolidat", "promote",
)

#: Известные швы и причина, по которой каждый пока терпим. Список только
#: СОКРАЩАЕТСЯ: всякая новая строка здесь — признание, что механизм построен
#: для человека и автомату недоступен.
_KNOWN_SEAMS: dict[str, str] = {
    "core/state_store_drill.py::run_state_store_drill":
        "учение по хранилищу — проверка руками перед прогоном, автомату не нужна",
    # Массовый приём по команде человека остаётся ручным СОЗНАТЕЛЬНО: он берёт
    # тему, проект или ленту целиком и тянет наружу десятки страниц за раз —
    # это решение о расходе и о доверии к незнакомому источнику, а такие
    # принимает человек. Настоящая дыра была не здесь: работающий агент не
    # записывал даже то, что УЖЕ прочитал сам. Она закрыта отдельно
    # (`core/read_sources_registry.py`): страница, открытая через web_fetch, и
    # файл, прочитанный через file_read, попадают в реестр источников по
    # окончании хода. Если однажды понадобится, чтобы агент принимал тему сам,
    # эти строки убираются — но тогда вместе с решением о расходе.
    "core/ingestion.py::ingest_source":
        "приём одного источника по команде — решение человека о доверии к нему",
    "core/ingestion.py::ingest_project":
        "приём проекта целиком — разовая операция человека, не работа хода",
    "core/ingestion.py::ingest_web_topic":
        "приём темы с поиском наружу — решение о расходе; агент читает сам, и "
        "прочитанное регистрируется в core/read_sources_registry.py",
    "core/ingestion.py::ingest_rss_feed":
        "ленты — подписка, которой агенту никто не давал (rss_fetch закрыт "
        "для автономной цели в _AUTONOMOUS_GOAL_BLOCKED_TOOLS)",
}


def _sources(*globs: str) -> str:
    out = []
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            out.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def _seams() -> dict[str, str]:
    """Механизмы записи, которые видит команда и не видит автомат."""
    hand = _sources("cli/*.py")
    automaton = _sources(
        "agent_tick.py", "core/autonomous_runtime*.py", "core/campaign*.py",
        "core/loop*.py", "core/drive*.py", "core/patch_route.py",
        "core/stuck_route.py", "core/self_build*.py", "core/defect_intake.py",
        "core/note_contract.py",
    )
    # `core/ingestion.py` сюда НЕ входит намеренно: функции приёма зовут друг
    # друга внутри модуля, и, засчитав его автоматом, сторож объявил бы шов
    # закрытым, не будучи им. Прибор, который прячет то, что ищет, — это ровно
    # та поломка, против которой он написан.
    found: dict[str, str] = {}
    for path in sorted(ROOT.glob("core/*.py")) + sorted(ROOT.glob("tools/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name.startswith("_") or not any(w in name.lower() for w in _WRITING_NAMES):
                continue
            called = re.compile(rf"\b{re.escape(name)}\s*\(")
            if called.search(hand) and not called.search(automaton):
                rel = path.relative_to(ROOT).as_posix()
                found[f"{rel}::{name}"] = (ast.get_docstring(node) or "").split("\n")[0][:80]
    return found


def test_no_unnamed_seam_between_hand_and_automaton() -> None:
    seams = _seams()
    surprises = sorted(set(seams) - set(_KNOWN_SEAMS))

    assert not surprises, (
        "механизм записи умеет человек руками, а автомат — нет: "
        + "; ".join(f"{s} ({seams[s]})" for s in surprises)
        + ". Подключи его к автономному пути — или назови в _KNOWN_SEAMS "
          "вместе с причиной, по которой он остаётся ручным."
    )


def test_the_known_list_does_not_rot() -> None:
    """Закрытый шов не остаётся в списке: список только сокращается."""
    seams = _seams()
    stale = sorted(set(_KNOWN_SEAMS) - set(seams))

    assert not stale, (
        f"эти швы уже закрыты, убери их из _KNOWN_SEAMS: {stale}")


def test_the_guard_can_actually_see_a_seam() -> None:
    """Сторож проверяется на себе: он обязан находить настоящий разрыв.

    Без этого тест мог бы молчать из-за опечатки в образце имени и годами
    считаться зелёным — ровно тот случай, против которого он и написан.
    """
    hand = "run_state_store_drill(agent, workspace)"
    called = re.compile(r"\brun_state_store_drill\s*\(")

    assert called.search(hand), "образец имени не ловит собственный вызов"
    assert any(w in "run_state_store_drill" for w in _WRITING_NAMES)
